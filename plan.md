# Retrosynthesis Hang Fix Plan

## Problem

OpenCode hangs at the retrosynthesis step in Docker container deployments. The root cause is an inconsistency between where retrosynthesis timeout environment variables are set:

| Variable | Python default (`retrosynthesis_service.py`) | `entrypoint.sh` fallback | `docker-compose.yml` |
|---|---|---|---|
| `BIOLOGIX_TREE_TIMEOUT` | 120 | 90 | **NOT SET** |
| `BIOLOGIX_PDF_TIMEOUT` | 60 | 30 | **NOT SET** |
| `BIOLOGIX_AIZYNTH_TIMEOUT` | 180 | **NOT SET** | 180 |

The Python defaults (lines 46–48 of `retrosynthesis_service.py`) are:
```python
_PDF_DOWNLOAD_TIMEOUT = int(os.environ.get("BIOLOGIX_PDF_TIMEOUT", "60"))
_TREE_CONSTRUCT_TIMEOUT = int(os.environ.get("BIOLOGIX_TREE_TIMEOUT", "120"))
_AIZYNTH_TIMEOUT = int(os.environ.get("BIOLOGIX_AIZYNTH_TIMEOUT", "180"))
```

## Analysis

- **`entrypoint.sh` (lines 40–41)** exports `BIOLOGIX_PDF_TIMEOUT=30` and `BIOLOGIX_TREE_TIMEOUT=90` — so timeouts *do* get set when running via the entrypoint.
- **`docker-compose.yml`** only declares `BIOLOGIX_AIZYNTH_TIMEOUT`. The other two are invisible to anyone deploying via compose, not overrideable via `.env`, and inconsistent with the entrypoint's values.
- **`entrypoint.sh`** does NOT set `BIOLOGIX_AIZYNTH_TIMEOUT` — so `docker run` without compose falls back to the Python default of 180s, but tree/PDF timeouts use the entrypoint's tighter 90/30 values.

The hang described (retrosynthesis process appearing stuck) is most likely a **very deep graph expansion** in `tree.construct_tree()` that simply needs the timeout to fire and kill the subprocess. The `_run_tree_with_timeout` function already has proper `os.killpg(SIGTERM)` → `proc.kill()` logic, so the timeout *should* work if the timeout value is honored.

## Fix

### 1. `docker-compose.yml` — add missing timeout vars

Add two missing environment variables to the `biologix` service:

```yaml
- BIOLOGIX_TREE_TIMEOUT=${BIOLOGIX_TREE_TIMEOUT:-120}
- BIOLOGIX_PDF_TIMEOUT=${BIOLOGIX_PDF_TIMEOUT:-60}
```

**Effect**: Compose now declares all three timeouts, defaults match Python code (120/60/180), and all three are overridable via `.env` or `-e`.

### 2. `entrypoint.sh` — add AIZYNTH timeout for parity

Add to the entrypoint's timeouts block (around lines 40–41):

```bash
export BIOLOGIX_AIZYNTH_TIMEOUT="${BIOLOGIX_AIZYNTH_TIMEOUT:-180}"
```

**Effect**: `docker run` without compose also has a consistent AiZynth timeout, matching the Python default.

### 3. Verification

After the changes:

```bash
# Check all three are set inside the container
docker compose run --rm biologix bash -lc \
  'echo TREE=$BIOLOGIX_TREE_TIMEOUT PDF=$BIOLOGIX_PDF_TIMEOUT AIZ=$BIOLOGIX_AIZYNTH_TIMEOUT'
```

Expected output:
```
TREE=120 PDF=60 AIZ=180
```

## Hardening (if hang persists)

If the timeout fires (`_run_tree_with_timeout` logs "killed after Xs") but the subprocess still hangs, the issue is in the process group kill path. Potential fixes:

1. Use `concurrent.futures.ProcessPoolExecutor` with `wait(..., timeout=X)` for more reliable subprocess termination.
2. Add a Python `signal.alarm()` inside `_tree_worker` to catch even C-extension hangs (SIGALARM interrupts syscalls).
3. Log `tree.construct_tree()` progress to stdout periodically so we can see where it's stuck.

## Files Changed

- `docker-compose.yml` — add `BIOLOGIX_TREE_TIMEOUT` and `BIOLOGIX_PDF_TIMEOUT`
- `docker/entrypoint.sh` — add `BIOLOGIX_AIZYNTH_TIMEOUT`