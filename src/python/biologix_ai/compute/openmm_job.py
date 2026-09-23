"""One OpenMM matrix evaluation, runnable in the MCP process or in a Modal worker.

:func:`run_openmm_job` is the body of ``openmm_evaluate_psmiles``. The web
container calls it directly (local backend) or ships a :class:`OpenMMJobSpec`
to a CPU or GPU Modal function, which runs :func:`run_openmm_job_remote` in a
scratch directory and returns the result plus the artifact files it wrote, so
no volume has to be shared or reloaded between containers.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Cross-container progress: the worker writes its current stage here and the web
# container, which holds the job a client is checking on, reads it back.
PROGRESS_DICT = "biologix-job-progress"
_PROGRESS_MIN_INTERVAL_S = 3.0

# Where the target lives inside the image; a spec names it instead of shipping it.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


@dataclass
class OpenMMJobSpec:
    """Everything a worker needs; plain data so Modal can serialize it."""

    psmiles: List[str]
    verbose: bool = False
    concise: bool = True
    max_workers: int = 1
    openmm_platform: str = ""
    target_chains: str = ""
    # Exactly one of these names the protein: a path inside the package (the
    # bundled insulin), or the PDB text of a prepared target. Neither = default insulin.
    target_package_path: str = ""
    target_pdb_text: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    # Where a worker reports its stage so the caller's job can show it.
    progress_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OpenMMJobSpec":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def target_for_spec(target_pdb_path: str) -> Dict[str, str]:
    """Spec fields for a target PDB on this machine."""
    if not target_pdb_path:
        return {}
    path = Path(target_pdb_path).resolve()
    try:
        return {"target_package_path": str(path.relative_to(PACKAGE_ROOT))}
    except ValueError:
        return {"target_pdb_text": path.read_text(encoding="utf-8")}


def run_openmm_job(
    spec: OpenMMJobSpec,
    *,
    target_pdb_path: str = "",
    artifacts_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Evaluate ``spec.psmiles`` and return the ``openmm_evaluate_psmiles`` payload."""
    from biologix_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        return {
            "ok": False,
            "abort": True,
            "error": (
                "OpenMM screening stack incomplete (openmm, openmmforcefields, openff.toolkit, "
                "and AmberTools antechamber/parmchk2 on PATH)."
            ),
        }
    from biologix_ai.simulation import MDSimulator

    candidates = [
        {"material_name": f"Candidate_{i}", "chemical_structure": p}
        for i, p in enumerate(spec.psmiles)
    ]
    sim = MDSimulator(
        n_steps=5000,
        target_pdb_path=target_pdb_path,
        target_chains=spec.target_chains,
        openmm_platform=spec.openmm_platform,
    )
    result = sim.evaluate_candidates(
        candidates,
        max_candidates=len(candidates),
        verbose=spec.verbose,
        artifacts_dir=artifacts_dir,
        max_workers=max(1, int(spec.max_workers or 1)),
        progress_callback=progress_callback,
    )
    try:
        from biologix_ai.simulation.scoring import discovery_score

        score = discovery_score(result)
    except Exception:
        score = None

    outcomes = []
    for ep in result.get("evaluation_progress") or []:
        status = ep.get("status", "unknown")
        oc: Dict[str, Any] = {
            "index": ep.get("index"),
            "material_name": ep.get("material_name"),
            "status": status,
        }
        if status == "completed":
            oc["interaction_energy_kj_mol"] = ep.get("interaction_energy_kj_mol")
            # Conditions the report has to disclose beside the number.
            for key in (
                "openmm_platform",
                "box_nm",
                "box_enlarged",
                "packmol_retry",
                "n_protein_chains",
                "counterions",
                "polymer_chain_charge",
            ):
                if ep.get(key) is not None:
                    oc[key] = ep[key]
        else:
            if ep.get("stage"):
                oc["stage"] = ep["stage"]
            if ep.get("reason"):
                oc["reason"] = ep["reason"]
        outcomes.append(oc)

    out: Dict[str, Any] = {
        "ok": True,
        "high_performers": result["high_performers"],
        "effective_mechanisms": result["effective_mechanisms"],
        "problematic_features": result["problematic_features"],
    }
    if result.get("property_analysis"):
        out["property_analysis"] = result["property_analysis"]
    if score is not None:
        out["discovery_score"] = round(score, 4)
    out["candidate_outcomes"] = outcomes
    if not spec.concise:
        if spec.verbose and result.get("evaluation_progress") is not None:
            out["evaluation_progress"] = result["evaluation_progress"]
        if result.get("evaluation_note"):
            out["evaluation_note"] = result["evaluation_note"]
    if result.get("structure_artifacts_dir"):
        out["structure_artifacts_dir"] = result["structure_artifacts_dir"]
    if not spec.concise:
        paths = []
        for r in result.get("md_results_raw") or []:
            if not isinstance(r, dict):
                continue
            paths.append(
                {
                    "psmiles": r.get("psmiles"),
                    "complex_pdb_path": r.get("complex_pdb_path"),
                    "monomer_png_path": r.get("monomer_png_path"),
                    "complex_preview_png_path": r.get("complex_preview_png_path"),
                    "complex_chemviz_png_path": r.get("complex_chemviz_png_path"),
                    "packing_metrics": r.get("packing_metrics"),
                }
            )
        if paths:
            out["structure_artifact_paths"] = paths
    return out


def modal_progress_writer(key: str) -> Optional[Callable[[Dict[str, Any]], None]]:
    """A progress callback that records the latest stage under *key* in a Modal Dict.

    Progress is a courtesy: a Dict that cannot be reached must never fail a
    simulation, so every failure here is swallowed and the writer is skipped.
    """
    if not key:
        return None
    try:
        import modal  # noqa: PLC0415 — present only inside Modal containers

        store = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)
    except Exception:
        return None
    last: Dict[str, Any] = {"stage": None, "at": 0.0}

    def write(event: Dict[str, Any]) -> None:
        stage = str(event.get("stage") or "")
        if event.get("status") != "progress" or not stage:
            return
        now = time.monotonic()
        if stage == last["stage"] and now - last["at"] < _PROGRESS_MIN_INTERVAL_S:
            return
        last.update(stage=stage, at=now)
        try:
            store.put(key, {"stage": stage, "message": str(event.get("message") or ""), "at": time.time()})
        except Exception:
            pass

    return write


def run_openmm_job_remote(spec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Worker entry point: run in a scratch directory, return result and artifacts.

    Returns ``{"result": payload, "files": {"structures/<name>": bytes},
    "scratch_root": str}``; paths inside ``result`` start with ``scratch_root`` and
    are rewritten by :func:`materialize_remote_result` on the web side.
    """
    spec = OpenMMJobSpec.from_dict(spec_dict)
    for key, value in spec.env.items():
        os.environ[key] = value
    with tempfile.TemporaryDirectory(prefix="biologix_openmm_") as scratch:
        root = Path(scratch)
        target = ""
        if spec.target_package_path:
            target = str(PACKAGE_ROOT / spec.target_package_path)
        elif spec.target_pdb_text:
            target_file = root / "biologic_target.pdb"
            target_file.write_text(spec.target_pdb_text, encoding="utf-8")
            target = str(target_file)
        structures = root / "structures"
        structures.mkdir()
        result = run_openmm_job(
            spec,
            target_pdb_path=target,
            artifacts_dir=str(structures),
            progress_callback=modal_progress_writer(spec.progress_key),
        )
        files: Dict[str, bytes] = {}
        total = 0
        for path in sorted(structures.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                total += len(data)
                if total > _MAX_ARTIFACT_BYTES:
                    result.setdefault("warnings", []).append(
                        "structure artifacts over 64 MB were not returned from the worker"
                    )
                    break
                files[str(path.relative_to(root))] = data
        return {"result": result, "files": files, "scratch_root": str(root)}


def materialize_remote_result(response: Dict[str, Any], run_dir: Optional[Path]) -> Dict[str, Any]:
    """Write a worker's artifacts under *run_dir* and point the result at them."""
    result = response.get("result") or {}
    scratch = str(response.get("scratch_root") or "")
    if run_dir is None:
        return result
    run_dir = Path(run_dir)
    for rel, data in (response.get("files") or {}).items():
        dest = (run_dir / rel).resolve()
        if run_dir.resolve() not in dest.parents:
            continue  # never write outside the session
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return _rewrite_paths(result, scratch, str(run_dir)) if scratch else result


def _rewrite_paths(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old) else value
    if isinstance(value, list):
        return [_rewrite_paths(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_paths(v, old, new) for k, v in value.items()}
    return value
