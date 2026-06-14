"""Tests for evaluate_candidates structure artifact directory resolution."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_resolve_eval_structure_artifacts_dir_explicit(tmp_path):
    from biologix_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    sub = tmp_path / "custom_structures"
    d = resolve_eval_structure_artifacts_dir(str(sub))
    assert d == sub.resolve()
    assert d.is_dir()


def test_resolve_eval_structure_artifacts_dir_session_env(monkeypatch, tmp_path):
    from biologix_ai.run_paths import ENV_SESSION
    from biologix_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    session = tmp_path / "run1"
    session.mkdir()
    monkeypatch.setenv(ENV_SESSION, str(session))
    monkeypatch.delenv("BIOLOGIX_AI_EVAL_ARTIFACTS_DIR", raising=False)
    monkeypatch.delenv("BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS", raising=False)
    d = resolve_eval_structure_artifacts_dir(None)
    assert d == (session / "structures").resolve()
    assert d.is_dir()


def test_resolve_eval_structure_artifacts_dir_opt_out(monkeypatch, tmp_path):
    from biologix_ai.run_paths import ENV_SESSION
    from biologix_ai.simulation.md_simulator import resolve_eval_structure_artifacts_dir

    session = tmp_path / "run1"
    session.mkdir()
    monkeypatch.setenv(ENV_SESSION, str(session))
    monkeypatch.setenv("BIOLOGIX_AI_EVAL_NO_STRUCTURE_ARTIFACTS", "1")
    assert resolve_eval_structure_artifacts_dir(None) is None


def test_attach_matrix_structure_artifacts_writes_chemviz_paths(tmp_path, monkeypatch):
    from biologix_ai.simulation.md_simulator import attach_matrix_structure_artifacts

    struct = tmp_path / "structures"
    pdb = struct / "poly_complex_minimized.pdb"
    struct.mkdir(parents=True)
    pdb.write_text("ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n")

    def _mono(psmiles, out, overwrite=True):
        out = Path(out)
        out.write_bytes(b"png")
        return {"ok": True, "path": str(out)}

    def _prev(pdb_path, out):
        out = Path(out)
        out.write_bytes(b"png")
        return {"ok": True, "path": str(out)}

    def _chemviz(pdb_path, out, n_protein_atoms=None):
        out = Path(out)
        out.write_bytes(b"png")
        return {"ok": True, "path": str(out)}, "pymol"

    monkeypatch.setattr(
        "biologix_ai.psmiles_drawing.save_psmiles_png",
        _mono,
    )
    monkeypatch.setattr(
        "biologix_ai.simulation.pdb_preview.write_complex_preview_png",
        _prev,
    )
    monkeypatch.setattr(
        "biologix_ai.simulation.pymol_complex_viz.write_complex_viz_png_auto",
        _chemviz,
    )
    monkeypatch.setattr(
        "biologix_ai.simulation.matrix_packing_metrics.compute_matrix_packing_metrics",
        lambda cp, n: {"ok": True, "n_protein": n},
    )

    res = attach_matrix_structure_artifacts(
        {"ok": True, "n_insulin_atoms": 10, "n_polymer_atoms_per_chain": 5, "n_polymer_chains": 2},
        psmiles="[*]CC[*]",
        slug="poly",
        struct_dir=struct,
        pdb_out=pdb,
    )
    assert res["complex_chemviz_png_path"] == str(struct / "poly_complex_chemviz.png")
    assert (struct / "poly_complex_chemviz.png").is_file()
    assert res["monomer_png_path"] == str(struct / "poly_monomer.png")
    assert res["complex_preview_png_path"] == str(struct / "poly_complex_preview.png")
    assert res["structure_artifacts_dir"] == str(struct.resolve())


def test_run_openmm_matrix_cli_attaches_structure_artifacts(tmp_path, monkeypatch, capsys):
    import importlib.util

    session = tmp_path / "run_cli"
    session.mkdir()
    fake_eval = {
        "ok": True,
        "interaction_energy_kj_mol": -12.3,
        "n_insulin_atoms": 8,
        "n_polymer_atoms_per_chain": 4,
        "n_polymer_chains": 3,
    }

    def _attach(res, *, psmiles, slug, struct_dir, pdb_out=None):
        out = dict(res)
        struct = Path(struct_dir)
        chemviz = struct / f"{slug}_complex_chemviz.png"
        chemviz.parent.mkdir(parents=True, exist_ok=True)
        chemviz.write_bytes(b"png")
        out["complex_chemviz_png_path"] = str(chemviz)
        out["structure_artifacts_dir"] = str(struct.resolve())
        return out

    monkeypatch.setattr(
        "biologix_ai.simulation.md_simulator._run_matrix_eval_with_timeout",
        lambda psmiles, kw, timeout: fake_eval,
    )
    monkeypatch.setattr(
        "biologix_ai.simulation.md_simulator.attach_matrix_structure_artifacts",
        _attach,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_openmm_matrix.py",
            "[*]CC[*]",
            "--run-dir",
            str(session),
            "--material-name",
            "Candidate_1",
            "--no-npt",
        ],
    )

    script = ROOT / "scripts" / "run_openmm_matrix.py"
    spec = importlib.util.spec_from_file_location("run_openmm_matrix_testmod", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["complex_chemviz_png_path"].endswith("Candidate_1_complex_chemviz.png")
    assert Path(payload["complex_chemviz_png_path"]).is_file()
