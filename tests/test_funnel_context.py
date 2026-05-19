"""Tests for funnel context checkpoint service."""

from __future__ import annotations

import json
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))

from insulin_ai.services.funnel_context import (
    save_funnel_context,
    get_funnel_context,
    list_funnel_stages,
)


def test_save_and_retrieve_by_stage(tmp_path):
    data = {"top_candidates": ["[*]OCC[*]"], "scores": [42.0]}
    save_funnel_context("post_screening", data, tmp_path)
    cp = get_funnel_context(tmp_path, stage="post_screening")
    assert cp is not None
    assert cp["stage"] == "post_screening"
    assert cp["data"]["top_candidates"] == ["[*]OCC[*]"]


def test_get_latest_returns_last(tmp_path):
    save_funnel_context("post_screening", {"step": 1}, tmp_path)
    save_funnel_context("post_retro", {"step": 2}, tmp_path)
    cp = get_funnel_context(tmp_path)  # latest
    assert cp["stage"] == "post_retro"


def test_missing_stage_returns_none(tmp_path):
    save_funnel_context("post_screening", {"x": 1}, tmp_path)
    cp = get_funnel_context(tmp_path, stage="nonexistent_stage")
    assert cp is None


def test_empty_session_returns_none(tmp_path):
    assert get_funnel_context(tmp_path) is None


def test_overwrite_same_stage(tmp_path):
    save_funnel_context("post_retro", {"v": 1}, tmp_path)
    save_funnel_context("post_retro", {"v": 2}, tmp_path)
    cp = get_funnel_context(tmp_path, stage="post_retro")
    assert cp["data"]["v"] == 2


def test_list_stages(tmp_path):
    save_funnel_context("post_screening", {}, tmp_path)
    save_funnel_context("post_retro", {}, tmp_path)
    stages = list_funnel_stages(tmp_path)
    names = [s["stage"] for s in stages]
    assert "post_screening" in names
    assert "post_retro" in names


def test_checkpoint_file_on_disk(tmp_path):
    save_funnel_context("post_compile", {"done": True}, tmp_path)
    cp_file = tmp_path / "checkpoints" / "post_compile.json"
    assert cp_file.is_file()
    payload = json.loads(cp_file.read_text())
    assert payload["data"]["done"] is True
