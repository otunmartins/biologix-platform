# Third-party benchmark systems (non–Bayesian optimization)

These benchmarks are **independent of the MCP server** ([`insulin_ai_mcp_server.py`](../insulin_ai_mcp_server.py)) and of OpenCode tools. They live under [`extern/benchmarks/`](../extern/benchmarks/) (clones are gitignored); thin entry scripts are in [`benchmarks/`](../benchmarks/). Same separation as the in-repo Optuna PSMILES benchmark ([`benchmarks/optuna_psmiles_discovery.py`](../benchmarks/optuna_psmiles_discovery.py)).

## 1. Polymer Generative Models Benchmark (Wisconsin)

| | |
|--|--|
| **Paper** | [Benchmarking study of deep generative models for inverse polymer design](https://doi.org/10.1039/D4DD00395K) — *Digital Discovery* |
| **Code** | [ytl0410/Polymer-Generative-Models-Benchmark](https://github.com/ytl0410/Polymer-Generative-Models-Benchmark) |
| **Method** | Generative models (VAE, AAE, ORGAN, CharRNN, REINVENT, GraphINVENT); RL fine-tuning in later experiments — **not** Bayesian optimization over a surrogate |
| **Weights / data** | Zenodo links in upstream README (MOSES folder, GraphINVENT, RL checkpoints) |

**Clone:** [`extern/benchmarks/polymer-generative-models/README.md`](../extern/benchmarks/polymer-generative-models/README.md) or `bash scripts/clone_external_benchmarks.sh`

**Wrapper:** `python benchmarks/polymer_generative_models_benchmark.py` — verifies clone and MOSES layout.

**Objective:** Metrics are **as defined in the paper**, not insulin-ai's `discovery_score` (PSMILES + OpenMM) unless you add a custom adapter later.

## 2. IBM logical-agent-driven polymer discovery (upstream smoke check)

| | |
|--|--|
| **Paper** | [Reinforcement Learning with Logical Action-Aware Features for Polymer Discovery](https://research.ibm.com/publications/reinforcement-learning-with-logical-action-aware-features-for-polymer-discovery) — RL4RealLife @ ICML 2021 |
| **Code** | [IBM/logical-agent-driven-polymer-discovery](https://github.com/IBM/logical-agent-driven-polymer-discovery) |
| **Method** | Neuro-symbolic RL (logical action-aware features; DQN-style training in upstream) — **not** Bayesian optimization |

**Clone:** [`extern/benchmarks/ibm-logical-agent-polymer/README.md`](../extern/benchmarks/ibm-logical-agent-polymer/README.md)

**Setup:** `pip install -e md-envs`, unzip `data/polymerDiscovery.zip`, `python scripts/update_pickled_function.py` (see upstream README).

**Smoke wrapper:** `python benchmarks/ibm_polymer_rl_benchmark.py` — runs `python scripts/main.py test -h` as a CLI check when the clone is present.

**License:** Follow upstream `LICENSE` for redistribution.

## 3. IBM RL adapted to insulin-ai evaluation (insulin-ai adapter)

This is the primary benchmarking target: the IBM neuro-symbolic RL **optimization loop** (which PSMILES to try next) is retained intact, but the **evaluation** and **scoring** are replaced with insulin-ai's OpenMM pipeline — identical to the agentic MCP `openmm_evaluate_psmiles` tool.

| Component | Source |
|-----------|--------|
| Optimization loop (which polymer next) | IBM DQN / PPO with logical action-aware features |
| PSMILES proposal | `insulin_ai.mutation.feedback_guided_mutation` |
| Evaluation | `MDSimulator.evaluate_candidates` (OpenMM Packmol matrix) |
| Scoring | `scoring.composite_screening_score` + `scoring.discovery_score` |
| Feedback | `PropertyExtractor.extract_feedback` |

All scoring functions are **identical** to the agentic MCP loop and the Optuna benchmark, enabling direct comparison.

### New files

| File | Purpose |
|------|---------|
| [`benchmarks/ibm_insulin_env.py`](../benchmarks/ibm_insulin_env.py) | `InsulinPSMILESEnv` (base Gym env) and `LogicalInsulinPSMILESEnv` (logical-feature wrapper) |
| [`benchmarks/ibm_insulin_rl_benchmark.py`](../benchmarks/ibm_insulin_rl_benchmark.py) | Train/test entry script (mirrors `optuna_psmiles_discovery.py`); outputs JSON + TSV |
| [`tests/test_ibm_insulin_env.py`](../tests/test_ibm_insulin_env.py) | Unit tests (skip when Gym not installed) |

There is **no offline precompute** step: each new PSMILES goes through **live OpenMM** (`MDSimulator.evaluate_candidates`), matching the agentic discovery loop. The env may reuse in-memory results for a canonical PSMILES already evaluated in the same process (similar to skipping duplicate work), but there is no batch job before training.

### Quick start

**Step 1 — install RL dependencies**

```bash
pip install stable-baselines3 GPy gymnasium
```

**Step 2 — train and test (live OpenMM + Packmol)**

CLI defaults match **20 agentic iterations × 10 evals**: **`--n-timesteps 200`**, **`--max-steps 10`**, **`--n-proposals 10`**, **`--n-episodes 20`**. Override flags if you need a different budget.

```bash
python benchmarks/ibm_insulin_rl_benchmark.py \
    --mode train_and_test --algorithm dqn \
    --model-path models/ibm_dqn_insulin.zip \
    --output results/ibm_dqn.json \
    --comparison-tsv benchmarks/comparison_results.tsv
```

SB3 pipeline tests in `tests/test_ibm_insulin_env.py` inject a stub evaluator so CI does not require OpenMM.

### Reward structure

IBM's 4-tier reward is mapped from insulin-ai's interaction energy:

| Tier | IBM reward | insulin-ai condition |
|------|-----------|----------------------|
| `target` | +1.0 | `interaction_energy_kj_mol < -5.0 kJ/mol` |
| `valid` | -0.01 | evaluated, energy above threshold |
| `revisit` | -0.5 | PSMILES already tried in this episode |
| `no-go` | -1.0 | fails `validate_psmiles` or `prescreen_psmiles_for_md` |

### Comparison harness

All benchmark methods write a shared TSV (`benchmarks/comparison_results.tsv`) with fixed columns:

```
method | n_evaluations | best_discovery_score | best_interaction_energy_kj_mol
n_high_performers_found | n_unique_psmiles_evaluated | wall_time_s | ...
```

This enables fixed-budget comparison (e.g. **20 iterations × 8 evals = 160** successful OpenMM evaluations in the paper study script) across:
- `agentic` (LLM + MCP tools)
- `optuna_tpe` (`benchmarks/optuna_psmiles_discovery.py`; `--output` / `--comparison-tsv`)
- `random_psmiles` (`benchmarks/random_psmiles_baseline.py` — memoryless mutation baseline)
- `ibm_rl_dqn` and `ibm_rl_ppo` (this adapter)

For a **fresh paper study** (separate from ad hoc rows in `comparison_results.tsv`), use **`benchmarks/comparison_results_study.tsv`** and run **`benchmarks/run_paper_study.sh`** (see [`BENCHMARK_AND_REPRO_STUDY.md`](BENCHMARK_AND_REPRO_STUDY.md)).

## Dependencies

Heavy stacks (**PyTorch**, **stable-baselines3**, **GPy**, **gymnasium**) are not pinned in insulin-ai's base [`pyproject.toml`](../pyproject.toml). Install inside a dedicated venv or conda env if you run full training. See also [`docs/DEPENDENCIES.md`](DEPENDENCIES.md).

## Optional: GLAS (genetic algorithm)

[GLAS](https://github.com/drcassar/glas) ([arXiv:2008.09187](https://arxiv.org/abs/2008.09187)) — GA + ML for optical glasses; same `extern/benchmarks/` pattern if added later.
