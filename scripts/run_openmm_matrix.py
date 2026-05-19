#!/usr/bin/env python3
"""
OpenMM matrix: insulin + polymers via Packmol (**shell** annulus or **bulk** full cell),
minimize, then compute interaction energy.

Requires: packmol (pip install packmol), insulin-ai-sim env (openmm, openff, rdkit).
  mamba activate insulin-ai-sim
  python scripts/run_openmm_matrix.py '[*]CC[*]'
"""
import argparse
import json
import os
import sys

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
        default=0.6,
        metavar="G_CM3",
        help="Target polymer density (g/cm³) when --density-driven",
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
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    from insulin_ai.simulation.openmm_complex import run_openmm_matrix_relax_and_energy

    kw: dict = dict(
        n_repeats=args.n_repeats,
        box_size_nm=args.box_nm,
        save_packed_pdb=args.save_packed,
        save_minimized_pdb=args.save_minimized,
        verbose=args.verbose,
        packing_mode=args.packing_mode,
        run_npt=not args.no_npt,
        barostat_interval_fs=args.barostat_interval_fs,
        npt_duration_ps=args.npt_ps,
        wall_clock_limit_s=args.wall_clock_min * 60,
        progressive_pack=args.progressive_pack,
        progressive_per_attempt_timeout_s=args.progressive_pack_timeout,
        progressive_max_total_s=args.progressive_pack_max_total_s,
        progressive_n_max=args.progressive_pack_n_max,
    )
    if args.no_restrain_shell:
        kw["restrain_shell"] = False
    elif args.packing_mode == "bulk":
        kw["restrain_shell"] = None
    else:
        kw["restrain_shell"] = True
    if args.density_driven:
        kw["target_density_g_cm3"] = args.target_density
    else:
        kw["n_polymers"] = args.n_polymers
        if args.packing_mode != "bulk":
            kw["shell_only_angstrom"] = args.shell_angstrom

    result = run_openmm_matrix_relax_and_energy(args.psmiles, **kw)
    if result is None:
        print("run_openmm_matrix_relax_and_energy failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
