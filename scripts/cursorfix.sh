#!/usr/bin/env bash
# Rewrite commits authored or committed by Cursor (or matching patterns) to a
# canonical human identity, then force-push a full mirror of the remote.
#
# Usage:
#   TARGET_NAME="Your Name" TARGET_EMAIL="you@example.com" \
#     ./scripts/cursorfix.sh git@github.com:ORG/REPO.git
#
# Optional environment:
#   CURSOR_EMAILS   comma-separated (default: cursor noreply + cursor.com)
#   CURSOR_NAMES    comma-separated display names
#   CURSOR_SUBSTRINGS comma-separated substrings matched in name or email (lower)
#   WORKDIR         existing empty path or unset (uses mktemp under /tmp)
#   KEEP_WORKDIR=1  leave the bare clone on disk for inspection
#
# Requires: git, python3 (for git-filter-repo). Installs git-filter-repo via
# Homebrew when available, otherwise pip install --user (extends PATH for the run).
set -euo pipefail

REPO_URL="${1:-}"
TARGET_NAME="${TARGET_NAME:-$(git config --global --get user.name 2>/dev/null || true)}"
TARGET_EMAIL="${TARGET_EMAIL:-$(git config --global --get user.email 2>/dev/null || true)}"
CURSOR_EMAILS="${CURSOR_EMAILS:-cursoragent@users.noreply.github.com,cursoragent@cursor.com}"
CURSOR_NAMES="${CURSOR_NAMES:-cursoragent,Cursor Agent,Cursoragent}"
CURSOR_SUBSTRINGS="${CURSOR_SUBSTRINGS:-cursoragent,cursor agent,@cursor.com}"

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: Missing repository URL."
  echo "Example: $0 git@github.com:ORG/REPO.git"
  exit 1
fi

if [[ -z "$TARGET_NAME" || -z "$TARGET_EMAIL" ]]; then
  cat <<MSG
ERROR: Missing TARGET_NAME or TARGET_EMAIL.
Set them explicitly, for example:
  TARGET_NAME="Your Name" TARGET_EMAIL="you@example.com" $0 $REPO_URL
Or configure your global git identity:
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
MSG
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is not installed."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is not installed (required for git-filter-repo)."
  exit 1
fi

_append_user_python_bin_to_path() {
  local user_base
  user_base="$(python3 -m site --user-base 2>/dev/null || true)"
  if [[ -n "$user_base" && -d "$user_base/bin" ]]; then
    export PATH="${user_base}/bin:${PATH}"
  fi
}

ensure_git_filter_repo() {
  if git filter-repo -h >/dev/null 2>&1; then
    return 0
  fi

  echo "git-filter-repo not found; attempting install..."
  if command -v brew >/dev/null 2>&1; then
    brew install git-filter-repo
  elif command -v pipx >/dev/null 2>&1; then
    pipx install git-filter-repo
  else
    python3 -m pip install --user git-filter-repo
    _append_user_python_bin_to_path
  fi

  if ! git filter-repo -h >/dev/null 2>&1; then
    cat <<MSG
ERROR: git-filter-repo is still not available after install.
If you used pip install --user, ensure your PATH includes Python's user bin, e.g.:
  export PATH="\$(python3 -m site --user-base)/bin:\$PATH"
MSG
    exit 1
  fi
}

ensure_git_filter_repo

if [[ -n "${WORKDIR:-}" ]]; then
  if [[ -e "$WORKDIR" ]]; then
    echo "ERROR: WORKDIR already exists: $WORKDIR"
    exit 1
  fi
  mkdir -p "$WORKDIR"
else
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/cursorfix-mirror-XXXXXXXX")"
fi

cleanup() {
  if [[ -z "${KEEP_WORKDIR:-}" && -n "${WORKDIR:-}" && -e "$WORKDIR" ]]; then
    rm -rf "$WORKDIR"
  fi
}
trap cleanup EXIT

echo "Cloning mirror: $REPO_URL"
echo "Bare repo: $WORKDIR"
git clone --mirror "$REPO_URL" "$WORKDIR"

cd "$WORKDIR"

export TARGET_NAME TARGET_EMAIL CURSOR_EMAILS CURSOR_NAMES CURSOR_SUBSTRINGS

CALLBACK_FILE="$(mktemp)"
trap 'rm -f "$CALLBACK_FILE"; cleanup' EXIT

cat > "$CALLBACK_FILE" <<'PY'
import os

# This block is the body of git-filter-repo's per-commit callback: it runs once
# per commit. Do not use module-level caches assigned with "=" here (they reset
# every call). Rebuilding cfg from os.environ is cheap.


def _split_csv(value):
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _cfg_from_environ():
    def _lower_bytes_set(items):
        out = set()
        for item in items:
            out.add(item.lower().encode("utf-8"))
        return out

    return {
        "target_name": os.environ["TARGET_NAME"].encode("utf-8"),
        "target_email": os.environ["TARGET_EMAIL"].encode("utf-8"),
        "cursor_emails": _lower_bytes_set(_split_csv(os.environ.get("CURSOR_EMAILS", ""))),
        "cursor_names": _lower_bytes_set(_split_csv(os.environ.get("CURSOR_NAMES", ""))),
        "cursor_substrings": [
            s.lower().encode("utf-8")
            for s in _split_csv(os.environ.get("CURSOR_SUBSTRINGS", ""))
        ],
    }


def _looks_like_cursor(name_b, email_b, cfg):
    nl = name_b.lower()
    el = email_b.lower()
    if el in cfg["cursor_emails"] or nl in cfg["cursor_names"]:
        return True
    return any(sub in nl or sub in el for sub in cfg["cursor_substrings"])


cfg = _cfg_from_environ()

if _looks_like_cursor(commit.author_name, commit.author_email, cfg):
    commit.author_name = cfg["target_name"]
    commit.author_email = cfg["target_email"]

if _looks_like_cursor(commit.committer_name, commit.committer_email, cfg):
    commit.committer_name = cfg["target_name"]
    commit.committer_email = cfg["target_email"]
PY

echo "Rewriting history with git filter-repo..."
# shellcheck disable=SC2046
git filter-repo --force --commit-callback "$(cat "$CALLBACK_FILE")"
rm -f "$CALLBACK_FILE"

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REPO_URL"
fi

# GitHub rejects pushes to refs/pull/* ("hidden refs"). Mirror clones may still
# carry them; delete locally so --mirror push succeeds.
echo "Dropping refs/pull/* if present (GitHub hidden refs)..."
git for-each-ref refs/pull --format='%(refname)' 2>/dev/null | while IFS= read -r ref; do
  [[ -z "$ref" ]] && continue
  git update-ref -d "$ref"
done

echo "Force pushing rewritten refs to origin (mirror)..."
git push --force --mirror origin

echo "Done. Tell collaborators to re-clone or reset. GitHub contributor stats can lag."
