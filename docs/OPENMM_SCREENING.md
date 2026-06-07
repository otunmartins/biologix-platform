# OpenMM screening (MCP `openmm_evaluate_psmiles` = matrix / Packmol)

## Packmol matrix (default for `openmm_evaluate_psmiles`)

MCP **`openmm_evaluate_psmiles`** and [`MDSimulator.evaluate_candidates`](../src/python/biologix_ai/simulation/md_simulator.py) use **only** the matrix path:

- **Code:** [`run_openmm_matrix_relax_and_energy`](../src/python/biologix_ai/simulation/openmm_complex.py).
- **Requirement:** the **`packmol` binary on PATH**. If Packmol is missing, evaluation **raises** (no fallback). Install via conda-forge (`packmol`) or `pip install packmol` (see [`DEPENDENCIES.md`](DEPENDENCIES.md)).
- **Geometry:** Insulin (chains A+B) fixed at the box center; **N polymer chains** from **Packmol** in a periodic cube.
  - **`BIOLOGIX_AI_OPENMM_MATRIX_PACKING_MODE=bulk` (default):** space-filling **bulk-in-cell** — no `outside sphere`; chains fill the box (overlap with insulin avoided by Packmol `tolerance`). Density-driven chain counts use **full-cell** volume (see [`matrix_density.py`](../src/python/biologix_ai/simulation/matrix_density.py)).
  - **`shell`:** annulus / encapsulation — polymers constrained with **`outside sphere`** (shell around insulin). Density-driven chain counts use **shell** volume (box minus inner sphere).
- **Relaxation:** `LocalEnergyMinimizer`, optional short **NPT** segment (`BIOLOGIX_AI_OPENMM_MATRIX_NPT`), then **interaction energy** (kJ/mol). Spherical **shell restraints** during minimize apply only in **shell** mode unless you set a shell radius and enable restraint.

**CLI (same physics, more options):** [`scripts/run_openmm_matrix.py`](../scripts/run_openmm_matrix.py).

### Environment variables (matrix evaluation)

| Variable | Default (typical) | Role |
|----------|-------------------|------|
| `BIOLOGIX_AI_OPENMM_N_REPEATS` (`BIOLOGIX_AI_GMX_N_REPEATS`) | `4` | Repeat units per polymer chain |
| `BIOLOGIX_AI_OPENMM_MATRIX_PACKING_MODE` | `bulk` | `bulk` = full-cell bulk packing (no `outside sphere`); `shell` = annulus (`outside sphere`) |
| `BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE` | `0` | If `1`, use fixed `N_POLYMERS` + `SHELL_A` below. If `0` (default), use **default density** unless overridden. |
| `BIOLOGIX_AI_OPENMM_MATRIX_DEFAULT_DENSITY_G_CM3` | `0.52` | When not in fixed mode and `TARGET_DENSITY` is unset: derive `n_polymers` (and shell radius in **shell** mode) from this target polymer density (g/cm³). |
| `BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS` | `8` | Chain count when **fixed mode** (or explicit override when not using density) |
| `BIOLOGIX_AI_OPENMM_MATRIX_BOX_NM` | `7.5` | Cubic box edge (nm). Larger cells need more chains to look “filled” at a given target density; old default **9.0** often looked sparse when *n* was capped. |
| `BIOLOGIX_AI_OPENMM_MATRIX_DENSITY_N_MIN` | `4` | Lower clamp for density-derived chain count |
| `BIOLOGIX_AI_OPENMM_MATRIX_DENSITY_N_MAX` | `100` | Upper clamp for density-derived chain count (lower values + large box ⇒ sparse visuals). Runtime is bounded by Packmol/OpenMM timeouts and agent budgets. |
| `BIOLOGIX_AI_OPENMM_MATRIX_SHELL_A` | `14.0` | Shell inner radius (Å) for Packmol `outside sphere` (**shell** mode; fixed / non–density-driven) |
| `BIOLOGIX_AI_OPENMM_MATRIX_TARGET_DENSITY_G_CM3` | *(optional)* | **Explicit** density driver; overrides default density when set (see `matrix_density.py`) |
| `BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS` | `2000` | Minimizer iterations |
| `BIOLOGIX_AI_OPENMM_MATRIX_NPT` | `0` (off) | Set `1` to run short NPT after minimize |
| `BIOLOGIX_AI_OPENMM_MATRIX_NPT_PS` | `0.5` | NPT length (ps) when NPT on |
| `BIOLOGIX_AI_OPENMM_MATRIX_WALL_CLOCK_S` | `180` | Stop NPT when wall-clock exceeds this (seconds) |
| `BIOLOGIX_AI_OPENMM_MATRIX_RESTRAIN_SHELL` | *(unset)* | If **unset**, minimize uses shell restraint only in **shell** mode (off for **bulk**). If set to `0`/`1`, forces off/on (on only applies when a shell radius exists). |
| `BIOLOGIX_AI_OPENMM_MATRIX_BAROSTAT_INTERVAL_FS` | `10` | Barostat interval when NPT on |
| `BIOLOGIX_AI_OPENMM_MATRIX_PROGRESSIVE_PACK` | `0` | If `1`, after the initial *n_polymers* (density or fixed), **greedily add chains** until the next Packmol run fails or times out, or limits below are hit. JSON includes **`packmol_progressive`**. |
| `BIOLOGIX_AI_OPENMM_MATRIX_PACK_PER_ATTEMPT_TIMEOUT_S` | `120` | Per Packmol subprocess timeout (seconds) during progressive packing. |
| `BIOLOGIX_AI_OPENMM_MATRIX_PACK_MAX_TOTAL_S` | *(unset)* | Optional **cumulative** wall-clock budget for all progressive Packmol attempts (unset = no total cap). |
| `BIOLOGIX_AI_OPENMM_MATRIX_PROGRESSIVE_N_MAX` | *(unset)* | Optional **maximum** chain count (cap progressive growth). |
| **Verbose / quiet:** `BIOLOGIX_AI_EVAL_QUIET=1` or `BIOLOGIX_AI_EVAL_VERBOSE=0` | | Smaller JSON / no stderr progress (also disables stderr heartbeat) |
| **Stderr heartbeat** (implicit) | *(on)* | When **`verbose=False`** on `openmm_evaluate_psmiles` / `evaluate_candidates` but **quiet env is not set**, stderr still prints **`[biologix-ai] i/n matrix eval starting: …`** and **`… finished …`** per candidate. Parallel runs also log **`Submitting N candidate(s) to W worker process(es).`** |
| `BIOLOGIX_AI_EVAL_MAX_WORKERS` | `1` | Number of parallel worker processes for `evaluate_candidates` / `openmm_evaluate_psmiles`. `1` = sequential (default, safe). Set to `2`–`4` to evaluate multiple candidates concurrently via `ProcessPoolExecutor`. Each worker loads a full OpenMM matrix system — start conservatively. Can also be set per-call via the `max_workers` argument on `openmm_evaluate_psmiles` or `MDSimulator.evaluate_candidates` (argument takes precedence over env). |
| `BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S` | `900` (Docker default) | Per-candidate wall-clock budget for the full matrix eval (Packmol + minimize + energy). Set `0` to disable. Timed-out candidates return `stage=timeout` in `candidate_outcomes` instead of blocking the whole batch indefinitely. |

**Note:** NPT is **off** by default so MCP runs finish in minutes; turn on for sampling-averaged interaction energy at the cost of runtime.

### Parallel evaluation

`openmm_evaluate_psmiles` and `MDSimulator.evaluate_candidates` support concurrent candidate evaluation via `ProcessPoolExecutor`:

- **Default:** `max_workers=1` — sequential, identical to previous behaviour, no regression.
- **Enable:** pass `max_workers=N` to the MCP tool, or set `BIOLOGIX_AI_EVAL_MAX_WORKERS=N` in the environment.  The explicit argument takes precedence over the environment variable.
- **Prescreen in parent:** validity checks and `prescreen_psmiles_for_md` run in the main process; only candidates that pass are dispatched to workers. Skipped/rejected entries are recorded at their original indices before dispatching.
- **Order preserved:** results are always returned in the original candidate order regardless of completion order.
- **Seeds:** each worker receives `seed = base_seed + candidate_index` so runs are reproducible per candidate. Parallel and sequential results may differ slightly (interleaved RNG state in OpenMM NPT) but relative rankings are stable.
- **RAM:** each worker holds a full OpenMM system in memory. Start with 2–4 workers; scale up only if RAM allows.
- **Diminishing returns:** if `max_workers` exceeds the number of candidates it is clamped automatically.

### Why one `openmm_evaluate_psmiles` call can run for many hours (or look “stuck”)

- **Single tool response:** The MCP tool returns **one** JSON only after **every** candidate in the batch finishes. OpenCode’s tool panel does not stream partial JSON. With **`response_format=concise`**, the returned payload is smaller, but the client still waits for the full batch.
- **`verbose=false` vs quiet:** Passing **`verbose=false`** (common with concise mode) **does not** silence stderr on the MCP server unless you also set **`BIOLOGIX_AI_EVAL_QUIET=1`** or **`BIOLOGIX_AI_EVAL_VERBOSE=0`**. By default, **`verbose=false`** still emits the **stderr heartbeat** lines described in the environment table so the terminal running **`biologix_ai_mcp_server.py`** shows which candidate is running. OpenCode’s UI may still look idle if you are not watching that server terminal.
- **Per-candidate timeout:** Docker sets **`BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S=900`** by default. When exceeded, that candidate is marked **`failed`** with **`stage=timeout`** and the batch continues. Override with `-e BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S=0` for no limit.
- **Stage heartbeats:** stderr lines like **`[biologix-ai] stage=packmol …`**, **`stage=minimize`**, **`stage=energy_eval`** are emitted during matrix evaluation (unless **`BIOLOGIX_AI_EVAL_QUIET=1`**). Session logs also land in **`runs/<session>/tool_events.jsonl`**.
- **Minimize step count:** [`run_openmm_matrix_relax_and_energy`](../src/python/biologix_ai/simulation/openmm_complex.py) calls **`openmm.LocalEnergyMinimizer.minimize(..., maxIterations=BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS)`** (Docker default **1500**). Each iteration can mean many force evaluations on a **large** system. The **`BIOLOGIX_AI_OPENMM_MATRIX_WALL_CLOCK_S`** limit applies only to the **optional NPT** loop after minimize—not to minimization itself.
- **Parallel workers:** **`max_workers=4`** runs four full matrix builds at once. That multiplies **RAM** and **CPU** contention; with **swap**, wall time explodes. Prefer **`max_workers=1`** or **`2`** unless you have confirmed headroom (see default in `BIOLOGIX_AI_EVAL_MAX_WORKERS`).
- **What to do:** Check **`htop`** for `packmol`, **`python`** (MCP server / workers), or **`pymol`** — rising CPU time means work in progress. For faster turns: smaller batches, **`max_workers=1`**, lower **`BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS`**, lower **`BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS`** (or density caps), **`BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1`** to skip PNG/PyMOL while debugging, or pass **`verbose=true`** on **`openmm_evaluate_psmiles`** for full per-candidate stderr detail (beyond the default heartbeat when `verbose=false`).

**Packing quality:** Each result includes **`packing_metrics`** (nearest protein–polymer heavy-atom distances in nm, fractions within 0.5 / 0.8 / 1.2 nm). Use **`min_polymer_protein_distance_nm`** and **`fraction_polymer_within_0.80_nm`** to spot sparse or disconnected polymer relative to insulin (e.g. very large min distance or low fraction within 0.8 nm after minimization).

### Faster diagnostic re-runs (verbose + lighter physics)

To confirm the pipeline is alive without waiting for a full batch:

1. Pass **`verbose=true`** on **`openmm_evaluate_psmiles`** (or call **`MDSimulator.evaluate_candidates(..., verbose=True)`**) so stderr shows Packmol/OpenMM detail.
2. On the **MCP server** environment, optionally set:
   - **`BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1`** — skip PDB/PNG/PyMOL writes.
   - **`BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS=150`** (or similar) — shorter minimization for screening-only checks.
   - **`BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1`**, **`BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS=2`**, **`BIOLOGIX_AI_OPENMM_N_REPEATS=1`** — smallest matrix useful for smoke tests.

Example (same physics as `tests/test_mdsimulator_eval.py` smoke), from the repo root:

```bash
BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1 \
BIOLOGIX_AI_OPENMM_MATRIX_NPT=0 \
BIOLOGIX_AI_OPENMM_MATRIX_FIXED_MODE=1 \
BIOLOGIX_AI_OPENMM_MATRIX_N_POLYMERS=2 \
BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS=150 \
BIOLOGIX_AI_OPENMM_N_REPEATS=1 \
mamba run -n biologix-ai-sim python -c "
from biologix_ai.simulation import MDSimulator
sim = MDSimulator(n_steps=100, random_seed=1)
r = sim.evaluate_candidates(
    [{'material_name': 'smoke', 'chemical_structure': '[*]CC[*]'}],
    max_candidates=1,
    verbose=True,
)
print(r['evaluation_progress'][0])
"
```

## Fast merged insulin + single oligomer (diagnostics only)

**Not** used by `openmm_evaluate_psmiles`. For a quick vacuum merge of insulin + **one** offset oligomer (no Packmol):

- **Code:** [`run_openmm_relax_and_energy`](../src/python/biologix_ai/simulation/openmm_complex.py)
- **CLI:** [`scripts/diagnose_openmm_complex.py`](../scripts/diagnose_openmm_complex.py)

```bash
mamba run -n biologix-ai-sim python scripts/diagnose_openmm_complex.py '[*]COC[*]'
```

Use this only for debugging or comparisons; it does **not** model encapsulation.

## Session structure artifacts (reports)

When **`BIOLOGIX_AI_SESSION_DIR`** points at the active run folder (or you pass MCP **`run_dir`** / **`artifacts_dir`**), each successful candidate gets files under **`<session>/structures/`**:

| File pattern | Content |
|--------------|---------|
| `<slug>_monomer.png` | 2D repeat unit (psmiles `savefig`) |
| `<slug>_complex_minimized.pdb` | Minimized **matrix** complex (insulin + many chains, periodic image) |
| `<slug>_complex_preview.png` | Matplotlib 3D scatter preview of that PDB |
| `<slug>_complex_chemviz.png` | PyMOL ray-traced insulin cartoon + polymer sticks (`pymol_complex_viz`); see `scripts/render_complex_chemviz.py` |

Disable with **`BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1`**. Override directory with **`BIOLOGIX_AI_EVAL_ARTIFACTS_DIR`**. Requires **matplotlib** for monomer + `*_complex_preview.png`; **PyMOL on PATH** for `*_complex_chemviz.png`.

The MCP response includes **`structure_artifacts_dir`** and **`structure_artifact_paths`** when artifacts are written. See **`docs/SUMMARY_REPORT_STYLE.md`** for embedding in `SUMMARY_REPORT.md`.

Rankings and absolute energies differ from historical GROMACS (AMBER99SB-ILDN + Acpype) runs.
