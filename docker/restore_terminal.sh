#!/usr/bin/env bash
# Restore the host TTY after OpenCode OpenTUI exits (especially docker run -it).
# OpenTUI enables SGR mouse tracking; if not disabled, the host terminal prints
# garbage (e.g. 35;95;8M) and keyboard input appears dead until `reset`.
#
# Safe to source or run directly on the host after docker kill as well.

restore_host_terminal() {
  local tty_dev=/dev/tty

  # Disable mouse-tracking and related DEC modes OpenTUI may enable.
  local disable_mouse=$'\e[?1000l\e[?1002l\e[?1003l\e[?1006l\e[?1015l'
  # Show cursor, leave alternate screen, reset attributes.
  local reset_attrs=$'\e[0m\e[?25h\e[?1049l'

  if [[ -w "$tty_dev" ]]; then
    printf '%s%s' "$disable_mouse" "$reset_attrs" >"$tty_dev" 2>/dev/null || true
    stty sane <"$tty_dev" 2>/dev/null || true
  else
    printf '%s%s' "$disable_mouse" "$reset_attrs" 2>/dev/null || true
    stty sane 2>/dev/null || true
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  restore_host_terminal
fi
