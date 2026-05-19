# Summary report style (SUMMARY_REPORT.md)

Instructions for agents authoring session summaries. Goal: read like a **primary research paper or technical report**, not like chat or generic LLM output.

## Structure

Use sections appropriate to a short paper, for example:

1. **Title** — specific to the campaign (not “Summary Report”).
2. **Abstract** — purpose, methods (literature, PSMILES, OpenMM screening), main findings, one sentence on limitations (≤250 words unless the user asks otherwise).
3. **Introduction** — delivery context (fridge-free insulin patch), gap, objective of this discovery run.
4. **Methods** — queries, validation, evaluation protocol (merged minimize, interaction energy), mutation strategy. Cite software and databases by version where known.
5. **Results** — tables of candidates, energies or scores, rankings; reference figures under `structures/` (see below). For biologics campaigns, include a dedicated **Retrosynthesis** subsection (see below).
6. **Discussion** — mechanisms, trade-offs, comparison to literature **with full citations**.
7. **Conclusions** — concise, testable next steps.
8. **References** — numbered list, formatted consistently (see below).

Adapt section names if the journal or lab template requires it; keep the same level of rigor.

## Retrosynthesis (required for biologics campaigns)

Before writing this section, call MCP **`assemble_retrosynthesis_report(run_dir, targets=...)`** and include the returned markdown **verbatim** (e.g. as §3.4 Retrosynthesis). Do not invent routes.

Each top candidate must have a matching `retrosynthesis/plan_*.json` artifact. Per candidate, the section must include:

- `route_provenance` and `retro_stages_completed`
- Table of polymer `steps` (reactants, product, conditions, literature source)
- Per monomer: AiZynth `synthesis_route` steps and `building_blocks` when present
- Explicit honesty when provenance is `template` or `none` (not a literature KG tree)

Interpretation and comparison to literature belong in **Discussion**, not as a substitute for the tool-generated tables.

## Figures (monomer + minimized complex)

Embed **two** visuals per candidate when available:

- **Repeat-unit 2D structure** — PNG from **psmiles** (`PolymerSmiles.savefig`), e.g. `structures/<slug>_monomer.png` (also produced by MCP `render_psmiles_png`).
- **Insulin–polymer complex after OpenMM minimization** — (1) a quick **point cloud** preview: `structures/<slug>_complex_preview.png`; (2) a **PyMOL** cartoon + polymer ball-and-stick figure: `structures/<slug>_complex_chemviz.png` (requires `pymol` on PATH). Regenerate the latter with `python scripts/render_complex_chemviz.py <session>/structures/`.
- **PDB** — `structures/<slug>_complex_minimized.pdb` for reproducibility.

With `INSULIN_AI_SESSION_DIR` set (or MCP `openmm_evaluate_psmiles` `run_dir` / `artifacts_dir`), **`openmm_evaluate_psmiles`** writes these files under `<session>/structures/` unless disabled (`INSULIN_AI_EVAL_NO_STRUCTURE_ARTIFACTS=1`). In `SUMMARY_REPORT.md` use relative Markdown images, for example:

```markdown
![Monomer](structures/Candidate_0_monomer.png)
![Minimized complex (OpenMM)](structures/Candidate_0_complex_preview.png)
```

Then run `compile_discovery_markdown_to_pdf` so the PDF embeds the same files.

**Batch tool `write_discovery_summary_report`:** regenerates a minimal report from `agent_iteration_*.json` and also scans `structures/` for the same PNG naming patterns above, so monomer and complex figures from evaluation appear in `SUMMARY_REPORT.md` / PDF even when you did not hand-edit the Markdown.

## References and citations

Any claim grounded in published work must carry a **numbered citation** in the text and a matching entry in **References**.

Use a **consistent chemistry-friendly style** (e.g. ACS- or Vancouver-like). Each reference should include, where available:

- **All authors** (or et al. per style) or **first author et al.** as your chosen style dictates (stay consistent).
- **Journal title abbreviated** per standard usage (ISO 4–style abbreviations are typical: *J. Am. Chem. Soc.*, *Biomacromolecules*, *Int. J. Pharm.*).
- **Year** in bold or plain per style.
- **Volume**, **first and last page** (or article number for online-only journals).
- **DOI** optional but encouraged for verification.

Example (illustrative only; follow one style throughout):

```text
(1) Smith, J. A.; Doe, R. Biomacromolecules 2024, 25, 1234–1245. https://doi.org/10.1021/...
```

Do **not** cite only by URL, bare DOI string in running text without a reference list, or “a 2020 paper” without full bibliographic data.

## Prose: avoid generic AI style

Editorial commentary on LLM-generated text often flags **overuse of the em dash (—)** as a connective (see e.g. discussion of em dashes as an AI-typical pattern in [PM Proofreading](https://proofreadingmalaysia.com/the-inevitable-em-dash-the-giveaway-of-ai-writing/) and related style notes), **heavy use of colons** (especially “Title: subtitle” cadence in running prose), **semicolon stacking**, and **stock phrases** that read as filler. Prefer:

- **Em dash (—):** avoid. Use a period and a new sentence, commas, or parentheses.
- **Colons:** use sparingly; do not chain “Keyword: explanation: detail.”
- **Semicolons:** rare; prefer shorter sentences.
- **Stock phrases** often associated with LLM prose (non-exhaustive): “delve,” “landscape,” “tapestry,” “leverage” (as verb), “robust” (overused), “paradigm,” “unlock,” “it is important to note,” “in conclusion, …” as a crutch. Replace with concrete verbs and nouns from your results.
- **Rhetorical symmetry** (“not X, but Y”; “less A, more B”) in every paragraph reads formulaic. Use when justified; otherwise state claims directly.
- **Second person** (“you should”) — avoid in formal report; use passive or “we” if the lab voice is appropriate.

## Tone and mechanics

- Prefer **third person** or **passive voice** for methods; **we** is acceptable if consistent with lab reports.
- **No emoji** in `SUMMARY_REPORT.md` unless the user explicitly requests them.
- **Define abbreviations** at first use (PEG, PLGA, RMSD, etc.).
- **Numbers:** significant figures consistent with simulation output; units (SI) explicit.

## Tools (after drafting)

1. `render_psmiles_png` for 2D structures; embed in Markdown as `![Short caption](structures/...)`.
2. `compile_discovery_markdown_to_pdf` to produce `SUMMARY_REPORT.pdf`.

You do **not** need duplicate “raster” copies of figures (e.g. `*_raster.png`): the PDF step re-encodes local images for fpdf2 automatically.

The tools do not rewrite prose; they only render figures and PDF.
