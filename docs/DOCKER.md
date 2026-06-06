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
| `No LLM provider key found` banner on start | Fill in `ANTHROPIC_API_KEY` (or another key) in `.env` |
| OpenCode exits immediately | Run with `OPENCODE_DISABLE=1` and check the error; likely a missing key |
| `opencode: command not found` | Rebuild the image (`docker compose build --no-cache`) |
| AiZynth model download fails | Network issue during first run; retry or set `SLIM=0` and rebuild |
| `Error response from daemon: no space left` | Docker has run out of disk space; run `docker system prune` |
| Slow on Mac (M1/M2/M3) | Expected for OpenMM under emulation; use a Linux host for production |
| `mamba: command not found` inside container | `/opt/conda/bin` should be on PATH; rebuild with `--no-cache` |
| `does not appear to be a Python project: neither 'setup.py' nor 'pyproject.toml' found` during build | The conda-env stage runs before the repo is copied; the Dockerfile strips `-e .` from the env YAML for this reason. Rebuild with the current Dockerfile — the project is installed later by `install_submodules.sh`. |
| `bad interpreter: ...bash^M: no such file or directory` on startup | Windows CRLF in shell scripts. The bundled `.gitattributes` prevents this on fresh clones. If already affected: `git config core.autocrlf false` then re-checkout, or rebuild the image — the Dockerfile strips CRs automatically. |
| OpenCode TUI frozen — cannot type or Ctrl+C (common in **Cursor** integrated terminal) | The session may still be running in the background. Open **Terminal.app** or **iTerm** for `docker compose run` / `docker run -it`. Add **`--init`** (Compose sets `init: true`). To stop a stuck container: second shell → `docker ps` → `docker kill <container_id>`. |
| Precursor DB rebuilds for 30–90+ minutes on every `docker run --rm` | Fixed in images built after the entrypoint path correction (`data/retrosynthesis/precursors.json`). Mount **`biologix-data:/app/data`** (Compose does this) so Molport/AiZynth data persists across `--rm`. |
| `monomer.png` files are SVG / PIL cannot open them | Headless `psmiles` may write SVG to `.png` paths. Rebuild the image after the `psmiles_drawing` fix, or upgrade to a tagged release that includes it. |
