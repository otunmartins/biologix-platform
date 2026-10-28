"""Regression checks for GPU dispatch without requiring Modal credentials."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "modal_app.py").read_text(encoding="utf-8")
tree = ast.parse(source)


def _function(name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_modal_defaults_to_gpu():
    assert 'os.environ.get("BIOLOGIX_DEFAULT_COMPUTE", "gpu")' in source
    assert '"BIOLOGIX_DEFAULT_COMPUTE": DEFAULT_COMPUTE' in source


def test_gpu_worker_requires_cuda():
    worker = _function("openmm_worker_gpu")
    body = ast.unparse(worker)
    assert 'select_openmm_platform("CUDA")' in body
    assert '"openmm_platform": "CUDA"' in body


def test_cpu_worker_requires_cpu():
    worker = _function("openmm_worker_cpu")
    assert '"openmm_platform": "CPU"' in ast.unparse(worker)


def test_gpu_worker_has_gpu_resource():
    worker = _function("openmm_worker_gpu")
    decorators = "\n".join(ast.unparse(d) for d in worker.decorator_list)
    assert "gpu=GPU_TYPE" in decorators
