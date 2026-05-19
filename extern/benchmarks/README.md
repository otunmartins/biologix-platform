# External benchmark systems (non–Bayesian optimization)

Third-party **cloneable** repositories live **only** under this tree. They are **not** part of `src/python/insulin_ai/` or [`insulin_ai_mcp_server.py`](../../insulin_ai_mcp_server.py). Same independence principle as [`benchmarks/optuna_psmiles_discovery.py`](../../benchmarks/optuna_psmiles_discovery.py) (MCP-free).

| Directory | Upstream | Role |
|-----------|----------|------|
| [`polymer-generative-models/`](polymer-generative-models/README.md) | [ytl0410/Polymer-Generative-Models-Benchmark](https://github.com/ytl0410/Polymer-Generative-Models-Benchmark) | Generative + RL fine-tuning benchmark (*Digital Discovery* [10.1039/D4DD00395K](https://doi.org/10.1039/D4DD00395K)) |
| [`ibm-logical-agent-polymer/`](ibm-logical-agent-polymer/README.md) | [IBM/logical-agent-driven-polymer-discovery](https://github.com/IBM/logical-agent-driven-polymer-discovery) | Neuro-symbolic RL polymer discovery (ICML 2021 RL4RealLife) |

## Clone both (from repo root)

```bash
bash scripts/clone_external_benchmarks.sh
```

Or clone manually (see each subdirectory `README.md`). Cloned directories are **gitignored** so large weights and forks do not bloat this repo.

## Pin versions

After cloning, record commits for reproducibility:

```bash
git -C extern/benchmarks/polymer-generative-models/Polymer-Generative-Models-Benchmark rev-parse HEAD
git -C extern/benchmarks/ibm-logical-agent-polymer/logical-agent-driven-polymer-discovery rev-parse HEAD
```

Append hashes to [`PINNED_VERSIONS.md`](PINNED_VERSIONS.md).

## Thin wrappers

Entry scripts in [`benchmarks/`](../../benchmarks/) invoke checks or upstream CLIs. See [`docs/THIRD_PARTY_BENCHMARKS.md`](../../docs/THIRD_PARTY_BENCHMARKS.md).
