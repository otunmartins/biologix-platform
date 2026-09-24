#!/usr/bin/env python3
"""
Discovery session reporting utilities.

``write_session_summary_reports`` builds ``SUMMARY_REPORT.md`` and its PDF from everything a
session saved (target, audit trail, findings, retrosynthesis, images). The agent may add its own
interpretation as ``narrative``. ``compile_markdown_to_pdf`` renders any Markdown file in the
session folder. No language model runs in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from biologix_ai.psmiles_drawing import safe_filename_basename, save_psmiles_png


def _load_iteration_files(session_dir: Path) -> List[Path]:
    files = sorted(
        f
        for f in session_dir.iterdir()
        if f.is_file() and f.name.startswith("agent_iteration_") and f.name.endswith(".json")
    )
    return sorted(files, key=lambda p: p.name)


def _psmiles_by_name(feedback: Dict[str, Any]) -> Dict[str, str]:
    """``{label: psmiles}`` from feedback sections that pair a name with a structure."""
    found: Dict[str, str] = {}
    for key in ("property_analysis", "candidates", "top_candidates", "materials"):
        section = feedback.get(key)
        entries: List[Tuple[str, Any]] = []
        if isinstance(section, dict):
            entries = list(section.items())
        elif isinstance(section, list):
            entries = [
                (str(item.get("name") or item.get("material_name") or "candidate"), item)
                for item in section
                if isinstance(item, dict)
            ]
        for label, value in entries:
            if not isinstance(value, dict):
                continue
            psm = value.get("psmiles") or value.get("chemical_structure")
            if psm and "[*]" in str(psm):
                found.setdefault(str(label), str(psm).strip())
    return found


def collect_psmiles_entries_from_feedback(feedback: Any) -> List[Tuple[str, str]]:
    """
    Extract (label, psmiles) pairs from a feedback dict.

    Accepts every shape the agent actually writes: ``high_performers`` as PSMILES
    strings, as names, or as dicts; ``high_performer_psmiles``; and names paired
    with a PSMILES inside ``property_analysis`` / ``candidates``. A run whose
    high performers are named ("PVA", "PVP") must still produce a report.
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

    # Names resolved through a PSMILES recorded elsewhere in the feedback.
    named = _psmiles_by_name(feedback)
    for item in hp if isinstance(hp, list) else []:
        if isinstance(item, str) and "[*]" not in item and item.strip() in named:
            out.append((item.strip(), named[item.strip()]))
    if not out:
        out.extend((name, psm) for name, psm in named.items())

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
    """Text for the report. The PDF renderer uses Unicode fonts, so nothing is degraded to "?"."""
    return text


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


def target_structure_lines(session_dir: Path) -> List[str]:
    """"Target structure" section from ``structures/biologic_target.json``.

    Lists where the simulated protein came from and every change made to it
    (substituted residues, removed ligands/glycans/waters, rebuilt gaps,
    unobserved termini), so no reader mistakes the model for the drug product.
    """
    meta_path = Path(session_dir) / "structures" / "biologic_target.json"
    if not meta_path.is_file():
        return []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lines = ["## Target structure", ""]
    lines.append(
        f"- **Biologic:** {_ascii_safe(str(meta.get('canonical_name') or meta.get('query') or ''))}"
    )
    lines.append(f"- **Resolved target:** `{meta.get('resolved_target', '')}`")
    source = str(meta.get("source") or "")
    source_id = str(meta.get("source_id") or "")
    lines.append(f"- **Source:** {source} {source_id}".rstrip())
    chains = meta.get("chains") or []
    lines.append(f"- **Chains simulated:** {', '.join(chains) if chains else 'all'}")
    if meta.get("n_residues"):
        lines.append(f"- **Residues:** {meta['n_residues']}")
    if meta.get("n_atoms"):
        lines.append(f"- **Atoms with hydrogens (AMBER14):** {meta['n_atoms']}")
    ph = meta.get("protonation_ph", 7.0)
    lines.append(
        f"- **Protonation:** pH {float(ph):.1f} "
        "(OpenMM addHydrogens; the resolver does not pick a different pH)"
    )
    if meta.get("max_extent_nm"):
        lines.append(f"- **Largest extent:** {meta['max_extent_nm']} nm")
    mods = meta.get("modifications") or []
    if mods:
        lines.append("- **Substituted residues:** " + "; ".join(
            f"{m.get('residue')} {m.get('chain')}{m.get('residue_id')} -> {m.get('replaced_by')}"
            for m in mods
        ))
    removed = meta.get("removed_heterogens") or []
    if removed:
        lines.append("- **Removed heterogens:** " + "; ".join(
            f"{h.get('residue')} x{h.get('count')} (chain {h.get('chain')})" for h in removed
        ))
    gaps = meta.get("rebuilt_gaps") or []
    if gaps:
        lines.append("- **Rebuilt internal gaps:** " + "; ".join(
            f"chain {g.get('chain')}: {g.get('n_residues')} residues" for g in gaps
        ))
    for warning in meta.get("warnings") or []:
        lines.append(f"- **Note:** {_ascii_safe(str(warning))}")
    lines.append("")
    lines.append(
        "Energies in this report are for this prepared model. Removed ligands, glycans, "
        "lipid side chains, and unobserved residues are absent from the simulation. "
        "Each OpenMM result records random_seed; the NPT leg uses that seed."
    )
    lines.append("")
    return lines


def write_session_summary_reports(
    session_dir: Path,
    *,
    title: str = "",
    include_all_iterations: bool = True,
    narrative: str = "",
) -> Dict[str, Any]:
    """
    Build ``SUMMARY_REPORT.md`` and ``SUMMARY_REPORT.pdf`` from everything the session saved.

    See :mod:`biologix_ai.report.builder` for what goes in and
    :mod:`biologix_ai.report.pdf_render` for the layout. ``narrative`` is the agent's own
    interpretation, placed after the summary; the caller has already style-checked it.
    """
    from biologix_ai.report.builder import build_report

    session_dir = Path(session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    structures = session_dir / "structures"
    structures.mkdir(parents=True, exist_ok=True)

    entries, _meta = collect_session_psmiles_entries(
        session_dir, include_all_iterations=include_all_iterations
    )
    # A candidate may have been simulated without being named in the feedback; the
    # builder adds those, so only the feedback-named ones need a 2D drawing here.
    label_images: Dict[str, Path] = {}
    render_errors: List[str] = []
    for idx, (label, psm) in enumerate(entries):
        out = structures / f"{idx:03d}_{safe_filename_basename(label)}.png"
        r = save_psmiles_png(psm, out, overwrite=True)
        if r.get("ok"):
            label_images[psm] = Path(r["path"])
        else:
            render_errors.append(f"{label}: {r.get('error', 'unknown')}")

    markdown, info = build_report(
        session_dir,
        title=title,
        narrative=narrative,
        include_all_iterations=include_all_iterations,
        label_images=label_images,
    )
    if not info["n_candidates"]:
        return {
            "ok": False,
            "error": "No candidates found: agent_iteration_*.json has no PSMILES and the audit log has no OpenMM results.",
            "session_dir": str(session_dir),
        }
    md_path = session_dir / "SUMMARY_REPORT.md"
    md_path.write_text(markdown, encoding="utf-8")
    pdf_out = compile_markdown_to_pdf(
        session_dir, markdown_filename=md_path.name, output_pdf_name="SUMMARY_REPORT.pdf"
    )
    out: Dict[str, Any] = {
        "ok": True,
        "session_dir": str(session_dir),
        "markdown": str(md_path),
        "n_candidates": info["n_candidates"],
        "n_simulated": info["n_simulated"],
        "render_errors": render_errors,
    }
    if pdf_out.get("ok"):
        out.update(
            pdf=pdf_out.get("pdf"),
            pages=pdf_out.get("pages"),
            figures=pdf_out.get("figures"),
        )
        if pdf_out.get("duplicate_figures_skipped"):
            out["duplicate_figures_skipped"] = pdf_out["duplicate_figures_skipped"]
        if pdf_out.get("missing_images"):
            out["missing_images"] = pdf_out["missing_images"]
    else:
        out["pdf_error"] = pdf_out.get("error", "pdf failed")
    return out


def compile_markdown_to_pdf(
    session_dir: Path,
    *,
    markdown_filename: str = "SUMMARY_REPORT.md",
    output_pdf_name: str = "SUMMARY_REPORT.pdf",
) -> Dict[str, Any]:
    """
    Render a Markdown file in the session folder to a PDF.

    Relative image paths resolve against ``session_dir``. Every image is scaled to fit the
    page and drawn once; see :mod:`biologix_ai.report.pdf_render`.
    """
    from biologix_ai.report.pdf_render import render_markdown_to_pdf

    session_dir = Path(session_dir).resolve()
    md_path = Path(markdown_filename)
    if not md_path.is_absolute():
        md_path = session_dir / markdown_filename
    if not md_path.is_file():
        return {"ok": False, "error": f"Markdown not found: {md_path}", "session_dir": str(session_dir)}
    pdf_path = session_dir / output_pdf_name
    try:
        result = render_markdown_to_pdf(
            md_path.read_text(encoding="utf-8"),
            pdf_path,
            session_dir,
            footer_label=session_dir.name,
        )
    except Exception as exc:  # a layout bug must not lose the Markdown
        return {
            "ok": False,
            "error": f"PDF rendering failed: {type(exc).__name__}: {exc}",
            "session_dir": str(session_dir),
            "markdown": str(md_path),
        }
    out: Dict[str, Any] = {
        "ok": True,
        "pdf": str(pdf_path.resolve()),
        "markdown": str(md_path.resolve()),
        "session_dir": str(session_dir),
        "pdf_render_mode": "native",
        "pages": result.pages,
        "figures": result.figures,
    }
    if result.duplicate_figures_skipped:
        out["duplicate_figures_skipped"] = result.duplicate_figures_skipped
    if result.missing_images:
        out["missing_images"] = result.missing_images
        out["warnings"] = ["Images not found or not embeddable: " + ", ".join(result.missing_images)]
    return out
