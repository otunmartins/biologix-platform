#!/usr/bin/env python3
"""
Property extraction after OpenMM minimize on merged insulin + polymer.

Uses interaction energy, optional RMSD/contacts when present, and composite score.
"""

from typing import Any, Dict, List, Optional

from insulin_ai.simulation.scoring import composite_screening_score


class PropertyExtractor:
    """Maps MD (OpenMM) results to feedback + composite score."""

    def __init__(
        self,
        interaction_favorable_max_kj: float = -5.0,
        interaction_unfavorable_min_kj: float = 50.0,
        min_insulin_polymer_contacts: int = 5,
        insulin_rmsd_problematic_nm: float = 0.45,
    ):
        self.interaction_favorable_max_kj = interaction_favorable_max_kj
        self.interaction_unfavorable_min_kj = interaction_unfavorable_min_kj
        self.min_insulin_polymer_contacts = min_insulin_polymer_contacts
        self.insulin_rmsd_problematic_nm = insulin_rmsd_problematic_nm

    def extract_feedback(
        self,
        md_results: List[Dict[str, Any]],
        material_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        high_performers: List[str] = []
        effective_mechanisms: List[str] = []
        problematic_features: List[str] = []
        property_analysis: Dict[str, Any] = {}

        for i, res in enumerate(md_results):
            name = (
                material_names[i]
                if material_names and i < len(material_names)
                else (res or {}).get("psmiles", f"candidate_{i}")
            )
            if res is None:
                problematic_features.append(f"evaluation_failed:{name}")
                continue
            e_int = res.get("interaction_energy_kj_mol")
            contacts = res.get("insulin_polymer_contacts")
            e_complex = res.get("potential_energy_complex_kj_mol")
            rmsd = res.get("insulin_rmsd_to_initial_nm")
            composite = None
            if e_int is not None and rmsd is not None:
                try:
                    composite = composite_screening_score(float(e_int), float(rmsd))
                except (TypeError, ValueError):
                    pass
            if e_int is not None:
                if e_int <= self.interaction_favorable_max_kj:
                    high_performers.append(name)
                    effective_mechanisms.append("favorable_interaction_energy")
                if e_int >= self.interaction_unfavorable_min_kj:
                    problematic_features.append(f"high_interaction_energy:{name}")
            if rmsd is not None and rmsd == rmsd:
                if rmsd <= 0.15:
                    effective_mechanisms.append("insulin_structure_preserved")
                if rmsd >= self.insulin_rmsd_problematic_nm:
                    problematic_features.append(f"high_insulin_distortion:{name}")
            if contacts is not None:
                if contacts >= self.min_insulin_polymer_contacts:
                    effective_mechanisms.append("insulin_polymer_contacts")
                elif contacts < 2:
                    problematic_features.append(f"low_insulin_contacts:{name}")
            property_analysis[name] = {
                "interaction_energy_kj_mol": e_int,
                "insulin_rmsd_to_initial_nm": rmsd,
                "composite_screening_score": composite,
                "potential_energy_complex_kj_mol": e_complex,
                "potential_energy_insulin_kj_mol": res.get("potential_energy_insulin_kj_mol"),
                "potential_energy_polymer_kj_mol": res.get("potential_energy_polymer_kj_mol"),
                "insulin_polymer_contacts": contacts,
                "method": res.get("method"),
                "psmiles": res.get("psmiles"),
            }

        if high_performers:
            scored = [
                (property_analysis[n].get("composite_screening_score") or -1e9, n)
                for n in high_performers
            ]
            scored.sort(key=lambda x: -x[0])
            high_performers = [n for _, n in scored[:5]] if scored else high_performers[:5]
        else:
            energy_rows: List[tuple[float, str]] = []
            for i, res in enumerate(md_results):
                if not res:
                    continue
                name = (
                    material_names[i]
                    if material_names and i < len(material_names)
                    else res.get("psmiles", f"candidate_{i}")
                )
                e_int = res.get("interaction_energy_kj_mol")
                e_c = res.get("potential_energy_complex_kj_mol")
                key = e_int if e_int is not None else e_c
                if key is not None:
                    try:
                        energy_rows.append((float(key), name))
                    except (TypeError, ValueError):
                        pass
            if energy_rows:
                effective_mechanisms.append("OpenMM_merged_screening")
                energy_rows.sort(key=lambda t: t[0])
                # Only promote below-median candidates if the best energy is actually
                # thermodynamically meaningful (negative interaction energy).
                if energy_rows[0][0] < 0:
                    median_e = energy_rows[len(energy_rows) // 2][0]
                    for e, name in energy_rows:
                        if e <= median_e:
                            high_performers.append(name)
                    high_performers = list(dict.fromkeys(high_performers))[:5]

        return {
            "high_performers": high_performers[:5],
            "effective_mechanisms": list(dict.fromkeys(effective_mechanisms))[:5],
            "problematic_features": list(dict.fromkeys(problematic_features))[:5],
            "property_analysis": property_analysis,
        }
