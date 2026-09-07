"""Claude-backed reasoning for the scientific pipeline.

The chemistry judgement calls - what a polymer's repeat unit is, how its backbone
disconnects, which precursors are real - are made by the model and then checked by
RDKit, PubChem and the retrosynthesis knowledge graph. Nothing here is trusted
until a deterministic tool has confirmed it.
"""

from typing import Any

from biologix_ai.llm.client import LLMUnavailable, complete_json, llm_available, model_name

__all__ = [
    "LLMUnavailable",
    "OllamaClient",
    "complete_json",
    "llm_available",
    "model_name",
]


def __getattr__(name: str) -> Any:
    """Keep OllamaClient importable without making ollama a hard dependency.

    The scientific pipeline imports this package on every run, and the API image does
    not ship ollama; importing it eagerly here would take the whole platform down.
    """
    if name == "OllamaClient":
        from biologix_ai.llm.ollama_client import OllamaClient

        return OllamaClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
