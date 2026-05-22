# PSMILES (polymer SMILES) — guide for agents and humans

This file is the **canonical in-repo reference** for what PSMILES are, how `[*]` works, and how they relate (or do **not** relate) to material names like “PEG” or “PLA-PEG-PLA”. OpenCode agents using **`biologics-delivery-discovery`** should treat this document as the definition to follow when proposing or checking structures.

## What PSMILES is

- **PSMILES** = polymer SMILES: a line notation for a **repeat unit** of a polymer, with **exactly two** connection points marked **`[*]`** (the stars attach to neighbors in the infinite chain).
- It is **not** a brand name, trade name, or block-copolymer acronym by itself. **“PLA-PEG-PLA”** in text does not automatically map to one correct PSMILES—you must choose a **chemistry-level repeat unit** (often simplified).

## Rules that validation enforces (syntax)

- The string must contain **`[*]`** (typically two stars for a linear backbone repeat unit).
- Packages (`psmiles`, RDKit) check **well-formedness** of the graph, not whether the string truly represents what a paper calls “chitosan”.

## Names vs structures (critical)

- **Automated name→PSMILES** is now available via **`generate_psmiles_from_name(material_name)`**. This tool resolves names in three tiers:
  1. **Known-polymer table** (~60 common polymers/abbreviations: PEG, PLA, PLGA, PCL, PS, PMMA, PVDF, PDMS, chitosan, …). High confidence, no network call.
  2. **PubChem lookup + auto-conversion**: strips "poly" prefix, fetches monomer SMILES from PubChem, then detects the polymerisation mechanism (vinyl C=C opening, hydroxy-acid ester condensation, amino-acid amide condensation) and places `[*]` at the backbone connection points. Medium confidence.
  3. If neither tier succeeds the tool returns `ok: false` with the raw PubChem monomer SMILES so the caller can attempt manual conversion.

  Always **validate the generated PSMILES** with `validate_psmiles` before evaluation, especially for PubChem auto results (medium confidence).

- If you already have a PSMILES and want to cross-check it against its name, call **`validate_psmiles(psmiles, material_name="...")`**. The tool returns three automated checks:

  1. **`functional_groups`** — RDKit SMARTS counts of carboxylic acid, ester, ether, amine, amide, hydroxyl, aldehyde, ketone, aromatic, etc. in the capped repeat unit.
  2. **`name_consistency`** — keyword rules check whether the name's implied chemistry (e.g. "acid" expects carboxylic_acid or ester) is present. If `consistent` is `false`, fix the PSMILES before evaluating.
  3. **`pubchem_lookup`** — strips "poly" prefix, queries PubChem PUG REST for the monomer's canonical SMILES (responses are **cached in-process** by monomer name so repeated checks stay fast), and computes Tanimoto similarity against the H-capped repeat unit. Low similarity (<0.4) is flagged.

- If `name_consistency.consistent` is false, **do not evaluate**; fix the PSMILES first (check PubChem monomer structure, literature, or the functional-group profile for guidance).
- **Never** write mechanistic claims in reports (e.g. "carboxylate-mediated stabilization") unless `name_consistency` passed for the specific PSMILES in the results table.

## Common examples (repeat units, illustrative)

| Name (informal) | Example PSMILES (repeat unit) | Notes |
|-----------------|--------------------------------|--------|
| PEG / PEO | `[*]OCC[*]` | Poly(ethylene oxide); simplest repeat. |
| Polyethylene | `[*]CC[*]` | |
| Polylactide (PLA) | often simplified, e.g. lactide-derived repeat; structures vary | Use literature for the repeat you intend. |
| Polystyrene | `[*]CC([*])c1ccccc1` (variants exist) | |

Copolymers and block sequences usually need a **single repeat** that encodes your model’s intent, or **separate** homopolymer screens—not one ambiguous acronym.

## What simulation uses

- **`openmm_evaluate_psmiles`** builds an **oligomer** from your PSMILES, places it near insulin, and runs **OpenMM** minimization + interaction energy. The physics sees **only the PSMILES graph**, not the English name.
- Report discussion sections must therefore tie mechanistic claims to the **actual functional groups** (from `functional_groups`), not the material name. If the name says "acid" but the structure is a ketone, say "ketone" in the report.

## Persistence in OpenCode

- This file lives in **`docs/PSMILES_GUIDE.md`**. It is **not** auto-injected into every model context; the **`biologics-delivery-discovery`** agent should **read this file** when unsure. For a long session, the agent may re-read it or you can paste a short excerpt into the chat.

## Further reading

- Ramprasad-group **psmiles** tooling (canonicalization, etc.).
- Primary literature for each **specific** polymer’s repeat unit when accuracy matters.
