from pathlib import Path

from biologix_ai.retrosynthesis.aizynth_config import models_ready


def write_config(directory: Path, model_path: str = "model.onnx") -> Path:
    config = directory / "config.yml"
    config.write_text(
        "expansion:\n"
        "  uspto:\n"
        f"    - {model_path}\n"
        "    - templates.csv.gz\n"
        "filter:\n"
        "  uspto: filter.onnx\n"
        "stock:\n"
        "  zinc: stock.hdf5\n",
        encoding="utf-8",
    )
    return config


def test_models_ready_requires_every_configured_asset(tmp_path, monkeypatch):
    config = write_config(tmp_path)
    monkeypatch.setenv("BIOLOGIX_AI_AIZYNTH_CONFIG", str(config))

    assert models_ready() is False

    for filename in ("model.onnx", "templates.csv.gz", "filter.onnx", "stock.hdf5"):
        (tmp_path / filename).write_bytes(b"model data")

    assert models_ready() is True


def test_models_ready_resolves_relative_and_absolute_paths(tmp_path, monkeypatch):
    external_model = tmp_path.parent / "external-model.onnx"
    external_model.write_bytes(b"model data")
    config = write_config(tmp_path, str(external_model))
    for filename in ("templates.csv.gz", "filter.onnx", "stock.hdf5"):
        (tmp_path / filename).write_bytes(b"model data")
    monkeypatch.setenv("BIOLOGIX_AI_AIZYNTH_CONFIG", str(config))

    assert models_ready() is True

    external_model.write_bytes(b"")
    assert models_ready() is False
