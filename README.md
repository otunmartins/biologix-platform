# Biologics AI Platform — Any-biologic delivery materials discovery

AI-driven discovery of **formulation and delivery materials for any biologic** (insulin, mAbs, enzymes, vaccines, peptides). A single discovery campaign produces: candidate polymer screening, retrosynthesis routes, residual-monomer ADMET, regulatory excipient compliance (EMA/FDA/GRAS), and an immutable GxP audit trail — all in one session via MCP (OpenCode) or FastAPI.

### One session, one biologic

Start with the **`biologics-delivery-discovery`** OpenCode agent (default). It guides you through:

```
biologic name/PDB → candidates → OpenMM screening → retrosynthesis →
monomer ADMET → excipient compliance → compiled report + audit trail
```

Session state is persisted to `runs/<session_id>/` via `discovery_world.json`, funnel-context checkpoints, and an append-only JSONL audit trail. If the chat disconnects mid-pipeline, `get_funnel_context` resumes from the last checkpoint.

### New MCP tools (May 2026, NovoMCP-inspired)

| Tool | What it does |
|------|-------------|
| `get_candidate_profile` | Single-call dossier: validate + ADMET + retro routes + compliance |
| `screen_candidate_library` | Batch screen up to 50 candidates with ADMET + compliance |
| `check_excipient_compliance` | EMA/FDA/GRAS lookup + immunogenicity SMARTS alerts |
| `save_funnel_context` / `get_funnel_context` | Named pipeline checkpoints for session resumption |
| `save_pipeline_stage` / `get_pipeline_audit` | Per-candidate GxP audit trail (21 CFR Part 11 style) |

---

## What you need

- **Conda or Mamba** — to install the simulation stack (OpenMM, RDKit, Packmol, psmiles). [Miniforge](https://github.com/conda-forge/miniforge) or Anaconda works.
- **An IDE with MCP** — Cursor is the main target; others that support MCP will work too.
- **Optional:** API keys for literature search (see [docs/SECURITY.md](docs/SECURITY.md)).

### OpenMM / RDKit are not installed until you use the conda env

**`pip install -r requirements.txt` alone does not install OpenMM, RDKit, pdbfixer, Packmol, or OpenFF Toolkit** (those are conda-forge binaries or not on PyPI). Until you run **`./install`** (or **`./install --conda-yml`** / **`mamba env create -f environment-simulation.yml`**), `python -c "import openmm"` will fail in a plain venv or a mismatched conda env.

Use the env named **`insulin-ai-sim`** (that is what `./install` and `scripts/run_mcp_server.sh` expect):

```bash
./install   # first time (default: chunked conda + pip)
mamba run -n insulin-ai-sim python -c "import openmm; print(openmm.__version__)"
```

If your shell shows a different env (for example `insulin-ai`), either activate `insulin-ai-sim` for screening or align that env with the same packages—see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

---

## Quick start

1. Clone this repo and run the installer from the repo root:

   ```bash
   ./install
   ```

2. Copy the MCP config and fix the paths:

   ```bash
   cp .cursor/mcp.json.example .cursor/mcp.json
   # Edit .cursor/mcp.json and replace /ABSOLUTE/PATH/TO/insulin-ai with your real path
   ```

3. Restart Cursor so it loads the MCP server.

For step-by-step instructions (including Windows), see [docs/MCP_GETTING_STARTED.md](docs/MCP_GETTING_STARTED.md).

---

## Install by platform

### macOS and Linux

From the repo root:

```bash
./install
```

**`./install`** builds **`insulin-ai-sim`** by default with **chunked conda-forge installs + pip** (lower RAM than solving the whole YAML at once). For a single-shot solve from the YAML (more RAM): **`./install --conda-yml`**.

Or create/update the env manually:

```bash
mamba env create -f environment-simulation.yml
# or low-RAM equivalent:  bash scripts/install_insulin_ai_sim_lowmem.sh
```

The environment is named **`insulin-ai-sim`**. You don't need to activate it to use MCP—the launcher script does that for you.

If the solver fails with **`libgfortran 1.0`** or unsatisfiable **packmol**, remove the legacy **`omnia`** channel (**`bash scripts/fix_conda_channels_for_insulin_ai.sh`**), then re-run **`./install`**. If you use **`./install --conda-yml`** and conda is **`Killed`** during **Solving environment**, use the default **`./install`** (chunked) instead. Details: [Dependencies — Troubleshooting](docs/DEPENDENCIES.md#troubleshooting-conda-solve-failures).

### Windows

The screening stack (OpenMM, Packmol, bash scripts) does **not** run on native Windows. Use **WSL2** (Windows Subsystem for Linux):

1. Install [WSL2 and Ubuntu](https://apps.microsoft.com/store/detail/ubuntu/9PDXGNCFSCZV).
2. Open a Ubuntu terminal and clone the repo inside Linux (e.g. `~/insulin-ai`).
3. Follow the same steps as macOS/Linux. In `.cursor/mcp.json`, use paths like `/home/yourname/insulin-ai/...`, not `C:\...`.

Full details: [docs/MCP_GETTING_STARTED.md](docs/MCP_GETTING_STARTED.md#windows-users-use-wsl2).

---

## Connect the MCP server in Cursor

1. Copy `.cursor/mcp.json.example` to `.cursor/mcp.json`.
2. Replace every `/ABSOLUTE/PATH/TO/insulin-ai` with the absolute path to your clone (e.g. `/Users/jane/insulin-ai` or `/home/jane/insulin-ai`).
3. Set `PAPER_DIRECTORY` and `PYTHONPATH` in the `env` block to point at your repo (the example shows the pattern).
4. Restart Cursor (Cmd/Ctrl+Shift+P → "Reload Window").

The MCP server runs via `scripts/run_mcp_server.sh`, which uses the `insulin-ai-sim` conda env automatically.

---

## Verify the setup

From a terminal:

```bash
mamba run -n insulin-ai-sim python scripts/diagnose_openmm_complex.py '[*]COC[*]'
```

You should see energies printed. If this fails, Packmol or OpenMM may be missing—see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

---

## New: Retrosynthesis & ADMET Pipeline

The `experimental` branch adds:

- **`plan_retrosynthesis`** — Two-engine retrosynthesis: RetroSynthesisAgent for polymer routes, AiZynthFinder for monomer routes to purchasable building blocks.
- **`check_monomer_admet`** — SMARTS structural alerts + ADMET-AI predictions (hERG, hepatotoxicity, mutagenicity) on residual monomers.
- **`compile_results`** — Full pipeline: retrosynthesis + ADMET + composite ranking + narrative report.
- **FastAPI** — `POST /retrosynthesis/plan`, `POST /admet/screen`, `POST /pipeline/compile` at `http://localhost:8000/docs`.

### Installation (vendored tools)

All third-party retrosynthesis and ADMET tools are vendored as git submodules under `extern/`. **If OpenCode reports "extern/RetroSynthesisAgent wasn't available" or you get an import error for `RetroSynAgent`, run:**

```bash
# 1. Make sure submodules are populated
git submodule update --init --recursive

# 2. Install everything — submodule deps + aizynthfinder + admet_ai + insulin-ai extras
bash scripts/install_submodules.sh
```

`RetroSynthesisAgent` has no `setup.py` so it cannot be `pip install -e`'d directly — the MCP server adds `extern/RetroSynthesisAgent` to `sys.path` at startup. `install_submodules.sh` installs its Python-side dependencies (`graphviz`, `pubchempy`, `pyvis`, `scholarly`, `jsonpickle`, `fake-useragent`, etc.).

```bash
# Verify after install:
python -c "import sys; sys.path.insert(0,'extern/RetroSynthesisAgent'); from RetroSynAgent.treeBuilder import Tree; print('RetroSynAgent OK')"
python -c "from aizynthfinder.aizynthfinder import AiZynthFinder; print('AiZynthFinder OK')"
python -c "from admet_ai import ADMETModel; print('ADMET-AI OK')"
```

Or manually (if you only want retro deps without running the full script):

```bash
pip install -e extern/aizynthfinder
pip install -e extern/admet_ai
pip install -e ".[retro,admet,api,dev]"
```

---

## Documentation

| Document | What it covers |
|----------|----------------|
| [MCP Getting Started](docs/MCP_GETTING_STARTED.md) | Full setup, WSL, Cursor config, troubleshooting. |
| [MCP Tool Reference](docs/MCP_SERVERS.md) | What each MCP tool does and what it needs. |
| [PSMILES Guide](docs/PSMILES_GUIDE.md) | Polymer notation and writing valid PSMILES. |
| [OpenMM Screening](docs/OPENMM_SCREENING.md) | How screening works, env vars, structure outputs. |
| [Dependencies](docs/DEPENDENCIES.md) | Conda env contents, Packmol, PyMOL, reporting libs. |
| [Security](docs/SECURITY.md) | API keys, where to put secrets, rotation. |
| [Project Structure](docs/PROJECT_STRUCTURE.md) | Repo layout, where code and config live. |
| [Summary Report Style](docs/SUMMARY_REPORT_STYLE.md) | How agent-written discovery reports are formatted. |
| [Third-Party Benchmarks](docs/THIRD_PARTY_BENCHMARKS.md) | Wisconsin and IBM benchmark adapters in `extern/`. |

---

## Other commands

**Optuna benchmark** (no MCP, same env):

```bash
mamba run -n insulin-ai-sim python benchmarks/optuna_psmiles_discovery.py --seed '[*]OCC[*]' --n-trials 5
```

**Matrix screening** (Packmol + OpenMM):

```bash
mamba run -n insulin-ai-sim python scripts/run_openmm_matrix.py '[*]CC[*]'
```
