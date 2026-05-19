# MILESTONE 4: Active Learning Framework & Feedback Integration
# This file contains the iterative functionality for the complete active learning system
# Implementation for Week 4 - when integrating MD simulation feedback

import json
import os
from typing import List, Dict, Optional, Any
from datetime import datetime

from insulin_ai.literature.mining_system import MaterialsLiteratureMiner


class IterativeLiteratureMiner(MaterialsLiteratureMiner):
    """
    Advanced iterative literature mining system with MD simulation feedback integration.
    Extends the basic MaterialsLiteratureMiner for Milestone 4.
    
    Features for Milestone 4:
    - Dynamic prompt evolution based on MD simulation results
    - Iterative refinement of search queries
    - Feedback integration from OpenMM / MDSimulator evaluation
    - Active learning cycle implementation
    """
    
    def __init__(self, *args, run_dir=None, **kwargs):
        super().__init__(*args, run_dir=run_dir, **kwargs)
        print("🔄 Iterative Literature Mining System (Milestone 4) initialized!")
    
    def mine_with_feedback(self, 
                          iteration: int = 1,
                          top_candidates: List[str] = None,
                          stability_mechanisms: List[str] = None,
                          target_properties: Dict[str, Any] = None,
                          limitations: List[str] = None,
                          md_simulation_results: Dict = None,
                          num_candidates: int = 15) -> Dict:
        """
        Iterative literature mining with MD simulation feedback.
        
        This is the core function for Milestone 4 - integrates with:
        - UMA-ASE MD simulation results
        - Dynamic prompt evolution
        
        Args:
            iteration (int): Current iteration in active learning cycle
            top_candidates (List[str]): High-performing materials from previous iterations
            stability_mechanisms (List[str]): Successful mechanisms from MD simulations
            target_properties (Dict): Properties to optimize based on simulation results
            limitations (List[str]): Failed approaches to avoid
            md_simulation_results (Dict): Results from MDSimulator (OpenMM)
            num_candidates (int): Number of new candidates to generate
        
        Returns:
            Dict: Comprehensive results for feeding back into active learning cycle
        """
        print(f"\n🔬 Iterative Literature Mining - Iteration {iteration}")
        
        # Process MD simulation feedback (Milestone 4 integration)
        if md_simulation_results:
            feedback = self._process_md_feedback(md_simulation_results)
            top_candidates = feedback.get('successful_materials', top_candidates)
            stability_mechanisms = feedback.get('working_mechanisms', stability_mechanisms)
            limitations = feedback.get('failed_approaches', limitations)
        
        import os
        from insulin_ai.literature.literature_scholar_only import run_scholar_mine

        results = run_scholar_mine(
            api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
            base_query="hydrogels insulin delivery transdermal patch polymer",
            iteration=iteration,
            top_candidates=top_candidates,
            stability_mechanisms=stability_mechanisms,
            limitations=limitations,
            target_properties=target_properties,
            run_dir=self.run_dir,
            num_candidates=num_candidates,
        )
        results["feedback_metadata"]["md_results_processed"] = (
            md_simulation_results is not None
        )
        
        self._save_iterative_results(results)
        return results
    
    def _process_md_feedback(self, md_results: Dict) -> Dict:
        """
        Process MD simulation results to extract feedback for next iteration.
            Integration point for MDSimulator feedback (OpenMM).
        """
        # This will integrate with the MD simulation pipeline
        # For now, return structured feedback format
        return {
            "successful_materials": md_results.get("high_performers", []),
            "working_mechanisms": md_results.get("effective_mechanisms", []),
            "failed_approaches": md_results.get("problematic_features", []),
            "property_insights": md_results.get("property_analysis", {})
        }
    
    def _generate_dynamic_queries(self, iteration: int, top_candidates: List[str], 
                                 stability_mechanisms: List[str], target_properties: Dict,
                                 limitations: List[str]) -> List[str]:
        """
        Generate search queries that evolve based on iteration and feedback.
        Implements the dynamic prompt evolution strategy from the proposal.
        """
        base_queries = [
            "hydrogels insulin delivery transdermal patch",
            "polymer protein stabilization thermal",
            "biocompatible materials drug delivery skin",
            "nanomaterials insulin encapsulation controlled release"
        ]
        
        if iteration == 1:
            # Initial broad exploration
            return base_queries + [
                "protein stabilization polymers temperature",
                "peptide delivery hydrogels biocompatible",
                "insulin stability materials room temperature",
                "transdermal drug delivery patches"
            ]
        
        elif iteration <= 3:
            # Incorporate initial insights
            refined_queries = base_queries.copy()
            
            if top_candidates:
                for material in top_candidates[:3]:
                    refined_queries.append(f"{material} insulin stabilization")
                    refined_queries.append(f"{material} protein drug delivery")
            
            if stability_mechanisms:
                for mechanism in stability_mechanisms[:2]:
                    refined_queries.append(f"protein stabilization {mechanism}")
            
            return refined_queries
        
        else:
            # Advanced targeted exploration
            targeted_queries = []
            
            if top_candidates and stability_mechanisms:
                for material in top_candidates[:2]:
                    for mechanism in stability_mechanisms[:2]:
                        targeted_queries.append(f"{material} {mechanism} insulin")
            
            if target_properties:
                for prop, value in target_properties.items():
                    targeted_queries.append(f"materials {prop} insulin delivery")
            
            if limitations:
                avoid_terms = " ".join([f"-{limit}" for limit in limitations[:2]])
                targeted_queries.append(f"insulin delivery materials {avoid_terms}")
            
            return targeted_queries + base_queries[:2]
    
    def _extract_with_dynamic_prompts(self, papers: List[Dict], iteration: int,
                                     top_candidates: List[str], stability_mechanisms: List[str],
                                     target_properties: Dict, limitations: List[str],
                                     num_candidates: int) -> List[Dict]:
        """Deprecated: scholar seeds only (no in-server LLM)."""
        from insulin_ai.literature.literature_scholar_only import seed_candidates_from_papers
        return seed_candidates_from_papers(papers, max_names=num_candidates)

    def _build_dynamic_prompt(self, iteration: int, top_candidates: List[str],
                             stability_mechanisms: List[str], target_properties: Dict,
                             limitations: List[str], num_candidates: int) -> str:
        """
        Build prompts that evolve based on iteration and feedback.
        Implements the prompt template architecture from the proposal.
        """
        base_prompt = f"""# Iterative Materials Extraction - Iteration {iteration}

EXTRACTION TASK: Identify {num_candidates} materials with potential for fridge-free insulin delivery patches.

INPUT CONTEXT:
- Iteration Number: {iteration}"""

        if top_candidates:
            base_prompt += f"""
- Previous High-performing Materials: {', '.join(top_candidates)}"""
        
        if stability_mechanisms:
            base_prompt += f"""
- Observed Stability Mechanisms: {', '.join(stability_mechanisms)}"""
        
        if target_properties:
            base_prompt += f"""
- Target Properties: {', '.join([f'{k}: {v}' for k, v in target_properties.items()])}"""
        
        if limitations:
            base_prompt += f"""
- Current Performance Limitations: {', '.join(limitations)}"""

        # Build requirements based on iteration
        if iteration == 1:
            requirements = """
MATERIAL REQUIREMENTS:
1. Demonstrated protein or peptide stabilization capability
2. Thermal stability at temperatures 25-40°C
3. Biocompatible for transdermal application
4. Controlled release properties for drug delivery"""
        
        elif iteration <= 3:
            requirements = """
MATERIAL REQUIREMENTS:
1. Similar stabilization mechanisms to successful candidates
2. Enhanced thermal stability compared to previous materials
3. Improved biocompatibility profiles
4. Optimized release kinetics for insulin delivery"""
            
            if top_candidates:
                requirements += f"""
5. Build upon successful features from: {', '.join(top_candidates[:2])}"""
        
        else:
            requirements = """
MATERIAL REQUIREMENTS:
1. Incorporate proven beneficial structural motifs
2. Address identified performance limitations
3. Target specific property improvements
4. Avoid problematic features from previous iterations"""
            
            if limitations:
                requirements += f"""
5. Specifically avoid: {', '.join(limitations[:2])}"""

        output_format = """
OUTPUT FORMAT: For each material, provide JSON-formatted data:
{
  "material_name": "Name of the material",
  "material_composition": "Chemical composition and formula",
  "chemical_structure": "Structural information (backbone, side chains, crosslinking)",
  "thermal_stability_temp_range": "Temperature stability range",
  "insulin_stability_duration": "Reported insulin stability duration",
  "biocompatibility_data": "Biocompatibility information",
  "release_kinetics": "Release kinetics and delivery mechanism",
  "delivery_efficiency": "Delivery efficiency data",
  "stabilization_mechanism": "Mechanism of protein stabilization",
  "literature_references": ["Reference 1", "Reference 2"],
  "confidence_score": "Score 1-10 based on evidence quality",
  "iteration_relevance": "How this material addresses current iteration goals"
}

Extract information ONLY if supported by the provided papers."""

        return f"{base_prompt}\n\n{requirements}\n\n{output_format}"
    
    def _save_iterative_results(self, results: Dict):
        """Save iterative mining results under session run_dir."""
        if not self.run_dir:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.run_dir / f"literature_iteration_{results['iteration']}_{timestamp}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"💾 Iterative results saved to: {filename}")
    
    def run_active_learning_cycle(self, max_iterations: int = 5,
                                 md_simulator=None,
                                 material_mutator=None,
                                 mutation_size: int = 10) -> List[Dict]:
        """
        Run complete active learning cycle for Milestone 4.

        This orchestrates the full pipeline:
        1. Literature mining (Ollama)
        2. Optional mutation (MaterialMutator; required if material_mutator is set)
            3. MDSimulator evaluation (OpenMM)
        4. Feedback integration
        5. Next iteration

        Args:
            max_iterations (int): Maximum number of learning cycles
            md_simulator: MDSimulator (OpenMM stack installed)
            material_mutator: MaterialMutator instance or True for default mutator
            mutation_size: Number of mutated candidates when material_mutator enabled

        Returns:
            List[Dict]: Results from all iterations
        """
        all_results = []
        feedback_state = {
            "top_candidates": [],
            "stability_mechanisms": [],
            "target_properties": {},
            "limitations": [],
            "high_performer_psmiles": [],
            "problematic_psmiles": [],
        }

        for iteration in range(1, max_iterations + 1):
            print(f"\n{'='*60}")
            print(f"ACTIVE LEARNING CYCLE - ITERATION {iteration}")
            print(f"{'='*60}")

            # Step 1: Literature mining with current feedback
            mining_results = self.mine_with_feedback(
                iteration=iteration,
                top_candidates=feedback_state["top_candidates"],
                stability_mechanisms=feedback_state["stability_mechanisms"],
                target_properties=feedback_state["target_properties"],
                limitations=feedback_state["limitations"]
            )

            candidates = list(mining_results["material_candidates"])

            # Step 2: Cheminformatics mutation (required deps if enabled)
            if material_mutator:
                try:
                    from insulin_ai.mutation import MaterialMutator, feedback_guided_mutation
                except ImportError as e:
                    raise RuntimeError(
                        "material_mutator requested but mutation deps missing (psmiles, etc.): "
                        "install insulin-ai with mutation extras."
                    ) from e
                mutator = material_mutator if isinstance(material_mutator, MaterialMutator) else MaterialMutator()
                if iteration > 1 and feedback_state.get("high_performer_psmiles"):
                    mutated = feedback_guided_mutation(
                        feedback_state, library_size=mutation_size,
                        feedback_fraction=0.7, random_seed=42 + iteration
                    )
                else:
                    mutated = mutator.generate_library(library_size=mutation_size)
                mining_results["generated_candidates"] = mutated
                candidates.extend(mutated)
                print(f"  Added {len(mutated)} mutated candidates")

            mining_results["material_candidates"] = candidates

            # Step 3: Evaluate with MDSimulator (OpenMM)
            if md_simulator:
                md_results = md_simulator.evaluate_candidates(
                    candidates, max_candidates=len(candidates)
                )
                mining_results["md_evaluation"] = md_results

                # Step 4: Update feedback state (with psmiles for mutation)
                feedback_state = self._update_feedback_state(
                    md_results, feedback_state, candidates
                )

            all_results.append(mining_results)

            print(f"Iteration {iteration} complete. Found {len(candidates)} candidates.")
        self._save_complete_cycle_results(all_results)
        return all_results
    
    def _update_feedback_state(
        self, md_results: Dict, current_state: Dict,
        candidates: Optional[List[Dict]] = None,
    ) -> Dict:
        """Update feedback state based on MD simulation results."""
        top = md_results.get("successful_materials", md_results.get("high_performers", []))
        limitations = md_results.get("failed_features", md_results.get("problematic_features", []))

        # Build high_performer_psmiles and problematic_psmiles for mutation
        name_to_psmiles: Dict[str, str] = {}
        if candidates:
            for c in candidates:
                name = c.get("material_name") or c.get("candidate_id")
                psm = c.get("chemical_structure") or c.get("psmiles")
                if name and psm:
                    name_to_psmiles[str(name)] = psm

        high_psmiles = []
        for name in top:
            psm = name_to_psmiles.get(str(name)) or (name if name and ("[*]" in str(name)) else None)
            if psm:
                high_psmiles.append(psm)

        problematic_psmiles = []
        for item in limitations:
            if not isinstance(item, str):
                continue
            if "[*]" in item:
                problematic_psmiles.append(item)
                continue
            # Format: "category:candidate_name" (e.g. "high_interaction_energy:Candidate_0")
            candidate_name = item.split(":", 1)[1] if ":" in item else item
            psm = name_to_psmiles.get(candidate_name)
            if psm:
                problematic_psmiles.append(psm)

        return {
            "top_candidates": top,
            "stability_mechanisms": md_results.get("effective_mechanisms", []),
            "target_properties": md_results.get("target_improvements", {}),
            "limitations": limitations,
            "high_performer_psmiles": high_psmiles,
            "problematic_psmiles": problematic_psmiles,
        }
    
    def _save_complete_cycle_results(self, all_results: List[Dict]):
        """Save complete active learning cycle into session run_dir/complete_cycle.json."""
        if not self.run_dir:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        filename = self.run_dir / "complete_cycle.json"
        cycle_summary = {
            "total_iterations": len(all_results),
            "timestamp": datetime.now().isoformat(),
            "iterations": all_results,
            "performance_progression": self._analyze_performance_progression(all_results),
        }
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(cycle_summary, f, indent=2, ensure_ascii=False)
        print(f"💾 Complete cycle results saved to: {filename}")
    
    def _analyze_performance_progression(self, all_results: List[Dict]) -> Dict:
        """Analyze how performance improved across iterations."""
        progression = {
            "candidate_count_per_iteration": [len(r["material_candidates"]) for r in all_results],
            "query_evolution": [r["search_queries"] for r in all_results],
            "feedback_evolution": [r.get("feedback_metadata", {}) for r in all_results]
        }
        return progression


# Demo: requires Ollama serve + model pulled; set INSULIN_AI_DEMO=1
if __name__ == "__main__":
    if os.environ.get("INSULIN_AI_DEMO") != "1":
        print("Set INSULIN_AI_DEMO=1 and run Ollama to execute this demo.")
        raise SystemExit(0)
    print("MILESTONE 4: Iterative Literature Mining Demo (Ollama required)")
    iterative_miner = IterativeLiteratureMiner()
    
    # Example: Simulated active learning cycle
    print("\n📊 Simulated 3-iteration cycle:")
    
    # Mock MD simulation results for demonstration
    mock_md_results = {
        "high_performers": ["PEG-based hydrogels", "chitosan derivatives"],
        "effective_mechanisms": ["hydrogen bonding", "hydrophobic interactions"],
        "problematic_features": ["high crystallinity", "poor water retention"]
    }
    
    results = iterative_miner.mine_with_feedback(
        iteration=3,
        md_simulation_results=mock_md_results,
        num_candidates=8
    )
    
    print(f"Found {len(results['material_candidates'])} candidates with feedback integration") 