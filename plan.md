---
name: OpenMM geometry and energy
overview: "Implement a complete, robust OpenMM path for insulin + ligand geometry relaxation and interaction energy using RDKit/OpenFF Gasteiger charges (no antechamber). Disulfide bonds are handled explicitly per OpenMM best practice. No fallbacks: the OpenMM path is the deliverable and must work end-to-end. TDD throughout."
todos: []
isProject: false
---

# OpenMM path: disulfides, RDKit charges, minimization, interaction energy (all-or-nothing, TDD)

## Principles

- **No fallbacks**: Do not implement "try OpenMM, else GROMACS" or "optional OpenMM." The deliverable is a working OpenMM pipeline; fix issues until it works. Dependencies (openmm, openmmforcefields, openff-toolkit) are required for this path.
- **Robust solutions only**: Use documented best practices (OpenMM issue #4420, user guide, OpenFF/openmmforcefields docs). No ad-hoc or fragile logic.
- **TDD**: Write tests first (or in lockstep); catch syntax, trivial, and runtime errors; do not stop until geometry relaxation and interaction energy both work in tests.

---

## 1. Disulfide bonds (solved first, robust)

**Context**: 4F1C has 6 SSBOND lines; for one monomer use chains **A+B only** (3 disulfides: A6–A11, A7–B7, A20–B19). OpenMM creates bonds from PDB when **SG–SG < 3 Å** and neither residue has HG ([openmm/topology.py](https://github.com/openmm/openmm/blob/2b8ad70394f38d296497215da757aa31ac36b87b/wrappers/python/openmm/app/topology.py#L357)). [Issue #4420](https://github.com/openmm/openmm/issues/4420): Modeller uses **existing bond information** when adding hydrogens (CYS vs CYX); so topology must have correct SS bonds before `addHydrogens(forcefield)`.

**Robust approach** (two parts):

1. **Prepare PDB**: Write a PDB that contains **only chains A and B** (and only ATOM lines for them), plus **SSBOND lines that reference only A and B** (3 lines). This avoids duplicate chains (C/D) and wrong/missing bonds. Optionally use **PDBFixer** to add missing atoms (e.g. OXT) before passing to Model*Ensure disulfide bonds in topology**:
  - **Option A (preferred)**: Load the prepared PDB with `openmm.app.PDBFile`. If crystal SG–SG distances are < 3 Å (4F1C SSBOND lines show ~2.01–2.05 Å), OpenMM will create the bonds automatically. Verify in tests that the topology has the expected number of SS bonds.  
  - **Option B (explicit)**: If we ever use a structure where proximity fails, parse SSBOND from the PDB, map (chain, resseq) → SG atom index in the loaded Topology, and call `Modeller.addBond(i, j)` for each pair before `addHydrogens`. Implement Option B so we are not dependent on proximity; use it when automatic bond count is wrong.

**Concrete steps**:

- Add `prepare_insulin_pdb_openmm(pdb_in, pdb_out)`: ke
