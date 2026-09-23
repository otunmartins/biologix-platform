"""Configuration tests for the Modal remote MCP deployment."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODAL_APP_PATH = REPO_ROOT / "modal_app.py"
sys.path.insert(0, str(REPO_ROOT / "src" / "python"))


def _load_modal_app():
    spec = importlib.util.spec_from_file_location("biologix_modal_app_test", MODAL_APP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_modal_app_uses_full_dockerfile_and_only_runtime_volumes() -> None:
    module = _load_modal_app()

    assert module.APP_NAME == "biologix-mcp"
    assert module.DOCKERFILE == REPO_ROOT / "Dockerfile"
    assert set(module.VOLUME_MOUNTS) == {"/app/runs", "/app/papers"}
    assert "/app/data" not in module.VOLUME_MOUNTS

    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    assert "modal.Image.from_dockerfile" in source
    # The only build arg defers the in-build verifier (it needs the mounted source).
    assert 'build_args={"VERIFY_IMAGE": "0"}' in source


def test_base_image_context_excludes_project_source() -> None:
    """Editing code must not rebuild the conda/model base image."""
    module = _load_modal_app()
    excluded = module._outside_base_context
    for source_path in (
        "biologix_ai_mcp_server.py",
        "modal_app.py",
        "src/python/biologix_ai/mcp_jobs.py",
        "src/python/biologix_ai/protocol/PROTOCOL.md",
        "tests/test_mcp_jobs.py",
        "CLAUDE.md",
        "clients/AGENTS.md",
        ".git/HEAD",
        "scripts/deploy_modal.sh",
        "scripts/verify_modal_image.py",
    ):
        assert excluded(Path(source_path)), source_path
    for dependency_path in (
        "Dockerfile",
        "environment-simulation.yml",
        "pyproject.toml",
        "README.md",
        "scripts/install_submodules.sh",
        "scripts/setup_aizynthfinder.sh",
        "scripts/build_precursor_db.py",
        "src/python/biologix_ai/retrosynthesis/retrosyn_bootstrap.py",
        "data/biologics/biologic_3WD5.pdb",
        "extern/RetroSynthesisAgent/setup.py",
    ):
        assert not excluded(Path(dependency_path)), dependency_path
    # .dockerignore still applies inside the allowed directories.
    assert excluded(Path("data/aizynthfinder/config.yml"))


def test_runtime_image_mounts_the_source_every_function_imports() -> None:
    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    assert '"/app/src/python"' in source and '"/app/scripts"' in source
    assert '"/app/biologix_ai_mcp_server.py"' in source
    assert "copy=True" not in source


def test_modal_runtime_knobs_enable_full_native_amd64_stack() -> None:
    module = _load_modal_app()

    expected_environment = {
        "BIOLOGIX_MCP_TRANSPORT": "http",
        "BIOLOGIX_OAUTH_ISSUER_URL": (
            "https://muhammadhasyim--biologix-mcp-serve.modal.run"
        ),
        "BIOLOGIX_MCP_RESOURCE_URL": (
            "https://muhammadhasyim--biologix-mcp-serve.modal.run/mcp"
        ),
        "BIOLOGIX_OAUTH_STORE_PATH": "/app/runs/.oauth/state.enc",
        "RETRO_LLM_BACKEND": "skip",
        "BIOLOGIX_AI_AIZYNTH_CONFIG": "/app/data/aizynthfinder/config.yml",
        "BIOLOGIX_AI_EVAL_MAX_WORKERS": "2",
        "OPENMM_CPU_THREADS": "2",
        "BIOLOGIX_SKIP_ZINC_BRIDGE": "0",
        "BIOLOGIX_AI_OPENMM_AUTO": "yes",
        "BIOLOGIX_AI_MCP_TIMEOUT_MS": "3300000",
        "BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S": "30",
        "BIOLOGIX_AI_MCP_RETRO_TIMEOUT_S": "1200",
        "BIOLOGIX_AI_MCP_ADMET_TIMEOUT_S": "300",
        "BIOLOGIX_AI_MCP_ADMET_BATCH_TIMEOUT_S": "600",
        "BIOLOGIX_AI_MCP_MINE_TIMEOUT_S": "600",
        "BIOLOGIX_AI_MCP_INDEX_TIMEOUT_S": "1800",
        "BIOLOGIX_PDF_TIMEOUT": "120",
        "BIOLOGIX_TREE_TIMEOUT": "900",
        "BIOLOGIX_AIZYNTH_TIMEOUT": "600",
        "BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S": "1200",
        "BIOLOGIX_AI_COMPUTE_BACKEND": "modal",
        "BIOLOGIX_MODAL_APP": "biologix-mcp",
        "BIOLOGIX_DEFAULT_COMPUTE": "cpu",
        "BIOLOGIX_TOOL_WAIT_S": "90",
        "BIOLOGIX_AI_STRUCTURE_CACHE": "/app/runs/.structures",
        "BIOLOGIX_MCP_PROFILE": "protocol",
    }
    assert module.MODAL_ENV == expected_environment

    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    for setting in (
        "cpu=4.0",
        "memory=16384",
        "timeout=3600",
        "startup_timeout=1800",
        "min_containers=0",
        "max_containers=1",
        "scaledown_window=900",
    ):
        assert setting in source


def test_openmm_runs_on_separate_cpu_and_gpu_workers(monkeypatch) -> None:
    monkeypatch.delenv("BIOLOGIX_GPU", raising=False)
    monkeypatch.delenv("BIOLOGIX_DEFAULT_COMPUTE", raising=False)
    module = _load_modal_app()
    assert module.GPU_TYPE == "L4"
    assert module.WORKER_ENV_CPU["BIOLOGIX_AI_OPENMM_PLATFORM"] == "CPU"
    assert module.WORKER_ENV_GPU["BIOLOGIX_AI_OPENMM_PLATFORM"] == "CUDA"
    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    gpu_block = source.split("def openmm_worker_gpu(", 1)[0].rsplit("@app.function(", 1)[1]
    cpu_block = source.split("def openmm_worker_cpu(", 1)[0].rsplit("@app.function(", 1)[1]
    assert "gpu=GPU_TYPE" in gpu_block and "gpu=" not in cpu_block
    # Workers exchange data in the call, never through the runs volume.
    assert "volumes=" not in gpu_block and "volumes=" not in cpu_block
    from biologix_ai.compute import WORKER_FUNCTIONS

    assert set(WORKER_FUNCTIONS.values()) == {"openmm_worker_cpu", "openmm_worker_gpu"}


def test_deploy_time_gpu_and_default_compute(monkeypatch) -> None:
    monkeypatch.setenv("BIOLOGIX_GPU", "A10G")
    monkeypatch.setenv("BIOLOGIX_DEFAULT_COMPUTE", "gpu")
    module = _load_modal_app()
    assert module.GPU_TYPE == "A10G"
    assert module.MODAL_ENV["BIOLOGIX_DEFAULT_COMPUTE"] == "gpu"


def test_serve_accepts_concurrent_inputs_and_stays_single_container() -> None:
    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    serve_block = source.split("def serve(", 1)[0]
    verify_block = source.split("def serve(", 1)[1]
    assert "max_containers=1" in serve_block
    assert "@modal.concurrent(max_inputs=" in serve_block
    function_at = serve_block.rfind("@app.function(")
    concurrent_at = serve_block.rfind("@modal.concurrent(")
    asgi_at = serve_block.rfind("@modal.asgi_app()")
    assert function_at < concurrent_at < asgi_at
    assert "@modal.concurrent(" not in verify_block


def test_modal_secret_requires_http_token() -> None:
    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    assert "modal.Secret.from_name" in source
    assert '"biologix-mcp-secrets"' in source
    assert 'required_keys=["BIOLOGIX_MCP_TOKEN"]' in source
    assert '"biologix-mcp-auth"' in source
    assert (
        'required_keys=["BIOLOGIX_OAUTH_APPROVAL_TOKEN", '
        '"BIOLOGIX_OAUTH_STORAGE_KEY"]'
    ) in source
    assert source.count("secrets=[mcp_secret, oauth_secret]") == 2


def test_oauth_encryption_dependency_is_explicit() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"cryptography>=44,<47"' in pyproject


def test_dockerfile_runs_full_image_verifier_after_data_snapshot() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    snapshot = "cp -a /app/data /app/.data-seed"
    verifier = "python scripts/verify_modal_image.py"
    assert snapshot in dockerfile
    assert verifier in dockerfile
    assert dockerfile.index(snapshot) < dockerfile.index(verifier)
    assert "ARG VERIFY_IMAGE=1" in dockerfile  # local Docker builds still verify by default


def test_runtime_verifier_accepts_modal_runtime_mount_without_distribution_metadata() -> None:
    source = MODAL_APP_PATH.read_text(encoding="utf-8")
    assert 'importlib.import_module("modal")' in source
    assert 'importlib.metadata.version("modal")' not in source
