# Agentic experiments for the paper (Studies D–F)

Non-agentic baselines are run via [`benchmarks/run_paper_study.sh`](../benchmarks/run_paper_study.sh) in the `biologix-ai-sim` environment. The studies below require the **LLM + MCP** autonomous discovery workflow (Cursor agent or equivalent).

**Benchmark contract** (match `run_paper_study.sh` and IBM parity defaults):

- **20 discovery iterations** × **up to 8 OpenMM evaluations per iteration** (target budget **160** successful evals, same as `--agentic-iterations 20 --evals-per-iteration 8` on `ibm_insulin_rl_benchmark.py`).
- Seed PSMILES: `[*]OCC[*]`
- Same OpenMM matrix settings as production (`openmm_evaluate_psmiles` / `MDSimulator`)
- **BIOLOGIX_AI_EVAL_MAX_WORKERS=1** recommended for reproducibility

The historical session `runs/autonomous-25iter/` used **25** iterations; new paper runs should use **20** so curves and tables align with non-agentic baselines.

---

## Study D — Agentic replicates (variance)

Run **two additional** full **20-iteration** campaigns with the same protocol as above (and the same iteration/eval budget as `run_paper_study.sh`):

1. Create a new session directory, e.g. `runs/autonomous-20iter-rep2/`, `runs/autonomous-20iter-rep3/`.
2. Each session: literature mining + PSMILES generation/validation + `openmm_evaluate_psmiles` + `mutate_psmiles` (full agentic loop).
3. After each session, ensure you have:
   - `agent_iteration_1.json` … `agent_iteration_20.json`
   - `ALL_ITERATIONS_BEST_CANDIDATES.tsv` (optional but useful for plots)
   - `session.json` with a distinct `session_id`

**Plotting** (after non-agentic JSONs exist under `results/`):

```bash
mamba run -n biologix-ai-sim python benchmarks/plot_paper_comparison.py \
  --results-dir results \
  --agentic-session runs/autonomous-20iter runs/autonomous-20iter-rep2 runs/autonomous-20iter-rep3 \
  --output results/paper_comparison_running_best.png
```

**Manuscript:** report mean ± std of **best** interaction energy (and optionally running-best curves) across the three agentic runs.

---

## Study E — Ablation: no literature mining

**Goal:** Isolate the value of literature search vs. mutation-only exploration.

- Session: e.g. `runs/autonomous-25iter-nolit/`
- **Do not** call Semantic Scholar, PaperQA, or literature-search tools.
- Start from `[*]OCC[*]`; generate candidates with `mutate_psmiles` (and validation) only, guided by OpenMM feedback and prior top candidates.
- Same **20-iteration** count and per-iteration eval cap (8) as Study D.

Document in `SUMMARY_REPORT.md` that literature was disabled.

---

## Study F — Ablation: no mutation

**Goal:** Isolate the value of `mutate_psmiles` vs. literature-only proposals.

- Session: e.g. `runs/autonomous-25iter-nomut/`
- Use literature mining to propose candidates; **do not** use `mutate_psmiles`.
- Same **20-iteration** structure where possible.

---

## Optional: append agentic rows to the study TSV

The shared schema is documented in [`THIRD_PARTY_BENCHMARKS.md`](THIRD_PARTY_BENCHMARKS.md). After a session completes, add one row per run to `benchmarks/comparison_results_study.tsv` (or a copy) with:

- `method`: e.g. `agentic_llm`
- `n_evaluations`: count of completed OpenMM evaluations
- `best_interaction_energy_kj_mol`, `n_unique_psmiles_evaluated`, `wall_time_s`, etc.

Then regenerate the paper table:

```bash
python benchmarks/generate_paper_comparison_table.py \
  --tsv benchmarks/comparison_results_study.tsv \
  --output docs/PAPER_TABLE2.md
```

---

## Related docs

- [`BENCHMARK_AND_REPRO_STUDY.md`](BENCHMARK_AND_REPRO_STUDY.md) — full benchmark / baseline / ablation checklist
- [`SUMMARY_REPORT_STYLE.md`](SUMMARY_REPORT_STYLE.md) — paper-style session writeups
