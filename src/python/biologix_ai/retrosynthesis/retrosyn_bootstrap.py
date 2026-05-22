"""Bootstrap RetroSynthesisAgent data files and patch CWD-relative emol.json lookup."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Set

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False

_BUILTIN_POLYMERS = [
    # Commodity polymers: kept as polymer-name roots so they are valid tree targets,
    # not purchasable leaves. Monomers/precursors live in precursors.json.
    "Polyethylene",
    "Polypropylene",
    "Polystyrene",
    "Polyvinyl chloride",
    "Polyethylene terephthalate",
    "Polytetrafluoroethylene",
    "Polycarbonate",
    "Poly(methyl methacrylate)",
    "Polyurethane",
    "Polyamide",
    "Polyvinyl acetate",
    "Polybutadiene",
    "Polychloroprene",
    "Poly(acrylonitrile-butadiene-styrene)",
    "Polyoxymethylene",
    "Polylactic acid",
    "Polyethylene glycol",
    "Poly(vinyl alcohol)",
    "Polyacrylamide",
    "Polyethylene oxide",
    "Poly(ethylene-co-vinyl acetate)",
    # Biologics-relevant additions
    "Poly(lactic-co-glycolic acid)",
    "Polycaprolactone",
    "Poly(N-isopropylacrylamide)",
    "Chitosan",
    "Poly(acrylic acid)",
    "Poly(2-ethyl-2-oxazoline)",
    "Polyglycolic acid",
    "Poly(caprolactone)",
    "Poly(ethylene glycol)",
    "Poly(propylene glycol)",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _bundled_emol_path() -> Path:
    return _repo_root() / "data" / "retrosynthesis" / "emol.json"


def ensure_retrosyn_agent_ready() -> Path:
    """Copy bundled emol.json and patch CommonSubstanceDB to use package-relative paths.

    Idempotent; safe to call before every Tree build.
    Returns path to emol.json in the RetroSynAgent package directory.

    The patch extends ``get_added_database`` with:
    - All names + aliases + SMILES from ``data/retrosynthesis/precursors.json``
    - Session-seeded names from ``precursor_registry._workspace_precursors``
    """
    global _BOOTSTRAPPED

    import RetroSynAgent
    from RetroSynAgent import treeBuilder as tb

    pkg_dir = Path(RetroSynAgent.__file__).resolve().parent
    emol_dest = pkg_dir / "emol.json"
    bundled = _bundled_emol_path()

    if not emol_dest.is_file():
        if bundled.is_file():
            emol_dest.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            emol_dest.write_text("[]", encoding="utf-8")
        logger.info("Installed RetroSynAgent emol.json at %s", emol_dest)

    if not _BOOTSTRAPPED:

        def _patched_get_added_database(self) -> Set[str]:  # noqa: ANN001
            polymers_lower = {p.lower() for p in _BUILTIN_POLYMERS}
            try:
                emol_list = tb.CommonSubstanceDB.read_data_from_json(str(emol_dest))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                emol_list = []

            base: Set[str] = (
                set(emol_list)
                | polymers_lower
                | {"2-chlorotrifluoromethylbenzene"}
            )

            # Bundled precursors.json
            try:
                from biologix_ai.retrosynthesis.precursor_registry import (
                    get_bundled_precursors,
                    get_workspace_precursors,
                )

                base |= get_bundled_precursors()
                base |= get_workspace_precursors()
            except Exception as exc:
                logger.debug("precursor_registry not available: %s", exc)

            return base

        tb.CommonSubstanceDB.get_added_database = _patched_get_added_database  # type: ignore[method-assign]
        _BOOTSTRAPPED = True

    return emol_dest
