#!/usr/bin/env python3
"""Lightweight OpenMM stack detection (no heavy imports until simulation runs)."""

from __future__ import annotations

import importlib.util


def _has_package(name: str) -> bool:
    """find_spec can raise ModuleNotFoundError for missing parent namespaces (e.g. openff.toolkit)."""
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def openmm_available() -> bool:
    """
    True if merged-screening dependencies can be imported: OpenMM, openmmforcefields,
    OpenFF Toolkit (for GAFF template generation path used in openmm_complex).
    """
    return (
        _has_package("openmm")
        and _has_package("openmmforcefields")
        and _has_package("openff.toolkit")
    )
