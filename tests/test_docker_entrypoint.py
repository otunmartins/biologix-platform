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
    # Must not exec opencode directly — trap must run on exit.
    assert "exec opencode" not in text
    assert "opencode ." in text
