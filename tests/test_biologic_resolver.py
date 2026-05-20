"""Tests for biologic name/PDB resolution (bundled data; no network when fetch disabled)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))

from biologix_ai.services import biologic_resolver as br
from biologix_ai.services.biologics_session import patch_world_retrosynthesis
from biologix_ai.discovery_world import load_world, world_path_for_session, ensure_world_for_session


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lookup_pdb_id_common_names():
    assert br.lookup_pdb_id("insulin") == "4F1C"
    assert br.lookup_pdb_id("  Adalimumab ") == "3WD5"
    assert br.lookup_pdb_id("1n8z") == "1N8Z"


def test_lookup_pdb_id_unknown():
    assert br.lookup_pdb_id("totally_unknown_molecule_xyz") == ""


def test_resolve_uses_bundled_insulin():
    bio = br.resolve_biologic_target("insulin", REPO_ROOT, fetch_pdb=False)
    assert bio.fetch_ok is True
    assert bio.pdb_id == "4F1C"
    assert "4F1C" in bio.pdb_path
    assert Path(bio.pdb_path).is_file()


def test_resolve_unknown_name_no_fetch():
    bio = br.resolve_biologic_target("not_in_lookup_table_xyz", REPO_ROOT, fetch_pdb=False)
    assert "unknown biologic" in " ".join(bio.errors).lower()


def test_resolve_fetch_false_missing_local_uses_errors():
    # Valid 4-letter code, empty isolated cache — no network, no accidental cache hit.
    with tempfile.TemporaryDirectory() as td:
        bio = br.resolve_biologic_target(
            "9ZZ9", REPO_ROOT, fetch_pdb=False, session_dir=None, cache_dir=Path(td)
        )
    assert bio.fetch_ok is False
    assert any("no local" in e.lower() or "fetch" in e.lower() for e in bio.errors)


def test_patch_world_retrosynthesis_merges_entry():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ensure_world_for_session(d, objective="test")
        entry = {"target": "PEG", "iteration": 1, "note": "unit"}
        patch_world_retrosynthesis(d, entry)
        wpath = world_path_for_session(d)
        world = load_world(wpath)
        entries = world.get("retrosynthesis_entries", [])
        assert any(e.get("target") == "PEG" for e in entries)
        merged = next(e for e in entries if e.get("target") == "PEG")
        assert merged.get("id"), "patch_world_retrosynthesis should assign id for apply_patch merge"


def test_write_retrosynthesis_artifact_path():
    from biologix_ai.services.biologics_session import write_retrosynthesis_artifact

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        p = write_retrosynthesis_artifact(d, "x.json", {"a": 1})
        assert p.parent.name == "retrosynthesis"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["a"] == 1
