"""Structured synthesis evidence captured in the UI, rendered for the retrosynthesis engine.

The engine consumes a {source_name: reaction_text} mapping in the RetroSynthesisAgent
llm_res format. Keeping the rendering here means the text format is written in one
place instead of being reimplemented in the browser.
"""

from typing import Any

from biologix_ai.retrosynthesis.retro_adapter import (
    normalize_extractions,
    validate_extractions_for_tree,
)

MAX_SOURCES = 25
MAX_REACTIONS = 40


def _clean(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def render_reaction(index: int, reaction: dict) -> str:
    reactants = _clean(reaction.get("reactants"))
    products = _clean(reaction.get("products"))
    if not reactants or not products:
        raise ValueError("Every reaction needs at least one reactant and one product")
    conditions = str(reaction.get("conditions") or "").strip() or "not specified"
    return (
        f"Reaction {index}:\n"
        f"Reactants: {', '.join(reactants)}\n"
        f"Products: {', '.join(products)}\n"
        f"Conditions: {conditions}"
    )


def render_sources(sources: Any) -> dict[str, str]:
    """Turn the UI's structured sources into the engine's {source: reaction_text} mapping."""
    if not isinstance(sources, list) or not sources:
        raise ValueError("Add at least one literature source")
    if len(sources) > MAX_SOURCES:
        raise ValueError(f"A maximum of {MAX_SOURCES} sources is supported")
    rendered: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must be an object")
        name = str(source.get("name") or "").strip()
        if not name:
            raise ValueError("Every source needs a citation or title")
        reactions = source.get("reactions")
        if not isinstance(reactions, list) or not reactions:
            raise ValueError(f"Source {name!r} needs at least one reaction")
        if len(reactions) > MAX_REACTIONS:
            raise ValueError(f"Source {name!r} exceeds {MAX_REACTIONS} reactions")
        if name in rendered:
            raise ValueError(f"Duplicate source name {name!r}")
        rendered[name] = "\n\n".join(
            render_reaction(index, reaction) for index, reaction in enumerate(reactions, start=1)
        )
    return rendered


def material_name_for(parameters: dict, polymer_target: str | None) -> str:
    """Mirror the worker's target choice so pre-flight checks match the real run."""
    return str(
        (parameters or {}).get("retrosynthesis_material_name")
        or polymer_target
        or ""
    ).strip()


def preflight(material_name: str, sources: Any) -> dict:
    """Validate structured evidence against the real engine checks before an experiment runs."""
    if not material_name:
        raise ValueError("A polymer target is required before synthesis evidence can be checked")
    extractions = normalize_extractions(render_sources(sources))
    report = validate_extractions_for_tree(extractions, material_name)
    report["extractions"] = extractions
    return report


def apply_to_parameters(parameters: dict, polymer_target: str | None) -> dict:
    """Render retrosynthesis_sources into the engine payload the worker already reads."""
    parameters = dict(parameters or {})
    sources = parameters.get("retrosynthesis_sources")
    if not sources:
        return parameters
    material_name = material_name_for(parameters, polymer_target)
    if not material_name:
        raise ValueError("A polymer target is required to attach synthesis evidence")
    report = preflight(material_name, sources)
    if not report["root_product_found"]:
        raise ValueError(
            f"No reaction produces {material_name!r}. At least one reaction must list it as a product, "
            "otherwise the retrosynthesis tree has no root."
        )
    parameters["retrosynthesis_extractions"] = report["extractions"]
    return parameters
