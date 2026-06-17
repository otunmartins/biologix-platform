"""Regression tests for Docker entrypoint terminal-restore helpers."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = REPO_ROOT / "docker" / "restore_terminal.sh"
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"


def test_restore_terminal_script_exists_and_disables_mouse_modes() -> None:
    assert RESTORE_SCRIPT.is_file()
    text = RESTORE_SCRIPT.read_text(encoding="utf-8")
    assert "restore_host_terminal" in text
    for seq in ("?1000l", "?1002l", "?1006l", "stty sane"):
        assert seq in text, f"missing terminal restore sequence: {seq}"


def test_entrypoint_wires_terminal_restore_and_docker_openmm_policy() -> None:
    assert ENTRYPOINT.is_file()
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "restore_terminal.sh" in text
    assert "trap" in text and "restore_host_terminal" in text
    assert "BIOLOGIX_AI_DOCKER=1" in text
    assert "BIOLOGIX_AI_OPENMM_AUTO" in text
    assert "exec opencode" not in text
    assert "opencode ." in text


def test_cpu_limit_script_math() -> None:
    script = REPO_ROOT / "scripts" / "docker_cpu_limit.sh"
    assert script.is_file()
    # 8 cores @ 75% → 6; 4 cores @ 75% → 3; 1 core → 1
    import math

    for host, pct, want in ((8, 75, 6), (4, 75, 3), (10, 75, 7), (1, 75, 1)):
        got = max(1, math.floor(host * pct / 100))
        assert got == want, (host, pct, got, want)


def test_openmm_thread_split_for_parallel_batch() -> None:
    """OMP threads when running up to 3 OpenMM candidates in parallel."""

    def omp_threads(cpus: int, workers: int, batch: int = 3) -> int:
        if workers == 1:
            return max(1, cpus)
        active = min(max(1, workers), batch, cpus)
        return max(1, cpus // active)

    assert omp_threads(8, 8) == 2
    assert omp_threads(10, 10) == 3
    assert omp_threads(4, 4) == 1
    assert omp_threads(8, 1) == 8


def test_eval_workers_full_container_cpus() -> None:
    import math

    for cpus, frac, want in ((8, 1.0, 8), (10, 1.0, 10), (8, 0.75, 6), (1, 1.0, 1)):
        got = max(1, math.floor(cpus * frac))
        assert got == want


def test_entrypoint_auto_cpu_defaults() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    cpu_defaults = (REPO_ROOT / "docker" / "cpu_defaults.sh").read_text(encoding="utf-8")
    assert "cpu_defaults.sh" in text
    assert "docker_default_eval_max_workers" in cpu_defaults
    assert "MKL_NUM_THREADS" in text
    assert "BIOLOGIX_AI_EVAL_CPU_FRACTION" in cpu_defaults
    assert 'frac="${BIOLOGIX_AI_EVAL_CPU_FRACTION:-1.0}"' in cpu_defaults


def test_entrypoint_mcp_safe_worker_and_timeout_defaults() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'export BIOLOGIX_AI_EVAL_MAX_WORKERS=1' in text
    assert 'BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S="${BIOLOGIX_AI_OPENMM_CANDIDATE_TIMEOUT_S:-540}"' in text
    assert 'BIOLOGIX_AI_MCP_TIMEOUT_MS="${BIOLOGIX_AI_MCP_TIMEOUT_MS:-600000}"' in text
    assert 'BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S="${BIOLOGIX_AI_MCP_INSTANT_TIMEOUT_S:-30}"' in text


def test_entrypoint_opencode_version_and_debug_log_level() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "opencode --version" in text
    assert "OPENCODE_LOG_LEVEL" in text
    assert 'opencode --log-level "$OPENCODE_LOG_LEVEL"' in text


def test_run_mcp_server_prefers_direct_conda_python() -> None:
    script = REPO_ROOT / "scripts" / "run_mcp_server.sh"
    text = script.read_text(encoding="utf-8")
    assert "/opt/conda/envs/${env_name}/bin/python" in text
    assert "_resolve_python" in text
    assert 'exec "$_py"' in text
    assert "mamba run" in text  # local dev fallback


def test_opencode_jsonc_local_mcp_hardening() -> None:
    cfg = REPO_ROOT / ".opencode" / "opencode.jsonc"
    text = cfg.read_text(encoding="utf-8")
    assert '"cwd": "."' in text
    assert '"timeout": 60000' in text
    assert '"PYTHONUNBUFFERED": "1"' in text
    assert '"mcp_timeout": 600000' in text
    assert '"snapshot": false' in text
    assert '"autoupdate": false' in text


def test_entrypoint_disables_opencode_autoupdate() -> None:
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "OPENCODE_DISABLE_AUTOUPDATE" in text


def test_docker_ghcr_run_supports_curl_pipe_tty() -> None:
    script = REPO_ROOT / "scripts" / "docker_ghcr_run.sh"
    text = script.read_text(encoding="utf-8")
    assert "_run_docker" in text
    assert "< /dev/tty" in text
    assert "[[ -t 0 ]]" in text
    assert "_restore_host_tty" in text
    # Startup restore before docker (not only EXIT trap)
    assert text.index("_restore_host_tty") < text.index("_run_docker")


def test_host_docker_tty_guard_wires_restore_on_host() -> None:
    guard = REPO_ROOT / "scripts" / "host_docker_tty_guard.sh"
    run_sh = REPO_ROOT / "scripts" / "docker_run.sh"
    compose_sh = REPO_ROOT / "scripts" / "docker_compose_run.sh"
    assert guard.is_file()
    guard_text = guard.read_text(encoding="utf-8")
    assert "restore_terminal.sh" in guard_text
    assert "trap" in guard_text and "restore_host_terminal" in guard_text
    for script in (run_sh, compose_sh):
        text = script.read_text(encoding="utf-8")
        assert "host_docker_tty_guard.sh" in text
        assert "exec docker" not in text


def test_dockerfile_pins_opencode_version() -> None:
    dockerfile = REPO_ROOT / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert 'OPENCODE_VERSION="1.17.4"' in text
    assert "opencode upgrade" in text
    assert "OPENCODE_DISABLE_AUTOUPDATE=true" in text
    assert 'git>=2.40' in text
    assert "pymol-open-source" in text
