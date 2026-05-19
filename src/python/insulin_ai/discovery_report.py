#!/usr/bin/env python3
"""
Discovery session reporting utilities.

**AI-driven workflow (preferred):** the agent writes narrative ``SUMMARY_REPORT.md`` in the
session folder (scientific prose, tables, interpretation), calls ``render_psmiles_png`` for 2D
structures, then ``compile_markdown_to_pdf`` to produce a PDF. No LLM runs inside this module.

**Batch helper:** ``write_session_summary_reports`` can regenerate a minimal MD+PDF from
``agent_iteration_*.json`` only—convenience when no narrative report is needed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from insulin_ai.psmiles_drawing import safe_filename_basename, save_psmiles_png


def _load_iteration_files(session_dir: Path) -> List[Path]:
    files = sorted(
        f
        for f in session_dir.iterdir()
        if f.is_file() and f.name.startswith("agent_iteration_") and f.name.endswith(".json")
    )
    return sorted(files, key=lambda p: p.name)


def collect_psmiles_entries_from_feedback(feedback: Any) -> List[Tuple[str, str]]:
    """
    Extract (label, psmiles) pairs from a feedback dict.

    Supports high_performers as list of dicts (name, psmiles) or list of strings,
    and high_performer_psmiles as list of strings.
    """
    out: List[Tuple[str, str]] = []
    if not isinstance(feedback, dict):
        return out

    hp = feedback.get("high_performers")
    if isinstance(hp, list):
        for item in hp:
            if isinstance(item, dict):
                name = item.get("name") or item.get("material_name") or "candidate"
                psm = item.get("psmiles") or item.get("chemical_structure")
                if psm and "[*]" in str(psm):
                    out.append((str(name), str(psm).strip()))
            elif isinstance(item, str):
                if "[*]" in item:
                    out.append((item[:48], item))

    hpp = feedback.get("high_performer_psmiles")
    if isinstance(hpp, list):
        for psm in hpp:
            if isinstance(psm, str) and "[*]" in psm:
                out.append((psm[:48], psm.strip()))

    # Dedupe by psmiles, keep first label
    seen: set[str] = set()
    deduped: List[Tuple[str, str]] = []
    for label, psm in out:
        if psm in seen:
            continue
        seen.add(psm)
        deduped.append((label, psm))
    return deduped


def collect_session_psmiles_entries(
    session_dir: Path,
    *,
    include_all_iterations: bool = True,
) -> Tuple[List[Tuple[str, str]], List[Dict[str, Any]]]:
    """
    Load iteration JSON files and return merged (label, psmiles) and raw metadata list.
    """
    files = _load_iteration_files(session_dir)
    if not include_all_iterations and files:
        files = [files[-1]]
    all_entries: List[Tuple[str, str]] = []
    meta: List[Dict[str, Any]] = []
    seen_psm: set[str] = set()
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fb = data.get("feedback") or {}
        meta.append(
            {
                "file": str(path.name),
                "iteration": data.get("iteration"),
                "timestamp": data.get("timestamp"),
                "notes": data.get("notes"),
            }
        )
        for label, psm in collect_psmiles_entries_from_feedback(fb):
            if psm in seen_psm:
                continue
            seen_psm.add(psm)
            all_entries.append((label, psm))
    return all_entries, meta


def _ascii_safe(text: str) -> str:
    """Best-effort ASCII for PDF core fonts."""
    return text.encode("ascii", "replace").decode("ascii")


# PNGs written by openmm_evaluate_psmiles / render scripts under session ``structures/``.
_STRUCTURE_VIZ_SUFFIXES: Tuple[Tuple[str, str, str], ...] = (
    ("_monomer.png", "monomer", "Repeat unit (2D)"),
    ("_complex_preview.png", "preview", "Complex preview (minimized)"),
    ("_complex_chemviz.png", "chemviz", "Complex (PyMOL cartoon + polymer sticks)"),
    ("_complex_minimized_pymol.png", "pymol", "Complex (PyMOL)"),
)


def gather_structure_visualizations(structures_dir: Path) -> Dict[str, Dict[str, str]]:
    """
    Collect openmm_evaluate_psmiles-style PNG paths grouped by basename (e.g. ``Candidate_0``).

    Returns mapping ``base_name -> {kind: "structures/<file>.png"}`` for files present on disk.
    """
    out: Dict[str, Dict[str, str]] = {}
    if not structures_dir.is_dir():
        return out
    for path in sorted(structures_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".png":
            continue
        name = path.name
        for suffix, kind, _cap in _STRUCTURE_VIZ_SUFFIXES:
            if name.endswith(suffix):
                base = name[: -len(suffix)]
                rel = f"structures/{name}"
                out.setdefault(base, {})[kind] = rel
                break
    return out


def _markdown_images_for_viz_group(base: str, kinds: Dict[str, str]) -> List[str]:
    """Markdown lines for one candidate's visualization set (fixed order)."""
    lines: List[str] = []
    label_base = _ascii_safe(base.replace("_", " "))
    for _suffix, kind, caption in _STRUCTURE_VIZ_SUFFIXES:
        rel = kinds.get(kind)
        if not rel:
            continue
        cap = _ascii_safe(f"{label_base} - {caption}")
        lines.append(f"![{cap}]({rel})")
        lines.append("")
    return lines


def write_markdown_summary(
    session_dir: Path,
    entries: List[Tuple[str, str]],
    png_paths: Dict[str, Path],
    *,
    title: str,
    iteration_meta: List[Dict[str, Any]],
) -> Path:
    """Write SUMMARY_REPORT.md (Markdown with relative image links)."""
    md_path = session_dir / "SUMMARY_REPORT.md"
    structures = session_dir / "structures"
    viz_all = gather_structure_visualizations(structures)
    viz_shown_slugs: set[str] = set()

    lines: List[str] = [
        f"# {_ascii_safe(title)}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Session:** `{session_dir.name}`  ",
        "",
        f"## Structures ({len(entries)} unique PSMILES)",
        "",
    ]
    for label, psm in entries:
        slug = safe_filename_basename(label)
        rel = png_paths.get(psm)
        img_line = f"![{label}](structures/{rel.name})" if rel else "*Image unavailable*"
        lines.extend(
            [
                f"### {_ascii_safe(label)}",
                "",
                f"**PSMILES:** `{psm}`",
                "",
                img_line,
                "",
            ]
        )
        if slug in viz_all:
            lines.extend(_markdown_images_for_viz_group(slug, viz_all[slug]))
            viz_shown_slugs.add(slug)
        lines.extend(["---", ""])

    extra_viz_bases = sorted(b for b in viz_all if b not in viz_shown_slugs)
    if extra_viz_bases:
        lines.append("## Molecular visualizations (session structures)")
        lines.append("")
        lines.append(
            "PNGs from OpenMM evaluation or render scripts under `structures/` "
            "(monomer 2D, complex preview, PyMOL chemviz; optional `*_complex_minimized_pymol.png`).",
        )
        lines.append("")
        for base in extra_viz_bases:
            lines.append(f"### {_ascii_safe(base.replace('_', ' '))}")
            lines.append("")
            lines.extend(_markdown_images_for_viz_group(base, viz_all[base]))
            lines.append("---")
            lines.append("")

    if iteration_meta:
        lines.append("## Iteration metadata")
        lines.append("")
        for m in iteration_meta:
            notes = m.get("notes") or ""
            lines.append(f"- `{m.get('file')}` - iteration {m.get('iteration')}, notes: {str(notes)[:200]}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def write_session_summary_reports(
    session_dir: Path,
    *,
    title: str = "Discovery summary",
    include_all_iterations: bool = True,
) -> Dict[str, Any]:
    """
    Render PNGs under ``session_dir/structures/``, write ``SUMMARY_REPORT.md`` and
    ``SUMMARY_REPORT.pdf` when possible.

    Returns a JSON-serializable result dict with paths and errors.
    """
    session_dir = Path(session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    structures = session_dir / "structures"
    structures.mkdir(parents=True, exist_ok=True)

    entries, meta = collect_session_psmiles_entries(
        session_dir, include_all_iterations=include_all_iterations
    )
    if not entries:
        return {
            "ok": False,
            "error": "No PSMILES found in agent_iteration_*.json feedback.",
            "session_dir": str(session_dir),
        }

    png_by_psmiles: Dict[str, Path] = {}
    render_errors: List[str] = []
    for idx, (label, psm) in enumerate(entries):
        base = safe_filename_basename(label)
        out = structures / f"{idx:03d}_{base}.png"
        r = save_psmiles_png(psm, out, overwrite=True)
        if r.get("ok"):
            png_by_psmiles[psm] = Path(r["path"])
        else:
            render_errors.append(f"{label}: {r.get('error', 'unknown')}")

    md_path = write_markdown_summary(
        session_dir, entries, png_by_psmiles, title=title, iteration_meta=meta
    )
    pdf_out = compile_markdown_to_pdf(
        session_dir,
        markdown_filename=md_path.name,
        output_pdf_name="SUMMARY_REPORT.pdf",
    )

    out: Dict[str, Any] = {
        "ok": True,
        "session_dir": str(session_dir),
        "markdown": str(md_path),
        "n_structures": len(entries),
        "n_png_rendered": len(png_by_psmiles),
        "render_errors": render_errors,
    }
    if pdf_out.get("ok"):
        out["pdf"] = pdf_out.get("pdf")
    else:
        out["pdf_error"] = pdf_out.get("error", "pdf failed")
    return out


def _html_resolve_image_src(html: str, base: Path) -> str:
    """Turn relative ``img`` paths into absolute file paths for fpdf2."""

    def sub_double(m) -> str:
        src = m.group(1).strip()
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = Path(src)
        if not p.is_absolute():
            p = (base / p).resolve()
        return f'src="{p.as_posix()}"'

    def sub_single(m) -> str:
        src = m.group(1).strip()
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = Path(src)
        if not p.is_absolute():
            p = (base / p).resolve()
        return f"src='{p.as_posix()}'"

    html = re.sub(r'src="([^"]+)"', sub_double, html)
    html = re.sub(r"src='([^']+)'", sub_single, html)
    return html


_IMG_EXT = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
)


def _normalize_image_for_fpdf(src: Path, cache_dir: Path) -> Path:
    """
    Re-encode a raster image to an 8-bit RGB PNG on white (no alpha) for reliable fpdf2 embedding.

    Matplotlib, psmiles, and some tools emit palette or RGBA PNGs that ``FPDF.write_html`` may reject;
    this avoids manual ``_raster`` renames in Markdown.
    """
    from PIL import Image

    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        st = src.stat()
        key = f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        key = str(src.resolve())
    h = hashlib.sha256(key.encode()).hexdigest()[:28]
    out = cache_dir / f"fpdf_img_{h}.png"
    if out.is_file():
        return out

    im = Image.open(src)
    im.load()
    if im.mode == "P":
        im = im.convert("RGBA") if "transparency" in im.info else im.convert("RGB")
    if im.mode in ("RGBA", "LA"):
        if im.mode == "LA":
            im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    im.save(out, format="PNG", optimize=True)
    return out


def _html_normalize_local_images_for_fpdf(html: str, cache_dir: Path) -> str:
    """Rewrite ``img`` src for local files to normalized PNG paths (see ``_normalize_image_for_fpdf``)."""

    def _maybe_norm(raw: str) -> str:
        s = raw.strip()
        if s.startswith(("http://", "https://", "data:")):
            return s
        p = Path(s)
        if not p.is_file():
            return s
        if p.suffix.lower() not in _IMG_EXT:
            return s
        try:
            return _normalize_image_for_fpdf(p, cache_dir).as_posix()
        except Exception:
            return s

    def sub_double(m) -> str:
        return f'src="{_maybe_norm(m.group(1))}"'

    def sub_single(m) -> str:
        return f"src='{_maybe_norm(m.group(1))}'"

    html = re.sub(r'src="([^"]+)"', sub_double, html)
    html = re.sub(r"src='([^']+)'", sub_single, html)
    return html


def compile_markdown_to_pdf(
    session_dir: Path,
    *,
    markdown_filename: str = "SUMMARY_REPORT.md",
    output_pdf_name: str = "SUMMARY_REPORT.pdf",
) -> Dict[str, Any]:
    """
    Convert an agent-authored Markdown file to a PDF (scientific-style layout via fpdf2 HTML).

    Resolves relative image paths against ``session_dir`` so ``![x](structures/foo.png)`` works.

    Dependencies: **markdown** (MD→HTML), **fpdf2** (PDF). See ``docs/DEPENDENCIES.md``.
    """
    session_dir = Path(session_dir).resolve()
    md_path = Path(markdown_filename)
    if not md_path.is_absolute():
        md_path = session_dir / markdown_filename
    if not md_path.is_file():
        return {
            "ok": False,
            "error": f"Markdown not found: {md_path}",
            "session_dir": str(session_dir),
        }

    text = md_path.read_text(encoding="utf-8")
    try:
        import markdown as md_lib
    except ImportError as e:
        return {
            "ok": False,
            "error": f"pip package 'markdown' required for MD→HTML: {e}",
            "session_dir": str(session_dir),
        }

    html = md_lib.markdown(
        text,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    html = f"<div>{html}</div>"
    html = _html_resolve_image_src(html, session_dir)
    cache_dir = session_dir / ".discovery_pdf_cache"
    html = _html_normalize_local_images_for_fpdf(html, cache_dir)

    try:
        from fpdf import FPDF
    except ImportError as e:
        return {
            "ok": False,
            "error": f"pip package 'fpdf2' required for PDF: {e}",
            "session_dir": str(session_dir),
        }

    pdf_path = session_dir / output_pdf_name
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    try:
        pdf.write_html(html)
    except Exception as e:
        return {
            "ok": False,
            "error": f"fpdf2 write_html failed (try simpler Markdown or smaller images): {e}",
            "session_dir": str(session_dir),
            "markdown": str(md_path),
        }

    try:
        pdf.output(str(pdf_path))
    except Exception as e:
        return {"ok": False, "error": str(e), "session_dir": str(session_dir)}

    return {
        "ok": True,
        "pdf": str(pdf_path.resolve()),
        "markdown": str(md_path.resolve()),
        "session_dir": str(session_dir),
    }
