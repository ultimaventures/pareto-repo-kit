#!/bin/sh
# Check: ShellCheck on shell files changed in the range (by
# extension or shebang), grading the range's BLOBS via
# tools/shellcheck_check.py (v0.4.8 — the working tree's bytes
# and a newline-mangled name list were the pre-v0.4.8 story).
# The binary is pinned in the dev environment / CI, never
# fetched at hook time. A missing binary is a coverage gap
# ONLY when the range actually contains shell files (round 6
# item 2): the enumeration runs FIRST, so a repo with nothing
# to lint never hears about the missing binary at all.
set -u
HERE=$(dirname "$0")
RANGE="${1:-HEAD~1..HEAD}"
_COUNT=$(python3 -E -s "$HERE/../shellcheck_check.py" --repo . \
  --range "$RANGE" --list) || {
  echo "shellcheck: the range could not be read — failing closed"
  exit 1
}
if [ "$_COUNT" = "0" ]; then
  echo "shellcheck: no shell files in range"
  exit 0
fi
if ! command -v shellcheck >/dev/null 2>&1; then
  # A skipped lint is a COVERAGE GAP (round 6, owner ruling
  # 2026-11-18): in agent mode it fails closed (the
  # supervisor's image is expected to provide the binary);
  # in personal mode the shared helper
  # (tools/coverage_gap.py) asks on the terminal and fails
  # closed where there is none.
  if [ "${REPOKIT_AGENT_MODE:-0}" = "1" ]; then
    echo "shellcheck not installed — shell lint CANNOT run in" >&2
    echo "agent mode: failing closed; the supervisor must install" >&2
    echo "shellcheck in the agent's image." >&2
    exit 1
  fi
  if python3 "$HERE/../coverage_gap.py" \
      "shellcheck is not installed — the shell lint cannot run." \
      "Next steps: install shellcheck (e.g. brew install shellcheck), or if it is installed, put its directory on PATH. To skip this check permanently for THIS repo: repo-kit checks --off shellcheck. To proceed despite the gap for one push: REPOKIT_ALLOW_COVERAGE_GAPS=1 git push …"; then
    exit 0
  fi
  exit 1
fi
exec python3 -E -s "$HERE/../shellcheck_check.py" --repo . \
  --range "$RANGE"
