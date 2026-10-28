"""Standalone, low-cost Modal CUDA/OpenMM smoke test.

Does not import Biologix, build its Dockerfile, mount volumes, or load secrets.
Run: modal run scripts/modal_cuda_smoke.py
"""
import modal

app = modal.App("biologix-cuda-smoke-isolated")
image = modal.Image.debian_slim(python_version="3.11").pip_install("openmm==8.4.0")


@app.function(image=image, gpu="L4", cpu=1.0, timeout=180, scaledown_window=0)
def verify_cuda():
    import subprocess
    import openmm
    from openmm import unit

    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        check=True, capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    system = openmm.System()
    system.addParticle(39.9)
    system.addParticle(39.9)
    bond = openmm.HarmonicBondForce()
    bond.addBond(0, 1, 0.25, 1000.0)
    system.addForce(bond)
    integrator = openmm.VerletIntegrator(0.001 * unit.picoseconds)
    platform = openmm.Platform.getPlatformByName("CUDA")
    context = openmm.Context(system, integrator, platform)
    context.setPositions([
        openmm.Vec3(0, 0, 0), openmm.Vec3(0.3, 0, 0)
    ] * unit.nanometers)
    integrator.step(20)
    state = context.getState(getEnergy=True, getPositions=True)
    energy = state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    actual = context.getPlatform().getName()
    result = {"ok": actual == "CUDA", "gpu": gpu, "platform": actual,
              "openmm_version": openmm.__version__, "steps": 20,
              "potential_energy_kj_mol": energy}
    print(result, flush=True)
    if not result["ok"]:
        raise RuntimeError("OpenMM did not execute on CUDA")
    return result


@app.local_entrypoint()
def main():
    result = verify_cuda.remote()
    print("CUDA_SMOKE_RESULT:", result)
