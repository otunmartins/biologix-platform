#!/usr/bin/env bash
# Resume paper study: missing Optuna seeds (123, 456) + Study B (IBM DQN ablations).
# Skips Optuna seed 42 if results/optuna_seed42.json already exists.
#
# Usage (from repo root):
#   ./benchmarks/run_optuna_resume_study_b.sh
#
# Parallel Optuna (123 and 456 at once; TSV appended once after both finish):
#   PARALLEL_OPTUNA=1 ./benchmarks/run_optuna_resume_study_b.sh
#
# Skip re-running Optuna 123/456 if JSON already present (e.g. after manual completion):
#   SKIP_EXISTING_OPTUNA=1 ./benchmarks/run_optuna_resume_study_b.sh
#
# Force Study B on CPU only (avoids 3-way GPU contention if you parallelize later):
#   STUDY_B_CPU_ONLY=1 ./benchmarks/run_optuna_resume_study_b.sh
#
# Env:
#   PAPER_STUDY_TSV — comparison TSV path (default: benchmarks/comparison_results_study.tsv)
#   CONDA_ENV — conda env name (default: insulin-ai-sim)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONUNBUFFERED=1
CONDA_ENV="${CONDA_ENV:-insulin-ai-sim}"
RUN="conda run -n ${CONDA_ENV} --no-capture-output"
TSV="${PAPER_STUDY_TSV:-benchmarks/comparison_results_study.tsv}"
SEED_PSMILES="[*]OCC[*]"
N_TRIALS=20
LIB_SIZE=8
mkdir -p results logs

optuna_one() {
  local rs=$1
  local out_json=$2
  local logf=$3
  local use_tsv=$4
  if [[ "${SKIP_EXISTING_OPTUNA:-0}" == "1" && -f "${out_json}" ]]; then
    echo "--- Optuna random-seed ${rs}: skip (exists ${out_json}) ---"
    return 0
  fi
  echo "--- Optuna random-seed ${rs} -> ${out_json} (log: ${logf}) ---"
  if [[ "${use_tsv}" == "1" ]]; then
    ${RUN} python benchmarks/optuna_psmiles_discovery.py \
      --seed "${SEED_PSMILES}" \
      --n-trials "${N_TRIALS}" \
      --library-size "${LIB_SIZE}" \
      --random-seed "${rs}" \
      --output "${out_json}" \
      --comparison-tsv "${TSV}" \
      2>&1 | tee "${logf}"
  else
    ${RUN} python benchmarks/optuna_psmiles_discovery.py \
      --seed "${SEED_PSMILES}" \
      --n-trials "${N_TRIALS}" \
      --library-size "${LIB_SIZE}" \
      --random-seed "${rs}" \
      --output "${out_json}" \
      2>&1 | tee "${logf}"
  fi
}

echo "== Resume: Optuna seeds 123 and 456 (skip 42 if present) =="
if [[ -f results/optuna_seed42.json ]]; then
  echo "Found results/optuna_seed42.json — not re-running Optuna seed 42."
else
  echo "WARNING: results/optuna_seed42.json missing; running seed 42 first."
  optuna_one 42 results/optuna_seed42.json logs/optuna_resume_seed42.log 1
fi

if [[ "${PARALLEL_OPTUNA:-0}" == "1" ]]; then
  echo "PARALLEL_OPTUNA=1: running seeds 123 and 456 without TSV; append after wait."
  optuna_one 123 results/optuna_seed123.json logs/optuna_resume_seed123.log 0 &
  pid123=$!
  optuna_one 456 results/optuna_seed456.json logs/optuna_resume_seed456.log 0 &
  pid456=$!
  ec123=0
  ec456=0
  wait "${pid123}" || ec123=$?
  wait "${pid456}" || ec456=$?
  if [[ "${ec123}" -ne 0 || "${ec456}" -ne 0 ]]; then
    echo "error: one or more Optuna jobs failed (123 exit ${ec123}, 456 exit ${ec456})" >&2
    exit 1
  fi
  echo "Appending Optuna comparison rows for 123 and 456 -> ${TSV}"
  ${RUN} python benchmarks/append_optuna_comparison_rows.py \
    --tsv "${TSV}" \
    results/optuna_seed123.json \
    results/optuna_seed456.json
else
  echo "Sequential Optuna: TSV row appended after each seed."
  optuna_one 123 results/optuna_seed123.json logs/optuna_resume_seed123.log 1
  optuna_one 456 results/optuna_seed456.json logs/optuna_resume_seed456.log 1
fi

echo "== Study B: IBM DQN reward-shaping ablation (seed 42) =="
if [[ "${STUDY_B_CPU_ONLY:-0}" == "1" ]]; then
  echo "STUDY_B_CPU_ONLY=1: CUDA_VISIBLE_DEVICES empty for Study B."
fi
for tgt in -5.0 -500.0 -1000.0; do
  tag="${tgt//./_}"
  echo "--- target_energy_kj ${tgt} ---"
  if [[ "${STUDY_B_CPU_ONLY:-0}" == "1" ]]; then
    env CUDA_VISIBLE_DEVICES="" ${RUN} python benchmarks/ibm_insulin_rl_benchmark.py \
      --algorithm dqn \
      --seed "${SEED_PSMILES}" \
      --random-seed 42 \
      --target-energy "${tgt}" \
      --agentic-iterations 20 \
      --evals-per-iteration 8 \
      --output "results/ibm_dqn_ablation_target${tag}.json" \
      --comparison-tsv "${TSV}" \
      --comparison-notes "ablation_target_energy_kj=${tgt}"
  else
    ${RUN} python benchmarks/ibm_insulin_rl_benchmark.py \
      --algorithm dqn \
      --seed "${SEED_PSMILES}" \
      --random-seed 42 \
      --target-energy "${tgt}" \
      --agentic-iterations 20 \
      --evals-per-iteration 8 \
      --output "results/ibm_dqn_ablation_target${tag}.json" \
      --comparison-tsv "${TSV}" \
      --comparison-notes "ablation_target_energy_kj=${tgt}"
  fi
done

echo "== Done. Optional: table + figure =="
echo "  ${RUN} python benchmarks/generate_paper_comparison_table.py --tsv ${TSV} --output docs/PAPER_TABLE2.md"
echo "  ${RUN} python benchmarks/plot_paper_comparison.py --results-dir results --output results/paper_comparison_running_best.png"
