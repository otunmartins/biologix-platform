#!/usr/bin/env python3
"""Run bundled ADMET-AI models inside their dependency-isolated environment."""

from __future__ import annotations

import contextlib
import importlib
import json
import sys
from typing import Any


def normalize_predictions(
    raw_predictions: Any,
    smiles_list: list[str],
) -> list[dict[str, Any]]:
    """Convert ADMET-AI dictionary or DataFrame output to JSON-safe rows."""
    if isinstance(raw_predictions, dict):
        if len(smiles_list) != 1:
            raise ValueError("Dictionary predictions require exactly one SMILES")
        predictions = {
            str(key): float(value)
            for key, value in raw_predictions.items()
            if isinstance(value, (int, float))
        }
        return [{"smiles": smiles_list[0], "predictions": predictions}]

    if hasattr(raw_predictions, "iterrows"):
        by_smiles = {
            str(index): {
                str(key): float(value)
                for key, value in row.to_dict().items()
                if isinstance(value, (int, float))
            }
            for index, row in raw_predictions.iterrows()
        }
        return [
            {"smiles": smiles, "predictions": by_smiles.get(smiles, {})}
            for smiles in smiles_list
        ]

    raise TypeError(
        f"Unsupported ADMET prediction type: {type(raw_predictions).__name__}"
    )


def main() -> None:
    """Read a SMILES batch from stdin and emit one JSON prediction payload."""
    request = json.load(sys.stdin)
    smiles_list = request.get("smiles")
    if not isinstance(smiles_list, list) or not all(
        isinstance(smiles, str) and smiles.strip() for smiles in smiles_list
    ):
        raise ValueError("stdin must contain a non-empty string list under 'smiles'")

    with contextlib.redirect_stdout(sys.stderr):
        admet_module = importlib.import_module("admet_ai")
        model = admet_module.ADMETModel()
        raw_predictions = model.predict(
            smiles=smiles_list[0] if len(smiles_list) == 1 else smiles_list
        )
        predictions = normalize_predictions(raw_predictions, smiles_list)
    print(
        json.dumps(
            {"predictions": predictions},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
