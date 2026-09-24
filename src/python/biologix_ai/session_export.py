"""
Package a discovery session's output for download.

A session folder (``runs/<id>/``) holds everything a run produced: the summary
report, minimized complex PDBs, structure renders, retrosynthesis output, energies,
and logs. On a hosted server that folder is unreachable, so the agent packages it
into one zip and hands the user signed, time-limited download links.

Links are HMAC-signed so a browser can open them without an Authorization header.
A token names one file under ``runs/`` and an expiry; it grants nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EXPORT_DIRNAME = "exports"
DOWNLOAD_PATH = "/downloads"
DEFAULT_TTL_S = 24 * 3600
MAX_HIGHLIGHTS = 30

_SKIP_DIR_NAMES = {EXPORT_DIRNAME, "__pycache__", ".discovery_pdf_cache"}
_REPORT_NAMES = {"SUMMARY_REPORT.pdf", "SUMMARY_REPORT.md", "SESSION_TRANSCRIPT.md"}
_IMAGE_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".pse", ".pml"}
_STRUCTURE_SUFFIXES = {".pdb", ".cif", ".mmcif", ".xyz", ".sdf", ".mol2", ".dcd", ".xtc"}
_LOG_NAMES = {"tool_events.jsonl", "tool_errors.log", "protocol_state.json"}

CATEGORY_ORDER = ("report", "structure", "image", "result", "log")
CATEGORY_TITLES = {
    "report": "Reports",
    "structure": "Structures (PDB and trajectories)",
    "image": "Images and PyMOL renders",
    "result": "Energies, retrosynthesis and discovery data",
    "log": "Logs and audit trail",
}


def _key() -> bytes:
    raw = (
        os.environ.get("BIOLOGIX_DOWNLOAD_KEY")
        or os.environ.get("BIOLOGIX_OAUTH_STORAGE_KEY")
        or os.environ.get("BIOLOGIX_MCP_TOKEN")
        or ""
    ).strip()
    return raw.encode("utf-8")


def signing_available() -> bool:
    return bool(_key())


def public_base_url() -> str:
    """The server's public origin, or "" when it has none (stdio)."""
    explicit = os.environ.get("BIOLOGIX_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    resource = os.environ.get("BIOLOGIX_MCP_RESOURCE_URL", "").strip().rstrip("/")
    if resource.endswith("/mcp"):
        resource = resource[: -len("/mcp")]
    return resource


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(relpath: str, ttl_s: int = DEFAULT_TTL_S, now: Optional[float] = None) -> str:
    """Token granting read access to *relpath* (relative to the runs root) until it expires."""
    payload = json.dumps(
        {"p": relpath, "e": int((now if now is not None else time.time()) + ttl_s)},
        separators=(",", ":"),
    ).encode("utf-8")
    body = _b64(payload)
    return f"{body}.{_b64(hmac.new(_key(), body.encode('ascii'), hashlib.sha256).digest())}"


def verify_token(token: str, now: Optional[float] = None) -> Optional[str]:
    """The relative path a valid, unexpired *token* names, else None."""
    if not _key():
        return None
    try:
        body, signature = token.split(".", 1)
        expected = _b64(hmac.new(_key(), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_unb64(body))
        if int(payload["e"]) < (now if now is not None else time.time()):
            return None
        return str(payload["p"])
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def resolve_download(token: str, runs_root: Path, now: Optional[float] = None) -> Optional[Path]:
    """The file *token* grants, or None. Never leaves *runs_root*."""
    relpath = verify_token(token, now=now)
    if not relpath:
        return None
    root = runs_root.resolve()
    target = (root / relpath).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def _categorize(rel: Path) -> str:
    name = rel.name
    suffix = rel.suffix.lower()
    if name in _REPORT_NAMES or (rel.parts[0] == "retrosynthesis" and suffix in {".pdf", ".md"}):
        return "report"
    if suffix in _STRUCTURE_SUFFIXES:
        return "structure"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if name in _LOG_NAMES or rel.parts[0] == "audit" or suffix == ".log":
        return "log"
    return "result"


def collect_files(session: Path) -> List[Dict[str, Any]]:
    """Every deliverable file in *session*, with its category and size."""
    files: List[Dict[str, Any]] = []
    for path in sorted(session.rglob("*")):
        rel = path.relative_to(session)
        if not path.is_file() or path.suffix == ".zip":
            continue
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in rel.parts):
            continue
        files.append(
            {
                "path": rel.as_posix(),
                "category": _categorize(rel),
                "bytes": path.stat().st_size,
            }
        )
    return files


def _manifest_markdown(session: Path, files: List[Dict[str, Any]]) -> str:
    lines = [
        f"# Biologix session export: {session.name}",
        "",
        f"Created {datetime.now().isoformat(timespec='seconds')}. {len(files)} files.",
        "",
    ]
    for category in CATEGORY_ORDER:
        group = [f for f in files if f["category"] == category]
        if not group:
            continue
        lines += [f"## {CATEGORY_TITLES[category]}", ""]
        lines += [f"- `{f['path']}` ({f['bytes']:,} bytes)" for f in group]
        lines.append("")
    return "\n".join(lines)


def build_zip(session: Path) -> Tuple[Path, List[Dict[str, Any]]]:
    """Zip *session* (minus caches and earlier exports) into ``exports/``, with a MANIFEST.md."""
    files = collect_files(session)
    out_dir = session / EXPORT_DIRNAME
    out_dir.mkdir(exist_ok=True)
    archive = out_dir / f"{session.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(f"{session.name}/MANIFEST.md", _manifest_markdown(session, files))
        for entry in files:
            bundle.write(session / entry["path"], f"{session.name}/{entry['path']}")
    return archive, files


def _highlight_rank(entry: Dict[str, Any]) -> Tuple[int, int, str]:
    """Reports first, then minimized complexes and renders, then the rest."""
    path = entry["path"]
    minimized = 0 if "minimized" in path else 1
    return (CATEGORY_ORDER.index(entry["category"]), minimized, path)


def package_session(
    session: Path,
    runs_root: Path,
    include_logs: bool = True,
    ttl_s: int = DEFAULT_TTL_S,
) -> Dict[str, Any]:
    """Build the zip and return download links for it and for the key files."""
    if not session.is_dir():
        return {"ok": False, "error": f"Session folder not found: {session.name}"}
    archive, files = build_zip(session)
    base = public_base_url()
    linkable = bool(base) and signing_available()
    root = runs_root.resolve()

    def link(path: Path) -> Optional[str]:
        if not linkable:
            return None
        rel = path.resolve().relative_to(root).as_posix()
        return f"{base}{DOWNLOAD_PATH}/{sign_token(rel, ttl_s)}/{path.name}"

    shown = [f for f in files if include_logs or f["category"] != "log"]
    highlights = sorted(
        (f for f in shown if f["category"] in {"report", "structure", "image"}),
        key=_highlight_rank,
    )[:MAX_HIGHLIGHTS]
    counts: Dict[str, int] = {}
    for entry in files:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    result: Dict[str, Any] = {
        "ok": True,
        "session": session.name,
        "zip": {
            "name": archive.name,
            "bytes": archive.stat().st_size,
            "download_url": link(archive),
            "server_path": str(archive),
        },
        "files_in_zip": len(files),
        "counts_by_category": counts,
        "key_files": [
            {
                "path": f["path"],
                "category": f["category"],
                "bytes": f["bytes"],
                "download_url": link(session / f["path"]),
            }
            for f in highlights
        ],
        "links_expire_in_hours": round(ttl_s / 3600, 1),
    }
    if linkable:
        result["instructions"] = (
            "Show the user the zip download_url as a link, then the report and image links "
            "from key_files. State that links expire in links_expire_in_hours hours and that "
            "you can package the session again for fresh ones. This does not change the "
            "pipeline: if a run is in progress, continue with the last next_required_tool."
        )
    else:
        result["instructions"] = (
            "This server has no public URL, so there are no download links. Tell the user the "
            "zip is at zip.server_path on the machine running the server."
        )
    return result
