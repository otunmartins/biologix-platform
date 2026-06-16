#!/usr/bin/env bash
# Host-side TTY restore after interactive Docker sessions (OpenCode OpenTUI).
#
# OpenTUI enables xterm SGR mouse tracking (\e[?1002h \e[?1006h).  When the
# container is killed or the session ends abruptly, the host terminal keeps
# reporting mouse moves as gibberish (e.g. 35;90;1M).  Container EXIT traps do
# not always reach the host PTY — especially after `docker kill`.
#
# Usage (from other launch scripts):
#   source "$(dirname "$0")/host_docker_tty_guard.sh"
#   docker run -it ...    # do NOT exec — the shell must survive for the trap
#
# Or run directly after a hung session:
#   bash scripts/host_docker_tty_guard.sh

if [[ -n "${BIOLOGIX_HOST_TTY_GUARD_INSTALLED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
export BIOLOGIX_HOST_TTY_GUARD_INSTALLED=1

_GUARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-${0}}")" && pwd)"
_REPO_ROOT="$(cd "${_GUARD_DIR}/.." && pwd)"
# shellcheck source=docker/restore_terminal.sh
source "${_REPO_ROOT}/docker/restore_terminal.sh"
trap 'restore_host_terminal' EXIT INT TERM HUP

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  restore_host_terminal
fi
