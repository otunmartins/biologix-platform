"""Skip marks for dependencies that only exist in the built image.

The platform spans three environments: ``biologix-ai-sim`` (OpenMM, RDKit),
``biologix-admet`` (ADMET-AI, a newer RDKit), and the ``extern/`` submodules
(RetroSynthesisAgent, AiZynthFinder). A developer checkout usually has only the
first, so tests that need the others must skip rather than fail: a suite that is
permanently red reports nothing.

Those paths are still covered where the dependencies exist. The image build runs
``scripts/verify_modal_image.py``, which imports every required module and runs
``verify_retrosynthesis_stack.py`` (a real RetroSynAgent route plus an
AiZynthFinder monomer search) and an ADMET-AI prediction. ``modal run
modal_app.py::verify_runtime`` runs the same checks against a deployed image.
"""

from __future__ import annotations

import importlib.util

import pytest


def module_available(name: str) -> bool:
    """True when *name* can be imported (parent namespaces may be missing)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def admet_available() -> bool:
    """ADMET-AI runs in its own environment via BIOLOGIX_ADMET_PYTHON."""
    try:
        from biologix_ai.services.toxicity_service import _is_admet_available

        return bool(_is_admet_available())
    except Exception:
        return False


HAS_FASTAPI = module_available("fastapi")
HAS_RETROSYN = module_available("RetroSynAgent")
HAS_ADMET = admet_available()

requires_fastapi = pytest.mark.skipif(
    not HAS_FASTAPI, reason="fastapi not installed (pip install -e '.[api]')"
)
requires_retrosyn = pytest.mark.skipif(
    not HAS_RETROSYN,
    reason="RetroSynthesisAgent submodule not installed (scripts/install_submodules.sh); "
    "covered in the image by scripts/verify_retrosynthesis_stack.py",
)
requires_admet = pytest.mark.skipif(
    not HAS_ADMET,
    reason="ADMET-AI environment not installed (scripts/install_submodules.sh); "
    "covered in the image by scripts/verify_modal_image.py",
)
