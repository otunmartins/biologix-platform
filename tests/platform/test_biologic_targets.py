"""Biologic target resolution.

lookup_pdb_id only knew a fixed list of names and returned "" for anything else,
so the physics stage had no structure to work with. Lysozyme, shipped as an
example in the experiment form, was one of the names it did not know.
"""

import pytest

from biologix_ai.services import biologic_resolver
from biologix_ai.services.biologic_resolver import lookup_pdb_id


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Curated lookups must not need the network; search is exercised explicitly."""
    def explode(*args, **kwargs):
        raise AssertionError("curated lookup should not hit the network")
    monkeypatch.setattr(biologic_resolver.requests, "post", explode)
    biologic_resolver._search_cache.clear()


@pytest.mark.parametrize("name,expected", [
    ("Human insulin", "4F1C"),
    ("Lysozyme", "1LYZ"),
    ("lysozyme", "1LYZ"),
    ("Hen egg white lysozyme", "1LYZ"),
    ("human serum albumin", "1AO6"),
    ("Adalimumab", "3WD5"),
    ("erythropoietin", "1BUY"),
])
def test_curated_names_resolve_offline(name, expected):
    assert lookup_pdb_id(name) == expected


def test_every_example_in_the_experiment_form_resolves():
    for name in ("Human insulin", "Adalimumab", "Lysozyme"):
        assert lookup_pdb_id(name), f"{name} is offered as an example but does not resolve"


def test_a_pdb_code_passes_through():
    assert lookup_pdb_id("4f1c") == "4F1C"


def test_blank_input_resolves_to_nothing():
    assert lookup_pdb_id("   ") == ""


def test_search_is_not_used_when_disabled():
    assert lookup_pdb_id("some unknown protein", allow_search=False) == ""


def test_unknown_names_fall_back_to_search(monkeypatch):
    class _Resp:
        ok = True
        @staticmethod
        def json():
            return {"result_set": [{"identifier": "4xix", "score": 1.0}]}

    monkeypatch.setattr(biologic_resolver.requests, "post", lambda *a, **k: _Resp())
    assert lookup_pdb_id("carbonic anhydrase II") == "4XIX"


def test_search_failures_degrade_to_empty(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(biologic_resolver.requests, "post", boom)
    assert lookup_pdb_id("something obscure") == ""


def test_search_results_are_cached(monkeypatch):
    calls = []

    class _Resp:
        ok = True
        @staticmethod
        def json():
            return {"result_set": [{"identifier": "1ABC"}]}

    def counted(*args, **kwargs):
        calls.append(1)
        return _Resp()

    monkeypatch.setattr(biologic_resolver.requests, "post", counted)
    assert lookup_pdb_id("repeated query") == "1ABC"
    assert lookup_pdb_id("repeated query") == "1ABC"
    assert len(calls) == 1
