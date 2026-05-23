# Manuscripts

Fairmeta preprints for this project.

## Layout

```
paper/
├── shared/                 # Bibliography and package imports (insulin preprint)
│   ├── main_imports.tex
│   └── references.bib
├── insulin/                # Benchmark preprint (insulin, RL/BO baselines)
│   ├── main.tex
│   ├── compile_main.sh
│   └── figures/
└── biologics/              # Pointer → standalone repo (see README there)
```

## Papers

| Manuscript | Location | Build |
|------------|----------|-------|
| Insulin benchmark | `paper/insulin/` | `cd paper/insulin && ./compile_main.sh` |
| Biologics AI showcase | [biologics-ai-paper](https://github.com/otunmartins/biologics-ai-paper) | See that repo (Overleaf-friendly) |

The insulin and biologics preprints cite each other (`martins2026insulin` in `shared/references.bib`).

## Shared assets (insulin preprint)

- **`shared/references.bib`** — bibliography for the insulin preprint and `docs/proposal.tex`.
- **`shared/main_imports.tex`** — TikZ, tables, and math packages.
