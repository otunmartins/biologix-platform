# Dependencies

This document lists the scientific and reporting dependencies for biologix-ai (OpenMM, RDKit, Packmol, psmiles, etc.). For install and MCP setup, start with [MCP Getting Started](MCP_GETTING_STARTED.md).

---

## OpenMM stack: conda first (often “not installed yet”)

The following are **required for real physics** (`openmm_evaluate_psmiles`, `MDSimulator`, benchmarks) and are **not** fully covered by `pip install -r requirements.txt`:

| Piece | How it is installed |
|--------|---------------------|
| **OpenMM**, **RDKit**, **pdbfixer**, **Packmol** (binary) | Conda packages in **`environment-simulation.yml`** → env **`biologix-ai-sim`** |
| **OpenFF Toolkit**, **openff-units** | Conda-forge only (same yml) |
| **openmmforcefields** | Pip inside that env (listed in the yml) |

Until you run **`./install`** (default: chunked conda + pip), **`./install --conda-yml`**, or **`mamba env create -f environment-simulation.yml`**, and use **`mamba run -n biologix-ai-sim`** / **`conda activate biologix-ai-sim`**, checks like `python -c "import openmm"` in another environment (e.g. a generic **`biologix-ai`** conda env or a bare venv) may succeed only if you installed those packages there yourself—they are **not** implied by cloning the repo.

**Quick check:**

```bash
mamba run -n biologix-ai-sim python -c "import openmm, rdkit, openff.toolkit; print('OK')"
```

### Troubleshooting (conda solve failures)

If `conda env update` or `./install` fails with **`nothing provides libgfortran 1.0 needed by packmol-1!17.221`** or a long chain of **icu** / **libboost** / **python 3.11** conflicts, conda is almost certainly mixing **conda-forge** with a **legacy channel** (often **`omnia`** from an old OpenMM install).

**1. Remove `omnia` (run one command at a time; confirm it is gone)**

Do **not** paste a whole block with comments into the shell unless each line is a real command. After each `conda config --remove channels omnia`, check:

```bash
conda config --show channels
```

**`omnia` must not appear.** If remove does nothing, edit **`~/.condarc`** and delete the `omnia` line.

Helper (from repo root):

```bash
bash scripts/fix_conda_channels_for_biologix_ai.sh
```

**2. Channel priority + solver**

- **`channel_priority strict`** with the **libmamba** solver often prints many **`SOLVER_RULE_STRICT_REPO_PRIORITY`** warnings and may appear to hang. Prefer:
  - `conda config --set channel_priority flexible`
  - then update with the **classic** solver:

```bash
conda env update -f environment-simulation.yml --prune --solver classic
```

**`./install --conda-yml`** uses a one-shot YAML solve and passes **`--solver classic`** when the runner is **`conda`** (not **mamba**). The default **`./install`** uses chunked installs and does not run that solve.

**3. Optional:** put **conda-forge** first: `conda config --prepend channels conda-forge`

This repo’s YAML lists **conda-forge only**; dropping **omnia** avoids obsolete **packmol** builds that need **`libgfortran 1.0`** (not provided on current Linux).

**If the solver dies with only `Killed` (no Python traceback)** during **Solving environment**, the Linux **OOM killer** usually stopped conda—the classic solver can use **many GB of RAM** on one big environment.

Mitigations (pick one or combine):

1. **Chunked conda installs** (smaller solves, lower peak RAM) — this is **`./install`**’s default; or run:  
   `bash scripts/install_biologix_ai_sim_lowmem.sh`
2. **More swap** (e.g. 8–16 GiB) or close other applications, then retry.
3. **Mamba** for the full file (often lower peak memory than classic conda on one shot):  
   `mamba env update -f environment-simulation.yml --prune`  
   (use **`channel_priority flexible`** if you see endless `SOLVER_RULE_STRICT_REPO_PRIORITY` warnings.)

The `.*` **warnings** from conda about version specs are harmless noise from upstream repodata.

**If `mamba install` or conda's built-in libmamba crashes** (`malloc(): corrupted top size`, `Aborted (core dumped)`) — your **libmamba** is outdated (1.5.x ships with conda 24.x and has known heap-corruption bugs). **`./install`** auto-downloads **[micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html)** (standalone binary, ships libmamba 2.x, ~10 MB) and uses it for conda-forge solves. Alternatively upgrade your base conda to 26.x or install [Miniforge](https://github.com/conda-forge/miniforge).

**Network: `RemoteDisconnected` / “Remote end closed connection”** while downloading conda-forge **`repodata.json`** — usually a **transient** CDN or Wi‑Fi/VPN glitch; conda **retries** automatically. If the install ultimately fails, run **`conda clean -i`** (refresh index cache) and **`./install`** again. Optional: **`conda config --set remote_max_retries 10`**.

**`Solving environment:` with a spinner** — the **classic** solver can take **many minutes** per wave (CPU-bound); that is normal. Wait unless the process exits with **`Killed`** (OOM) or a hard error. **micromamba** is typically 5–10x faster.

---

| File | Role |
|------|------|
| **`environment-simulation.yml`** | **conda env `biologix-ai-sim`:** Python, **rdkit**, **openmm**, **pdbfixer**, **packmol**, **openff-toolkit**, **openff-units**, pip (openmmforcefields, psmiles, mcp, paper-qa, benchmarks, `-e .`, …) |
| **`requirements.txt`** | Pip-only supplement; **does not** install OpenMM/RDKit/Packmol/OpenFF—see table above |

## Simulation / evaluate

- **OpenMM**, **openmmforcefields**, **OpenFF Toolkit**, **RDKit**, **pdbfixer**, **matplotlib** (structure preview PNGs for reports).
- **PyMOL** (open-source: `conda install -c conda-forge pymol` or `pip install pymol-open-source`): **`pymol` on PATH** is required for **`*_complex_chemviz.png`** (insulin cartoon + DSS, polymer ball-and-stick). There is no matplotlib fallback; if PyMOL is missing, evaluation still succeeds but `complex_chemviz_png_error` is set.
- **Re-render session PDBs with PyMOL** (same logic as MCP): `mamba run -n biologix-ai-sim python scripts/render_complex_pymol.py runs/<session>/structures` — writes `Candidate_*_complex_minimized_pymol.png` next to each minimized PDB (no RDKit import required). On **macOS ARM**, `pip install pymol-open-source` may need conda-forge **`glew`** and **`libnetcdf`** in the same env if the wheel fails to load `libGLEW` / `libnetcdf` (see PyMOL install notes for your platform).
- **packmol** (binary on PATH): **required** for MCP **`openmm_evaluate_psmiles`** / **`MDSimulator.evaluate_candidates`**, which run **Packmol matrix encapsulation** (`run_openmm_matrix_relax_and_energy`). Without Packmol, evaluation raises at startup. See `docs/OPENMM_SCREENING.md` for matrix env vars (`BIOLOGIX_AI_OPENMM_MATRIX_*`, etc.).
- For a **fast merged** single-oligomer diagnostic (no Packmol), use **`scripts/diagnose_openmm_complex.py`** only; it is not the MCP screening path.

```bash
pytest tests/test_simulation.py tests/test_openmm_complex.py tests/test_material_mappings.py -v
```

## Benchmark (Optuna PSMILES discovery)

- **Preferred:** use conda env **`biologix-ai-sim`** (same as OpenMM screening and MCP). After **`./install`** (or **`mamba env update -f environment-simulation.yml`**), **Optuna** is installed with the rest of the pip stack.
- **Without conda:** `pip install -e ".[benchmark]"` adds **Optuna** only; you still need the OpenMM/RDKit stack for real evaluation (see `docs/OPENMM_SCREENING.md`).
- **Run (real physics):** `mamba run -n biologix-ai-sim python benchmarks/optuna_psmiles_discovery.py --seed '[*]OCC[*]' --n-trials 5`
- **Does not** require MCP, LLMs, or literature tools.

## External third-party benchmarks (non-BO; MCP-independent)

Clones live under **`extern/benchmarks/`** (gitignored); see **[`docs/THIRD_PARTY_BENCHMARKS.md`](THIRD_PARTY_BENCHMARKS.md)** and **`scripts/clone_external_benchmarks.sh`**.

| System | Upstream deps | biologix-ai `pyproject.toml` |
|--------|----------------|-----------------------------|
| **Polymer Generative Models Benchmark** (Wisconsin) | PyTorch / MOSES / upstream Zenodo per [ytl0410](https://github.com/ytl0410/Polymer-Generative-Models-Benchmark) | Not pinned; install in a separate venv when running full training |
| **IBM logical-agent polymer RL** | `pip install -e md-envs` + data zip per [IBM repo](https://github.com/IBM/logical-agent-driven-polymer-discovery) | Not pinned |

Thin smoke scripts: `python benchmarks/polymer_generative_models_benchmark.py`, `python benchmarks/ibm_polymer_rl_benchmark.py` (no extra biologix-ai deps beyond Python).

## MCP — discovery figures & PDF reports

These power **biologix-ai** MCP tools; they are **not** used by the Optuna benchmark unless you import reporting helpers.

| Dependency | Role | If missing |
|------------|------|------------|
| **[psmiles](https://github.com/FermiQ/psmiles)** (git in `pyproject.toml`) | `PolymerSmiles.savefig` — 2D PNG of repeat units; `render_psmiles_png` | Tool returns install hint; use `biologix-ai-sim` |
| **fpdf2** | PDF output (`compile_discovery_markdown_to_pdf`, batch `write_discovery_summary_report`) | PDF step fails; error JSON lists `pip install fpdf2` |
| **markdown** | MD → HTML before PDF (`compile_discovery_markdown_to_pdf`) | Tool error; `pip install markdown` |
| **Pillow** | Re-encode local ``img`` files (RGBA, palette, etc.) to RGB PNG before fpdf2 embeds them—no manual ``*_raster.png`` workarounds | `pip install Pillow` |
| **duckduckgo-search** | `validate_psmiles(..., crosscheck_web=true)` snippets | Cross-check disabled or error per tool |

**AI-driven reporting (preferred):** the agent writes `SUMMARY_REPORT.md`, calls `render_psmiles_png` for figures (or relies on `openmm_evaluate_psmiles` session artifacts for monomer + complex preview), then `compile_discovery_markdown_to_pdf`. **Matplotlib** is required for automatic complex preview PNGs from `openmm_evaluate_psmiles` when structure artifacts are enabled.

**Optional:** `write_discovery_summary_report` rebuilds a minimal MD+PDF from `agent_iteration_*.json` only (quick skeleton, no narrative)—same dependencies.

**Beyond this repo’s defaults:** if you add Pandoc, LaTeX, or WeasyPrint yourself, document them in your environment; they are **not** required by the shipped tools.
