# Run Biologix AI with Docker

Docker is the recommended way to run Biologix AI. It packages the full conda simulation stack (OpenMM, RDKit, Packmol, AmberTools, OpenFF), all Python dependencies, retrosynthesis submodules, and the OpenCode TUI into a single image. No conda, no `./install`, no path fiddling.

## What you need

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose) — or the Docker Engine + Compose plugin on Linux.
- An LLM provider API key (Anthropic, OpenAI, or OpenRouter). OpenCode needs this to run the agent.

That is it.

## Quick start (two commands)

**macOS / Linux:**
```bash
cp .env.example .env
# Edit .env and fill in ANTHROPIC_API_KEY (or OPENAI_API_KEY / OPENROUTER_API_KEY)
docker compose build          # first build: 20-40 min, ~8-12 GB; subsequent builds are fast
docker compose run --rm biologix
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
# Edit .env and fill in your LLM key
docker compose build
docker compose run --rm biologix
```

**Windows (cmd.exe):**
```cmd
copy .env.example .env
:: Edit .env and fill in your LLM key
docker compose build
docker compose run --rm biologix
```

OpenCode opens immediately in your terminal with the `biologics-delivery-discovery` agent ready. Session outputs are written to `./runs/` on your host.

## Credentials

Your API key is never baked into the image. It is read from `.env` at runtime.

```
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

You can also pass it directly without a `.env` file:

```bash
docker run -it --rm --init -e ANTHROPIC_API_KEY=sk-ant-... biologix-ai:local
```

Or authenticate interactively inside the container:

```bash
docker compose run --rm biologix bash   # drop to shell
opencode auth login                      # follow prompts; auth saved to named volume
```

The `opencode-auth` named volume persists your login across container restarts.

### Optional: Semantic Scholar / ASTA tools

Set `ASTA_API_KEY` in `.env` and change `"enabled": false` to `"enabled": true` for the `asta` MCP in `.opencode/opencode.jsonc` to activate enhanced literature search. The platform works fine without it.

## Persisted data

| Host path | What is stored |
|-----------|---------------|
| `./runs/` | Discovery session outputs (reports, structures, audit trails) |
| `./papers/` | PDFs used by the literature mining tool |
| `opencode-auth` (named volume) | OpenCode auth state |
| `biologix-data` (named volume) | Precursor DB, Molport cache, AiZynth models (survives `--rm`) |

Compose uses project name `biologix-ai` (set in `docker-compose.yml`), so Docker resources are named `biologix-ai_*` — not the clone folder name.

Session artifacts survive container restarts because `./runs/` is mounted into the container.

## Slim vs full image

By default the image bakes in the ~800 MB AiZynthFinder models and the precursor database so everything works on first run.

To build a smaller image that downloads those on first startup:

```bash
SLIM=1 docker compose build   # or: docker build --build-arg SLIM=1 -t biologix-ai:slim .
```

The first `docker compose run` with a slim image will download the models and build the database (needs a network connection; takes a few minutes). Results are stored inside the container layer — add a volume mount for `data/aizynthfinder/` if you want to persist them across image rebuilds.

## Verify the installation

After the container starts, open a second terminal and run:

```bash
docker compose run --rm biologix bash -c \
  "python scripts/diagnose_openmm_complex.py '[*]COC[*]'"
```

You should see interaction energies printed. If this fails, OpenMM or Packmol is missing from the environment — rebuild the image.

## Platforms and architecture

The image is `linux/amd64` and runs identically on Windows, macOS, and Linux — Docker handles the OS difference for you.

| Host OS | How it runs |
|---------|------------|
| Linux (x86) | Native — fastest |
| macOS Intel | Linux VM via Docker Desktop — native speed |
| macOS Apple Silicon (M-series) | Linux VM + Rosetta 2 emulation — works, but OpenMM runs on CPU only (slower) |
| Windows 10/11 | WSL2 Linux VM via Docker Desktop — full functionality |

For best OpenMM performance use a Linux x86 host or a cloud instance (AWS, GCP, Azure).

If you ever need to pass the platform flag explicitly (e.g. running `docker build` outside Compose):

```bash
docker build --platform linux/amd64 -t biologix-ai:local .
docker run --platform linux/amd64 -it --rm --init biologix-ai:local
```

## Debug shell

To drop into a shell instead of launching OpenCode:

```bash
docker compose run --rm -e OPENCODE_DISABLE=1 biologix bash
```

This starts the container, activates `biologix-ai-sim`, and gives you a prompt for manual testing.

## Pull a pre-built image (GHCR)

Images are published to **`ghcr.io/otunmartins/biologix-ai`** on version tags (e.g. `v0.3.0` → tags `0.3.0`, `0.3`, `latest`).

After the first publish, the package owner must set visibility to **Public** once: GitHub → **Packages** → **biologix-ai** → **Package settings** → **Change visibility**.

When a tagged release is published:

```bash
# macOS / Linux
docker pull ghcr.io/otunmartins/biologix-ai:latest
docker run --platform linux/amd64 -it --rm --init \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v "$(pwd)/runs:/app/runs" \
  -v "$(pwd)/papers:/app/papers" \
  -v biologix-data:/app/data \
  ghcr.io/otunmartins/biologix-ai:latest
```

```powershell
# Windows PowerShell
docker pull ghcr.io/otunmartins/biologix-ai:latest
docker run --platform linux/amd64 -it --rm --init `
  -e ANTHROPIC_API_KEY=sk-ant-... `
  -v "${PWD}/runs:/app/runs" `
  -v "${PWD}/papers:/app/papers" `
  -v biologix-data:/app/data `
  ghcr.io/otunmartins/biologix-ai:latest
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| OpenCode asks for a provider / LLM errors inside a session | Run `opencode auth login` inside the container, or set a key in `.env` if you use one — **not required at container start** |
| OpenCode exits immediately | Run with `OPENCODE_DISABLE=1` and check logs; MCP tools work without any cloud API key |
| `opencode: command not found` | Rebuild the image (`docker compose build --no-cache`) |
| AiZynth model download fails | Network issue during first run; retry or set `SLIM=0` and rebuild |
| `Error response from daemon: no space left` | Docker has run out of disk space; run `docker system prune` |
| Slow on Mac (M1/M2/M3) | Expected for OpenMM under emulation; use a Linux host for production |
| `mamba: command not found` inside container | `/opt/conda/bin` should be on PATH; rebuild with `--no-cache` |
| `does not appear to be a Python project: neither 'setup.py' nor 'pyproject.toml' found` during build | The conda-env stage runs before the repo is copied; the Dockerfile strips `-e .` from the env YAML for this reason. Rebuild with the current Dockerfile — the project is installed later by `install_submodules.sh`. |
| `bad interpreter: ...bash^M: no such file or directory` on startup | Windows CRLF in shell scripts. The bundled `.gitattributes` prevents this on fresh clones. If already affected: `git config core.autocrlf false` then re-checkout, or rebuild the image — the Dockerfile strips CRs automatically. |
| OpenCode TUI frozen — cannot type, Esc/Ctrl+C useless | OpenCode OpenTUI can stop reading keyboard while still drawing (especially `linux/amd64` on Apple Silicon). The session may be **waiting at a prompt**, not running a tool. Recovery: second shell → `docker ps` → `docker kill <container_id>` → in the original tab run **`reset`**. Images **≥ 0.5.7** also run `docker/restore_terminal.sh` on exit via the entrypoint trap. Prefer **Terminal.app** / **iTerm** over IDE-embedded terminals. |
| Terminal prints garbage like `35;95;8M` after OpenCode exits | OpenTUI left **mouse-tracking** enabled. Run **`reset`**, or `bash /app/docker/restore_terminal.sh` on the host if you copied the script. Entrypoint **≥ 0.5.7** disables mouse modes on normal exit. |
| Stuck at "Run OpenMM on top ≤3 candidates?" | Mid-pipeline Yes/No prompts are removed in Docker **≥ 0.5.7**. Set **`BIOLOGIX_AI_OPENMM_AUTO=yes`** (default) to auto-run, or **`skip`** to skip without typing. |
| Frozen during Step 3 (`generate_psmiles_from_name` × many) | OpenCode batched **parallel** MCP calls → **stdio pipe deadlock** (not slow chemistry). OpenCode log shows `CallToolRequest` with no completion; MCP server idle. Recovery: `docker kill` + `reset`. Agent **≥ 0.5.8** enforces **sequential** MCP calls. Until then: restart the session — literature is saved under `runs/<session>/`. |
| Precursor DB rebuilds for 30–90+ minutes on every `docker run --rm` | Fixed in images built after the entrypoint path correction (`data/retrosynthesis/precursors.json`). Mount **`biologix-data:/app/data`** (Compose does this) so Molport/AiZynth data persists across `--rm`. |
| `monomer.png` files are SVG / PIL cannot open them | Headless `psmiles` may write SVG to `.png` paths. Rebuild the image after the `psmiles_drawing` fix, or upgrade to a tagged release that includes it. |
| `setup_aizynthfinder.sh` failed during `docker build` / GitHub Actions | Usually a transient Zenodo/Figshare download error. Re-run the workflow (re-push the tag or use **Actions → Build and publish Docker image → Run workflow** with push enabled). Builds after the curl-based downloader fix are more resilient. |
| OpenCode looks stuck on a tool (OpenMM, PDF, PNG) | The tool may still be running. In a second shell: `docker ps`, `docker logs --tail 50 <container>`, or `docker exec <container> tail -f /app/runs/<session>/tool_events.jsonl`. Expensive tools log **`started` / `completed` / `failed`** there and in MCP stderr. **≥ 0.5.16** fixes ProcessPoolExecutor shutdown hangs after OpenMM timeouts, adds MCP tool wall-clock caps, and sets `OPENMM_CPU_THREADS=1` for Rosetta. For MCP setup/catalog hangs, use **≥ 0.5.15** (OpenCode **≥ 1.17.4** pin, per-server `cwd`/`timeout`, direct conda python launcher). Run with **`-e OPENCODE_LOG_LEVEL=DEBUG`** and inspect `/root/.local/share/opencode/log/` (persist with `-v opencode-auth:/root/.local/share/opencode`). |
| `save_pipeline_stage` red icon / timeout | Before latch: usually **parallel MCP calls** blocked stdio. After **any MCP timeout**, the session **latches to CLI-only** — use the `save_pipeline_stage` CLI one-liner in `.opencode/MCP_CLI_FALLBACK.md`; do not call MCP again. |
| PDF compile failed on Markdown tables | Upgrade to an image with the `plain_tables_fallback` PDF path, or check `tool_errors.log` in the session folder. `SUMMARY_REPORT.md` is still valid even if PDF fails. |

## Docker safety defaults

The entrypoint sets interactive defaults (override with `docker run -e …`):

| Variable | Docker default | Purpose |
|----------|----------------|---------|
| `BIOLOGIX_AI_OPENMM_AUTO` | `yes` | `yes` = run OpenMM on ≤3 pass candidates without a mid-pipeline prompt; `skip` = skip OpenMM |
| `BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S` | `540` | Per-candidate OpenMM wall-clock limit (below MCP transport) |
| `BIOLOGIX_AI_MCP_TIMEOUT_MS` | `600000` | OpenCode MCP transport budget (ms); aligned with `.opencode/opencode.jsonc` |
| `BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S` | `30` | In-process cap for session/audit/catalog MCP tools |
| `BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS` | `1500` | Shorter minimization for faster turns |
| `BIOLOGIX_AI_EVAL_MAX_WORKERS` | **`1`** (MCP-safe) | Parallel OpenMM **candidates**; use `-e BIOLOGIX_AI_EVAL_MAX_WORKERS=N` for batch HPC (e.g. `-e BIOLOGIX_AI_EVAL_MAX_WORKERS=4`) |
| `OMP_NUM_THREADS` | **`nproc`** when `workers=1` | OpenMM threads per worker |
| `BIOLOGIX_AI_EVAL_CPU_FRACTION` | `1.0` | Fraction of container CPUs for workers (e.g. `0.75` for headroom) |
| `DOCKER_CPU_PCT` | `75` (host scripts only) | Optional `--cpus` quota via `scripts/docker_run.sh` |

### CPU auto-detection (GHCR — no wrapper required)

The **image entrypoint** runs `nproc` on every start and configures OpenMM with **`BIOLOGIX_AI_EVAL_MAX_WORKERS=1`** (MCP-safe) while **`OMP_NUM_THREADS`** uses all container CPUs for math inside each candidate:

```bash
docker run --platform linux/amd64 -it --rm --init \
  -v "$(pwd)/runs:/app/runs" \
  -v biologix-data:/app/data \
  ghcr.io/otunmartins/biologix-ai:latest
```

The startup banner prints `CPUs visible`, `OpenMM workers`, `OMP`, and timeout budgets.

### OpenCode CLI pin (recommended)

The image records the installed OpenCode version in **`/app/.opencode-version`**. Default build pin is **`1.17.4`** (MCP abort signals, clean setup failures, local `cwd`/`timeout`).

```bash
docker build --build-arg OPENCODE_VERSION=1.17.4 -t biologix-ai:local .
```

Minimum tested: **`OPENCODE_MIN_VERSION=1.17.4`** (see `scripts/verify_opencode_mcp_host.sh`).

**Debug logs:** set `-e OPENCODE_LOG_LEVEL=DEBUG` and persist OpenCode state (compose already mounts `opencode-auth` → `/root/.local/share/opencode`; logs live under `log/` there):

```bash
docker run ... -e OPENCODE_LOG_LEVEL=DEBUG -v biologix-opencode:/root/.local/share/opencode ...
```

**Container CPU count** depends on Docker, not the image:

- Plain `docker run` → all CPUs allocated to **Docker Desktop → Resources → CPUs**.
- `docker run --cpus 6` → capped at 6.
- `./scripts/docker_run.sh` → optional `--cpus` at 75% of **host** CPUs (macOS headroom).

OpenMM runs up to **3 candidates in parallel** (agent limit). With 8 container CPUs: `workers=8`, `OMP=2` → ~6 threads active. Retrosynthesis remains mostly single-threaded.

Overrides:

```bash
-e BIOLOGIX_AI_EVAL_MAX_WORKERS=1     # sequential candidates, all OMP threads each
-e BIOLOGIX_AI_EVAL_CPU_FRACTION=0.5 # half the container for OpenMM workers
-e OMP_NUM_THREADS=4                 # fixed per-worker thread cap
```

Optional host wrapper (75% `--cpus`):

```bash
./scripts/docker_run.sh
./scripts/docker_compose_run.sh
```

## Smoke test (before publish or after local build)

```bash
docker compose build
docker run --rm --entrypoint bash biologix-ai:local /app/scripts/docker_smoke_test.sh
```

CI runs the same script (no API keys; 15-minute job timeout) before pushing to GHCR.
