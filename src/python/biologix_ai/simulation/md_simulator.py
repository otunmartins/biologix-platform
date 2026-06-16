#!/usr/bin/env python3
"""Evaluate PSMILES via OpenMM Packmol matrix encapsulation + minimize (AMBER14SB + GAFF + Gasteiger)."""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from biologix_ai.run_paths import repo_root_from_package, session_dir_from_env

from .openmm_compat import openmm_available
from .property_extractor import PropertyExtractor


def _env_int(primary: str, fallback: str, default: str) -> int:
    v = os.environ.get(primary) or os.environ.get(fallback) or default
    return int(v)


def _env_float(primary: str, fallback: str, default: str) -> float:
    v = os.environ.get(primary) or os.environ.get(fallback) or default
    return float(v)


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _matrix_target_density_g_cm3() -> Optional[float]:
    raw = os.environ.get("BIOLOGIX_AI_OPENMM_MATRIX_TARGET_DENSITY_G_CM3", "").strip()
    if not raw:
        return None
    return float(raw)


def _matrix_packing_mode() -> str:
    """BIOLOGIX_AI_OPENMM_MATRIX_PACKING_MODE: ``bulk`` (default) or ``shell``."""
    raw = os.environ.get("BIOLOGIX_AI_OPENMM_MATRIX_PACKING_MODE", "bulk").strip().lower()
    if raw == "bulk":
        return "bulk"
    return "shell"


def _matrix_progressive_pack() -> bool:
    """BIOLOGIX_AI_OPENMM_MATRIX_PROGRESSIVE_PACK: greedily add chains until Packmol effort limits."""
    return _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_PROGRESSIVE_PACK", False)


def _matrix_progressive_per_attempt_timeout_s() -> float:
    return _env_float("BIOLOGIX_AI_OPENMM_MATRIX_PACK_PER_ATTEMPT_TIMEOUT_S", "", "120")


def _matrix_progressive_max_total_s() -> Optional[float]:
    raw = os.environ.get("BIOLOGIX_AI_OPENMM_MATRIX_PACK_MAX_TOTAL_S", "").strip()
    if not raw:
        return None
    return float(raw)


def _matrix_progressive_n_max() -> Optional[int]:
    raw = os.environ.get("BIOLOGIX_AI_OPENMM_MATRIX_PROGRESSIVE_N_MAX", "").strip()
    if not raw:
        return None
    return int(raw)


def _effective_matrix_target_density_g_cm3() -> Optional[float]:
    """
    Explicit ``BIOLOGIX_AI_OPENMM_MATRIX_TARGET_DENSITY_G_CM3`` wins.

    Otherwise, unless ``BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1``, use default density-driven
    packing (``BIOLOGIX_AI_OPENMM_MATRIX_DEFAULT_DENSITY_G_CM3``, default 0.52 g/cm³).
    """
    explicit = _matrix_target_density_g_cm3()
    if explicit is not None:
        return explicit
    if _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE", False):
        return None
    return _env_float("BIOLOGIX_AI_OPENMM_MATRIX_DEFAULT_DENSITY_G_CM3", "", "0.52")


def _packmol_required_error() -> RuntimeError:
    return RuntimeError(
        "Packmol is required for openmm_evaluate_psmiles / MDSimulator.evaluate_candidates (matrix encapsulation). "
        "Install the packmol binary on PATH (e.g. conda: conda-forge::packmol, or pip: pip install packmol). "
        "See docs/OPENMM_SCREENING.md and docs/DEPENDENCIES.md."
    )


def _eval_quiet() -> bool:
    """
    Suppress per-candidate progress (JSON + stderr) when user opts out.

    BIOLOGIX_AI_EVAL_QUIET=1, or BIOLOGIX_AI_EVAL_VERBOSE=0/false/no.
    """
    if os.environ.get("BIOLOGIX_AI_EVAL_QUIET", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    v = os.environ.get("BIOLOGIX_AI_EVAL_VERBOSE", "").strip().lower()
    if v in ("0", "false", "no"):
        return True
    return False


def _progress_log(msg: str) -> None:
    """Visible in MCP server stderr (terminal running the server), not in tool return."""
    print(msg, file=sys.stderr, flush=True)


def resolve_eval_structure_artifacts_dir(artifacts_dir: Optional[str] = None) -> Optional[Path]:
    """
    Directory for monomer PNG (psmiles), minimized complex PDB, and preview PNG.

    Resolution order:

    1. Non-empty ``artifacts_dir`` argument.
    2. Env ``BIOLOGIX_AI_EVAL_ARTIFACTS_DIR`` (absolute or relative path).
    3. If ``BIOLOGIX_AI_SESSION_DIR`` is set and ``BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS``
       is not 1/true/yes: ``<session>/structures``.

    Returns None if no directory should be used (callers skip writing structure files).
    """
    raw = (artifacts_dir or "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    env_ad = os.environ.get("BIOLOGIX_AI_EVAL_ARTIFACTS_DIR", "").strip()
    if env_ad:
        p = Path(env_ad).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if os.environ.get("BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return None
    sd = session_dir_from_env(repo_root_from_package())
    if sd:
        p = (sd / "structures").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return None


def attach_matrix_structure_artifacts(
    res: Dict[str, Any],
    *,
    psmiles: str,
    slug: str,
    struct_dir: Union[str, Path],
    pdb_out: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Write monomer PNG, matplotlib preview, and PyMOL chemviz PNG for one matrix eval result.

    Matches MCP ``evaluate_candidates`` artifact layout under ``<struct_dir>/``:
    ``{slug}_monomer.png``, ``{slug}_complex_preview.png``, ``{slug}_complex_chemviz.png``.
    Requires ``pymol`` on PATH for chemviz (open-source PyMOL); evaluation still succeeds if missing.
    """
    from biologix_ai.psmiles_drawing import save_psmiles_png

    from .pdb_preview import write_complex_preview_png
    from .pymol_complex_viz import write_complex_viz_png_auto

    out = dict(res)
    struct = Path(struct_dir).resolve()
    struct.mkdir(parents=True, exist_ok=True)

    if pdb_out:
        out["complex_pdb_path"] = str(Path(pdb_out).resolve())
    elif out.get("minimized_pdb"):
        out["complex_pdb_path"] = out["minimized_pdb"]

    npc = out.get("n_polymer_atoms_per_chain")
    nch = out.get("n_polymer_chains")
    if npc is not None and nch is not None:
        out["n_polymer_atoms"] = int(npc) * int(nch)

    cp = out.get("complex_pdb_path")
    nprot = out.get("n_insulin_atoms")
    if cp and nprot is not None:
        try:
            from .matrix_packing_metrics import compute_matrix_packing_metrics

            out["packing_metrics"] = compute_matrix_packing_metrics(str(cp), int(nprot))
        except Exception as ex:
            out["packing_metrics"] = {"ok": False, "error": str(ex)}

    _progress_log(f"[biologix-ai] stage=artifact_render writing structure PNGs for {slug}")
    monomer_png = struct / f"{slug}_monomer.png"
    r_mono = save_psmiles_png(psmiles, monomer_png, overwrite=True)
    out["monomer_png_path"] = r_mono.get("path") if r_mono.get("ok") else None
    out["monomer_png_error"] = r_mono.get("error")
    if cp:
        preview_png = struct / f"{slug}_complex_preview.png"
        r_prev = write_complex_preview_png(str(cp), str(preview_png))
        out["complex_preview_png_path"] = r_prev.get("path") if r_prev.get("ok") else None
        out["complex_preview_png_error"] = r_prev.get("error")
        chemviz_png = struct / f"{slug}_complex_chemviz.png"
        r_cv, cv_backend = write_complex_viz_png_auto(
            str(cp),
            str(chemviz_png),
            n_protein_atoms=out.get("n_insulin_atoms"),
        )
        out["complex_chemviz_png_path"] = r_cv.get("path") if r_cv.get("ok") else None
        out["complex_chemviz_png_error"] = r_cv.get("error")
        out["complex_chemviz_backend"] = cv_backend
    else:
        out["complex_preview_png_path"] = None
        out["complex_preview_png_error"] = "complex PDB not written"
        out["complex_chemviz_png_path"] = None
        out["complex_chemviz_png_error"] = "complex PDB not written"
        out["complex_chemviz_backend"] = None
    out["structure_artifacts_dir"] = str(struct)
    return out


def _shutdown_process_pool(
    executor: ProcessPoolExecutor,
    *,
    kill_alive: bool = True,
) -> None:
    """Release ProcessPoolExecutor without blocking on stuck workers.

    ``ProcessPoolExecutor`` context managers call ``shutdown(wait=True)`` on
    exit, which blocks indefinitely when a worker is hung in OpenMM/Packmol even
    after ``future.result(timeout=...)`` raised ``FuturesTimeoutError``.
    """
    if kill_alive:
        for proc in list(getattr(executor, "_processes", {}).values()):
            if proc.is_alive():
                try:
                    proc.kill()
                except OSError:
                    pass
    executor.shutdown(wait=False, cancel_futures=True)


def _candidate_timeout_s() -> Optional[float]:
    """
    Per-candidate wall-clock budget for OpenMM matrix evaluation.

    ``BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S`` (default 540). Set ``0`` to disable.
    """
    raw = os.environ.get("BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S", "540").strip()
    if not raw:
        return 540.0
    val = float(raw)
    if val <= 0:
        return None
    return val


def _run_matrix_eval_with_timeout(
    psmiles: str,
    matrix_kw: Dict[str, Any],
    timeout_s: Optional[float],
) -> Dict[str, Any]:
    """Run matrix evaluation in a subprocess so wall-clock limits can be enforced."""
    from .openmm_complex import run_openmm_matrix_relax_and_energy

    if timeout_s is None:
        res = run_openmm_matrix_relax_and_energy(psmiles, **matrix_kw)
        if res is None:
            return {"ok": False, "error": "unknown failure", "stage": "openmm"}
        return res

    executor = ProcessPoolExecutor(max_workers=1)
    future = executor.submit(run_openmm_matrix_relax_and_energy, psmiles, **matrix_kw)
    try:
        res = future.result(timeout=timeout_s)
    except FuturesTimeoutError:
        _shutdown_process_pool(executor, kill_alive=True)
        return {
            "ok": False,
            "error": (
                f"candidate exceeded BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S={timeout_s}s"
            ),
            "stage": "timeout",
            "psmiles": psmiles,
        }
    else:
        _shutdown_process_pool(executor, kill_alive=False)
    if res is None:
        return {"ok": False, "error": "unknown failure", "stage": "openmm"}
    return res


def _env_max_workers() -> int:
    """
    Read ``BIOLOGIX_AI_EVAL_MAX_WORKERS`` from the environment.

    Returns 1 (sequential) when the variable is absent or zero.
    """
    raw = os.environ.get("BIOLOGIX_AI_EVAL_MAX_WORKERS", "").strip()
    if not raw:
        return 1
    v = int(raw)
    return max(1, v)


def _evaluate_one_matrix_candidate(
    index: int,
    psmiles: str,
    name: str,
    n_total: int,
    slug: str,
    pdb_out: Optional[str],
    struct_dir_str: Optional[str],
    matrix_kw: Dict[str, Any],
    random_seed_offset: int,
) -> Tuple[int, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Module-level picklable worker for ProcessPoolExecutor.

    Runs ``run_openmm_matrix_relax_and_energy`` for a single prescreen-passed
    candidate and assembles the post-processing block (packing metrics, PNG
    artifacts) identical to the sequential path.

    Parameters
    ----------
    index:
        Candidate index within the original ``to_eval`` list; used for
        deterministic output ordering and per-index random seed.
    psmiles:
        Validated PSMILES string (already passed prescreen in the parent).
    name:
        Human-readable material name (for progress logging).
    n_total:
        Total number of candidates being evaluated (for progress messages).
    slug:
        Filename-safe basename for artifact naming.
    pdb_out:
        Absolute path string for the minimized complex PDB, or None.
    struct_dir_str:
        Absolute path string of the structure artifacts directory, or None.
    matrix_kw:
        Serializable keyword arguments forwarded to
        ``run_openmm_matrix_relax_and_energy``; ``random_seed`` inside will be
        replaced by ``random_seed_offset`` to ensure per-candidate independence.
    random_seed_offset:
        Seed = base seed + index, passed down to the OpenMM call.

    Returns
    -------
    (index, result_dict_or_none, progress_entry_or_none)
    """
    from .openmm_complex import run_openmm_matrix_relax_and_energy

    kw = dict(matrix_kw)
    kw["random_seed"] = random_seed_offset
    # Suppress verbose output from inside workers; parent logs completions.
    kw["verbose"] = False

    preview = str(psmiles)[:60] + ("…" if len(str(psmiles)) > 60 else "")
    t0 = time.perf_counter()

    try:
        res = _run_matrix_eval_with_timeout(psmiles, kw, _candidate_timeout_s())
    except Exception as exc:
        res = {"ok": False, "error": str(exc), "stage": "openmm", "psmiles": psmiles}

    elapsed = time.perf_counter() - t0

    if res is None or (isinstance(res, dict) and res.get("ok") is False):
        err_msg = (res or {}).get("error", "unknown failure")
        stage = (res or {}).get("stage", "unknown")
        entry = {
            "index": index,
            "total": n_total,
            "status": "failed",
            "reason": err_msg,
            "stage": stage,
            "material_name": name,
            "seconds": round(elapsed, 3),
        }
        return index, None, entry

    if pdb_out:
        res["complex_pdb_path"] = pdb_out
    elif res.get("minimized_pdb"):
        res["complex_pdb_path"] = res["minimized_pdb"]

    if struct_dir_str is not None:
        res = attach_matrix_structure_artifacts(
            res,
            psmiles=psmiles,
            slug=slug,
            struct_dir=struct_dir_str,
            pdb_out=pdb_out,
        )

    pm = res.get("packing_metrics") or {}
    entry = {
        "index": index,
        "total": n_total,
        "status": "completed",
        "material_name": name,
        "psmiles_preview": preview,
        "seconds": round(elapsed, 3),
        "method": res.get("method"),
        "interaction_energy_kj_mol": res.get("interaction_energy_kj_mol"),
        "potential_energy_complex_kj_mol": res.get("potential_energy_complex_kj_mol"),
        "n_insulin_atoms": res.get("n_insulin_atoms"),
        "n_polymer_atoms": res.get("n_polymer_atoms"),
        "n_polymer_chains": res.get("n_polymer_chains"),
        "complex_pdb_path": res.get("complex_pdb_path"),
        "monomer_png_path": res.get("monomer_png_path"),
        "complex_preview_png_path": res.get("complex_preview_png_path"),
        "complex_chemviz_png_path": res.get("complex_chemviz_png_path"),
        "complex_chemviz_backend": res.get("complex_chemviz_backend"),
    }
    if pm.get("ok"):
        entry["min_polymer_protein_distance_nm"] = pm.get("min_polymer_protein_distance_nm")
        entry["fraction_polymer_within_0.80_nm"] = pm.get("fraction_polymer_within_0.80_nm")

    return index, res, entry


class MDSimulator:
    def __init__(
        self,
        n_steps: int = 50000,
        temperature: float = 298.0,
        random_seed: int = 42,
    ):
        if not openmm_available():
            raise RuntimeError(
                "OpenMM screening stack not importable. Install with: "
                "pip install -e '.[openmm]' (or conda: openmm, pip: openmmforcefields, openff-toolkit, pdbfixer, rdkit)."
            )
        self.extractor = PropertyExtractor()
        self.n_steps = n_steps
        self.random_seed = random_seed

    def _get_psmiles(self, candidate: Dict[str, Any]) -> Optional[str]:
        if isinstance(candidate, str):
            return candidate
        p = candidate.get("psmiles") or candidate.get("chemical_structure")
        if p:
            return p
        m = candidate.get("material_name", "")
        return m if m and "[*]" in str(m) else None

    def evaluate_candidates(
        self,
        candidates: List[Dict[str, Any]],
        max_candidates: int = 10,
        verbose: bool = True,
        artifacts_dir: Optional[str] = None,
        max_workers: Optional[int] = None,
        progress_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate polymer PSMILES candidates via OpenMM Packmol matrix.

        Parameters
        ----------
        candidates:
            List of candidate dicts with ``psmiles`` / ``chemical_structure`` keys.
        max_candidates:
            Maximum number of candidates to evaluate from the head of the list.
        verbose:
            When ``True``, emit detailed per-candidate stderr progress and add
            ``evaluation_note`` to the returned dict. When ``False`` and the
            process has not opted out via ``BIOLOGIX_AI_EVAL_QUIET`` /
            ``BIOLOGIX_AI_EVAL_VERBOSE=0``, a short **stderr heartbeat** still
            prints one start and one finish line per candidate (useful with MCP
            ``verbose=false`` / ``response_format=concise``).
        artifacts_dir:
            Directory to write structure artifacts (PDB, PNG). Resolves via
            ``resolve_eval_structure_artifacts_dir`` when not supplied.
        max_workers:
            Number of parallel worker processes. ``1`` (default) = sequential,
            identical to previous behaviour. ``None`` reads
            ``BIOLOGIX_AI_EVAL_MAX_WORKERS`` from the environment (default 1).
            Values ``>1`` dispatch each prescreen-passed candidate to a
            ``ProcessPoolExecutor``; output order is always preserved.

            **RAM warning:** each worker loads a full OpenMM matrix system.
            Start with 2–4 on a machine with plenty of RAM.
        """
        from biologix_ai.material_mappings import prescreen_psmiles_for_md
        from biologix_ai.psmiles_drawing import safe_filename_basename

        from .packmol_packer import _packmol_available
        from .pdb_preview import write_complex_preview_png
        from .pymol_complex_viz import write_complex_viz_png_auto
        from .openmm_complex import clear_stage_heartbeat_hook, register_stage_heartbeat_hook

        if not _packmol_available():
            raise _packmol_required_error()

        def _emit_progress(**fields: Any) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(fields)
            except Exception:
                pass

        if progress_callback is not None:
            register_stage_heartbeat_hook(
                lambda stage, msg: _emit_progress(
                    status="progress",
                    stage=stage,
                    message=msg,
                )
            )
        else:
            clear_stage_heartbeat_hook()

        eval_quiet_env = _eval_quiet()
        if eval_quiet_env:
            verbose = False
        # One-line stderr progress per candidate when the caller passes verbose=False
        # (e.g. MCP concise mode) but has not opted out via BIOLOGIX_AI_EVAL_QUIET / _VERBOSE=0.
        stderr_heartbeat = (not verbose) and (not eval_quiet_env)
        struct_dir = resolve_eval_structure_artifacts_dir(artifacts_dir)
        to_eval = candidates[:max_candidates]
        if not to_eval:
            raise ValueError("empty candidates")
        md_results: List[Optional[Dict[str, Any]]] = []
        material_names = []
        progress: List[Dict[str, Any]] = []
        n_repeats = _env_int("BIOLOGIX_AI_OPENMM_N_REPEATS", "BIOLOGIX_AI_GMX_N_REPEATS", "4")
        n_polymers = _env_int("BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS", "", "8")
        box_nm = _env_float("BIOLOGIX_AI_OPENMM_MATRIX_BOX_NM", "", "7.5")
        density_n_min = _env_int("BIOLOGIX_AI_OPENMM_MATRIX_DENSITY_N_MIN", "", "4")
        density_n_max = _env_int("BIOLOGIX_AI_OPENMM_MATRIX_DENSITY_N_MAX", "", "100")
        shell_a = _env_float("BIOLOGIX_AI_OPENMM_MATRIX_SHELL_A", "", "14.0")
        max_minimize = int(os.environ.get("BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS", "2000"))
        target_density = _effective_matrix_target_density_g_cm3()
        packing_mode = _matrix_packing_mode()
        run_npt = _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_NPT", False)
        npt_ps = _env_float("BIOLOGIX_AI_OPENMM_MATRIX_NPT_PS", "", "0.5")
        wall_s = _env_float("BIOLOGIX_AI_OPENMM_MATRIX_WALL_CLOCK_S", "", "180.0")
        progressive_pack = _matrix_progressive_pack()
        _rs = os.environ.get("BIOLOGIX_AI_OPENMM_MATRIX_RESTRAIN_SHELL")
        if _rs is None or not str(_rs).strip():
            restrain_shell: Optional[bool] = None
        else:
            restrain_shell = _env_bool("BIOLOGIX_AI_OPENMM_MATRIX_RESTRAIN_SHELL", True)
        barostat_fs = _env_float("BIOLOGIX_AI_OPENMM_MATRIX_BAROSTAT_INTERVAL_FS", "", "10.0")
        n_total = len(to_eval)
        candidate_timeout_s = _candidate_timeout_s()

        # Resolve parallelism: explicit argument > env > 1.
        effective_workers: int
        if max_workers is None:
            effective_workers = _env_max_workers()
        else:
            effective_workers = max(1, int(max_workers))
        effective_workers = min(effective_workers, n_total)

        geom = "polymer bulk (full cell)" if packing_mode == "bulk" else "polymer shell"
        msg = (
            f"[biologix-ai] OpenMM matrix (Packmol): {n_total} candidate(s) — "
            f"insulin + {geom}, minimize"
            + (" + NPT sampling" if run_npt else "")
            + ", interaction energy (kJ/mol)."
            + (f" workers={effective_workers}" if effective_workers > 1 else "")
        )
        print(f"  Evaluating {n_total} via OpenMM Packmol matrix...", file=sys.stderr, flush=True)
        if verbose:
            _progress_log(msg)

        # ------------------------------------------------------------------ #
        # Build the shared matrix_kw template (without random_seed, which is  #
        # per-candidate in the parallel path).                                 #
        # ------------------------------------------------------------------ #
        matrix_kw_template: Dict[str, Any] = dict(
            n_repeats=n_repeats,
            random_seed=self.random_seed,  # overridden per-candidate in parallel
            max_minimize_steps=max_minimize,
            verbose=verbose,
            restrain_shell=restrain_shell,
            run_npt=run_npt,
            barostat_interval_fs=barostat_fs,
            npt_duration_ps=npt_ps,
            wall_clock_limit_s=wall_s,
            packing_mode=packing_mode,
        )
        _bio_pdb = os.environ.get("BIOLOGIX_AI_TARGET_PROTEIN_PDB", "").strip()
        if _bio_pdb:
            p = Path(_bio_pdb).expanduser().resolve()
            if p.is_file():
                matrix_kw_template["insulin_pdb_path"] = str(p)
        if progressive_pack:
            matrix_kw_template["progressive_pack"] = True
            matrix_kw_template["progressive_per_attempt_timeout_s"] = (
                _matrix_progressive_per_attempt_timeout_s()
            )
            matrix_kw_template["progressive_max_total_s"] = _matrix_progressive_max_total_s()
            matrix_kw_template["progressive_n_max"] = _matrix_progressive_n_max()
        if target_density is not None:
            matrix_kw_template["target_density_g_cm3"] = target_density
            matrix_kw_template["box_size_nm"] = box_nm
            matrix_kw_template["density_polymer_n_min"] = density_n_min
            matrix_kw_template["density_polymer_n_max"] = density_n_max
        else:
            matrix_kw_template["n_polymers"] = n_polymers
            matrix_kw_template["box_size_nm"] = box_nm
            if packing_mode != "bulk":
                matrix_kw_template["shell_only_angstrom"] = shell_a

        if effective_workers <= 1:
            # ---------------------------------------------------------------- #
            # Sequential path — unchanged behaviour.                           #
            # ---------------------------------------------------------------- #
            for i, cand in enumerate(to_eval):
                psmiles = self._get_psmiles(cand)
                name = cand.get("material_name", psmiles or f"candidate_{i}")
                material_names.append(name)

                if not psmiles or "[*]" not in str(psmiles):
                    md_results.append(None)
                    entry = {
                        "index": i,
                        "total": n_total,
                        "status": "skipped",
                        "reason": "no valid PSMILES with [*]",
                        "material_name": name,
                    }
                    progress.append(entry)
                    if verbose:
                        _progress_log(f"[biologix-ai] {i + 1}/{n_total} skipped (no valid PSMILES)")
                    elif stderr_heartbeat:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} skipped (no valid PSMILES with [*])"
                        )
                    continue

                pre = prescreen_psmiles_for_md(psmiles)
                if not pre.get("ok"):
                    md_results.append(None)
                    reason = pre.get("error", "prescreen rejected")
                    entry = {
                        "index": i,
                        "total": n_total,
                        "status": "rejected",
                        "reason": reason,
                        "material_name": name,
                        "stage": "prescreen",
                    }
                    progress.append(entry)
                    if verbose:
                        _progress_log(f"[biologix-ai] {i + 1}/{n_total} rejected: {reason}")
                    elif stderr_heartbeat:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} rejected (prescreen): "
                            f"{reason[:120]}"
                        )
                    continue

                preview = str(psmiles)[:60] + ("…" if len(str(psmiles)) > 60 else "")
                t0 = time.perf_counter()
                if verbose:
                    _progress_log(
                        f"[biologix-ai] {i + 1}/{n_total} Packmol+matrix: {preview} "
                        f"(max {max_minimize} minimizer steps)"
                    )
                elif stderr_heartbeat:
                    _progress_log(
                        f"[biologix-ai] {i + 1}/{n_total} matrix eval starting: {preview}"
                    )
                _emit_progress(
                    status="progress",
                    stage="candidate_start",
                    candidate_index=i,
                    total=n_total,
                    material_name=name,
                    message=f"candidate {i + 1}/{n_total} matrix eval starting",
                )
                slug = safe_filename_basename(str(name))
                pdb_out: Optional[str] = None
                if struct_dir is not None:
                    pdb_out = str(struct_dir / f"{slug}_complex_minimized.pdb")
                matrix_kw: Dict[str, Any] = dict(matrix_kw_template)
                matrix_kw["save_minimized_pdb"] = pdb_out

                try:
                    res = _run_matrix_eval_with_timeout(
                        psmiles, matrix_kw, candidate_timeout_s
                    )
                except Exception as exc:
                    res = {"ok": False, "error": str(exc), "stage": "openmm", "psmiles": psmiles}

                elapsed = time.perf_counter() - t0

                if res is None or (isinstance(res, dict) and res.get("ok") is False):
                    err_msg = (res or {}).get("error", "unknown failure")
                    stage = (res or {}).get("stage", "unknown")
                    md_results.append(None)
                    entry = {
                        "index": i,
                        "total": n_total,
                        "status": "failed",
                        "reason": err_msg,
                        "stage": stage,
                        "material_name": name,
                        "seconds": round(elapsed, 3),
                    }
                    progress.append(entry)
                    _emit_progress(
                        status="failed",
                        stage=stage,
                        candidate_index=i,
                        total=n_total,
                        material_name=name,
                        message=str(err_msg)[:200],
                    )
                    if verbose:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} FAILED ({stage}): {err_msg[:200]}"
                        )
                    elif stderr_heartbeat:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} finished in {elapsed:.1f}s "
                            f"status=failed stage={stage}"
                        )
                    continue

                if struct_dir is not None:
                    res = attach_matrix_structure_artifacts(
                        res,
                        psmiles=psmiles,
                        slug=slug,
                        struct_dir=struct_dir,
                        pdb_out=pdb_out,
                    )
                elif pdb_out:
                    res["complex_pdb_path"] = pdb_out
                elif res.get("minimized_pdb"):
                    res["complex_pdb_path"] = res["minimized_pdb"]
                md_results.append(res)
                entry = {
                    "index": i,
                    "total": n_total,
                    "status": "completed",
                    "material_name": name,
                    "psmiles_preview": preview,
                    "seconds": round(elapsed, 3),
                    "method": res.get("method"),
                    "interaction_energy_kj_mol": res.get("interaction_energy_kj_mol"),
                    "potential_energy_complex_kj_mol": res.get(
                        "potential_energy_complex_kj_mol"
                    ),
                    "n_insulin_atoms": res.get("n_insulin_atoms"),
                    "n_polymer_atoms": res.get("n_polymer_atoms"),
                    "n_polymer_chains": res.get("n_polymer_chains"),
                    "complex_pdb_path": res.get("complex_pdb_path"),
                    "monomer_png_path": res.get("monomer_png_path"),
                    "complex_preview_png_path": res.get("complex_preview_png_path"),
                    "complex_chemviz_png_path": res.get("complex_chemviz_png_path"),
                    "complex_chemviz_backend": res.get("complex_chemviz_backend"),
                }
                pm = res.get("packing_metrics") or {}
                if pm.get("ok"):
                    entry["min_polymer_protein_distance_nm"] = pm.get(
                        "min_polymer_protein_distance_nm"
                    )
                    entry["fraction_polymer_within_0.80_nm"] = pm.get(
                        "fraction_polymer_within_0.80_nm"
                    )
                progress.append(entry)
                _emit_progress(
                    status="completed",
                    stage="done",
                    candidate_index=i,
                    total=n_total,
                    material_name=name,
                    message=(
                        f"candidate {i + 1}/{n_total} completed "
                        f"E_int={res.get('interaction_energy_kj_mol')} kJ/mol"
                    ),
                )
                if verbose:
                    log_tail = f"E_int={res.get('interaction_energy_kj_mol')} kJ/mol"
                    if pm.get("ok"):
                        log_tail += (
                            f", d_min(poly-prot)="
                            f"{pm.get('min_polymer_protein_distance_nm'):.3f} nm"
                        )
                    _progress_log(
                        f"[biologix-ai] {i + 1}/{n_total} done in {elapsed:.1f}s {log_tail}"
                    )
                elif stderr_heartbeat:
                    eint = res.get("interaction_energy_kj_mol")
                    _progress_log(
                        f"[biologix-ai] {i + 1}/{n_total} finished in {elapsed:.1f}s "
                        f"status=completed E_int={eint} kJ/mol"
                    )

        else:
            # ---------------------------------------------------------------- #
            # Parallel path — ProcessPoolExecutor.                             #
            # Prescreen happens in the parent; workers only run OpenMM.        #
            # Output order is guaranteed by sorting on the returned index.     #
            # ---------------------------------------------------------------- #

            # Phase 1: prescreen all candidates in the parent.
            # Collect (index, psmiles, name, slug, pdb_out) for passing candidates
            # and immediately record skipped/rejected entries.
            md_results = [None] * n_total  # type: ignore[assignment]
            material_names = [""] * n_total

            jobs: List[Tuple[int, str, str, str, Optional[str]]] = []
            for i, cand in enumerate(to_eval):
                psmiles = self._get_psmiles(cand)
                name = cand.get("material_name", psmiles or f"candidate_{i}")
                material_names[i] = name

                if not psmiles or "[*]" not in str(psmiles):
                    entry = {
                        "index": i,
                        "total": n_total,
                        "status": "skipped",
                        "reason": "no valid PSMILES with [*]",
                        "material_name": name,
                    }
                    progress.append(entry)
                    if verbose:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} skipped (no valid PSMILES)"
                        )
                    elif stderr_heartbeat:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} skipped (no valid PSMILES with [*])"
                        )
                    continue

                pre = prescreen_psmiles_for_md(psmiles)
                if not pre.get("ok"):
                    reason = pre.get("error", "prescreen rejected")
                    entry = {
                        "index": i,
                        "total": n_total,
                        "status": "rejected",
                        "reason": reason,
                        "material_name": name,
                        "stage": "prescreen",
                    }
                    progress.append(entry)
                    if verbose:
                        _progress_log(f"[biologix-ai] {i + 1}/{n_total} rejected: {reason}")
                    elif stderr_heartbeat:
                        _progress_log(
                            f"[biologix-ai] {i + 1}/{n_total} rejected (prescreen): "
                            f"{reason[:120]}"
                        )
                    continue

                slug = safe_filename_basename(str(name))
                pdb_out_p: Optional[str] = None
                if struct_dir is not None:
                    pdb_out_p = str(struct_dir / f"{slug}_complex_minimized.pdb")
                jobs.append((i, psmiles, name, slug, pdb_out_p))

            # Phase 2: dispatch passing candidates to workers.
            n_jobs = len(jobs)
            if n_jobs and (verbose or stderr_heartbeat):
                _progress_log(
                    f"[biologix-ai] Submitting {n_jobs} candidate(s) to "
                    f"{effective_workers} worker process(es)."
                )

            struct_dir_str = str(struct_dir) if struct_dir is not None else None
            # strip verbose from the template; workers always run quietly
            worker_kw = {k: v for k, v in matrix_kw_template.items() if k != "verbose"}

            executor = ProcessPoolExecutor(max_workers=effective_workers)
            try:
                future_map = {
                    executor.submit(
                        _evaluate_one_matrix_candidate,
                        idx,
                        ps,
                        nm,
                        n_total,
                        sl,
                        pdb,
                        struct_dir_str,
                        worker_kw,
                        self.random_seed + idx,  # per-candidate seed for reproducibility
                    ): idx
                    for idx, ps, nm, sl, pdb in jobs
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    try:
                        if candidate_timeout_s is None:
                            idx, res, entry = future.result()
                        else:
                            idx, res, entry = future.result(timeout=candidate_timeout_s)
                    except FuturesTimeoutError:
                        name = material_names[idx] if idx < len(material_names) else f"candidate_{idx}"
                        entry = {
                            "index": idx,
                            "total": n_total,
                            "status": "failed",
                            "reason": (
                                f"candidate exceeded BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S="
                                f"{candidate_timeout_s}s"
                            ),
                            "stage": "timeout",
                            "material_name": name,
                        }
                        res = None
                    md_results[idx] = res
                    if entry is not None:
                        progress.append(entry)
                        if verbose:
                            status = entry.get("status", "")
                            if status == "completed":
                                log_tail = (
                                    f"E_int={entry.get('interaction_energy_kj_mol')} kJ/mol"
                                )
                                if entry.get("min_polymer_protein_distance_nm") is not None:
                                    log_tail += (
                                        f", d_min(poly-prot)="
                                        f"{entry['min_polymer_protein_distance_nm']:.3f} nm"
                                    )
                                _progress_log(
                                    f"[biologix-ai] {idx + 1}/{n_total} done in "
                                    f"{entry.get('seconds', '?')}s {log_tail}"
                                )
                            else:
                                _progress_log(
                                    f"[biologix-ai] {idx + 1}/{n_total} {status}: "
                                    f"{entry.get('reason', '')}"
                                )
                        elif stderr_heartbeat:
                            status = entry.get("status", "")
                            sec = entry.get("seconds", "?")
                            if status == "completed":
                                eint = entry.get("interaction_energy_kj_mol")
                                _progress_log(
                                    f"[biologix-ai] {idx + 1}/{n_total} finished in {sec}s "
                                    f"status=completed E_int={eint} kJ/mol"
                                )
                            else:
                                _progress_log(
                                    f"[biologix-ai] {idx + 1}/{n_total} finished in {sec}s "
                                    f"status={status}"
                                )
            finally:
                _shutdown_process_pool(executor, kill_alive=True)

        feedback = self.extractor.extract_feedback(md_results, material_names)
        out: Dict[str, Any] = {
            "high_performers": feedback["high_performers"],
            "effective_mechanisms": feedback["effective_mechanisms"],
            "problematic_features": feedback["problematic_features"],
            "property_analysis": feedback["property_analysis"],
            "successful_materials": feedback["high_performers"],
            "md_results_raw": md_results,
        }
        if struct_dir is not None:
            out["structure_artifacts_dir"] = str(struct_dir)
        out["evaluation_progress"] = progress
        if verbose:
            out["evaluation_note"] = (
                "Each candidate: Packmol-packed polymer shell around insulin (periodic box), "
                "LocalEnergyMinimizer, optional short NPT segment (BIOLOGIX_AI_OPENMM_MATRIX_NPT), "
                "then interaction energy (kJ/mol). Requires packmol on PATH. "
                "By default uses density-driven chain count (BIOLOGIX_AI_OPENMM_MATRIX_DEFAULT_DENSITY_G_CM3); "
                "set BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1 for fixed N_POLYMERS + shell instead. "
                "packing_metrics reports polymer–protein proximity on the minimized PDB."
                + (
                    f" Evaluated with {effective_workers} parallel worker process(es) "
                    "(BIOLOGIX_AI_EVAL_MAX_WORKERS)."
                    if effective_workers > 1
                    else ""
                )
            )
        clear_stage_heartbeat_hook()
        return out
