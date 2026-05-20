# Benchmark definition, baselines, ablations, and reproducibility

This document captures how to strengthen a peer-reviewed paper and the biologix-ai **platform** for: benchmark definition, baselines, ablations, and reproducibility. It is intended for an AI agent (or human) orchestrating a systematic benchmarking study later.

Related in-repo docs:

- [`THIRD_PARTY_BENCHMARKS.md`](THIRD_PARTY_BENCHMARKS.md) — IBM insulin RL adapter, Optuna, `comparison_results.tsv` schema
- [`SUMMARY_REPORT_STYLE.md`](SUMMARY_REPORT_STYLE.md) — paper-like reporting for discovery sessions
- [`PAPER_AGENTIC_EXPERIMENTS.md`](PAPER_AGENTIC_EXPERIMENTS.md) — agentic replicates and ablations for the paper (Studies D–F)

---

## 1. Benchmark definition

### Paper

- **Task:** One paragraph that fixes the *decision problem*: search over PSMILES (or repeat units) to minimize **interaction energy** (or maximize `discovery_score`) under **one** OpenMM matrix protocol.
- **Environment contract:** Box size, packing mode, minimizer steps, NPT on/off, insulin structure file, prescreen rules, and **what counts as one “evaluation”** (one `evaluate_candidates` call per PSMILES vs batch).
- **Budget:** Primary results at a **fixed evaluation budget** aligned across methods (e.g. **20 discovery iterations × 8 evals/iteration = 160** successful OpenMM evaluations for IBM RL / Optuna / random baseline in `run_paper_study.sh`), not only mismatched iteration counts on plots. Curves (e.g. `plot_ibm_vs_agentic_interaction_energy.py`) are fine for visualization; the **main table** should report **n_evaluations**, **n_unique_psmiles**, **wall_time_s** (see `comparison_results.tsv` columns in `THIRD_PARTY_BENCHMARKS.md`).
- **Train vs test (RL):** State clearly whether curves are **test-phase** only or include training; point to fields in benchmark JSON (`evaluation_trace` phases, e.g. `results/ibm_dqn.json`).
- **Primary vs secondary metrics:** Primary = running-best interaction energy at budget *B*; secondary = discovery score, prescreen failure rate, etc.

### Platform (recommended additions)

- Add a **`benchmark_manifest.yaml`** (or extend session `session.json`) per run: git commit, environment fingerprint, all `BIOLOGIX_AI_*` env vars that affect physics, CLI flags, and benchmark / session identifiers.
- Provide a single **`benchmarks/run_all_baselines.sh`** (or Makefile target) that runs every method with the **same** budget *B* and writes one combined TSV + one results JSON per method.

---

## 2. Baselines

### Paper

Reviewers expect **more than one** non–agentic baseline. Minimum credible set:

| Baseline | Role |
|----------|------|
| **Random valid PSMILES** | Sanity check: same eval pipeline, uniform or grammar-constrained sampling after prescreen. |
| **Greedy / single-chain** | e.g. always mutate from best-so-far only — shows value of exploration. |
| **Optuna** | Classical search on same objective — [`benchmarks/optuna_psmiles_discovery.py`](../benchmarks/optuna_psmiles_discovery.py). |
| **IBM RL (DQN and/or PPO)** | Main RL story — [`benchmarks/ibm_insulin_rl_benchmark.py`](../benchmarks/ibm_insulin_rl_benchmark.py). |
| **Scripted autoresearch** | No LLM — isolates literature + LLM reasoning from the agentic curve — `run_autonomous_discovery` / MCP / CLI per [`docs/THIRD_PARTY_BENCHMARKS.md`](THIRD_PARTY_BENCHMARKS.md). |

For the **LLM agent**, report **cost** separately (tokens, model id, API vs local) so it is not an unfairly “cheap” baseline vs RL.

### Platform

- Implement **`benchmarks/random_psmiles_baseline.py`** (or env mode) using the same `MDSimulator.evaluate_candidates`, stopping at `n_evaluations == B`.
- Ensure **every** method appends one row to **`benchmarks/comparison_results.tsv`** (or a study-specific copy) using the **same** column schema documented in `THIRD_PARTY_BENCHMARKS.md`.

---

## 3. Ablations

### Paper

| Ablation | What it tests |
|----------|----------------|
| **No literature** | Fixed query or skip mining; validate → evaluate → mutate only. |
| **No mutation tool** | Literature + fixed candidate set per iteration. |
| **Different LLM** | Smaller vs larger model (same tool schema). |
| **IBM: reward threshold** | Vary `target_energy_kj` in the IBM insulin benchmark. |
| **IBM: algorithm** | DQN vs PPO. |
| **Parallel `max_workers`** | 1 vs *k* at the same total eval budget (wall time vs running-best). |

### Platform

- **Feature flags** in MCP or **`discovery_config.json`** in the session: e.g. `use_literature`, `use_mutate`, `llm_model`, so runs are replayable without parsing chat logs.
- Persist **`candidate_outcomes`** from `openmm_evaluate_psmiles` inside **`save_discovery_state`** `feedback_json` so ablations can be compared on **failure modes**, not only best energy.

---

## 4. Reproducibility

### Paper

- **Code:** Public repo + **git tag** matching the manuscript (e.g. `v1.0-benchmark`).
- **Data:** Zenodo (or equivalent) archive with **frozen** `results/ibm_dqn.json`, agent `agent_iteration_*.json`, `ALL_ITERATIONS_BEST_CANDIDATES.tsv`, and the **exact** plotting command.
- **Hardware:** CPU/GPU, OS, OpenMM CPU vs GPU if applicable.
- **Randomness:** List all seeds (RL, `MDSimulator.random_seed`, any stochastic MD steps); report **mean ± std** over **≥3** seeds for RL and ideally for stochastic LLM runs, or state single-run limitation clearly.

### Platform

- Ship **`conda list --explicit`** or **`pip freeze`** per release; optional **Dockerfile** (packmol + OpenMM + pinned SB3).
- Document nondeterminism (parallel workers, GPU, threads); recommend **`BIOLOGIX_AI_EVAL_MAX_WORKERS=1`** for strict repro mode.
- **CI:** Keep stub-based tests for benchmark scripts; optional workflow that runs parity checks without OpenMM.
- **Plotting:** Document in README/SI:  
  `python benchmarks/plot_ibm_vs_agentic_interaction_energy.py --ibm-json ... --agentic-session ...`  
  including **`--ibm-window`** and the definition of the x-axis.

---

## 5. Suggested manuscript artifacts

1. **Table 1:** Benchmark specification (budget *B*, metrics, physics settings).
2. **Table 2:** All baselines at fixed *B* (best energy, unique PSMILES, wall time, failures).
3. **Figure 1:** Running-best plot + note that axis alignment is defined in SI.
4. **Table 3 or appendix:** 2–3 ablations (e.g. no literature, scripted vs LLM).
5. **SI:** Full env var list, one seed’s raw JSON paths, Zenodo link.

---

## 6. Priority order (engineering vs reviewer impact)

1. **Fixed budget table** + **Optuna + random** baselines.
2. **Multiple RL seeds** (rerun `ibm_insulin_rl_benchmark.py` train+test).
3. **Scripted autoresearch** vs **LLM agent** (isolates what the LLM adds).
4. **Zenodo + git tag + environment file**.
5. Deeper ablations (literature off, model size).

---

## 7. Key code and doc paths

| Asset | Path |
|-------|------|
| IBM insulin RL benchmark | `benchmarks/ibm_insulin_rl_benchmark.py` |
| Gym env | `benchmarks/ibm_insulin_env.py` |
| Optuna benchmark | `benchmarks/optuna_psmiles_discovery.py` |
| Comparison TSV schema | `docs/THIRD_PARTY_BENCHMARKS.md` |
| IBM vs agentic plot | `benchmarks/plot_ibm_vs_agentic_interaction_energy.py` |
| Paper study orchestrator (non-agentic) | `benchmarks/run_paper_study.sh` |
| Paper running-best figure (multi-method) | `benchmarks/plot_paper_comparison.py` |
| Paper Table 2 generator | `benchmarks/generate_paper_comparison_table.py` |
| Random PSMILES baseline | `benchmarks/random_psmiles_baseline.py` |
| Study TSV (fresh runs) | `benchmarks/comparison_results_study.tsv` |
| MDSimulator / eval | `src/python/biologix_ai/simulation/md_simulator.py` |

---

*Generated for orchestration of a systematic benchmarking study; extend this file as the study design solidifies.*
