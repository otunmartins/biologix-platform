"""Modal deployment for the authenticated Biologix Streamable HTTP MCP server.

One MCP URL serves every client. The web function (``serve``) runs the MCP
protocol, literature, screening, and retrosynthesis; OpenMM runs on separate
worker functions so it never blocks the web container:

* ``openmm_worker_cpu``: CPU platform, many threads.
* ``openmm_worker_gpu``: CUDA platform (mixed precision) on ``BIOLOGIX_GPU``
  (default ``L4``).

``openmm_evaluate_psmiles(compute="cpu"|"gpu")`` picks the worker; an empty
``compute`` uses ``BIOLOGIX_DEFAULT_COMPUTE`` read at deploy time. Workers get
their inputs and return their artifacts in the call itself, so no Volume is
shared between containers.

The image has two parts so a code change deploys in minutes:

* ``base_image``: the Dockerfile (conda env, AiZynthFinder models, precursor DB,
  ADMET env) built from a dependency-only context (``BASE_CONTEXT_*``). Modal
  caches a Dockerfile build as one layer keyed on its whole context, so this
  rebuilds only when a dependency input changes.
* the project source (``src/python``, ``scripts``, the server) is added when a
  container starts, not built in.

The Dockerfile's verifier needs that source, so it runs after deploy instead:
``scripts/deploy_modal.sh`` deploys and then runs ``verify_runtime``.

Deploy-time knobs (read from the shell running ``modal deploy``)::

    BIOLOGIX_GPU=L4|A10G|T4|L40S|A100|H100   GPU type of the GPU worker (default L4)
    BIOLOGIX_DEFAULT_COMPUTE=cpu|gpu        default OpenMM compute (default cpu)
    BIOLOGIX_MIN_CONTAINERS=0|1             keep one web container warm (default 0)
    BIOLOGIX_MODAL_APP=biologix-mcp         app name (a second app gives a second URL)
"""

from __future__ import annotations

import importlib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

import modal


APP_NAME = os.environ.get("BIOLOGIX_MODAL_APP", "biologix-mcp").strip() or "biologix-mcp"
REPO_ROOT = Path(__file__).resolve().parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
GPU_TYPE = os.environ.get("BIOLOGIX_GPU", "L4").strip() or "L4"
DEFAULT_COMPUTE = os.environ.get("BIOLOGIX_DEFAULT_COMPUTE", "cpu").strip().lower() or "cpu"
MIN_CONTAINERS = int(os.environ.get("BIOLOGIX_MIN_CONTAINERS", "0") or 0)
WORKSPACE_HOST = f"muhammadhasyim--{APP_NAME}-serve.modal.run"

MODAL_ENV = {
    "BIOLOGIX_MCP_TRANSPORT": "http",
    "BIOLOGIX_OAUTH_ISSUER_URL": f"https://{WORKSPACE_HOST}",
    "BIOLOGIX_MCP_RESOURCE_URL": f"https://{WORKSPACE_HOST}/mcp",
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
    # OpenMM runs on the worker functions below; the web container only dispatches.
    "BIOLOGIX_AI_COMPUTE_BACKEND": "modal",
    "BIOLOGIX_MODAL_APP": APP_NAME,
    "BIOLOGIX_DEFAULT_COMPUTE": DEFAULT_COMPUTE,
    # Remote clients time out long calls; longer calls come back as jobs.
    "BIOLOGIX_TOOL_WAIT_S": "90",
    "BIOLOGIX_AI_STRUCTURE_CACHE": "/app/runs/.structures",
    "BIOLOGIX_MCP_PROFILE": "protocol",
}

WORKER_ENV_CPU = {
    "BIOLOGIX_AI_OPENMM_PLATFORM": "CPU",
    "OPENMM_CPU_THREADS": "8",
    "OMP_NUM_THREADS": "8",
    "BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S": "3000",
}
WORKER_ENV_GPU = {
    "BIOLOGIX_AI_OPENMM_PLATFORM": "CUDA",
    "OPENMM_CPU_THREADS": "4",
    "OMP_NUM_THREADS": "4",
    "BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S": "3000",
}

# Files the Dockerfile build reads. Anything else (project source, tests, docs,
# .git) is left out of the base context so editing it never rebuilds the base.
BASE_CONTEXT_FILES = frozenset(
    {
        "Dockerfile",
        ".dockerignore",
        "environment-simulation.yml",
        "pyproject.toml",
        "README.md",
        "scripts/build_precursor_db.py",
        # install_submodules.sh bootstraps RetroSynthesisAgent with this module.
        "src/python/biologix_ai/__init__.py",
        "src/python/biologix_ai/retrosynthesis/__init__.py",
        "src/python/biologix_ai/retrosynthesis/retrosyn_bootstrap.py",
    }
)
BASE_CONTEXT_DIRS = ("extern/", "data/", "docker/")
_DOCKERIGNORE = modal.FilePatternMatcher.from_file(REPO_ROOT / ".dockerignore")


def _outside_base_context(path: Path) -> bool:
    """Ignore rule for the base build: True excludes *path* (relative to the repo)."""
    rel = Path(path).as_posix()
    if _DOCKERIGNORE(Path(rel)):
        return True
    if rel in BASE_CONTEXT_FILES or rel.startswith(BASE_CONTEXT_DIRS):
        return False
    install_script = rel.startswith("scripts/") and rel.endswith(".sh") and rel.count("/") == 1
    return not install_script or rel == "scripts/deploy_modal.sh"


_SOURCE_IGNORE = ["**/__pycache__/**", "**/*.pyc"]

base_image = modal.Image.from_dockerfile(
    DOCKERFILE,
    context_dir=REPO_ROOT,
    ignore=_outside_base_context,
    build_args={"VERIFY_IMAGE": "0"},
)
# Node runs the avoid-ai-writing detector that checks report prose. A pip wheel supplies it
# as a thin layer over the base image, so adding it does not rebuild the base.
image = (
    base_image.pip_install("nodejs-wheel-binaries>=22")
    .add_local_dir(REPO_ROOT / "src" / "python", "/app/src/python", ignore=_SOURCE_IGNORE)
    .add_local_dir(REPO_ROOT / "scripts", "/app/scripts", ignore=_SOURCE_IGNORE)
    .add_local_file(REPO_ROOT / "biologix_ai_mcp_server.py", "/app/biologix_ai_mcp_server.py")
)
runs_volume = modal.Volume.from_name("biologix-mcp-runs", create_if_missing=True)
papers_volume = modal.Volume.from_name("biologix-mcp-papers", create_if_missing=True)
VOLUME_MOUNTS = {
    "/app/runs": runs_volume,
    "/app/papers": papers_volume,
}
mcp_secret = modal.Secret.from_name(
    "biologix-mcp-secrets", required_keys=["BIOLOGIX_MCP_TOKEN"]
)
oauth_secret = modal.Secret.from_name(
    "biologix-mcp-auth",
    required_keys=["BIOLOGIX_OAUTH_APPROVAL_TOKEN", "BIOLOGIX_OAUTH_STORAGE_KEY"],
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    env=MODAL_ENV,
    secrets=[mcp_secret, oauth_secret],
    volumes=VOLUME_MOUNTS,
    cpu=4.0,
    memory=16384,
    timeout=3600,
    startup_timeout=1800,
    # Background jobs outlive the request that started them; keep the container
    # up between await_biologix_job calls.
    scaledown_window=900,
    min_containers=MIN_CONTAINERS,
    max_containers=1,
)
@modal.concurrent(max_inputs=32)
@modal.asgi_app()
def serve() -> Any:
    """Return the authenticated Streamable HTTP ASGI application."""
    server = importlib.import_module("biologix_ai_mcp_server")
    return server.create_http_app()


def _run_openmm(spec: Dict[str, Any]) -> Dict[str, Any]:
    job = importlib.import_module("biologix_ai.compute.openmm_job")
    return job.run_openmm_job_remote(spec)


@app.function(
    image=image,
    env={**MODAL_ENV, **WORKER_ENV_CPU},
    cpu=8.0,
    memory=16384,
    timeout=3600,
    startup_timeout=1800,
    max_containers=4,
)
def openmm_worker_cpu(spec: Dict[str, Any]) -> Dict[str, Any]:
    """One OpenMM matrix evaluation on the CPU platform."""
    return _run_openmm(spec)


@app.function(
    image=image,
    env={**MODAL_ENV, **WORKER_ENV_GPU},
    gpu=GPU_TYPE,
    cpu=4.0,
    memory=16384,
    timeout=3600,
    startup_timeout=1800,
    max_containers=4,
)
def openmm_worker_gpu(spec: Dict[str, Any]) -> Dict[str, Any]:
    """One OpenMM matrix evaluation on the CUDA platform."""
    return _run_openmm(spec)


@app.function(
    image=image,
    env=MODAL_ENV,
    secrets=[mcp_secret, oauth_secret],
    volumes=VOLUME_MOUNTS,
    cpu=4.0,
    memory=16384,
    timeout=3600,
    startup_timeout=1800,
    min_containers=0,
    max_containers=1,
)
def verify_runtime() -> dict[str, Any]:
    """Run the complete image verifier inside Modal's runtime environment."""
    verifier = importlib.import_module("scripts.verify_modal_image")
    report = verifier.verify_image()
    importlib.import_module("biologix_ai")
    importlib.import_module("biologix_ai_mcp_server")
    modal_module = importlib.import_module("modal")
    return {
        "ok": True,
        "modal_runtime_path": str(Path(modal_module.__file__).parent),
        "report_tools": _verify_report_tools(),
        **asdict(report),
    }


def _verify_report_tools() -> dict[str, Any]:
    """The report needs Node (style detector) and bundled fonts (PDF text); prove both work."""
    import tempfile

    style = importlib.import_module("biologix_ai.report.style_check")
    pdf = importlib.import_module("biologix_ai.report.pdf_render")
    seen = style.analyze("It is important to note that this robust, comprehensive tool delves in.")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.pdf"
        result = pdf.render_markdown_to_pdf("# Check\n\nEnergy \u22123931.2 kJ/mol \u2014 ok.\n", out, Path(tmp))
        rendered = out.is_file() and out.stat().st_size > 1000
    ok = bool(seen["available"] and seen["issues"] and rendered)
    return {"ok": ok, "detector": seen["available"], "findings": len(seen["issues"]), "pdf": rendered,
            "pages": result.pages}


@app.function(
    image=image,
    env={**MODAL_ENV, **WORKER_ENV_GPU},
    gpu=GPU_TYPE,
    timeout=1800,
    startup_timeout=1800,
)
def verify_gpu() -> dict[str, Any]:
    """Prove the GPU worker runs OpenMM on CUDA: platform probe plus one insulin screen."""
    import subprocess

    complex_module = importlib.import_module("biologix_ai.simulation.openmm_complex")
    _platform, info = complex_module.select_openmm_platform("CUDA")
    smi = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False)
    screen = _run_openmm({"psmiles": ["[*]OCC[*]"], "concise": True})
    outcomes = screen["result"].get("candidate_outcomes") or []
    return {
        "ok": bool(outcomes) and outcomes[0].get("status") == "completed",
        "gpu": smi.stdout.strip(),
        "platform": info,
        "outcome": outcomes[0] if outcomes else screen["result"],
    }
