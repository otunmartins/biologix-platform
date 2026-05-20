# Biologix-AI rebrand migration

The platform was renamed from **insulin-ai** to **biologix-ai** to reflect its biologic-agnostic scope (insulin, mAbs, enzymes, vaccines, peptides). This page documents the one-time migration steps.

## What changed

| Layer | Before | After |
|-------|--------|-------|
| CLI launcher | `insulin-ai` | `biologix-ai` |
| pip package | `insulin-ai` | `biologix-ai` |
| Python imports | `from insulin_ai.…` | `from biologix_ai.…` |
| MCP server script | `insulin_ai_mcp_server.py` | `biologix_ai_mcp_server.py` |
| MCP config key | `"insulin-ai"` | `"biologix-ai"` |
| Conda env | `insulin-ai-sim` | `biologix-ai-sim` |
| Env vars | `INSULIN_AI_*` | `BIOLOGIX_AI_*` |

## What did NOT change

- **Repo folder name** — the Git clone can still live at `~/insulin-ai` (or any path).
- **Biologic-domain code** — files like `openmm_insulin.py`, `ibm_insulin_env.py`, and PDB artifacts describe the insulin **target protein**, not the platform.
- **Session artifacts** — existing `runs/*/` folders do not need migration.

## Steps

### 1. Recreate the conda environment

```bash
mamba env remove -n insulin-ai-sim    # remove the old env
./install                              # creates biologix-ai-sim
```

Or manually:

```bash
mamba env create -f environment-simulation.yml   # name: biologix-ai-sim
```

### 2. Update MCP config

**OpenCode** (`.opencode/opencode.jsonc`): already shipped with the new key `"biologix-ai"`.

**Cursor** (`.cursor/mcp.json`): rename the server key from `"insulin-ai"` to `"biologix-ai"`. Example:

```json
{
  "mcpServers": {
    "biologix-ai": {
      "command": "bash",
      "args": ["/path/to/repo/scripts/run_mcp_server.sh"],
      "env": { ... }
    }
  }
}
```

### 3. Update environment variables

If you set any `INSULIN_AI_*` variables in shell profiles, CI, or `.env` files, rename them:

| Old | New |
|-----|-----|
| `INSULIN_AI_SESSION_DIR` | `BIOLOGIX_AI_SESSION_DIR` |
| `INSULIN_AI_ROOT` | `BIOLOGIX_AI_ROOT` |
| `INSULIN_AI_TARGET_PROTEIN_PDB` | `BIOLOGIX_AI_TARGET_PROTEIN_PDB` |
| `INSULIN_AI_AIZYNTH_CONFIG` | `BIOLOGIX_AI_AIZYNTH_CONFIG` |
| `INSULIN_AI_OPENMM_*` | `BIOLOGIX_AI_OPENMM_*` |
| `INSULIN_AI_EVAL_*` | `BIOLOGIX_AI_EVAL_*` |
| `INSULIN_AI_CORS_ORIGINS` | `BIOLOGIX_AI_CORS_ORIGINS` |
| `INSULIN_AI_DEMO` | `BIOLOGIX_AI_DEMO` |

The pattern is mechanical: replace prefix `INSULIN_AI_` with `BIOLOGIX_AI_`.

### 4. Re-install the package

```bash
pip install -e .
# or from the conda env:
mamba run -n biologix-ai-sim pip install -e .
```

### 5. Verify

```bash
biologix-ai                    # should launch OpenCode
bash scripts/verify_install.sh   # all checks must pass
conda run -n biologix-ai-sim python -c "import biologix_ai; import openmm"
bash scripts/run_mcp_server.sh # MCP server should start
```

## Repair broken or partial install

If OpenMM, Packmol, or MCP tools are missing after `./install` (common cause: broken
`~/.local/bin/micromamba` from a wrong-platform download):

```bash
rm -f ~/.local/bin/micromamba
./install
bash scripts/verify_install.sh
```

Do not switch MCP back to the old `insulin-ai-sim` env — finish `biologix-ai-sim` instead.
