"""MCP tool regression tests: verify new tools are importable and callable.

These tests exercise the tool functions directly (bypassing the MCP
protocol layer) to ensure they don't crash on basic inputs.
"""

import json
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "python"))

from biologix_ai.discovery_world import ensure_world_for_session, load_world, world_path_for_session


def _import_mcp_server():
    """Import the MCP server module without starting the server."""
    import importlib
    spec = importlib.util.spec_from_file_location(
        "biologix_ai_mcp_server",
        os.path.join(os.path.dirname(__file__), "..", "biologix_ai_mcp_server.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["biologix_ai_mcp_server"] = mod

    original_run = None
    try:
        from mcp.server.fastmcp import FastMCP
        original_run = FastMCP.run
        FastMCP.run = lambda self, *a, **kw: None
    except Exception:
        pass

    spec.loader.exec_module(mod)

    if original_run is not None:
        FastMCP.run = original_run

    return mod


@pytest.fixture
def _restore_insulin_session_env():
    keys = ("BIOLOGIX_AI_SESSION_DIR", "BIOLOGIX_AI_TARGET_PROTEIN_PDB")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k in keys:
        v = saved[k]
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestNewMCPTools:
    @pytest.fixture(autouse=True)
    def _server(self):
        self.server = _import_mcp_server()

    def test_plan_retrosynthesis_returns_json(self):
        result = self.server.plan_retrosynthesis(
            target="PEG",
            biologic_target="insulin",
            max_routes=1,
        )
        parsed = json.loads(result)
        assert "request" in parsed
        assert "polymer_routes" in parsed
        assert parsed["request"]["biologic_target"] == "insulin"

    def test_plan_retrosynthesis_custom_biologic(self):
        result = self.server.plan_retrosynthesis(
            target="test_polymer",
            biologic_target="adalimumab",
        )
        parsed = json.loads(result)
        assert parsed["request"]["biologic_target"] == "adalimumab"

    def test_prepare_retrosynthesis_requires_run_dir(self):
        result = self.server.prepare_retrosynthesis(target="PEG", run_dir="")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_submit_and_plan_chain(self, tmp_path):
        prep = self.server.prepare_retrosynthesis(
            target="[*]CC([*])C(=O)O",
            run_dir=str(tmp_path),
            max_pdfs=0,
        )
        prep_data = json.loads(prep)
        assert prep_data.get("material_name") == "poly(acrylic acid)"

        extractions = json.dumps(
            {
                "test_paper": (
                    "Reaction 001:\n"
                    "Reactants: acrylic acid\n"
                    "Products: poly(acrylic acid)\n"
                    "Conditions: RAFT, 70°C"
                ),
            }
        )
        sub = self.server.submit_retro_extractions(
            run_dir=str(tmp_path),
            material_name="poly(acrylic acid)",
            extractions=extractions,
        )
        sub_data = json.loads(sub)
        assert sub_data.get("ok") is True

        plan = self.server.plan_retrosynthesis(
            target="[*]CC([*])C(=O)O",
            run_dir=str(tmp_path),
            max_routes=2,
        )
        plan_data = json.loads(plan)
        assert "polymer_routes" in plan_data
        assert plan_data["metadata"].get("session_extractions_present") is True

    def test_assemble_retrosynthesis_report(self, tmp_path):
        sub = self.server.submit_retro_extractions(
            run_dir=str(tmp_path),
            target="[*]CC([*])C(=O)O",
            material_name="poly(acrylic acid)",
            extractions=json.dumps(
                {
                    "t": (
                        "Reaction 001:\n"
                        "Reactants: acrylic acid\n"
                        "Products: poly(acrylic acid)\n"
                        "Conditions: RAFT"
                    ),
                }
            ),
        )
        assert json.loads(sub).get("ok") is True
        self.server.plan_retrosynthesis(
            target="[*]CC([*])C(=O)O",
            run_dir=str(tmp_path),
            max_routes=1,
        )
        out = self.server.assemble_retrosynthesis_report(
            run_dir=str(tmp_path),
            targets="[*]CC([*])C(=O)O",
        )
        data = json.loads(out)
        assert data.get("ok") is True
        assert Path(data["markdown_path"]).is_file()

    def test_compile_results_uses_session_dir(self, tmp_path):
        self.server.submit_retro_extractions(
            run_dir=str(tmp_path),
            target="[*]CC([*])C(=O)O",
            material_name="poly(acrylic acid)",
            extractions=json.dumps(
                {
                    "t": (
                        "Reaction 001:\n"
                        "Reactants: acrylic acid\n"
                        "Products: poly(acrylic acid)\n"
                        "Conditions: RAFT"
                    ),
                }
            ),
        )
        self.server.plan_retrosynthesis(
            target="[*]CC([*])C(=O)O",
            run_dir=str(tmp_path),
            max_routes=1,
        )
        comp = json.loads(
            self.server.compile_results(
                target="[*]CC([*])C(=O)O",
                run_dir=str(tmp_path),
                run_admet=False,
                use_cached_plan=True,
            )
        )
        assert comp.get("narrative") or comp.get("scorecards") is not None
        prov = str(comp.get("raw_data", {}).get("retro_metadata", {}))
        assert "session_agent_llm" in prov or "route_provenance" in prov

    def test_check_monomer_admet_returns_json(self):
        result = self.server.check_monomer_admet(smiles="CCO")
        parsed = json.loads(result)
        assert parsed["smiles"] == "CCO"
        assert "safe" in parsed

    def test_check_monomers_batch_returns_list(self):
        result = self.server.check_monomers_batch(smiles_list="CCO,CC(=O)O")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_compile_results_returns_report(self):
        result = self.server.compile_results(
            target="PEG",
            biologic_target="insulin",
            max_routes=1,
            run_admet=False,
        )
        parsed = json.loads(result)
        assert "scorecards" in parsed
        assert "next_steps" in parsed
        assert parsed["biologic_target"] == "insulin"

    def test_resolve_biologic_target_insulin_bundled(self):
        result = self.server.resolve_biologic_target("insulin", fetch_pdb=False)
        parsed = json.loads(result)
        assert parsed["pdb_id"] == "4F1C"
        assert parsed["fetch_ok"] is True
        assert "4F1C" in (parsed.get("pdb_path") or "")

    @pytest.mark.usefixtures("_restore_insulin_session_env")
    def test_start_biologics_session_creates_world(self):
        result = self.server.start_biologics_session(
            biologic_target="insulin",
            polymer_target="PEG",
            run_name="mcp_test_biologics_session",
            fetch_pdb=True,
        )
        parsed = json.loads(result)
        assert "error" not in parsed
        assert parsed["biologic_resolution"]["pdb_id"] == "4F1C"
        sdir = parsed["session_dir"]
        assert os.path.isfile(os.path.join(sdir, "discovery_world.json"))
        world = load_world(world_path_for_session(Path(sdir)))
        links = (world.get("meta") or {}).get("links") or {}
        assert links.get("biologic_target") == "insulin"
        assert "biologic_pdb_path" in links

    def test_run_biologics_discovery_inprocess_smoke(self):
        """Light in-process loop: bundled insulin, explicit polymer, no ADMET/OpenMM."""
        result = self.server.run_biologics_discovery(
            biologic_target="insulin",
            polymer_target="PEG",
            budget_minutes=5.0,
            run_in_background=False,
            run_name="mcp_test_biologics_loop",
            max_routes=1,
            run_admet=False,
            run_openmm=False,
        )
        parsed = json.loads(result)
        assert "error" not in parsed, parsed
        assert "session_dir" in parsed
        assert "iterations" in parsed
        sdir = Path(parsed["session_dir"])
        summary = sdir / "biologics_discovery_summary.json"
        assert summary.is_file()
        summary_payload = json.loads(summary.read_text(encoding="utf-8"))
        assert summary_payload.get("biologic_resolution", {}).get("pdb_id") == "4F1C"
        assert summary_payload.get("candidates"), "expected explicit polymer_target in candidates"
        assert "iterations" in summary_payload

    def test_plan_retrosynthesis_run_dir_writes_artifact_and_world_patch(self, tmp_path):
        """Session-aware persistence from the plan: retrosynthesis/ + discovery_world retrosynthesis_entries."""
        session = tmp_path / "retro_sess"
        ensure_world_for_session(session, objective="unit")
        tgt = "[*]OCC[*]"
        result = self.server.plan_retrosynthesis(
            target=tgt,
            biologic_target="insulin",
            max_routes=1,
            run_dir=str(session),
        )
        parsed = json.loads(result)
        assert isinstance(parsed.get("session_artifact"), str), parsed
        assert Path(parsed["session_artifact"]).is_file()
        retro_dir = session / "retrosynthesis"
        assert retro_dir.is_dir()
        assert any(retro_dir.glob("plan_*.json"))
        world = load_world(world_path_for_session(session))
        entries = world.get("retrosynthesis_entries", [])
        assert any(e.get("polymer_target") == tgt for e in entries)

    def test_compile_results_run_dir_writes_compiled_artifact(self, tmp_path):
        session = tmp_path / "compile_sess"
        ensure_world_for_session(session, objective="unit")
        tgt = "[*]OCC[*]"
        result = self.server.compile_results(
            target=tgt,
            biologic_target="insulin",
            max_routes=1,
            run_admet=False,
            run_dir=str(session),
        )
        parsed = json.loads(result)
        assert isinstance(parsed.get("session_artifact"), str), parsed
        assert Path(parsed["session_artifact"]).is_file()
        retro_dir = session / "retrosynthesis"
        assert any(retro_dir.glob("compile_*.json"))
        world = load_world(world_path_for_session(session))
        kinds = [e.get("kind") for e in world.get("retrosynthesis_entries", [])]
        assert "compiled_report" in kinds

    def test_check_monomer_admet_run_dir_writes_artifact(self, tmp_path):
        session = tmp_path / "admet_sess"
        ensure_world_for_session(session, objective="unit")
        result = self.server.check_monomer_admet(smiles="CCO", run_dir=str(session))
        parsed = json.loads(result)
        assert parsed.get("smiles") == "CCO"
        assert any((session / "retrosynthesis").glob("admet_*.json"))


class TestExistingToolsPreserved:
    """Ensure existing MCP tools still exist after our additions."""

    @pytest.fixture(autouse=True)
    def _server(self):
        self.server = _import_mcp_server()

    @pytest.mark.parametrize("tool_name", [
        "mine_literature",
        "paper_qa_index_status",
        "index_papers",
        "lookup_material",
        "validate_psmiles",
        "openmm_evaluate_psmiles",
        "generate_psmiles_from_name",
        "mutate_psmiles",
        "start_discovery_session",
        "run_autonomous_discovery",
        "get_materials_status",
        "semantic_scholar_search",
        "pubmed_search",
        "arxiv_search",
        "web_search",
        "psmiles_canonicalize",
        "psmiles_dimerize",
        "psmiles_fingerprint",
        "psmiles_similarity",
        "render_psmiles_png",
        "compile_discovery_markdown_to_pdf",
        "write_discovery_summary_report",
        "resolve_biologic_target",
        "start_biologics_session",
        "run_biologics_discovery",
        "prepare_retrosynthesis",
        "submit_retro_extractions",
        "plan_retrosynthesis",
        "assemble_retrosynthesis_report",
        "check_monomer_admet",
        "check_monomers_batch",
        "compile_results",
    ])
    def test_tool_exists(self, tool_name):
        assert hasattr(self.server, tool_name), f"Missing tool: {tool_name}"
        assert callable(getattr(self.server, tool_name))
