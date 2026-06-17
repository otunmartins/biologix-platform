#!/usr/bin/env python3
"""
OpenMM matrix: insulin + polymers via Packmol (**shell** annulus or **bulk** full cell),
minimize, then compute interaction energy.

Requires: packmol (pip install packmol), biologix-ai-sim env (openmm, openff, rdkit).
  mamba activate biologix-ai-sim
  python scripts/run_openmm_matrix.py '[*]CC[*]'

When CLI flags are omitted, defaults follow BIOLOGIX_AI_* env vars (same as MCP OpenMM path).

With --run-dir (or BIOLOGIX_AI_SESSION_DIR set), writes <session>/structures/ artifacts:
monomer PNG, matplotlib preview, PyMOL chemviz PNG (*_complex_chemviz.png), minimized PDB.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "python"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psmiles", nargs="?", default="[*]CC[*]")
    ap.add_argument("--n-repeats", type=int, default=4)
    ap.add_argument("--n-polymers", type=int, default=8)
    ap.add_argument(
        "--box-nm",
        type=float,
        default=7.5,
        help="Cubic box edge (nm); smaller = denser visuals for fixed chain budget",
    )
    ap.add_argument("--shell-angstrom", type=float, default=14.0)
    ap.add_argument(
        "--packing-mode",
        choices=("shell", "bulk"),
        default="bulk",
        help="shell: Packmol outside-sphere annulus; bulk: space-filling cell (no outside sphere)",
    )
    ap.add_argument(
        "--density-driven",
        action="store_true",
        help="Use target density as primary; derive n_polymers (and shell radius in shell mode)",
    )
    ap.add_argument(
        "--target-density",
        type=float,
        default=None,
        metavar="G_CM3",
        help="Target polymer density (g/cm³) when --density-driven",
    )
    ap.add_argument(
        "--run-dir",
        metavar="PATH",
        help="Session run directory; writes <run-dir>/structures/ PNGs and minimized PDB (MCP parity)",
    )
    ap.add_argument(
        "--material-name",
        default="cli_candidate",
        help="Candidate label for artifact filenames (default: cli_candidate)",
    )
    ap.add_argument(
        "--slug",
        metavar="NAME",
        help="Filename slug for artifacts (default: derived from --material-name)",
    )
    ap.add_argument("--save-packed", metavar="PATH", help="Save packed PDB (before minimization)")
    ap.add_argument(
        "--save-minimized",
        metavar="PATH",
        default=os.path.join(REPO, "runs", "openmm_matrix_minimized.pdb"),
        help="Save minimized complex PDB (default: runs/openmm_matrix_minimized.pdb)",
    )
    ap.add_argument(
        "--no-restrain-shell",
        action="store_true",
        help="Disable spherical shell restraint on polymer atoms during minimization",
    )
    ap.add_argument(
        "--no-npt",
        action="store_true",
        help="Skip NPT MD; use single-point interaction energy from minimized structure",
    )
    ap.add_argument(
        "--barostat-interval-fs",
        type=float,
        default=10.0,
        metavar="FS",
        help="Barostat applied every N fs (default: 10)",
    )
    ap.add_argument(
        "--npt-ps",
        type=float,
        default=1.0,
        metavar="PS",
        help="NPT simulation length in ps (default: 1)",
    )
    ap.add_argument(
        "--wall-clock-min",
        type=float,
        default=15.0,
        metavar="MIN",
        help="Stop NPT when wall-clock exceeds this many minutes (default: 15)",
    )
    ap.add_argument(
        "--max-minimize-steps",
        type=int,
        default=None,
        help="LocalEnergyMinimizer step cap (default: BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS or 2000)",
    )
    ap.add_argument(
        "--candidate-timeout-s",
        type=float,
        default=None,
        help="Wall-clock cap for this run (default: BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S)",
    )
    ap.add_argument(
        "--progressive-pack",
        action="store_true",
        help="After initial n_polymers, keep adding chains until Packmol fails/times out or limits hit",
    )
    ap.add_argument(
        "--progressive-pack-timeout",
        type=float,
        default=120.0,
        metavar="SEC",
        help="Per Packmol attempt timeout when --progressive-pack (default: 120)",
    )
    ap.add_argument(
        "--progressive-pack-max-total-s",
        type=float,
        default=None,
        metavar="SEC",
        help="Optional cumulative wall-clock cap for all progressive Packmol attempts",
    )
    ap.add_argument(
        "--progressive-pack-n-max",
        type=int,
        default=None,
        metavar="N",
        help="Optional max chain count (cap progressive growth)",
    )
    ap.add_argument(
        "--render-chemviz-only",
        action="store_true",
        help="Skip OpenMM; re-render *_complex_chemviz.png from existing minimized PDB "
        "(requires --run-dir and --material-name or --slug)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    from biologix_ai.run_paths import ENV_SESSION
    from biologix_ai.simulation.md_simulator import (
        _run_matrix_eval_with_timeout,
        attach_matrix_structure_artifacts,
        resolve_eval_structure_artifacts_dir,
    )
    from biologix_ai.simulation.openmm_cli_config import resolve_openmm_cli_kwargs
    from biologix_ai.psmiles_drawing import safe_filename_basename

    if args.render_chemviz_only:
        if args.run_dir:
            os.environ[ENV_SESSION] = str(Path(args.run_dir).resolve())
        struct_dir = resolve_eval_structure_artifacts_dir(args.run_dir)
        if struct_dir is None:
            print("render-chemviz-only requires --run-dir or BIOLOGIX_AI_SESSION_DIR", file=sys.stderr)
            sys.exit(1)
        slug = (args.slug or "").strip() or safe_filename_basename(args.material_name)
        pdb_path = struct_dir / f"{slug}_complex_minimized.pdb"
        if not pdb_path.is_file():
            print(f"PDB not found: {pdb_path}", file=sys.stderr)
            sys.exit(1)
        meta_path = struct_dir / f"{slug}_complex_meta.json"
        n_prot: int | None = None
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            n_prot = meta.get("n_insulin_atoms")
        from biologix_ai.simulation.pymol_complex_viz import write_complex_viz_png_auto

        chemviz_png = struct_dir / f"{slug}_complex_chemviz.png"
        r_cv, backend = write_complex_viz_png_auto(
            str(pdb_path),
            str(chemviz_png),
            n_protein_atoms=n_prot,
        )
        payload = {
            "ok": r_cv.get("ok"),
            "complex_pdb_path": str(pdb_path),
            "complex_chemviz_png_path": r_cv.get("path") if r_cv.get("ok") else None,
            "complex_chemviz_png_error": r_cv.get("error"),
            "complex_chemviz_backend": backend,
            "n_insulin_atoms": n_prot,
        }
        print(json.dumps(payload, indent=2))
        if not r_cv.get("ok"):
            print(
                f"PyMOL chemviz failed: {r_cv.get('error')} "
                "(Docker image ≥0.5.26 includes pymol-viz env; *_complex_preview.png is a dot cloud, not for reports)",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Wrote {chemviz_png}", file=sys.stderr)
        return

    density_flag = True if args.density_driven else None
    no_npt_flag = True if args.no_npt else None

    env_kw = resolve_openmm_cli_kwargs(
        density_driven_flag=density_flag,
        n_polymers_flag=None if density_flag else args.n_polymers,
        target_density_flag=args.target_density,
        no_npt_flag=no_npt_flag,
        max_minimize_steps_flag=args.max_minimize_steps,
        candidate_timeout_flag=args.candidate_timeout_s,
    )

    kw: dict = dict(
        n_repeats=args.n_repeats,
        box_size_nm=args.box_nm,
        save_packed_pdb=args.save_packed,
        save_minimized_pdb=args.save_minimized,
        verbose=args.verbose,
        packing_mode=args.packing_mode,
        barostat_interval_fs=args.barostat_interval_fs,
        npt_duration_ps=args.npt_ps,
        wall_clock_limit_s=args.wall_clock_min * 60,
        progressive_pack=args.progressive_pack,
        progressive_per_attempt_timeout_s=args.progressive_pack_timeout,
        progressive_max_total_s=args.progressive_pack_max_total_s,
        progressive_n_max=args.progressive_pack_n_max,
    )
    kw.update({k: v for k, v in env_kw.items() if k not in ("candidate_timeout_s", "run_npt")})
    if "run_npt" in env_kw:
        kw["run_npt"] = env_kw["run_npt"]
    if env_kw.get("target_density_g_cm3") is not None:
        kw["target_density_g_cm3"] = env_kw["target_density_g_cm3"]
    elif env_kw.get("n_polymers") is not None:
        kw["n_polymers"] = env_kw["n_polymers"]
    if env_kw.get("max_minimize_steps") is not None:
        kw["max_minimize_steps"] = env_kw["max_minimize_steps"]

    if args.no_restrain_shell:
        kw["restrain_shell"] = False
    elif args.packing_mode == "bulk":
        kw["restrain_shell"] = None
    else:
        kw["restrain_shell"] = True
    if not env_kw.get("target_density_g_cm3") and args.packing_mode != "bulk":
        kw["shell_only_angstrom"] = args.shell_angstrom

    if args.run_dir:
        os.environ[ENV_SESSION] = str(Path(args.run_dir).resolve())

    struct_dir = resolve_eval_structure_artifacts_dir(None)
    slug = (args.slug or "").strip() or safe_filename_basename(args.material_name)
    pdb_out: str | None = None
    if struct_dir is not None:
        pdb_out = str(struct_dir / f"{slug}_complex_minimized.pdb")
        kw["save_minimized_pdb"] = pdb_out
    elif args.save_minimized:
        kw["save_minimized_pdb"] = args.save_minimized

    timeout_s = env_kw.get("candidate_timeout_s")
    result = _run_matrix_eval_with_timeout(args.psmiles, kw, timeout_s)
    if not isinstance(result, dict) or result.get("ok") is False:
        print(json.dumps(result or {"ok": False, "error": "unknown failure"}, indent=2))
        print("run_openmm_matrix_relax_and_energy failed", file=sys.stderr)
        sys.exit(1)

    if struct_dir is not None:
        result = attach_matrix_structure_artifacts(
            result,
            psmiles=args.psmiles,
            slug=slug,
            struct_dir=struct_dir,
            pdb_out=pdb_out,
        )
        if not result.get("complex_chemviz_png_path"):
            print(
                f"WARNING: PyMOL chemviz PNG missing: {result.get('complex_chemviz_png_error')} "
                f"(re-run: python3 scripts/run_openmm_matrix.py --render-chemviz-only "
                f"--run-dir {struct_dir.parent} --material-name {args.material_name!r})",
                file=sys.stderr,
            )
        elif result.get("complex_chemviz_png_path"):
            print(f"Structure figure: {result['complex_chemviz_png_path']}", file=sys.stderr)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
