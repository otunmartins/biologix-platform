# Manuscripts

Two Fairmeta preprints live under `paper/`, plus shared LaTeX assets.

## Layout

```
paper/
├── shared/                 # Bibliography and package imports (both papers)
│   ├── main_imports.tex
│   └── references.bib
├── insulin/                # Benchmark preprint (insulin, RL/BO baselines)
│   ├── main.tex
│   ├── compile_main.sh
│   └── figures/
└── biologics/              # Showcase preprint (any biologic + retrosynthesis)
    ├── main.tex
    ├── compile_main.sh
    ├── copy_figures.sh
    └── figures/
```

## Papers

| Directory | Title (short) | Build |
|-----------|---------------|-------|
| `insulin/` | Towards Discovery of Polymeric Materials for Insulin Delivery via Physics-Grounded Agentic Workflows | `cd paper/insulin && ./compile_main.sh` |
| `biologics/` | Biologics AI: End-to-End Polymer Excipient Discovery with Agent-Backed Retrosynthesis | `cd paper/biologics && ./compile_main.sh` |

Both manuscripts cite each other where appropriate (`martins2026insulin` in `shared/references.bib`).

## Shared assets

- **`shared/references.bib`** — single bibliography for both preprints and `docs/proposal.tex`.
- **`shared/main_imports.tex`** — TikZ, tables, and math packages included by both `main.tex` files.

## Biologics figures

Before building the showcase PDF, refresh structure assets from local runs:

```bash
paper/biologics/copy_figures.sh
```

## Build both PDFs

```bash
(cd paper/insulin && ./compile_main.sh)
(cd paper/biologics && ./compile_main.sh)
```
