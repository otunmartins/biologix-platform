"""PhysicsService: facade over existing OpenMM simulation code.

Generalizes from insulin-only to arbitrary biologic targets by
accepting biologic_target as a parameter.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def _resolve_cargo(cargo_name: str) -> Dict[str, Any]:
    """Look the cargo up by name in PubChem.

    Anything that is not a macromolecule is treated as a small-molecule cargo, so the
    platform is not limited to a hardcoded list of one.
    """
    from biologix_ai.material_mappings import lookup_monomer_pubchem, pubchem_timeout_s

    found = lookup_monomer_pubchem(cargo_name, timeout=pubchem_timeout_s())
    if not found.get("ok") or not found.get("pubchem_smiles"):
        raise RuntimeError(
            f"{cargo_name!r} is neither a resolvable structure in the PDB nor a compound "
            f"PubChem knows: {found.get('error') or 'no match'}"
        )
    return {
        "smiles": str(found["pubchem_smiles"]),
        "cid": found.get("pubchem_cid"),
        "name": found.get("monomer_name") or cargo_name,
    }


def _small_molecule_compatibility(psmiles_list: List[str], cargo_name: str) -> Dict[str, Any]:
    """Single-pose MMFF94 cargo/polymer compatibility screen.

    This is deliberately distinct from the protein OpenMM path.  It measures a
    gas-phase pair interaction for a PubChem 3-D cargo and a capped 4-mer, so it
    is useful for comparative screening but is not a binding free energy.
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem

    from biologix_ai.simulation.polymer_build import build_polymer_oligomer_smiles

    cargo_record = _resolve_cargo(cargo_name)
    cargo_smiles = cargo_record["smiles"]
    cid = cargo_record["cid"]

    entries: list[dict[str, Any]] = []
    for psmiles in psmiles_list:
        oligomer_smiles, repeats = build_polymer_oligomer_smiles(psmiles, 4)
        cargo = Chem.AddHs(Chem.MolFromSmiles(cargo_smiles))
        polymer = Chem.AddHs(Chem.MolFromSmiles(oligomer_smiles))
        for index, mol in enumerate((cargo, polymer)):
            if AllChem.EmbedMolecule(mol, randomSeed=42 + index) != 0:
                raise RuntimeError("RDKit could not generate a 3-D cargo/polymer conformer")
            if not AllChem.MMFFHasAllMoleculeParams(mol):
                raise RuntimeError("MMFF94 parameters are unavailable for the cargo/polymer pair")
            AllChem.MMFFOptimizeMolecule(mol, maxIters=500)

        cargo_conf = cargo.GetConformer()
        polymer_conf = polymer.GetConformer()
        cargo_x = [cargo_conf.GetAtomPosition(i).x for i in range(cargo.GetNumAtoms())]
        polymer_x = [polymer_conf.GetAtomPosition(i).x for i in range(polymer.GetNumAtoms())]
        shift_x = max(cargo_x) - min(polymer_x) + 2.8
        for i in range(polymer.GetNumAtoms()):
            point = polymer_conf.GetAtomPosition(i)
            point.x += shift_x
            polymer_conf.SetAtomPosition(i, point)

        pair = Chem.CombineMols(cargo, polymer)
        cargo_props = AllChem.MMFFGetMoleculeProperties(cargo)
        polymer_props = AllChem.MMFFGetMoleculeProperties(polymer)
        pair_props = AllChem.MMFFGetMoleculeProperties(pair)
        cargo_e = AllChem.MMFFGetMoleculeForceField(cargo, cargo_props).CalcEnergy()
        polymer_e = AllChem.MMFFGetMoleculeForceField(polymer, polymer_props).CalcEnergy()
        pair_e = AllChem.MMFFGetMoleculeForceField(
            pair,
            pair_props,
            nonBondedThresh=100.0,
            ignoreInterfragInteractions=False,
        ).CalcEnergy()
        interaction_kj = (pair_e - cargo_e - polymer_e) * 4.184
        entries.append({
            "psmiles": psmiles,
            "cargo": cargo_name,
            "cargo_pubchem_cid": cid,
            "cargo_isomeric_smiles": cargo_smiles,
            "polymer_oligomer_repeats": repeats,
            "interaction_energy_kj_mol": round(interaction_kj, 6),
            "method": "MMFF94 single-pose capped-oligomer interaction screen",
            "interpretation_limit": (
                "Comparative gas-phase force-field score; not a binding free energy "
                "and not a substitute for explicit-solvent sampling."
            ),
            "ok": True,
        })
    return {
        "psmiles_list": psmiles_list,
        "biologic_target": cargo_name,
        "target_type": "small_molecule_cargo",
        "results": entries,
        "errors": [],
    }


def run_simulation(
    psmiles_list: List[str],
    biologic_target: str = "insulin",
    temperature_k: float = 313.0,
    n_steps: int = 5000,
) -> Dict[str, Any]:
    """Run MD simulation for polymer-biologic system.

    Delegates to the existing MDSimulator; biologic_target parameterizes
    which protein structure to use.
    """
    result: Dict[str, Any] = {
        "psmiles_list": psmiles_list,
        "biologic_target": biologic_target,
        "temperature_k": temperature_k,
        "results": [],
        "errors": [],
    }

    try:
        from biologix_ai.services.biologic_resolver import resolve_biologic_target

        repo_root = Path(os.getenv("BIOLOGIX_AI_ROOT", Path(__file__).resolve().parents[4]))
        session_dir_raw = os.getenv("BIOLOGIX_AI_SESSION_DIR", "").strip()
        resolved = resolve_biologic_target(
            biologic_target,
            repo_root,
            session_dir=Path(session_dir_raw) if session_dir_raw else None,
            fetch_pdb=True,
        )
        if not resolved.fetch_ok:
            # No structure means this is not a protein target. A small molecule still has
            # a defensible comparative screen, so try that before giving up.
            try:
                return _small_molecule_compatibility(psmiles_list, biologic_target)
            except Exception as cargo_exc:
                result["errors"].append(
                    "; ".join(resolved.errors) or "target structure resolution failed"
                )
                result["errors"].append(str(cargo_exc))
                result["target_structure"] = resolved.model_dump_public()
                return result

        old_env = {
            key: os.environ.get(key)
            for key in (
                "BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS",
                "BIOLOGIX_AI_OPENMM_TEMPERATURE_K",
                "BIOLOGIX_AI_TARGET_PROTEIN_PDB",
            )
        }
        os.environ["BIOLOGIX_AI_OPENMM_MAX_MINIMIZE_STEPS"] = str(n_steps)
        os.environ["BIOLOGIX_AI_OPENMM_TEMPERATURE_K"] = str(temperature_k)
        os.environ["BIOLOGIX_AI_TARGET_PROTEIN_PDB"] = resolved.pdb_path
        from biologix_ai.simulation.md_simulator import MDSimulator

        sim = MDSimulator()
        candidates = [{"psmiles": s, "chemical_structure": s} for s in psmiles_list]
        try:
            sim_result = sim.evaluate_candidates(
                candidates, max_candidates=len(candidates), verbose=False
            )
            result["results"] = sim_result if isinstance(sim_result, dict) else [sim_result]
            result["target_structure"] = resolved.model_dump_public()
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    except ImportError:
        result["errors"].append(
            "simulation module not available; install with: pip install biologix-ai[simulation]"
        )
    except Exception as exc:
        result["errors"].append(str(exc))

    return result
