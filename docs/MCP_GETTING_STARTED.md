# Getting started with the insulin-ai MCP server

This guide walks you through installing insulin-ai and connecting its MCP (Model Context Protocol) server to an AI-assisted editor like Cursor. The MCP server lets AI assistants search literature, validate polymer structures (PSMILES), and run physics-based screening—all from inside your IDE.

**New to MCP?** The [Model Context Protocol](https://modelcontextprotocol.io/) lets AI tools call external services. In insulin-ai, those services are things like “evaluate this polymer against insulin” or “search papers for materials that stabilize proteins.”

---

## Before you start

| Requirement | What to have |
|-------------|--------------|
| **Operating system** | macOS, Linux, or **Windows via WSL2** (see below). Native Windows is not supported. |
| **Conda or Mamba** | For the simulation stack (OpenMM, RDKit, Packmol). [Miniforge](https://github.com/conda-forge/miniforge) or [Anaconda](https://docs.anaconda.com/anaconda/install/) works. |
| **IDE with MCP** | Cursor, or another editor that speaks MCP. |
| **Disk space** | A few GB for the conda environment and papers cache. |

---

## Windows users: use WSL2

The insulin-ai screening stack (OpenMM, Packmol, bash scripts) does **not** run natively on Windows. You need **WSL2** (Windows Subsystem for Linux).

1. Install WSL2 and Ubuntu from the [Microsoft Store](https://apps.microsoft.com/store/detail/ubuntu/9PDXGNCFSCZV) or with `wsl --install` in PowerShell.
2. Open a **Ubuntu** (or other Linux) terminal—this is your Linux environment.
3. Clone the insulin-ai repo **inside** your Linux home directory, e.g. `~/insulin-ai`, not `C:\Users\...\insulin-ai`.

From here, follow the same steps as Linux users. Paths in MCP config will look like `/home/your-username/insulin-ai/...`.

---

## Step 1: Clone and install

From a terminal in the repo root:

```bash
# Creates conda env insulin-ai-sim (chunked conda-forge + pip — safer on low RAM)
./install
```

If `./install` fails because you don’t have mamba or conda, use pip instead:

```bash
./install --pip-only
```

For a **single** solve from `environment-simulation.yml` (needs more RAM than the default):

```bash
./install --conda-yml
```

The preferred path uses a conda env named **`insulin-ai-sim`**. You can create it manually with **`mamba env create -f environment-simulation.yml`** or **`bash scripts/install_insulin_ai_sim_lowmem.sh`** (same as default `./install`).

---

## Step 2: Verify the environment

Check that the simulation stack is usable:

```bash
mamba run -n insulin-ai-sim python scripts/diagnose_openmm_complex.py '[*]COC[*]'
```

You should see a short run with energies printed. If this fails, Packmol or OpenMM may be missing—see [Dependencies](DEPENDENCIES.md) and [OpenMM screening](OPENMM_SCREENING.md).

---

## Step 3: Configure MCP in Cursor

1. Copy the example MCP config into your project:

   ```bash
   cp .cursor/mcp.json.example .cursor/mcp.json
   ```

2. Edit `.cursor/mcp.json` and replace **every** `/ABSOLUTE/PATH/TO/insulin-ai` with the real path to your repo. Examples:

   - macOS/Linux: `/Users/jane/insulin-ai` or `/home/jane/insulin-ai`
   - Windows + WSL: `/home/jane/insulin-ai` (use the Linux path inside WSL, not `C:\...`)

3. The insulin-ai block should look like this (paths updated):

   ```json
   "insulin-ai": {
     "command": "bash",
     "args": ["/home/jane/insulin-ai/scripts/run_mcp_server.sh"],
     "env": {
       "PAPER_DIRECTORY": "/home/jane/insulin-ai/papers",
       "PYTHONPATH": "/home/jane/insulin-ai/src/python"
     }
   }
   ```

4. Restart Cursor (or reload the window) so it picks up the new MCP config.

---

## Step 4: Confirm the server is running

In Cursor, open a chat and ask the AI to “list available insulin-ai tools” or “validate the PSMILES `[*]OCC[*]`.” If the MCP server is connected, it should call `validate_psmiles` and return a result.

Discovery runs write outputs to `runs/<session_id>/`. Each session gets its own folder with `agent_iteration_*.json`, `SUMMARY_REPORT.md`, and—when screening runs—`structures/` (PNGs, minimized PDBs). See [OpenMM screening](OPENMM_SCREENING.md) for details.

---

## What works with vs. without OpenMM

| Capability | Requires |
|------------|----------|
| `validate_psmiles`, `generate_psmiles_from_name`, `render_psmiles_png` | RDKit, psmiles (from conda env). No OpenMM. |
| `openmm_evaluate_psmiles`, `run_autonomous_discovery` | OpenMM, Packmol, insulin PDB (`data/4F1C.pdb` or `ensure_insulin_pdb`). |
| `mine_literature` (semantic search) | Optional: Asta API key (`ASTA_API_KEY`) for richer search; else Semantic Scholar (no key). |

If OpenMM or Packmol is missing, literature search and validation still work; screening tools will fail with a clear error. See [DEPENDENCIES.md](DEPENDENCIES.md) and [OPENMM_SCREENING.md](OPENMM_SCREENING.md).

---

## API keys (optional)

For literature mining and web cross-checks:

- **Asta** (Allen AI): set `ASTA_API_KEY` in your environment or Cursor config.
- **Brave Search**: `BRAVE_API_KEY` if you use the Brave search MCP server.
- **DuckDuckGo**: no key, but `duckduckgo-search` must be installed for `validate_psmiles(crosscheck_web=true)`.

**Never commit real keys.** Use `.cursor/mcp.json.example` as a template and store secrets in environment variables. See [SECURITY.md](SECURITY.md) for details.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| **“Command not found: mamba”** | Install [Miniforge](https://github.com/conda-forge/miniforge) or use `conda` instead of `mamba` in the commands above. |
| **“No such file: run_mcp_server.sh”** | Open Cursor from the insulin-ai repo root (or set `command` to the full absolute path to `run_mcp_server.sh`). |
| **Paths still point to /ABSOLUTE/PATH/** | Replace every `/ABSOLUTE/PATH/TO/insulin-ai` in `.cursor/mcp.json` with your real path. On Windows, use the WSL path (e.g. `/home/user/insulin-ai`). |
| **Packmol not found** | Install via `conda install -c conda-forge packmol` in the `insulin-ai-sim` env. Packmol must be on `PATH` when the MCP server runs. |
| **Wrong Python / wrong env** | The launcher uses `mamba run -n insulin-ai-sim` (or `conda run`). Ensure `insulin-ai-sim` exists: `mamba env list`. |
| **MCP server doesn’t show up in Cursor** | Reload the window (Cmd/Ctrl+Shift+P → “Reload Window”). Confirm `.cursor/mcp.json` exists and has valid JSON. |

---

## Next steps

- [MCP tool reference](MCP_SERVERS.md) — what each tool does and what it needs.
- [PSMILES primer](PSMILES_GUIDE.md) — polymer notation and how to write valid PSMILES.
- [OpenMM screening](OPENMM_SCREENING.md) — matrix encapsulation, env vars, artifacts.
- [Dependencies](DEPENDENCIES.md) — full list of scientific and reporting dependencies.
- [Security](SECURITY.md) — API keys and safe configuration.
