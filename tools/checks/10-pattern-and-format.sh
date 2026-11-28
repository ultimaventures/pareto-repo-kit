#!/bin/sh
# Check: commit messages + diff vs the personal pattern list, plus the
# soft format/plan reminders. Delegates to ci_checks.py — its exit code IS
# the tier decision (1 = hard pattern failure, 0 = clean or warnings only).
# The pattern lists are local-only. They are found, in order:
#   1. $GATE_PATTERNS_FILE, if set (may hold several paths joined
#      by ':' — all of them are used)
#   2. patterns-strict.txt AND patterns-relaxed.txt at the root of
#      the tooling checkout — BOTH are loaded and their patterns
#      unioned (strict is the relaxed list's additions, never a
#      replacement, so the two files cannot drift apart). The
#      relaxed list is the one `repo-kit setup` builds and the only
#      one CI ever receives (as the GATE_PATTERNS secret); the
#      strict list never leaves this machine.
# Discovery itself is gate_common's one rule (tools/gate_common.py);
# this script only asks it, so the shell carries no second
# implementation of the rule.
set -u
HERE=$(dirname "$0")
TOOLING_ROOT=$(cd "$HERE/../.." && pwd)
if [ -z "${GATE_PATTERNS_FILE:-}" ]; then
  GATE_PATTERNS_FILE=$(python3 -E -s "$HERE/../gate_common.py" \
    pattern-files "$TOOLING_ROOT")
  [ -n "$GATE_PATTERNS_FILE" ] && export GATE_PATTERNS_FILE
fi
RANGE="${1:-HEAD~1..HEAD}"
exec python3 -E -s "$HERE/../ci_checks.py" --repo . --range "$RANGE"
