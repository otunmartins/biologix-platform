"""OpenMM merged minimize timing (requires OpenMM + openmmforcefields + OpenFF).

This benchmarks ``run_openmm_relax_and_energy`` (single oligomer), not MCP ``openmm_evaluate_psmiles``
(matrix / Packmol). For matrix timing, use ``scripts/run_openmm_matrix.py``.
"""

import time


def benchmark_openmm_merged(psmiles: str) -> float:
    from insulin_ai.simulation.openmm_compat import openmm_available
    from insulin_ai.simulation.openmm_complex import run_openmm_relax_and_energy

    if not openmm_available():
        return -1.0
    t0 = time.perf_counter()
    run_openmm_relax_and_energy(psmiles, n_repeats=2)
    return time.perf_counter() - t0


if __name__ == "__main__":
    ps = "[*]CC[*]"
    t = benchmark_openmm_merged(ps)
    print(f"OpenMM merged minimize [{ps}]: {t:.3f} s" if t >= 0 else "OpenMM stack not available")
