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


def test_force_field_check_uses_the_simulation_ph():
    import inspect

    from biologix_ai.simulation.openmm_complex import PROTONATION_PH

    source = inspect.getsource(br.force_field_check)
    assert "PROTONATION_PH" in source
    assert "pH=PROTONATION_PH" in source
    target = br.BiologicTarget(query="insulin", protonation_ph=PROTONATION_PH)
    assert target.protonation_ph == 7.0


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


# -- any-biologic resolver: input grammar, search, sources, preparation ----------

INSULIN_AB = REPO_ROOT / "src" / "python" / "biologix_ai" / "simulation" / "data" / "insulin_AB.pdb"


@pytest.mark.parametrize(
    "text,kind,value,chains,rng",
    [
        ("4ZGM:B", "pdb", "4ZGM", ["B"], None),
        ("1n8z:A,B", "pdb", "1N8Z", ["A", "B"], None),
        ("1HZH", "pdb", "1HZH", [], None),
        ("uniprot:P01308", "uniprot", "P01308", [], None),
        ("P01275:98-127", "uniprot", "P01275", [], (98, 127)),
        ("sequence:haeg tfts", "sequence", "HAEGTFTS", [], None),
        ("semaglutide", "name", "semaglutide", [], None),
        ("human growth hormone", "name", "human growth hormone", [], None),
    ],
)
def test_parse_target_query(text, kind, value, chains, rng):
    parsed = br.parse_target_query(text)
    assert (parsed.kind, parsed.value, parsed.chains, parsed.residue_range) == (kind, value, chains, rng)


def test_curated_semaglutide_and_corrected_antibodies():
    assert br.lookup_pdb_id("semaglutide") == "4ZGM"
    assert br._CURATED_TARGETS["semaglutide"] == "4ZGM:B"
    # These three previously pointed at EGFR kinase, a bacterial protein and tankyrase.
    assert br.lookup_pdb_id("infliximab") == "5VH3"
    assert br.lookup_pdb_id("omalizumab") == "2XA8"
    assert "etanercept" not in br._CURATED_TARGETS


class _FakeRcsb:
    """Offline stand-in for ``biologic_resolver._http``."""

    def __init__(self, entity_hits, entries, fulltext_hits=()):
        self.entity_hits = entity_hits
        self.entries = entries  # {pdb_id: [(description, "A,C"), ...]}
        self.fulltext_hits = list(fulltext_hits)
        self.urls = []

    def __call__(self, method, url, *, json_body=None, data=None, expect="json", timeout=0):
        self.urls.append(url)
        if url == br.RCSB_SEARCH:
            if json_body["return_type"] == "polymer_entity":
                hits = [{"identifier": h} for h in self.entity_hits]
            else:
                hits = [{"identifier": h} for h in self.fulltext_hits]
            return {"result_set": hits} if hits else None
        for pdb_id, entities in self.entries.items():
            if url == br.RCSB_ENTRY.format(pdb_id=pdb_id):
                ids = [str(i + 1) for i in range(len(entities))]
                return {"rcsb_entry_container_identifiers": {"polymer_entity_ids": ids}}
            for i, (description, strands) in enumerate(entities):
                if url == br.RCSB_ENTITY.format(pdb_id=pdb_id, entity_id=str(i + 1)):
                    return {
                        "entity_poly": {"type": "polypeptide(L)", "pdbx_strand_id": strands},
                        "rcsb_polymer_entity": {"pdbx_description": description},
                    }
        raise br.ResolutionError(f"unexpected URL {url}")


def test_name_search_selects_matching_entity_one_chain(monkeypatch):
    fake = _FakeRcsb(
        ["4ZGM_2"],
        {"4ZGM": [("Glucagon-like peptide 1 receptor", "A"), ("Semaglutide peptide backbone", "B,C")]},
    )
    monkeypatch.setattr(br, "_http", fake)
    pdb_id, first, all_chains, _candidates, warnings = br.select_entities_by_name("semaglutide")
    assert (pdb_id, first, all_chains, warnings) == ("4ZGM", ["B"], ["B", "C"], [])


def test_name_search_ranks_biologic_above_its_receptor(monkeypatch):
    fake = _FakeRcsb(
        ["3C59_1", "1BH0_1"],
        {"3C59": [("Glucagon receptor", "A")], "1BH0": [("Glucagon", "A")]},
    )
    monkeypatch.setattr(br, "_http", fake)
    pdb_id, first, _all, _c, warnings = br.select_entities_by_name("glucagon")
    assert pdb_id == "1BH0" and first == ["A"] and warnings == []


def test_name_search_no_match_falls_back_with_warning(monkeypatch):
    fake = _FakeRcsb([], {"7ABC": [("Hypothetical protein", "A"), ("Other protein", "B")]}, ["7ABC"])
    monkeypatch.setattr(br, "_http", fake)
    pdb_id, first, _all, _c, warnings = br.select_entities_by_name("mysteryzumab")
    assert pdb_id == "7ABC" and first == ["A", "B"]
    assert warnings and "pass PDB:chains explicitly" in warnings[0]


def test_unknown_name_without_fetch_asks_model_to_retry(tmp_path):
    bio = br.resolve_biologic_target("mysteryzumab", REPO_ROOT, fetch_pdb=False, cache_dir=tmp_path)
    assert bio.fetch_ok is False
    text = " ".join(bio.errors)
    assert "unknown biologic" in text.lower()
    assert "PDB:chains" in text and "Do not ask the user" in text


def test_uniprot_and_sequence_routes_build_expected_urls(monkeypatch, tmp_path):
    calls = []
    peptide = INSULIN_AB.read_bytes()

    def fake_http(method, url, **kwargs):
        calls.append((method, url, kwargs.get("data")))
        if url == br.ALPHAFOLD_PREDICTION.format(accession="P01308"):
            return [{"entryId": "AF-P01308-F1", "pdbUrl": "https://alphafold.example/AF-P01308-F1.pdb"}]
        if url.endswith(".pdb") or url == br.ESMFOLD_API:
            return peptide if kwargs.get("expect") == "bytes" else peptide.decode()
        raise br.ResolutionError(url)

    def fake_prepare(source, keep, dest):
        return br.PreparedStructure(dest, ["A"], {"A": "G"}, [], [], [], 1, 1.0, [])

    def fake_finish(target):
        target.fetch_ok, target.ff_check = True, "passed"

    monkeypatch.setattr(br, "_http", fake_http)
    monkeypatch.setattr(br, "prepare_structure", fake_prepare)
    monkeypatch.setattr(br, "_finish", fake_finish)
    monkeypatch.setattr(br, "_write_target", lambda d, t, p: t)

    uni = br.resolve_biologic_target("uniprot:P01308", REPO_ROOT, cache_dir=tmp_path)
    assert uni.source == "alphafold" and uni.source_id == "AF-P01308-F1"
    assert uni.resolved_target == "uniprot:P01308"
    seq = br.resolve_biologic_target("sequence:HAEGTFTSDV", REPO_ROOT, cache_dir=tmp_path)
    assert seq.source == "esmfold" and seq.resolved_target == "sequence:HAEGTFTSDV"
    assert ("POST", br.ESMFOLD_API, "HAEGTFTSDV") in calls
    assert any(url == br.ALPHAFOLD_PREDICTION.format(accession="P01308") for _, url, _ in calls)


def test_esmfold_length_limit(tmp_path):
    bio = br.resolve_biologic_target("sequence:" + "A" * 401, REPO_ROOT, cache_dir=tmp_path)
    assert bio.fetch_ok is False
    assert "at most 400" in " ".join(bio.errors)


def test_mmcif_label_to_auth(tmp_path):
    pytest.importorskip("openmm")
    cif = tmp_path / "x.cif"
    cif.write_text(
        "data_X\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.label_asym_id\n"
        "_atom_site.auth_asym_id\nATOM 1 A B\nHETATM 2 C B\nHETATM 3 D A\n",
        encoding="utf-8",
    )
    assert br._mmcif_label_to_auth(cif) == {"A": "B", "C": "B", "D": "A"}


def _insulin_fixture_with_aib_and_heterogens(path: Path) -> Path:
    """insulin_AB with Ala B14 written as AIB, plus a zinc ion and two waters."""
    lines = []
    for line in INSULIN_AB.read_text().splitlines():
        if line.startswith("ATOM") and line[17:20] == "ALA" and line[21] == "B" and line[22:26].strip() == "14":
            name = "CB1" if line[12:16].strip() == "CB" else line[12:16].strip()
            line = "HETATM" + line[6:12] + f" {name:<3s}" + " AIB" + line[20:]
        lines.append(line)
    lines += [
        "HETATM 9001 ZN    ZN B 101      10.000  10.000  10.000  1.00  0.00          ZN",
        "HETATM 9002  O   HOH A 201      12.000  12.000  12.000  1.00  0.00           O",
        "HETATM 9003  O   HOH B 202      14.000  12.000  12.000  1.00  0.00           O",
        "END",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_prepare_structure_records_substitution_heterogens_and_disulfides(tmp_path):
    pytest.importorskip("pdbfixer")
    pytest.importorskip("openmm")
    source = _insulin_fixture_with_aib_and_heterogens(tmp_path / "fixture.pdb")
    prepared = br.prepare_structure(source, ["A", "B"], tmp_path / "prepared.pdb")
    assert prepared.chains == ["A", "B"]
    assert {"chain": "B", "residue": "AIB", "replaced_by": "ALA"}.items() <= prepared.modifications[0].items()
    removed = {(h["chain"], h["residue"]) for h in prepared.removed_heterogens}
    assert {("B", "ZN"), ("A", "HOH"), ("B", "HOH")} <= removed
    text = prepared.path.read_text()
    assert text.count("SSBOND") == 3
    assert " AIB " not in text and " ZN " not in text

    n_atoms, error = br.force_field_check(prepared.path)
    assert error == "" and n_atoms > 700


def test_prepare_structure_keeps_selected_chain_only(tmp_path):
    pytest.importorskip("pdbfixer")
    source = _insulin_fixture_with_aib_and_heterogens(tmp_path / "fixture.pdb")
    prepared = br.prepare_structure(source, ["B"], tmp_path / "prepared_b.pdb")
    assert prepared.chains == ["B"]
    assert "SSBOND" not in prepared.path.read_text()  # insulin B has no intra-chain disulfide
    with pytest.raises(br.ResolutionError, match="not in"):
        br.prepare_structure(source, ["Z"], tmp_path / "bad.pdb")


def test_force_field_check_reports_unmatched_residue(tmp_path):
    pytest.importorskip("openmm")
    bad = tmp_path / "bad.pdb"
    bad.write_text(
        "ATOM      1  N   XYZ A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  XYZ A   1       1.458   0.000   0.000  1.00  0.00           C\nEND\n"
    )
    n_atoms, error = br.force_field_check(bad)
    assert n_atoms == 0 and "AMBER14 cannot parameterize" in error


def test_bundled_insulin_keeps_original_path_and_chains(tmp_path):
    bio = br.resolve_biologic_target("insulin", REPO_ROOT, fetch_pdb=False, session_dir=tmp_path)
    assert bio.resolved_target == "4F1C:A,B" and bio.chains == ["A", "B"]
    assert bio.pdb_path.endswith("simulation/data/4F1C.pdb")
    meta = json.loads((tmp_path / "structures" / "biologic_target.json").read_text())
    assert meta["resolved_target"] == "4F1C:A,B"
    assert br.load_session_target(tmp_path).pdb_path == bio.pdb_path
