#!/usr/bin/env python3
"""Smoke test: OpenMM merged minimize + interaction energy (same path as openmm_evaluate_psmiles)."""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "python"))

_HINT = """
OpenMM stack not importable. Use insulin-ai-sim env or:
  pip install -e '.[openmm]'
Create env: mamba env create -f environment-simulation.yml
""".strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psmiles", nargs="?", default="[*]COC[*]")
    ap.add_argument("--n-repeats", type=int, default=2)
    ap.add_argument(
        "--offset-nm",
        type=float,
        default=2.5,
        help="Ligand offset along x (nm), same role as INSULIN_AI_GMX_OFFSET_NM",
    )
    ap.add_argument(
        "--max-minimize-steps",
        type=int,
        default=5000,
        help="LocalEnergyMinimizer max iterations",
    )
    args = ap.parse_args()

    from insulin_ai.simulation.openmm_compat import openmm_available

    if not openmm_available():
        print("openmm + openmmforcefields + openff.toolkit: not all importable")
        print(_HINT)
        sys.exit(1)

    from insulin_ai.simulation.openmm_complex import run_openmm_relax_and_energy

    print("OpenMM screening stack: OK")
    r = run_openmm_relax_and_energy(
        args.psmiles,
        n_repeats=args.n_repeats,
        ligand_offset_nm=(args.offset_nm, 0.0, 0.0),
        max_minimize_steps=args.max_minimize_steps,
    )
    print(json.dumps(r, indent=2) if r else "run_openmm_relax_and_energy returned None")
    if r is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
