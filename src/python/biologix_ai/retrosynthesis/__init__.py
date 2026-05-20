"""
Retrosynthesis planning for polymer excipients.

Two-engine architecture:
- RetroSynthesisAgent for macromolecular polymer routes (literature-driven, LLM + KG)
- AiZynthFinder for small-molecule monomer routes (template-based, MCTS)
"""
