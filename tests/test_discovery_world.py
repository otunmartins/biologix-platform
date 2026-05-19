"""Tests for discovery_world structured session state (Kosmos-style rollup)."""

import json
from pathlib import Path

import pytest

from insulin_ai.discovery_world import (
    SCHEMA_VERSION,
    DEFAULT_WORLD_FILENAME,
    apply_patch,
    empty_world,
    ensure_world_for_session,
    load_world,
    planning_context,
    save_world,
)


def test_empty_world_shape():
    w = empty_world()
    assert w["schema_version"] == SCHEMA_VERSION
    assert w["objective"] == ""
    assert w["literature_entries"] == []
    assert w["simulation_entries"] == []
    assert w["hypotheses"] == []
    assert w["open_questions"] == []
    assert w["human_directives"] == []
    assert "updated_at" in w["meta"]


def test_apply_patch_new_literature_and_hypothesis():
    base = empty_world()
    patch = {
        "objective": "Find patch polymers",
        "literature_entries": [
            {"id": "L1", "title": "Paper A", "claim": "PEG stabilizes", "iteration": 1},
        ],
        "hypotheses": [
            {"id": "H1", "text": "PEG-like backbones help", "supporting_ids": ["L1"], "status": "open"},
        ],
    }
    out = apply_patch(base, patch)
    assert out["objective"] == "Find patch polymers"
    assert len(out["literature_entries"]) == 1
    assert out["literature_entries"][0]["claim"] == "PEG stabilizes"
    assert out["hypotheses"][0]["status"] == "open"


def test_apply_patch_updates_existing_by_id():
    base = empty_world()
    base["literature_entries"] = [
        {"id": "L1", "title": "Old", "claim": "old", "iteration": 1},
    ]
    patch = {"literature_entries": [{"id": "L1", "title": "New", "claim": "revised", "iteration": 2}]}
    out = apply_patch(base, patch)
    assert len(out["literature_entries"]) == 1
    assert out["literature_entries"][0]["claim"] == "revised"
    assert out["literature_entries"][0]["iteration"] == 2


def test_apply_patch_appends_new_ids():
    base = empty_world()
    base["simulation_entries"] = [{"id": "S1", "psmiles": "[*]O[*]", "iteration": 1, "status": "ok"}]
    patch = {
        "simulation_entries": [
            {"id": "S2", "psmiles": "[*]CC[*]", "iteration": 1, "status": "ok", "interaction_energy_kj_mol": -5.0},
        ]
    }
    out = apply_patch(base, patch)
    ids = [x["id"] for x in out["simulation_entries"]]
    assert ids == ["S1", "S2"]


def test_apply_patch_rejects_wrong_schema_in_patch():
    base = empty_world()
    with pytest.raises(ValueError, match="schema_version"):
        apply_patch(base, {"schema_version": 99})


def test_apply_patch_objective_moves_previous_to_history():
    base = empty_world()
    base["objective"] = "First goal"
    out = apply_patch(base, {"objective": "Second goal"})
    assert out["objective"] == "Second goal"
    assert len(out["objective_history"]) >= 1
    assert any("First goal" in str(h) for h in out["objective_history"])


def test_planning_context_includes_sections():
    w = empty_world()
    w["objective"] = "Test objective"
    w["hypotheses"] = [{"id": "H1", "text": "Hyp A", "supporting_ids": [], "status": "open"}]
    w["open_questions"] = [{"id": "Q1", "text": "What about chitosan?", "iteration": 2}]
    w["human_directives"] = [{"id": "D1", "iteration": 2, "text": "Focus hydrogels"}]
    w["literature_entries"] = [{"id": "L1", "title": "T", "claim": "C", "iteration": 1}]
    w["simulation_entries"] = [{"id": "S1", "psmiles": "[*]O[*]", "iteration": 1, "status": "ok"}]
    text = planning_context(w, max_chars=10_000)
    assert "Test objective" in text
    assert "Hyp A" in text
    assert "chitosan" in text
    assert "hydrogels" in text
    assert "PEG" not in text or "[*]O[*]" in text or "L1" in text


def test_planning_context_respects_max_chars():
    w = empty_world()
    w["objective"] = "X" * 5000
    text = planning_context(w, max_chars=100)
    assert len(text) <= 150  # small slack for headers


def test_load_save_roundtrip(tmp_path: Path):
    p = tmp_path / DEFAULT_WORLD_FILENAME
    w = empty_world()
    w["objective"] = "Roundtrip"
    save_world(p, w)
    w2 = load_world(p)
    assert w2["objective"] == "Roundtrip"
    assert w2["schema_version"] == SCHEMA_VERSION


def test_load_world_missing_returns_empty():
    w = load_world(Path("/nonexistent/discovery_world.json"))
    assert w["schema_version"] == SCHEMA_VERSION
    assert w["objective"] == ""


def test_ensure_world_creates_file(tmp_path: Path):
    d = tmp_path / "sess"
    d.mkdir()
    p = d / DEFAULT_WORLD_FILENAME
    assert not p.is_file()
    ensure_world_for_session(d, objective="Hello")
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["objective"] == "Hello"


def test_open_questions_merge_by_id():
    base = empty_world()
    base["open_questions"] = [{"id": "Q1", "text": "a", "iteration": 1}]
    out = apply_patch(base, {"open_questions": [{"id": "Q1", "text": "b", "iteration": 2}]})
    assert len(out["open_questions"]) == 1
    assert out["open_questions"][0]["text"] == "b"


def test_meta_links_merged():
    base = empty_world()
    out = apply_patch(base, {"meta": {"links": {"foo": "bar"}, "last_iteration": 3}})
    assert out["meta"]["last_iteration"] == 3
    assert out["meta"]["links"]["foo"] == "bar"
