#!/usr/bin/env python3
"""Lightweight OpenMM stack detection (no heavy imports until simulation runs)."""

from __future__ import annotations

import importlib.util
import shutil


def _has_package(name: str) -> bool:
    """find_spec can raise ModuleNotFoundError for missing parent namespaces (e.g. openff.toolkit)."""
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _ambertools_on_path() -> bool:
    """GAFFTemplateGenerator shells out to antechamber and parmchk2."""
    return shutil.which("antechamber") is not None and shutil.which("parmchk2") is not None


def openmm_available() -> bool:
    """
    True if merged-screening dependencies can be imported: OpenMM, openmmforcefields,
    OpenFF Toolkit, and AmberTools binaries (antechamber/parmchk2 for GAFF templates).
    """
    return (
        _has_package("openmm")
        and _has_package("openmmforcefields")
        and _has_package("openff.toolkit")
        and _ambertools_on_path()
    )
