"""Tests for deep validation of baked scientific databases and models."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from scripts.verify_scientific_assets import (
    MIN_MOLPORT_INCHIKEYS,
    MIN_PRECURSOR_ENTRIES,
    MIN_TEMPLATE_ROWS,
    MIN_ZINC_INCHIKEYS,
    ScientificAssetError,
    audit_scientific_assets,
)


def _populate_asset_fixture(app_root: Path) -> None:
    data_dir = app_root / "data" / "aizynthfinder"
    data_dir.mkdir(parents=True)
    config = f"""\
expansion:
  uspto:
    - {data_dir / 'uspto_model.onnx'}
    - {data_dir / 'uspto_templates.csv.gz'}
  ringbreaker:
    - {data_dir / 'uspto_ringbreaker_model.onnx'}
    - {data_dir / 'uspto_ringbreaker_templates.csv.gz'}
filter:
  uspto: {data_dir / 'uspto_filter_model.onnx'}
stock:
  zinc: {data_dir / 'zinc_stock.hdf5'}
"""
    (data_dir / "config.yml").write_text(config, encoding="utf-8")
    for model_name in (
        "uspto_model.onnx",
        "uspto_ringbreaker_model.onnx",
        "uspto_filter_model.onnx",
    ):
        (data_dir / model_name).write_bytes(b"onnx-model")
    for template_name in (
        "uspto_templates.csv.gz",
        "uspto_ringbreaker_templates.csv.gz",
    ):
        with gzip.open(data_dir / template_name, "wt", encoding="utf-8") as stream:
            stream.write("index,smarts\n")
            for index in range(MIN_TEMPLATE_ROWS):
                stream.write(f"{index},[C:1]>>[C:1]\n")
    (data_dir / "zinc_stock.hdf5").write_bytes(b"hdf5")

    retro_dir = app_root / "data" / "retrosynthesis"
    retro_dir.mkdir(parents=True)
    manual_count = 250
    smipoly_count = MIN_PRECURSOR_ENTRIES - manual_count
    entries = [
        {
            "name": f"manual-{index}",
            "aliases": [],
            "smiles": "CC",
            "source": "manual",
        }
        for index in range(manual_count)
    ]
    entries.extend(
        {
            "name": f"smipoly-{index}",
            "aliases": [],
            "smiles": "CCC",
            "source": "smipoly",
        }
        for index in range(smipoly_count)
    )
    (retro_dir / "precursors.json").write_text(
        json.dumps({"entries": entries}),
        encoding="utf-8",
    )
    (retro_dir / "molport_inchikeys.pkl").write_bytes(b"pickle")
    (retro_dir / "emol.json").write_text("[]", encoding="utf-8")
    external_emol = app_root / "extern" / "RetroSynthesisAgent" / "RetroSynAgent"
    external_emol.mkdir(parents=True)
    (external_emol / "emol.json").write_text("[]", encoding="utf-8")

    for relative_path in (
        "src/python/biologix_ai/simulation/data/4F1C.pdb",
        "src/python/biologix_ai/simulation/data/insulin_AB.pdb",
        "data/biologics/biologic_1BUY.pdb",
        "data/biologics/biologic_3WD5.pdb",
    ):
        pdb_path = app_root / relative_path
        pdb_path.parent.mkdir(parents=True, exist_ok=True)
        pdb_path.write_text(
            "".join(
                f"ATOM  {index:5d}  CA  ALA A{index:4d}    0.000   0.000   0.000\n"
                for index in range(1, 102)
            ),
            encoding="utf-8",
        )


def test_scientific_asset_audit_validates_database_contents(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _populate_asset_fixture(app_root)
    inspected_models: list[str] = []

    report = audit_scientific_assets(
        app_root=app_root,
        onnx_inspector=lambda path: inspected_models.append(path.name),
        hdf_row_counter=lambda _path: MIN_ZINC_INCHIKEYS,
        pickle_entry_counter=lambda _path: MIN_MOLPORT_INCHIKEYS,
    )

    assert sorted(inspected_models) == [
        "uspto_filter_model.onnx",
        "uspto_model.onnx",
        "uspto_ringbreaker_model.onnx",
    ]
    assert report.precursor_entries == MIN_PRECURSOR_ENTRIES
    assert report.molport_inchikeys == MIN_MOLPORT_INCHIKEYS
    assert report.zinc_inchikeys == MIN_ZINC_INCHIKEYS
    assert report.onnx_models == 3
    assert report.pdb_structures == 4


def test_scientific_asset_audit_rejects_truncated_databases(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    _populate_asset_fixture(app_root)

    with pytest.raises(ScientificAssetError) as error_info:
        audit_scientific_assets(
            app_root=app_root,
            onnx_inspector=lambda _path: None,
            hdf_row_counter=lambda _path: MIN_ZINC_INCHIKEYS - 1,
            pickle_entry_counter=lambda _path: MIN_MOLPORT_INCHIKEYS - 1,
        )

    message = str(error_info.value)
    assert "ZINC stock" in message
    assert "Molport database" in message
