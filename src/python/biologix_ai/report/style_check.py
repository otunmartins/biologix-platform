"""
Writing-style check for reports, using the avoid-ai-writing detector.

The detector (vendored under ``vendor/avoid_ai_writing``, MIT) finds the word,
phrasing, and punctuation patterns that make prose read as machine-written. It runs
under Node. This module does three things with it:

- :func:`mechanical_fixes` repairs what can be repaired without changing meaning
  (em dashes, curly quotes, emoji), outside code spans and code blocks;
- :func:`analyze` returns the detector's findings for the remaining text;
- :func:`review` decides whether an agent-written passage may be published or must be
  rewritten first, and hands back exactly what to fix.

Nothing here rewrites vocabulary: swapping "robust" for "strong" by rule produces
worse prose than the original, and the server has no language model. The agent
rewrites; the detector confirms.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "avoid_ai_writing"
RUNNER = VENDOR_DIR / "run_detector.js"

# Findings tolerated in prose the agent wrote before it must be rewritten.
MAX_FINDINGS = int(os.environ.get("BIOLOGIX_STYLE_MAX_FINDINGS", "2") or 2)
# Rewrite rounds asked of the agent before the text is published as it stands.
MAX_REVISIONS = int(os.environ.get("BIOLOGIX_STYLE_MAX_REVISIONS", "2") or 2)
_SHOWN_FINDINGS = 14

_CODE = re.compile(r"(```.*?```|`[^`\n]+`)", re.S)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️‍]+"
)
_ZERO_WIDTH = re.compile("[​‌⁠﻿]")


def node_executable() -> Optional[str]:
    """``node`` on PATH, else the one shipped by the ``nodejs-wheel-binaries`` package."""
    found = shutil.which("node")
    if found:
        return found
    try:
        import nodejs_wheel  # type: ignore

        root = Path(nodejs_wheel.__file__).resolve().parent
        for candidate in (root / "bin" / "node", root / "node.exe", root / "node"):
            if candidate.is_file():
                return str(candidate)
    except Exception:
        pass
    return None


def _outside_code(text: str, fix) -> str:
    """Apply *fix* to the prose of *text*, leaving code spans and fenced blocks alone."""
    return "".join(part if _CODE.fullmatch(part) else fix(part) for part in _CODE.split(text))


def mechanical_fixes(text: str) -> Tuple[str, List[str]]:
    """Repair punctuation tells that carry no meaning. Returns ``(text, what_changed)``."""
    changes: Dict[str, int] = {}

    def bump(label: str, n: int) -> None:
        if n:
            changes[label] = changes.get(label, 0) + n

    def prose(part: str) -> str:
        out, n = re.subn("[“”]", '"', part)
        bump("curly double quotes", n)
        out, n = re.subn("[‘’]", "'", out)
        bump("curly single quotes", n)
        # A dash in a heading separates title from subtitle; elsewhere it is a comma-sized pause.
        out, n = re.subn(r"^(#{1,6}[^\n]*?)\s*[—–]\s+", r"\1: ", out, flags=re.M)
        bump("dashes in headings", n)
        out, n = re.subn(r"\s*—\s*", ", ", out)
        bump("em dashes", n)
        out, n = re.subn(r"\s+–\s+", ", ", out)
        bump("spaced en dashes", n)
        out, n = _EMOJI.subn("", out)
        bump("emoji", n)
        out, n = _ZERO_WIDTH.subn("", out)
        bump("zero-width characters", n)
        return out

    fixed = _outside_code(text, prose)
    return fixed, [f"{count} {label}" for label, count in changes.items()]


def analyze(
    text: str,
    *,
    context: str = "general",
    source_mode: str = "rendered-markdown",
    timeout_s: float = 25.0,
) -> Dict[str, Any]:
    """Run the detector. ``available`` is False when Node is missing or the run fails."""
    node = node_executable()
    if node is None or not RUNNER.is_file():
        return {"available": False, "reason": "node is not installed", "issues": [], "words": 0}
    try:
        proc = subprocess.run(
            [node, str(RUNNER)],
            input=json.dumps({"text": text, "contextMode": context, "sourceMode": source_mode}),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            return {"available": False, "reason": (proc.stderr or "detector failed")[-300:], "issues": [], "words": 0}
        result = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "issues": [], "words": 0}
    return {
        "available": True,
        "issues": list(result.get("issues") or []),
        "score": result.get("score"),
        "label": result.get("label"),
        "words": int((result.get("stats") or {}).get("wordCount") or 0),
    }


def _describe(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": issue.get("type"),
        "text": issue.get("text"),
        "fix": issue.get("suggestion") or "rewrite the sentence in plain words",
    }


def review(text: str, *, attempt: int = 0, label: str = "the text") -> Dict[str, Any]:
    """Decide whether *text* (agent-written prose) may be published.

    ``attempt`` counts rewrites already requested. Returns ``{"text", "publish",
    "revise", ...}``: *text* has the mechanical repairs applied; when ``revise`` is true,
    ``findings`` lists what to rewrite. After ``MAX_REVISIONS`` rewrites the text is
    published with whatever remains, and the remainder is reported.
    """
    fixed, changes = mechanical_fixes(text)
    report = analyze(fixed)
    out: Dict[str, Any] = {
        "text": fixed,
        "mechanical_fixes": changes,
        "detector_available": report["available"],
        "findings_count": len(report["issues"]),
        "attempt": attempt,
    }
    if not report["available"]:
        out.update(publish=True, revise=False, note=f"Style check skipped: {report.get('reason')}")
        return out
    over = len(report["issues"]) > MAX_FINDINGS
    out["findings"] = [_describe(i) for i in report["issues"][:_SHOWN_FINDINGS]]
    if over and attempt < MAX_REVISIONS:
        out.update(publish=False, revise=True)
    else:
        out.update(publish=True, revise=False)
        if over:
            out["note"] = (
                f"{len(report['issues'])} style findings remain in {label} after "
                f"{attempt} rewrite(s); published as written."
            )
    return out


def revision_instructions(findings: List[Dict[str, Any]], label: str) -> str:
    """Short, concrete instruction the agent can act on."""
    return (
        f"Rewrite {label} before this report is written. The style check found the patterns "
        "listed in findings. Fix each one in your own words: cut filler openers and hedges, "
        "replace inflated words with the plain word, state the measured value instead of "
        "calling it significant, and keep every number, unit, name, and caveat unchanged. "
        "Do not add claims. Then call this tool again with the revised text."
    )


# -- attempts ---------------------------------------------------------------------


def _state_path(session: Path) -> Path:
    return Path(session) / "report_style_state.json"


def attempts(session: Path, key: str) -> int:
    """Rewrites already requested for *key* ("summary" or "compile") in this session."""
    try:
        return int(json.loads(_state_path(session).read_text(encoding="utf-8")).get(key, 0))
    except (OSError, ValueError, TypeError):
        return 0


def record_attempt(session: Path, key: str) -> int:
    path = _state_path(session)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[key] = int(data.get(key, 0)) + 1
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data[key]


def reset_attempts(session: Path, key: str) -> None:
    path = _state_path(session)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop(key, None)
        path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass


def revision_payload(review_result: Dict[str, Any], label: str, retry_tool: str) -> Dict[str, Any]:
    """The tool result that asks the agent to rewrite. Not a failure; nothing was written."""
    return {
        "ok": False,
        "revision_requested": True,
        "not_a_failure": True,
        "error": "STYLE_REVISION_REQUESTED",
        "findings_count": review_result["findings_count"],
        "findings": review_result.get("findings", []),
        "instructions": revision_instructions(review_result.get("findings", []), label),
        "retry_tool": retry_tool,
    }
