# Method notes

- **Screening:** OpenMM **Packmol matrix** — default **`INSULIN_AI_OPENMM_MATRIX_PACKING_MODE=bulk`** (space-filling cell) or **`shell`** (annulus / encapsulation). Default **density-driven** chain count unless `INSULIN_AI_OPENMM_MATRIX_FIXED_MODE=1` (**shell** volume vs **full-cell** volume for **bulk**; see `matrix_density.py`). **Local energy minimization**, optional short NPT, then **interaction energy** (kJ/mol). Requires **packmol** on PATH. Protein: **AMBER14SB**; polymers: **GAFF** via openmmforcefields, **Gasteiger** charges. Each run adds **`packing_metrics`** (polymer–protein proximity on the minimized PDB). Feedback uses interaction energy and median-split ranking for batch comparison. Rankings differ from legacy GROMACS (AMBER99SB-ILDN + Acpype) runs.
- **Literature:** Semantic Scholar / Asta + PSMILES proposals.

## Discovery reports (MCP; AI-authored)

Preferred flow: the **agent** writes `SUMMARY_REPORT.md` in the session run folder, calls **`render_psmiles_png`** ([psmiles](https://github.com/FermiQ/psmiles) 2D PNGs), then **`compile_discovery_markdown_to_pdf`** (`markdown` + `fpdf2` + **Pillow** for image normalization; no manual `*_raster.png` files). Optional batch tool **`write_discovery_summary_report`** only rebuilds a minimal report from `agent_iteration_*.json` without narrative.

Style and citations: [SUMMARY_REPORT_STYLE.md](SUMMARY_REPORT_STYLE.md). **Chat archive:** each run must call `import_chat_transcript_file` or `save_session_transcript` so the transcript file lives **only** in the same `runs/<session>/` folder as the iteration outputs — **not** under `.cursor/` ([OpenCode_PLATFORM.md](OpenCode_PLATFORM.md)). Dependencies: [DEPENDENCIES.md](DEPENDENCIES.md) (MCP — discovery figures & PDF reports).

## Optuna PSMILES benchmark (agent-free)

[`benchmarks/optuna_psmiles_discovery.py`](../benchmarks/optuna_psmiles_discovery.py) runs **no** MCP, **no** LLM, and **no** literature mining. It uses [Optuna](https://optuna.org/) to maximize the same `discovery_score` as the screening stack: each trial draws discrete knobs (mutation RNG seed, feedback fraction), generates candidates with `feedback_guided_mutation`, validates PSMILES, then evaluates with `MDSimulator`. Polymer strings are **not** embedded in a continuous latent space; the mapping is **black-box optimization over discrete proposals** (categorical / integer parameters), consistent with how HPO frameworks treat expensive simulations. **Run it in conda env `insulin-ai-sim`** (see `environment-simulation.yml`), same as other OpenMM tools: `mamba run -n insulin-ai-sim python benchmarks/optuna_psmiles_discovery.py --seed '[*]OCC[*]' --n-trials 5`.

**References:** Akiba et al. (2019) Optuna, KDD; Bergstra et al. (2011) TPE, NeurIPS; Korovina et al. (2020) ChemBO, AISTATS (sample-efficient BO over structured chemistry—parallel motivation); Griffiths & Hernández-Lobato (2020) constrained BO with VAEs, *Chemical Science* (contrast: this benchmark avoids generative latent search). Full bibliography in the benchmark module docstring.
