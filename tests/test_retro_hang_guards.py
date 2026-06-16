"""Tests for retrosynthesis hang guards (PDF timeout, tree timeout, PSMILES guards)."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from biologix_ai.retrosynthesis.models import (
    RetrosynthesisConstraints,
    RetrosynthesisRequest,
)
from biologix_ai.services import retrosynthesis_service as rs


class TestWorkerSessionIsolation:
    def test_isolate_worker_session_calls_setsid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setsid_called: list[bool] = []
        monkeypatch.setattr(rs.os, "setsid", lambda: setsid_called.append(True))
        rs._isolate_worker_session()
        assert setsid_called

    def test_pdf_worker_calls_setsid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setsid_called: list[bool] = []

        monkeypatch.setattr(rs.os, "setsid", lambda: setsid_called.append(True))
        monkeypatch.setattr(rs.os, "chdir", lambda _path: None)

        pdf_module = MagicMock()
        pdf_module.PDFDownloader.side_effect = RuntimeError("stop after setsid")
        monkeypatch.setitem(
            __import__("sys").modules,
            "RetroSynAgent",
            MagicMock(pdfDownloader=pdf_module),
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "RetroSynAgent.pdfDownloader",
            pdf_module,
        )

        queue: multiprocessing.Queue[list[str]] = multiprocessing.Queue()
        rs._pdf_download_worker("polyethylene", "/tmp", "/tmp/pdfs", 1, queue)

        assert setsid_called

    def test_tree_worker_calls_setsid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setsid_called: list[bool] = []

        monkeypatch.setattr(rs.os, "setsid", lambda: setsid_called.append(True))
        monkeypatch.setattr(rs.os, "chdir", lambda _path: None)
        monkeypatch.setattr(
            "biologix_ai.retrosynthesis.retrosyn_bootstrap.ensure_retrosyn_agent_ready",
            lambda: (_ for _ in ()).throw(RuntimeError("stop after setsid")),
        )

        queue: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
        rs._tree_worker("chitosan", "/tmp", "/tmp/pdfs", "/tmp/results", queue)

        assert setsid_called

    def test_aizynth_worker_calls_setsid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        setsid_called: list[bool] = []

        monkeypatch.setattr(rs.os, "setsid", lambda: setsid_called.append(True))
        monkeypatch.setitem(
            __import__("sys").modules,
            "aizynthfinder",
            MagicMock(),
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "aizynthfinder.aizynthfinder",
            MagicMock(
                AiZynthFinder=MagicMock(side_effect=RuntimeError("stop after setsid"))
            ),
        )

        queue: multiprocessing.Queue[dict[str, object]] = multiprocessing.Queue()
        rs._aizynthfinder_worker("CCO", "/tmp/config.yml", queue)

        assert setsid_called


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

        def _fake_run_tree(*_args, **_kwargs):
            return [], "none", "Tree construction timed out after 2s"

        monkeypatch.setattr(rs, "_is_retrosynthesisagent_available", lambda: True)
        monkeypatch.setattr(
            "biologix_ai.retrosynthesis.retrosyn_bootstrap.ensure_retrosyn_agent_ready",
            lambda: None,
        )
        monkeypatch.setattr(rs, "_run_tree_with_timeout", _fake_run_tree)

        routes, _provenance, error = rs._run_retrosynthesis_agent(
            material_name="poly(acrylic acid)",
            session_dir=tmp_path,
        )

        assert routes == []
        assert error is not None
        assert "timed out" in error.lower()

    def test_tree_subprocess_killed_on_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hung tree subprocess is terminated; caller returns quickly with no routes."""

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
        result_dir = tmp_path / "results"
        pdf_dir.mkdir()
        result_dir.mkdir()

        start = time.monotonic()
        routes, provenance, error = rs._run_tree_with_timeout(
            "poly(acrylic acid)",
            tmp_path,
            pdf_dir,
            result_dir,
            timeout=2,
        )
        elapsed = time.monotonic() - start

        assert routes == []
        assert provenance == "none"
        assert error is not None
        assert "timed out" in error.lower()
        assert elapsed < 5


class TestRunPlanRetrosynthesisCli:
    def test_run_with_timeout_returns_error_on_hung_worker(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import importlib.util
        from pathlib import Path as _Path

        script = _Path(__file__).resolve().parents[1] / "scripts" / "run_plan_retrosynthesis.py"
        spec = importlib.util.spec_from_file_location("run_plan_retrosynthesis", script)
        assert spec and spec.loader
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)

        class _HungProcess:
            pid = 4242

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
                return multiprocessing.Queue()

        monkeypatch.setattr(cli.multiprocessing, "get_context", lambda _name: _MockCtx())
        monkeypatch.setattr(cli.os, "killpg", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(cli.os, "getpgid", lambda pid: pid)

        start = time.monotonic()
        payload = cli._run_with_timeout({"target": "Chitosan"}, timeout_s=2)
        elapsed = time.monotonic() - start

        assert payload.get("ok") is False
        assert "timed out" in str(payload.get("error", "")).lower()
        assert elapsed < 10


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
