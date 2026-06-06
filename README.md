# Biologics AI Platform — Any-biologic delivery materials discovery

AI-driven discovery of **formulation and delivery materials for any biologic** (insulin, mAbs, enzymes, vaccines, peptides). A single discovery campaign produces: candidate polymer screening, retrosynthesis routes, residual-monomer ADMET, regulatory excipient compliance (EMA/FDA/GRAS), and an immutable GxP audit trail — all in one session via MCP (OpenCode) or FastAPI.

See [Publications](#publications) for preprints.

### One session, one biologic

Start with the **`biologics-delivery-discovery`** OpenCode agent (default). It guides you through:

```
biologic name/PDB → candidates → OpenMM screening → retrosynthesis →
monomer ADMET → excipient compliance → compiled report + audit trail
```

Session state is persisted to `runs/<session_id>/` via `discovery_world.json`, funnel-context checkpoints, and an append-only JSONL audit trail. If the chat disconnects mid-pipeline, `get_funnel_context` resumes from the last checkpoint.

### OpenMM screening is expensive — skip it for quick iterations

**`openmm_evaluate_psmiles`** runs Packmol matrix packing plus energy minimization (and optional short NPT) per candidate. That is the slowest step in the pipeline: on CPU or emulated `linux/amd64` (Docker on Apple Silicon), **one candidate can take minutes to hours**.

For **fast iteration** — literature mining, PSMILES validation, library screening, ADMET, retrosynthesis, excipient compliance — you can **optionally skip OpenMM** and still move the discovery loop forward:

- In OpenCode, tell the agent explicitly: *skip `openmm_evaluate_psmiles` this iteration* and run validate → `screen_candidate_library` → ADMET / retro / compliance only.
- Use **`get_candidate_profile`** or **`screen_candidate_library`** on a small batch without calling OpenMM.
- Re-run OpenMM on a shortlist once candidates look promising.

The default **`biologics-delivery-discovery`** agent includes OpenMM in its fixed Steps 1–6; steering it to omit that step is intentional and supported. See [docs/OPENMM_SCREENING.md](docs/OPENMM_SCREENING.md) when you want full physics-based ranking (and env vars to shorten smoke tests).

### New MCP tools (May 2026)

| Tool | What it does |
|------|-------------|
| `get_candidate_profile` | Single-call dossier: validate + ADMET + retro routes + compliance |
| `screen_candidate_library` | Batch screen up to 50 candidates with ADMET + compliance |
| `check_excipient_compliance` | EMA/FDA/GRAS lookup + immunogenicity SMARTS alerts |
| `save_funnel_context` / `get_funnel_context` | Named pipeline checkpoints for session resumption |
| `save_pipeline_stage` / `get_pipeline_audit` | Per-candidate GxP audit trail (21 CFR Part 11 style) |

---

## Migrating from insulin-ai?

If you previously used `insulin-ai` (old name), see [docs/REBRAND_MIGRATION.md](docs/REBRAND_MIGRATION.md) for the one-time steps (conda env recreate, MCP key rename, env var prefix change).

---

## What you need

- **Conda or Mamba** — to install the simulation stack (OpenMM, RDKit, Packmol, psmiles). [Miniforge](https://github.com/conda-forge/miniforge) or Anaconda works.
- **An IDE with MCP** — Cursor is the main target; others that support MCP will work too.
- **Optional:** API keys for literature search (see [docs/SECURITY.md](docs/SECURITY.md)).

### OpenMM / RDKit are not installed until you use the conda env

**`pip install -r requirements.txt` alone does not install OpenMM, RDKit, pdbfixer, Packmol, or OpenFF Toolkit** (those are conda-forge binaries or not on PyPI). Until you run **`./install`** (or **`./install --conda-yml`** / **`mamba env create -f environment-simulation.yml`**), `python -c "import openmm"` will fail in a plain venv or a mismatched conda env.

Use the env named **`biologix-ai-sim`** (that is what `./install` and `scripts/run_mcp_server.sh` expect):

```bash
./install   # first time (default: chunked conda + pip)
mamba run -n biologix-ai-sim python -c "import openmm; print(openmm.__version__)"
```

If your shell shows a different env (for example `biologix-ai`), either activate `biologix-ai-sim` for screening or align that env with the same packages—see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

---

## Run with Docker (recommended)

Pre-built images are published to **GHCR** — no conda, no `./install`, no local build unless you want one. The image runs on **Windows, macOS, and Linux**; Docker handles the OS difference.

### Prerequisites

1. **Docker installed and running**
   - [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows or Mac (includes Compose).
   - Linux: Docker Engine + [Compose plugin](https://docs.docker.com/compose/install/linux/).
2. **Disk space** — ~10–15 GB for the pulled image.
3. **A terminal** — the container launches the **OpenCode TUI** in your shell (`stdin` + TTY required). Prefer **Terminal.app** or **iTerm** over the Cursor integrated terminal; some IDE terminals do not forward keyboard input to Docker TUIs reliably.
4. **OpenCode familiarity** — you should already know how to authenticate (`opencode auth login`), pick/switch models, and use the agent UI. This README does not teach OpenCode; see [opencode.ai](https://opencode.ai) docs.
5. **Network on first run** — slim images or missing data volumes may build the retrosynthesis precursor database (tiers 1–4). Full GHCR images already include this; Compose persists it in the `biologix-data` volume so later runs start immediately.
6. **Platform flag on ARM Macs** — the image is `linux/amd64` only. On Apple Silicon (and other ARM hosts) you **must** pass `--platform linux/amd64` on `docker pull` and `docker run` (Docker Desktop runs it under emulation).

No Anthropic (or other) API key is required before you start — configure the LLM inside OpenCode after the TUI opens.

### Pull the pre-built image and run

From any directory where you want session folders (or clone this repo and use it as the working directory):

```bash
mkdir -p runs papers

docker pull --platform linux/amd64 ghcr.io/otunmartins/biologix-ai:latest

docker run --platform linux/amd64 -it --rm --init \
  -v "$(pwd)/runs:/app/runs" \
  -v "$(pwd)/papers:/app/papers" \
  -v biologix-data:/app/data \
  ghcr.io/otunmartins/biologix-ai:latest
```

**Windows (PowerShell)** — same flow; use `${PWD}` instead of `$(pwd)`:

```powershell
mkdir runs, papers -Force
docker pull --platform linux/amd64 ghcr.io/otunmartins/biologix-ai:latest
docker run --platform linux/amd64 -it --rm --init `
  -v "${PWD}/runs:/app/runs" `
  -v "${PWD}/papers:/app/papers" `
  -v biologix-data:/app/data `
  ghcr.io/otunmartins/biologix-ai:latest
```

Pin a release tag instead of `latest`, e.g. `ghcr.io/otunmartins/biologix-ai:0.3.0`.

**What happens:** the entrypoint activates the conda env, runs first-run data setup if needed, then starts **OpenCode** with the default **`biologics-delivery-discovery`** agent. Discovery outputs land in `./runs/` on your host.

**LLM access:** sign in or attach a provider from inside OpenCode (`opencode auth login`, or your usual model/provider switch). Optional: pass a key at launch with `-e OPENAI_API_KEY=...` / `-e OPENROUTER_API_KEY=...` / `-e ANTHROPIC_API_KEY=...` if you prefer env-based auth.

### Alternative: build locally with Compose

If you are developing the image or GHCR is unreachable:

```bash
git clone https://github.com/otunmartins/biologix-platform.git
cd biologix-platform
docker compose build    # first build: ~20–40 min
docker compose run --rm biologix
```

On Apple Silicon, Compose already targets `linux/amd64`; you may still see a platform warning — that is expected.

More detail: [docs/DOCKER.md](docs/DOCKER.md) (credentials, slim builds, troubleshooting, maintainer publish notes).

---

## Quick start (without Docker)

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

**`./install`** builds **`biologix-ai-sim`**, installs **OpenMM + Packmol + OpenFF + MCP + retro/admet submodules + AiZynthFinder models** (default), and runs **`scripts/verify_install.sh`** — install fails loudly if anything is missing. Options: **`--skip-aizynth-models`**, **`--skip-submodules`**, **`--conda-yml`**, **`--pip-only`**.

Verify anytime:

```bash
bash scripts/verify_install.sh
```

Repair a partial install:

```bash
rm -f ~/.local/bin/micromamba
./install
```

Or create/update the env manually:

```bash
mamba env create -f environment-simulation.yml
# or low-RAM equivalent:  bash scripts/install_biologix_ai_sim_lowmem.sh
```

The environment is named **`biologix-ai-sim`**. You don't need to activate it to use MCP—the launcher script does that for you.

After install, launch OpenCode in this repo:

```bash
biologix-ai          # or: ./run
```

If the solver fails with **`libgfortran 1.0`** or unsatisfiable **packmol**, remove the legacy **`omnia`** channel (**`bash scripts/fix_conda_channels_for_biologix_ai.sh`**), then re-run **`./install`**. If you use **`./install --conda-yml`** and conda is **`Killed`** during **Solving environment**, use the default **`./install`** (chunked) instead. Details: [Dependencies — Troubleshooting](docs/DEPENDENCIES.md#troubleshooting-conda-solve-failures).

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

The MCP server runs via `scripts/run_mcp_server.sh`, which uses the `biologix-ai-sim` conda env automatically.

---

## Verify the setup

From a terminal:

```bash
mamba run -n biologix-ai-sim python scripts/diagnose_openmm_complex.py '[*]COC[*]'
```

You should see energies printed. If this fails, Packmol or OpenMM may be missing—see [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md).

---

## New: Retrosynthesis & ADMET Pipeline

The `experimental` branch adds:

- **`prepare_retrosynthesis`** / **`submit_retro_extractions`** / **`plan_retrosynthesis`** — Agent-backed polymer retrosynthesis (OpenCode LLM extracts literature; no separate OpenAI key). AiZynthFinder enriches monomer routes. Run `bash scripts/setup_aizynthfinder.sh` once for monomer planning models.
- **`check_monomer_admet`** — SMARTS structural alerts + ADMET-AI predictions (hERG, hepatotoxicity, mutagenicity) on residual monomers.
- **`compile_results`** — Full pipeline: retrosynthesis + ADMET + composite ranking + narrative report.
- **FastAPI** — `POST /retrosynthesis/plan`, `POST /admet/screen`, `POST /pipeline/compile` at `http://localhost:8000/docs`.

### Installation (vendored tools)

**Included in `./install` by default** (submodules, AiZynthFinder package, ADMET-AI, model download). To verify:

```bash
bash scripts/verify_install.sh
```

Manual repair only if `./install` was skipped or interrupted:

```bash
bash scripts/install_submodules.sh
bash scripts/setup_aizynthfinder.sh   # ~800MB models
```

---

## Publications

| Paper | Description | Build |
|-------|-------------|-------|
| [arXiv:2605.18831](https://arxiv.org/abs/2605.18831) | Physics-grounded agentic discovery benchmark (insulin, RL/BO comparison) | `cd paper/insulin && ./compile_main.sh` |
| Biologics AI showcase (preprint) | End-to-end platform demo: any biologic + agent-backed retrosynthesis (insulin + adalimumab, 5 iterations each) | [biologix-paper](https://github.com/muhammadhasyim/biologix-paper) |

Insulin benchmark source: [`paper/insulin/main.tex`](paper/insulin/main.tex). Biologics showcase: standalone repo (Overleaf-ready). Shared bibliography for insulin: [`paper/shared/references.bib`](paper/shared/references.bib). Layout: [`paper/README.md`](paper/README.md).

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
mamba run -n biologix-ai-sim python benchmarks/optuna_psmiles_discovery.py --seed '[*]OCC[*]' --n-trials 5
```

**Matrix screening** (Packmol + OpenMM):

```bash
mamba run -n biologix-ai-sim python scripts/run_openmm_matrix.py '[*]CC[*]'
```
