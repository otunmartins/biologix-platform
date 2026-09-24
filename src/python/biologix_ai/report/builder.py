"""
Build ``SUMMARY_REPORT.md`` from everything a discovery session saved.

The old report kept a 200-character slice of the iteration notes and dropped the
rest. This one reads the whole session: the resolved target, the per-candidate audit
trail (validation, ADMET, compliance, OpenMM, retrosynthesis), the agent's saved
findings and caveats, the assembled retrosynthesis report, and the structure
images. Each image appears once, next to the candidate it shows.

Sentences generated here state measured values and plain facts. Text the agent wrote
(its findings, caveats, and optional ``narrative``) is included as written, after the
mechanical style repairs in :mod:`biologix_ai.report.style_check`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from biologix_ai.discovery_report import (
    collect_session_psmiles_entries,
    gather_structure_visualizations,
    target_structure_lines,
)
from biologix_ai.psmiles_drawing import safe_filename_basename
from biologix_ai.report.style_check import mechanical_fixes

MINUS = "\u2212"
# First line of a report this module wrote. The compile tool treats a report without it as
# agent-written and holds its prose to the style check.
GENERATED_MARKER = "<!-- biologix-report:generated -->"
_PATH_LINE = re.compile(r"^\s*\*?Artifact:.*$", re.I)


# -- loading ---------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _iterations(session: Path, include_all: bool) -> List[Dict[str, Any]]:
    files = sorted(session.glob("agent_iteration_*.json"))
    if not include_all and files:
        files = files[-1:]
    out = []
    for path in files:
        data = _read_json(path)
        if isinstance(data, dict):
            data["_file"] = path.name
            out.append(data)
    return out


def _audit(session: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Audit rows grouped by candidate PSMILES, oldest first."""
    rows: Dict[str, List[Dict[str, Any]]] = {}
    path = session / "audit" / "pipeline_audit.jsonl"
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        detail = row.get("detail")
        if isinstance(detail, str):
            try:
                row["detail"] = json.loads(detail)
            except ValueError:
                pass
        rows.setdefault(str(row.get("candidate_psmiles", "")), []).append(row)
    return rows


def _latest(rows: Iterable[Dict[str, Any]], stage: str) -> Optional[Dict[str, Any]]:
    found = [r for r in rows if r.get("stage") == stage]
    return found[-1] if found else None


def _energy(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_energy(value: Optional[float]) -> str:
    if value is None:
        return "not calculated"
    return f"{MINUS if value < 0 else ''}{abs(value):,.1f}"


def _tidy(text: Any) -> str:
    """One line of agent-written text with the mechanical style repairs applied."""
    fixed, _ = mechanical_fixes(str(text or "").strip())
    return re.sub(r"\s+", " ", fixed)


def _feedback(iterations: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Merge list-valued feedback across iterations, keeping first-seen order."""
    merged: Dict[str, List[str]] = {}
    for it in iterations:
        fb = it.get("feedback") or {}
        for key in ("high_performers", "effective_mechanisms", "problematic_features", "references"):
            for item in fb.get(key) or []:
                text = _tidy(item)
                if text and text not in merged.setdefault(key, []):
                    merged[key].append(text)
    return merged


def _property_analysis(iterations: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for it in iterations:
        section = (it.get("feedback") or {}).get("property_analysis")
        if isinstance(section, dict):
            for label, value in section.items():
                if isinstance(value, dict):
                    merged.setdefault(str(label), {}).update(value)
    return merged


# -- pieces ----------------------------------------------------------------------


def _viz_by_psmiles(session: Path) -> Tuple[Dict[str, Tuple[str, Dict[str, str]]], Dict[str, Dict[str, str]]]:
    """``({psmiles: (slug, images)}, all_groups)``.

    A structure image set belongs to a candidate when its ``*_complex_meta.json`` names the
    candidate's PSMILES. Sets that no candidate claims stay in ``all_groups`` for the caller.
    """
    structures = session / "structures"
    groups = gather_structure_visualizations(structures)
    out: Dict[str, Tuple[str, Dict[str, str]]] = {}
    for meta_path in sorted(structures.glob("*_complex_meta.json")):
        meta = _read_json(meta_path)
        if not isinstance(meta, dict) or not meta.get("psmiles"):
            continue
        slug = meta_path.name[: -len("_complex_meta.json")]
        if slug in groups:
            out[str(meta["psmiles"])] = (slug, groups[slug])
    return out, groups


def _retro_section(
    session: Path, dispositions: Dict[str, str], rows: Optional[List[Dict[str, Any]]] = None
) -> List[str]:
    path = session / "retrosynthesis" / "RETROSYNTHESIS_REPORT.md"
    lines = ["## Retrosynthesis", ""]
    failed = [r for r in (rows or []) if (r.get("retro") or "").lower().startswith(("fail", "reject"))]
    if failed and path.is_file():
        lines += [
            "Review outcome: "
            + "; ".join(f"{r['label']} ({r['retro']})" for r in failed)
            + ". The planner output below did not pass review, so it is shown for the record and "
            "should not be used as a synthesis route.",
            "",
        ]
    if not path.is_file():
        lines.append("No retrosynthesis report was assembled for this session.")
        for psmiles, outcome in dispositions.items():
            if outcome:
                lines.append(f"- `{psmiles}`: {outcome}")
        lines.append("")
        return lines
    body: List[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if _PATH_LINE.match(raw):
            continue  # a server path means nothing to the reader
        if re.match(r"^##\s+Retrosynthesis\s*$", raw):
            continue
        body.append(raw)
    text, _ = mechanical_fixes("\n".join(body).strip())
    lines.append(text)
    lines.append("")
    return lines


def _summary(
    biologic: str,
    rows: List[Dict[str, Any]],
    retro_ok: Dict[str, str],
    n_iterations: int,
) -> List[str]:
    simulated = [r for r in rows if r["energy"] is not None]
    parts = [
        f"This report covers {len(rows)} candidate polymer{'s' if len(rows) != 1 else ''} "
        f"for {biologic or 'the target biologic'}, from {n_iterations} iteration{'s' if n_iterations != 1 else ''}."
    ]
    if simulated:
        best = min(simulated, key=lambda r: r["energy"])
        platform = f" on {best['platform']}" if best.get("platform") else ""
        parts.append(
            f"{len(simulated)} of {len(rows)} completed an OpenMM interaction-energy calculation. "
            f"The most negative was {best['label']} at {_fmt_energy(best['energy'])} kJ/mol{platform}."
        )
    else:
        parts.append("No candidate has a completed OpenMM interaction energy yet.")
    failed_retro = [r["label"] for r in rows if (r.get("retro") or "").lower().startswith(("fail", "reject"))]
    if failed_retro:
        parts.append("Retrosynthesis did not produce a usable route for " + ", ".join(failed_retro) + ".")
    parts.append(
        "Each energy comes from one minimized configuration, so it ranks candidates against each other "
        "under the same setup. It is not a binding free energy and says nothing about shelf life."
    )
    return [" ".join(parts), ""]


_METHOD = (
    "Each polymer repeat unit was checked for structural validity, ADMET alerts, and excipient "
    "compliance. Candidates that passed were packed around the protein with Packmol and minimized "
    "in OpenMM with the AMBER14SB force field for the protein and GAFF with Gasteiger charges for "
    "the polymer. A charged polymer is packed with counterions so the polymer matrix is neutral. "
    "The interaction energy is the energy of the complex minus the energies of the protein and the "
    "polymer alone. The platform (CPU or GPU) and random seed are recorded with every result."
)


def _candidate_block(
    row: Dict[str, Any],
    viz: Dict[str, str],
    label_png: Optional[str],
) -> List[str]:
    lines = [f"### {row['label']}", "", f"**PSMILES:** `{row['psmiles']}`", ""]
    facts = []
    for stage, name in (("validation", "Validation"), ("admet", "ADMET"), ("compliance", "Compliance")):
        outcome = row["stages"].get(stage)
        if outcome:
            facts.append(f"{name}: {outcome}")
    if facts:
        lines.append("- **Screening:** " + "; ".join(facts))
    if row["energy"] is not None:
        tail = f" on {row['platform']}" if row.get("platform") else ""
        lines.append(f"- **Interaction energy:** {_fmt_energy(row['energy'])} kJ/mol{tail}")
    if row.get("target_note"):
        lines.append(f"- **Simulated target:** {row['target_note']}")
    if row.get("retro"):
        lines.append(f"- **Retrosynthesis:** {row['retro']}")
    if row.get("limitations"):
        lines.append(f"- **Limits of this result:** {row['limitations']}")
    lines.append("")
    figures = [(label_png, f"{row['label']}, repeat unit")] if label_png and "monomer" not in viz else []
    if viz.get("monomer"):
        figures.append((viz["monomer"], f"{row['label']}, repeat unit"))
    complex_image = viz.get("chemviz") or viz.get("pymol") or viz.get("preview")
    if complex_image:
        figures.append((complex_image, f"{row['label']} packed around the protein after minimization"))
    for path, caption in figures:
        lines += [f"![{caption}]({path})", ""]
    return lines


def build_report(
    session_dir: Path,
    *,
    title: str = "",
    narrative: str = "",
    include_all_iterations: bool = True,
    label_images: Optional[Dict[str, Path]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(markdown, info)`` for the session. ``label_images`` maps PSMILES to a 2D drawing."""
    session = Path(session_dir).resolve()
    iterations = _iterations(session, include_all_iterations)
    entries, _meta = collect_session_psmiles_entries(session, include_all_iterations=include_all_iterations)
    audit = _audit(session)
    fb = _feedback(iterations)
    analysis = _property_analysis(iterations)
    viz_by_psmiles, viz_groups = _viz_by_psmiles(session)
    target = _read_json(session / "structures" / "biologic_target.json") or {}
    biologic = str(target.get("canonical_name") or target.get("query") or "")
    if not biologic:
        world = _read_json(session / "discovery_world.json") or {}
        biologic = str(((world.get("meta") or {}).get("links") or {}).get("biologic_target", ""))

    # Candidates that were simulated but never named in the agent's feedback still belong here.
    known = {psmiles for _l, psmiles in entries}
    for psmiles in audit:
        if psmiles and psmiles not in known and _latest(audit[psmiles], "openmm"):
            entries.append((f"Candidate {len(entries) + 1}", psmiles))
            known.add(psmiles)

    rows: List[Dict[str, Any]] = []
    for label, psmiles in entries:
        rows_for = audit.get(psmiles, [])
        info = analysis.get(label, {})
        openmm = _latest(rows_for, "openmm")
        detail = openmm.get("detail") if openmm and isinstance(openmm.get("detail"), dict) else {}
        energy = _energy(detail.get("interaction_energy_kj_mol"))
        if energy is None:
            energy = _energy(info.get("interaction_energy_kj_mol"))
        retro_row = _latest(rows_for, "retrosynthesis")
        retro = _tidy(info.get("retrosynthesis_disposition") or "")
        if not retro and retro_row:
            detail_r = retro_row.get("detail")
            retro = f"{retro_row.get('disposition', '')}" + (
                f": {_tidy(detail_r)}" if isinstance(detail_r, str) and detail_r else ""
            )
        stages = {}
        for stage in ("validation", "admet", "compliance"):
            hit = _latest(rows_for, stage)
            if hit:
                stages[stage] = {"pass": "passed", "fail": "flagged", "warning": "warning"}.get(
                    str(hit.get("disposition")), str(hit.get("disposition"))
                )
        rows.append(
            {
                "label": _tidy(label),
                "psmiles": psmiles,
                "energy": energy,
                "platform": _tidy(detail.get("platform") or info.get("openmm_platform") or ""),
                "target_note": _tidy(detail.get("target") or info.get("target") or ""),
                "limitations": _tidy(detail.get("limitations") or ""),
                "retro": retro,
                "stages": stages,
            }
        )

    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    heading = title.strip() or (
        f"{biologic[:1].upper() + biologic[1:]}: polymer screen" if biologic else "Discovery summary"
    )
    md: List[str] = [
        GENERATED_MARKER,
        f"# {_tidy(heading)}",
        "",
        f"**Generated:** {when}  ",
        f"**Session:** `{session.name}`  ",
        f"**Iterations:** {', '.join(str(i.get('iteration')) for i in iterations) or 'none saved'}  ",
        "",
        "## Summary",
        "",
    ]
    md += _summary(biologic, rows, {}, len(iterations))
    if narrative.strip():
        md += [_tidy_block(narrative), ""]
    md += target_structure_lines(session) or []
    md += ["## Method", "", _METHOD, ""]

    md += [f"## Candidates ({len(rows)})", ""]
    if rows:
        md += ["| Candidate | Screening | Interaction energy (kJ/mol) | Platform | Retrosynthesis |", "|---|---|---|---|---|"]
        for r in rows:
            screening = ", ".join(f"{k} {v}" for k, v in r["stages"].items()) or "not recorded"
            md.append(
                f"| {r['label']} | {screening} | {_fmt_energy(r['energy'])} | {r['platform'] or 'n/a'} | {r['retro'] or 'not recorded'} |"
            )
        md.append("")
    claimed: set = set()
    for r in rows:
        png = (label_images or {}).get(r["psmiles"])
        slug, images = viz_by_psmiles.get(r["psmiles"], ("", {}))
        if not images:  # the agent may have named a candidate after its image set
            slug = safe_filename_basename(r["label"])
            images = viz_groups.get(slug, {})
        if images:
            claimed.add(slug)
        md += _candidate_block(r, images, f"structures/{Path(png).name}" if png else None)
    unclaimed = sorted(g for g in viz_groups if g not in claimed)
    if unclaimed:
        md += ["## Molecular visualizations", ""]
        for group in unclaimed:
            md += [f"### {group.replace('_', ' ')}", ""]
            monomer = viz_groups[group].get("monomer")
            complex_image = (
                viz_groups[group].get("chemviz") or viz_groups[group].get("pymol") or viz_groups[group].get("preview")
            )
            for path, caption in ((monomer, "repeat unit"), (complex_image, "complex after minimization")):
                if path:
                    md += [f"![{group.replace('_', ' ')}, {caption}]({path})", ""]

    if fb.get("high_performers") or fb.get("effective_mechanisms"):
        md += ["## What the results support", ""]
        md += [f"- {t}" for t in fb.get("high_performers", [])]
        md += [f"- {t}" for t in fb.get("effective_mechanisms", [])]
        md.append("")
    if fb.get("problematic_features"):
        md += ["## Limitations and open problems", ""]
        md += [f"- {t}" for t in fb["problematic_features"]]
        md.append("")

    notes = [(it.get("iteration"), _tidy(it.get("notes"))) for it in iterations if it.get("notes")]
    if notes:
        md += ["## Notes by iteration", ""]
        md += [f"- **Iteration {n}:** {t}" for n, t in notes]
        md.append("")

    md += _retro_section(session, {r["psmiles"]: r["retro"] for r in rows}, rows)

    if fb.get("references"):
        md += ["## References", ""]
        md += [f"{i}. {t}" for i, t in enumerate(fb["references"], 1)]
        md.append("")

    md += ["## Files", "", _files_paragraph(session), ""]
    info = {"n_candidates": len(rows), "n_simulated": sum(r["energy"] is not None for r in rows), "rows": rows}
    return "\n".join(md).rstrip() + "\n", info


def _tidy_block(text: str) -> str:
    fixed, _ = mechanical_fixes(text.strip())
    return fixed


def _files_paragraph(session: Path) -> str:
    kinds = []
    if list((session / "structures").glob("*_minimized.pdb")):
        kinds.append("minimized complex PDB files")
    if (session / "structures" / "biologic_target.pdb").is_file():
        kinds.append("the prepared target PDB")
    if list((session / "structures").glob("*.png")):
        kinds.append("structure images")
    if (session / "retrosynthesis").is_dir():
        kinds.append("retrosynthesis plans and ADMET results")
    if (session / "audit").is_dir():
        kinds.append("the audit log")
    listed = ", ".join(kinds) if kinds else "the session outputs"
    return (
        f"The session folder holds {listed}. Call export_session_outputs to package them "
        "into one download."
    )


def agent_written_text(
    session_dir: Path, narrative: str = "", include_all_iterations: bool = True
) -> str:
    """The prose the agent wrote that the report will carry: its narrative and saved findings."""
    session = Path(session_dir).resolve()
    iterations = _iterations(session, include_all_iterations)
    fb = _feedback(iterations)
    parts = [narrative.strip()] if narrative.strip() else []
    for it in iterations:
        if it.get("notes"):
            parts.append(_tidy(it["notes"]))
    for key in ("high_performers", "effective_mechanisms", "problematic_features"):
        parts.extend(fb.get(key, []))
    return "\n\n".join(p for p in parts if p)
