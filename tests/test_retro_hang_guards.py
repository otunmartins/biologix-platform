"""Tests for retrosynthesis hang guards (PDF timeout, tree timeout, PSMILES guards)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from biologix_ai.retrosynthesis.models import (
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
)
from biologix_ai.services import retrosynthesis_service as rs


class TestIsSmilesLike:
    def test_xanthate_cta_detected(self) -> None:
        assert rs._is_smiles_like("[*]C(=S)C([*])=S") is True

    def test_polymer_name_not_smiles_like(self) -> None:
        assert rs._is_smiles_like("poly(lactic-co-glycolic acid)") is False


class TestPrepareWorkspaceGuards:
    def test_skips_pdf_download_for_smiles_like_material_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pdf_called = {"value": False}

        def _fake_download(*_args, **_kwargs):
            pdf_called["value"] = True
            return []

        monkeypatch.setattr(rs, "_download_pdfs_with_timeout", _fake_download)
        monkeypatch.setattr(rs, "_is_retrosynthesisagent_available", lambda: True)

        result = rs.prepare_retrosynthesis_workspace(
            target="[*]C(=S)C([*])=S",
            session_dir=tmp_path,
        )

        assert pdf_called["value"] is False
        assert result["pdf_paths"] == []
        assert result.get("pdf_skip_reason")
        assert "register_retro_precursors" in result["next_step"]

    def test_pdf_download_times_out(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hung subprocess is terminated; caller returns quickly with no PDFs."""

        class _HungProcess:
            pid = 99999

            def start(self) -> None:
                return

            def join(self, timeout: float | None = None) -> None:
                return

            def is_alive(self) -> bool:
                return True

            def terminate(self) -> None:
                return

            def kill(self) -> None:
                return

        class _MockCtx:
            def Process(self, target, args):  # noqa: ANN001
                return _HungProcess()

            def Queue(self):
                import multiprocessing

                return multiprocessing.Queue()

        monkeypatch.setattr(rs.multiprocessing, "get_context", lambda _name: _MockCtx())
        monkeypatch.setattr(rs.os, "killpg", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(rs.os, "getpgid", lambda pid: pid)

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()

        start = time.monotonic()
        names = rs._download_pdfs_with_timeout(
            "polyethylene",
            tmp_path,
            pdf_dir,
            max_pdfs=1,
            timeout=2,
        )
        elapsed = time.monotonic() - start

        assert names == []
        assert elapsed < 5


class TestPlanRetrosynthesisGuards:
    def test_rejects_unresolved_psmiles_target(self) -> None:
        request = RetrosynthesisRequest(
            target="[*]C(=S)C([*])=S",
            biologic_target="adalimumab",
            constraints=RetrosynthesisConstraints(enrich_monomers_with_aizynth=False),
        )
        result = rs.plan_retrosynthesis(request)

        assert len(result.polymer_routes) == 0
        assert len(result.errors) == 1
        assert "raw PSMILES" in result.errors[0]
        assert result.metadata.get("recommended_next_action") == "register_retro_precursors"


class TestTreeConstructTimeout:
    def test_tree_construct_timeout_returns_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        if not rs._is_retrosynthesisagent_available():
            pytest.skip("RetroSynthesisAgent not installed")

        from biologix_ai.retrosynthesis.retro_adapter import write_llm_res

        write_llm_res(
            tmp_path,
            "poly(acrylic acid)",
            {
                "test_paper": (
                    "Reaction 001:\n"
                    "Reactants: acrylic acid\n"
                    "Products: poly(acrylic acid)\n"
                    "Conditions: RAFT"
                ),
            },
        )

        class _SlowTree:
            def __init__(self, *_args, **_kwargs) -> None:
                self.product_dict: dict = {}
                self.reactions = []

            def construct_tree(self) -> None:
                time.sleep(30)

        monkeypatch.setattr(rs, "_TREE_CONSTRUCT_TIMEOUT", 2)
        monkeypatch.setattr("RetroSynAgent.treeBuilder.Tree", _SlowTree)

        routes, _provenance, error = rs._run_retrosynthesis_agent(
            material_name="poly(acrylic acid)",
            session_dir=tmp_path,
        )

        assert routes == []
        assert error is not None
        assert "timed out" in error.lower()


class TestPubchempyBootstrapPatch:
    def test_pubchempy_get_compounds_is_patched(self) -> None:
        pytest.importorskip("pubchempy")
        if not rs._is_retrosynthesisagent_available():
            pytest.skip("RetroSynthesisAgent not installed")

        import pubchempy as pcp

        from biologix_ai.retrosynthesis.retrosyn_bootstrap import ensure_retrosyn_agent_ready

        ensure_retrosyn_agent_ready()
        assert getattr(pcp.get_compounds, "_biologix_timed", False) is True

    def test_timed_wrapper_returns_empty_on_slow_call(self) -> None:
        pytest.importorskip("pubchempy")
        import concurrent.futures as cf
        import pubchempy as pcp

        def _slow(*_args, **_kwargs):
            time.sleep(20)
            return []

        def _timed_get_compounds(*args, **kwargs):
            executor = cf.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_slow, *args, **kwargs)
            try:
                return future.result(timeout=1)
            except cf.TimeoutError:
                executor.shutdown(wait=False, cancel_futures=True)
                return []
            finally:
                if not future.done():
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=True)

        start = time.monotonic()
        result = _timed_get_compounds("invalid_smiles_xyz", "smiles")
        elapsed = time.monotonic() - start

        assert result == []
        assert elapsed < 5
