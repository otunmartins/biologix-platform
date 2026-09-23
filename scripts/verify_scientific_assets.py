#!/usr/bin/env python3
"""Deeply validate scientific models and retrosynthesis databases in an image."""

from __future__ import annotations

import gzip
import importlib
import json
import pickle
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MIN_TEMPLATE_ROWS = 1_000
MIN_PRECURSOR_ENTRIES = 900
MIN_MANUAL_PRECURSORS = 200
MIN_SMIPOLY_PRECURSORS = 500
MIN_MOLPORT_INCHIKEYS = 1_000_000
MIN_ZINC_INCHIKEYS = 1_000_000
MIN_PDB_ATOMS = 100

MODEL_FILES = (
    "uspto_model.onnx",
    "uspto_ringbreaker_model.onnx",
    "uspto_filter_model.onnx",
)
TEMPLATE_FILES = (
    "uspto_templates.csv.gz",
    "uspto_ringbreaker_templates.csv.gz",
)
PDB_FILES = (
    "src/python/biologix_ai/simulation/data/4F1C.pdb",
    "src/python/biologix_ai/simulation/data/insulin_AB.pdb",
    "data/biologics/biologic_1BUY.pdb",
    "data/biologics/biologic_3WD5.pdb",
)


class ScientificAssetError(RuntimeError):
    """Raised when baked scientific assets are missing, truncated, or unreadable."""


@dataclass(frozen=True)
class ScientificAssetReport:
    """Validated record and model counts for the baked scientific assets."""

    onnx_models: int
    template_rows: int
    precursor_entries: int
    manual_precursors: int
    smipoly_precursors: int
    molport_inchikeys: int
    zinc_inchikeys: int
    pdb_structures: int


def _inspect_onnx(path: Path) -> None:
    """Load one ONNX graph and require a usable input and output signature."""
    onnxruntime = importlib.import_module("onnxruntime")
    session = onnxruntime.InferenceSession(
        str(path),
        providers=["CPUExecutionProvider"],
    )
    if not session.get_inputs() or not session.get_outputs():
        raise ValueError("model has no input or output signature")


def _count_hdf_rows(path: Path) -> int:
    """Return the row count from the AiZynthFinder Pandas HDF stock."""
    h5py = importlib.import_module("h5py")
    with h5py.File(str(path), "r") as handle:
        return int(handle["table"]["axis1"].shape[0])


def _count_pickle_entries(path: Path) -> int:
    """Load the Molport InChIKey collection and return its cardinality."""
    with path.open("rb") as stream:
        entries = pickle.load(stream)
    if not isinstance(entries, (set, frozenset)):
        raise TypeError(f"expected set or frozenset, got {type(entries).__name__}")
    return len(entries)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load one YAML mapping without coupling the audit module to PyYAML at import."""
    yaml = importlib.import_module("yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("configuration root is not a mapping")
    return payload


def _count_gzip_rows(path: Path) -> int:
    """Count non-header records in a gzip-compressed template table."""
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as stream:
        row_count = sum(1 for _line in stream)
    return max(0, row_count - 1)


def _count_pdb_atoms(path: Path) -> int:
    """Count coordinate records in a bundled PDB structure."""
    return sum(
        line.startswith(("ATOM  ", "HETATM"))
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
    )


def audit_scientific_assets(
    app_root: Path = Path("/app"),
    onnx_inspector: Callable[[Path], None] = _inspect_onnx,
    hdf_row_counter: Callable[[Path], int] = _count_hdf_rows,
    pickle_entry_counter: Callable[[Path], int] = _count_pickle_entries,
    config_loader: Callable[[Path], dict[str, Any]] = _load_yaml,
) -> ScientificAssetReport:
    """Validate model readability, database cardinality, wiring, and PDB contents."""
    failures: list[str] = []
    aizynth_dir = app_root / "data" / "aizynthfinder"
    config_path = aizynth_dir / "config.yml"

    expected_config = {
        ("expansion", "uspto", 0): str(aizynth_dir / "uspto_model.onnx"),
        ("expansion", "uspto", 1): str(aizynth_dir / "uspto_templates.csv.gz"),
        ("expansion", "ringbreaker", 0): str(
            aizynth_dir / "uspto_ringbreaker_model.onnx"
        ),
        ("expansion", "ringbreaker", 1): str(
            aizynth_dir / "uspto_ringbreaker_templates.csv.gz"
        ),
        ("filter", "uspto"): str(aizynth_dir / "uspto_filter_model.onnx"),
        ("stock", "zinc"): str(aizynth_dir / "zinc_stock.hdf5"),
    }
    try:
        config = config_loader(config_path)
        for key_path, expected_value in expected_config.items():
            value: Any = config
            for key in key_path:
                value = value[key]
            if value != expected_value:
                failures.append(
                    f"AiZynth config {key_path}: {value!r}; require {expected_value!r}"
                )
    except Exception as error:
        failures.append(f"AiZynth config unreadable: {type(error).__name__}: {error}")

    loaded_models = 0
    for model_name in MODEL_FILES:
        model_path = aizynth_dir / model_name
        try:
            onnx_inspector(model_path)
            loaded_models += 1
        except Exception as error:
            failures.append(
                f"ONNX model {model_name}: {type(error).__name__}: {error}"
            )

    total_template_rows = 0
    for template_name in TEMPLATE_FILES:
        template_path = aizynth_dir / template_name
        try:
            rows = _count_gzip_rows(template_path)
            total_template_rows += rows
            if rows < MIN_TEMPLATE_ROWS:
                failures.append(
                    f"template {template_name}: {rows} rows; need {MIN_TEMPLATE_ROWS}"
                )
        except Exception as error:
            failures.append(
                f"template {template_name}: {type(error).__name__}: {error}"
            )

    zinc_rows = 0
    zinc_path = aizynth_dir / "zinc_stock.hdf5"
    try:
        zinc_rows = hdf_row_counter(zinc_path)
        if zinc_rows < MIN_ZINC_INCHIKEYS:
            failures.append(
                f"ZINC stock: {zinc_rows} InChIKeys; need {MIN_ZINC_INCHIKEYS}"
            )
    except Exception as error:
        failures.append(f"ZINC stock unreadable: {type(error).__name__}: {error}")

    retro_dir = app_root / "data" / "retrosynthesis"
    precursor_count = 0
    source_counts: Counter[str] = Counter()
    try:
        precursor_payload = json.loads(
            (retro_dir / "precursors.json").read_text(encoding="utf-8")
        )
        entries = precursor_payload["entries"]
        if not isinstance(entries, list):
            raise TypeError("entries is not a list")
        precursor_count = len(entries)
        source_counts.update(str(entry.get("source", "")) for entry in entries)
        if precursor_count < MIN_PRECURSOR_ENTRIES:
            failures.append(
                f"precursor database: {precursor_count} entries; "
                f"need {MIN_PRECURSOR_ENTRIES}"
            )
        if source_counts["manual"] < MIN_MANUAL_PRECURSORS:
            failures.append(
                f"manual precursor tier: {source_counts['manual']} entries; "
                f"need {MIN_MANUAL_PRECURSORS}"
            )
        if source_counts["smipoly"] < MIN_SMIPOLY_PRECURSORS:
            failures.append(
                f"SMiPoly precursor tier: {source_counts['smipoly']} entries; "
                f"need {MIN_SMIPOLY_PRECURSORS}"
            )
    except Exception as error:
        failures.append(
            f"precursor database unreadable: {type(error).__name__}: {error}"
        )

    molport_entries = 0
    try:
        molport_entries = pickle_entry_counter(retro_dir / "molport_inchikeys.pkl")
        if molport_entries < MIN_MOLPORT_INCHIKEYS:
            failures.append(
                f"Molport database: {molport_entries} InChIKeys; "
                f"need {MIN_MOLPORT_INCHIKEYS}"
            )
    except Exception as error:
        failures.append(
            f"Molport database unreadable: {type(error).__name__}: {error}"
        )

    try:
        bundled_emol = json.loads((retro_dir / "emol.json").read_text(encoding="utf-8"))
        external_emol = json.loads(
            (
                app_root
                / "extern"
                / "RetroSynthesisAgent"
                / "RetroSynAgent"
                / "emol.json"
            ).read_text(encoding="utf-8")
        )
        if not isinstance(bundled_emol, list) or external_emol != bundled_emol:
            failures.append("RetroSynAgent emol.json is not a synchronized JSON list")
    except Exception as error:
        failures.append(
            f"RetroSynAgent emol.json unreadable: {type(error).__name__}: {error}"
        )

    pdb_count = 0
    for relative_path in PDB_FILES:
        pdb_path = app_root / relative_path
        try:
            atom_count = _count_pdb_atoms(pdb_path)
            if atom_count < MIN_PDB_ATOMS:
                failures.append(
                    f"PDB {relative_path}: {atom_count} atoms; need {MIN_PDB_ATOMS}"
                )
            else:
                pdb_count += 1
        except Exception as error:
            failures.append(f"PDB {relative_path}: {type(error).__name__}: {error}")

    if failures:
        raise ScientificAssetError(
            "Scientific asset audit failed:\n- " + "\n- ".join(failures)
        )

    return ScientificAssetReport(
        onnx_models=loaded_models,
        template_rows=total_template_rows,
        precursor_entries=precursor_count,
        manual_precursors=source_counts["manual"],
        smipoly_precursors=source_counts["smipoly"],
        molport_inchikeys=molport_entries,
        zinc_inchikeys=zinc_rows,
        pdb_structures=pdb_count,
    )


def main() -> None:
    """Run the deep asset audit and print a machine-readable report."""
    report = audit_scientific_assets()
    print(json.dumps({"ok": True, **asdict(report)}, sort_keys=True))


if __name__ == "__main__":
    main()
