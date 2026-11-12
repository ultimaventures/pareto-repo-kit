#!/bin/sh
# Check: commit messages + diff vs the personal pattern list, plus the
# soft format/plan reminders. Delegates to ci_checks.py — its exit code IS
# the tier decision (1 = hard pattern failure, 0 = clean or warnings only).
# The pattern list is local-only: GATE_PATTERNS_FILE points at it.
set -u
HERE=$(dirname "$0")
RANGE="${1:-HEAD~1..HEAD}"
exec python3 "$HERE/../ci_checks.py" --repo . --range "$RANGE"
