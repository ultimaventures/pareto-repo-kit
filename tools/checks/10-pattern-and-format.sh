#!/bin/sh
# Check: commit messages + diff vs the personal pattern list, plus the
# soft format/plan reminders. Delegates to ci_checks.py — its exit code IS
# the tier decision (1 = hard pattern failure, 0 = clean or warnings only).
# The pattern list is local-only. It is found, in order:
#   1. $GATE_PATTERNS_FILE, if set
#   2. patterns.txt at the root of the tooling checkout (the documented
#      home for it — copied from templates/patterns.example.txt and
#      git-ignored by the tooling repo itself)
set -u
HERE=$(dirname "$0")
TOOLING_ROOT=$(cd "$HERE/../.." && pwd)
if [ -z "${GATE_PATTERNS_FILE:-}" ] && [ -f "$TOOLING_ROOT/patterns.txt" ]; then
  GATE_PATTERNS_FILE="$TOOLING_ROOT/patterns.txt"
  export GATE_PATTERNS_FILE
fi
RANGE="${1:-HEAD~1..HEAD}"
exec python3 "$HERE/../ci_checks.py" --repo . --range "$RANGE"
