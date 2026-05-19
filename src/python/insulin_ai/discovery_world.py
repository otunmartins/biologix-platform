"""
Structured discovery world state per session (Kosmos-style rollup).

Lives alongside agent_iteration_*.json under runs/<session>/ as discovery_world.json.
Timeline detail remains canonical in agent_iteration files; this module merges
cross-iteration hypotheses, literature claims, simulation summaries, and human steering.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Sequence, Union

SCHEMA_VERSION = 1
DEFAULT_WORLD_FILENAME = "discovery_world.json"

_Listish = Sequence[MutableMapping[str, Any]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_world() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "objective": "",
        "objective_history": [],
        "literature_entries": [],
        "simulation_entries": [],
        "hypotheses": [],
        "open_questions": [],
        "human_directives": [],
        "retrosynthesis_entries": [],
        "meta": {
            "updated_at": _utc_now_iso(),
            "last_iteration": None,
            "links": {},
        },
    }


def load_world(path: Path) -> Dict[str, Any]:
    """Load world from JSON; missing file returns a fresh empty_world()."""
    path = Path(path)
    if not path.is_file():
        return empty_world()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return empty_world()
    if not isinstance(data, dict):
        return empty_world()
    if data.get("schema_version") != SCHEMA_VERSION:
        return empty_world()
    return _normalize_loaded(data)


def _normalize_loaded(data: Dict[str, Any]) -> Dict[str, Any]:
    base = empty_world()
    for key in (
        "objective",
        "objective_history",
        "literature_entries",
        "simulation_entries",
        "hypotheses",
        "open_questions",
        "human_directives",
        "retrosynthesis_entries",
    ):
        if key in data and data[key] is not None:
            base[key] = data[key]
    if isinstance(data.get("meta"), dict):
        base["meta"].update(data["meta"])
    base["schema_version"] = SCHEMA_VERSION
    return base


def save_world(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically (temp + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["schema_version"] = SCHEMA_VERSION
    if "meta" not in data or not isinstance(data["meta"], dict):
        data["meta"] = {}
    data["meta"] = dict(data["meta"])
    data["meta"]["updated_at"] = _utc_now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _merge_id_lists(
    existing: List[MutableMapping[str, Any]],
    incoming: _Listish,
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in existing:
        if not isinstance(item, dict):
            continue
        iid = item.get("id")
        if not iid:
            continue
        sid = str(iid)
        by_id[sid] = dict(item)
        if sid not in order:
            order.append(sid)
    for item in incoming:
        if not isinstance(item, dict):
            continue
        iid = item.get("id")
        if not iid:
            continue
        sid = str(iid)
        if sid in by_id:
            merged = dict(by_id[sid])
            merged.update(item)
            by_id[sid] = merged
        else:
            by_id[sid] = dict(item)
            order.append(sid)
    return [by_id[i] for i in order]


def apply_patch(existing: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge patch into a copy of existing.

    List fields with ``id`` keys are merged by id (update or append).
    ``objective`` changes push the previous value onto ``objective_history`` when it was non-empty.
    ``objective_history`` items from the patch are appended.
    ``meta`` is shallow-merged; ``meta.links`` keys from patch override.
    """
    if not isinstance(patch, dict):
        raise ValueError("patch must be a dict")
    if "schema_version" in patch and patch["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"patch schema_version must be {SCHEMA_VERSION}")

    out = json.loads(json.dumps(existing))  # deep copy via JSON
    if out.get("schema_version") != SCHEMA_VERSION:
        out = empty_world()
        for k, v in existing.items():
            if k in out and k != "schema_version":
                out[k] = v
        out["schema_version"] = SCHEMA_VERSION

    id_list_keys = (
        "literature_entries",
        "simulation_entries",
        "hypotheses",
        "open_questions",
        "human_directives",
        "retrosynthesis_entries",
    )
    for key in id_list_keys:
        if key in patch and patch[key] is not None:
            cur = out.get(key) or []
            if not isinstance(cur, list):
                cur = []
            inc = patch[key]
            if not isinstance(inc, list):
                raise ValueError(f"patch.{key} must be a list")
            out[key] = _merge_id_lists(cur, inc)

    if "objective" in patch and patch["objective"] is not None:
        new_obj = str(patch["objective"]).strip()
        old_obj = str(out.get("objective") or "").strip()
        if new_obj and new_obj != old_obj:
            if old_obj:
                hist = list(out.get("objective_history") or [])
                hist.append(
                    {
                        "text": old_obj,
                        "recorded_at": _utc_now_iso(),
                    }
                )
                out["objective_history"] = hist
            out["objective"] = new_obj

    if patch.get("objective_history"):
        oh = patch["objective_history"]
        if not isinstance(oh, list):
            raise ValueError("patch.objective_history must be a list")
        base_oh = list(out.get("objective_history") or [])
        for item in oh:
            if isinstance(item, dict):
                base_oh.append(dict(item))
            else:
                base_oh.append({"text": str(item), "recorded_at": _utc_now_iso()})
        out["objective_history"] = base_oh

    if "meta" in patch and isinstance(patch["meta"], dict):
        meta_out = dict(out.get("meta") or {})
        for k, v in patch["meta"].items():
            if k == "links" and isinstance(v, dict):
                links = dict(meta_out.get("links") or {})
                links.update(v)
                meta_out["links"] = links
            else:
                meta_out[k] = v
        out["meta"] = meta_out

    out["meta"] = dict(out.get("meta") or {})
    out["meta"]["updated_at"] = _utc_now_iso()
    out["schema_version"] = SCHEMA_VERSION
    return out


def planning_context(world: Dict[str, Any], max_chars: int = 8000) -> str:
    """
    Deterministic, bounded text for prompts: objective, hypotheses, questions, directives, recent lit/sim.
    """
    lines: List[str] = []
    obj = str(world.get("objective") or "").strip()
    if obj:
        lines.append("## Objective")
        lines.append(obj)

    oh = world.get("objective_history") or []
    if isinstance(oh, list) and oh:
        lines.append("## Previous objectives (history)")
        for h in oh[-5:]:
            if isinstance(h, dict):
                lines.append(f"- {h.get('text', h)}")
            else:
                lines.append(f"- {h}")

    hyps = sorted(
        [h for h in (world.get("hypotheses") or []) if isinstance(h, dict) and h.get("id")],
        key=lambda x: str(x.get("id")),
    )
    if hyps:
        lines.append("## Hypotheses")
        for h in hyps:
            sid = h.get("id")
            st = h.get("status", "")
            txt = h.get("text", "")
            sup = h.get("supporting_ids") or []
            lines.append(f"- {sid} [{st}]: {txt} (support: {sup})")

    oq = sorted(
        [q for q in (world.get("open_questions") or []) if isinstance(q, dict) and q.get("id")],
        key=lambda x: str(x.get("id")),
    )
    if oq:
        lines.append("## Open questions")
        for q in oq:
            lines.append(f"- {q.get('id')}: {q.get('text', '')}")

    hd = sorted(
        [d for d in (world.get("human_directives") or []) if isinstance(d, dict) and d.get("id")],
        key=lambda x: str(x.get("id")),
    )
    if hd:
        lines.append("## Human directives")
        for d in hd:
            lines.append(f"- (iter {d.get('iteration', '?')}) {d.get('text', '')}")

    lit = sorted(
        [x for x in (world.get("literature_entries") or []) if isinstance(x, dict) and x.get("id")],
        key=lambda x: str(x.get("id")),
    )
    if lit:
        lines.append("## Literature (summary)")
        for x in lit[-20:]:
            title = (x.get("title") or "")[:120]
            claim = (x.get("claim") or "")[:200]
            lines.append(f"- {x.get('id')}: {title} — {claim}")

    sim = sorted(
        [x for x in (world.get("simulation_entries") or []) if isinstance(x, dict) and x.get("id")],
        key=lambda x: str(x.get("id")),
    )
    if sim:
        lines.append("## Simulation (summary)")
        for x in sim[-20:]:
            en = x.get("interaction_energy_kj_mol")
            en_s = f"{en:.2f}" if isinstance(en, (int, float)) else str(en)
            psm = str(x.get("psmiles", ""))[:80]
            lines.append(
                f"- {x.get('id')}: {psm} status={x.get('status')} E={en_s} iter={x.get('iteration')}"
            )

    retro = sorted(
        [
            x
            for x in (world.get("retrosynthesis_entries") or [])
            if isinstance(x, dict) and x.get("id")
        ],
        key=lambda x: str(x.get("id")),
    )
    if retro:
        lines.append("## Retrosynthesis (summary)")
        for x in retro[-15:]:
            tgt = str(x.get("polymer_target", ""))[:80]
            rr = x.get("n_routes", "")
            lines.append(f"- {x.get('id')}: target={tgt} routes={rr} iter={x.get('iteration')}")

    meta = world.get("meta") or {}
    if isinstance(meta, dict) and meta.get("last_iteration") is not None:
        lines.append("## Meta")
        lines.append(f"last_iteration: {meta.get('last_iteration')}")
        links = meta.get("links") or {}
        if isinstance(links, dict) and links:
            for k in sorted(links.keys()):
                lines.append(f"links.{k}: {links[k]}")

    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text
    # Truncate from the bottom (keep objective and early sections)
    head = "\n".join(lines[: min(8, len(lines))]).strip()
    if len(head) > max_chars:
        return head[: max_chars - 20] + "\n… [truncated]"
    for cut in range(len(lines) - 1, 8, -1):
        chunk = "\n".join(lines[:cut]).strip()
        if len(chunk) <= max_chars:
            return chunk + "\n… [truncated]"
    return text[: max_chars - 20] + "\n… [truncated]"


def world_path_for_session(session_dir: Union[str, Path]) -> Path:
    return Path(session_dir) / DEFAULT_WORLD_FILENAME


def ensure_world_for_session(session_dir: Union[str, Path], objective: Optional[str] = None) -> Path:
    """Create discovery_world.json if missing; optionally set objective."""
    d = Path(session_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / DEFAULT_WORLD_FILENAME
    if path.is_file():
        data = load_world(path)
        if objective is not None and str(objective).strip():
            data = apply_patch(data, {"objective": str(objective).strip()})
            save_world(path, data)
        return path
    data = empty_world()
    if objective is not None and str(objective).strip():
        data["objective"] = str(objective).strip()
    save_world(path, data)
    return path


def touch_meta_after_iteration(
    world: Dict[str, Any],
    iteration: int,
    agent_iteration_filename: str,
) -> Dict[str, Any]:
    """Set meta.last_iteration and links.last_agent_iteration_file (additive)."""
    patch = {
        "meta": {
            "last_iteration": iteration,
            "links": {"last_agent_iteration_file": agent_iteration_filename},
        }
    }
    return apply_patch(world, patch)
