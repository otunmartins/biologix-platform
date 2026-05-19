#!/usr/bin/env bash
# Run all non-agentic paper baselines (Study A + IBM reward ablations).
# Requires: conda env ``insulin-ai-sim`` (OpenMM + Packmol + SB3 + Optuna).
#
#   ./benchmarks/run_paper_study.sh
#
# Resume after partial failure (Optuna 123/456 + Study B only): see
# ``benchmarks/run_optuna_resume_study_b.sh``.
#
# Parity: 20 discovery iterations × 8 evals/iteration = 160 successful OpenMM evals
# for IBM RL, Optuna (20 trials × 8 candidates), and random baseline.
# Runtime: many hours (≈160 OpenMM evaluations × 15+ runs on CPU).
#
# Logging: ``conda run`` captures child stdout/stderr by default, so ``nohup`` logs
# look empty for hours. We use ``--no-capture-output`` and ``PYTHONUNBUFFERED=1``
# so lines reach ``nohup.out`` as they are produced (still sparse when SB3 verbose=0).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONUNBUFFERED=1
# Stream subprocess output; without this, nohup.out may stay nearly empty until exit.
RUN="conda run -n insulin-ai-sim --no-capture-output"
TSV="${PAPER_STUDY_TSV:-benchmarks/comparison_results_study.tsv}"
SEEDS=(42 123 456)

mkdir -p results

echo "== Study A: IBM DQN (3 seeds) =="
for s in "${SEEDS[@]}"; do
  echo "--- DQN seed ${s} ---"
  ${RUN} python benchmarks/ibm_insulin_rl_benchmark.py \
    --algorithm dqn \
    --seed "[*]OCC[*]" \
    --random-seed "${s}" \
    --agentic-iterations 20 \
    --evals-per-iteration 8 \
    --output "results/ibm_dqn_seed${s}.json" \
    --comparison-tsv "${TSV}"
done

echo "== Study A: IBM PPO (3 seeds) =="
for s in "${SEEDS[@]}"; do
  echo "--- PPO seed ${s} ---"
  ${RUN} python benchmarks/ibm_insulin_rl_benchmark.py \
    --algorithm ppo \
    --seed "[*]OCC[*]" \
    --random-seed "${s}" \
    --agentic-iterations 20 \
    --evals-per-iteration 8 \
    --output "results/ibm_ppo_seed${s}.json" \
    --comparison-tsv "${TSV}"
done

echo "== Study A: Optuna TPE (3 seeds, 20 trials × 8 candidates = 160 eval cap) =="
for s in "${SEEDS[@]}"; do
  echo "--- Optuna seed ${s} ---"
  ${RUN} python benchmarks/optuna_psmiles_discovery.py \
    --seed "[*]OCC[*]" \
    --n-trials 20 \
    --library-size 8 \
    --random-seed "${s}" \
    --output "results/optuna_seed${s}.json" \
    --comparison-tsv "${TSV}"
done

echo "== Study A: Random baseline (3 seeds, 160 evals = 20×8 parity) =="
for s in "${SEEDS[@]}"; do
  echo "--- Random seed ${s} ---"
  ${RUN} python benchmarks/random_psmiles_baseline.py \
    --seed "[*]OCC[*]" \
    --n-evaluations 160 \
    --library-size 8 \
    --random-seed "${s}" \
    --output "results/random_seed${s}.json" \
    --comparison-tsv "${TSV}"
done

echo "== Study B: IBM DQN reward-shaping ablation (seed 42) =="
for tgt in -5.0 -500.0 -1000.0; do
  tag="${tgt//./_}"
  echo "--- target_energy_kj ${tgt} ---"
  ${RUN} python benchmarks/ibm_insulin_rl_benchmark.py \
    --algorithm dqn \
    --seed "[*]OCC[*]" \
    --random-seed 42 \
    --target-energy "${tgt}" \
    --agentic-iterations 20 \
    --evals-per-iteration 8 \
    --output "results/ibm_dqn_ablation_target${tag}.json" \
    --comparison-tsv "${TSV}" \
    --comparison-notes "ablation_target_energy_kj=${tgt}"
done

echo "== Done. Generate table + figure: =="
echo "  ${RUN} python benchmarks/generate_paper_comparison_table.py --tsv ${TSV} --output docs/PAPER_TABLE2.md"
echo "  ${RUN} python benchmarks/plot_paper_comparison.py --results-dir results --agentic-session runs/autonomous-20iter --output results/paper_comparison_running_best.png"
