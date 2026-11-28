#!/bin/sh
# Check: language-agnostic file hygiene over the outgoing range.
#
# Primary implementation: the maintained `pre-commit-hooks` package
# (the pre-commit project's official hooks), pinned wherever it is
# installed (dev image / CI). This toolkit does not hand-roll what a
# maintained library already does. Fallback: tools/file_hygiene.py's
# stdlib subset, so the check degrades instead of disappearing.
# REPO_TOOLING_HYGIENE_FALLBACK=1 forces the fallback (used by tests).
#
# Both modes grade the range's BLOBS (v0.4.8, secondary review
# passes 3 + 6): the driver (tools/file_hygiene.py, via
# tools/check_blobs.py) extracts each distinct blob the range
# publishes and checks THAT — never the working tree's current
# bytes. The fixers therefore report instead of repairing: a
# warning names the committed content; fixing happens in the
# working tree, by the user, followed by a re-commit.
set -u
HERE=$(dirname "$0")
RANGE="${1:-HEAD~1..HEAD}"

# In agent mode the fallback selector is not the shell's to
# choose (round 5 item 3): the dispatcher scrubs the variable,
# and this script ignores it anyway — defense in depth, since
# the pinned primary package is part of the supervisor's image
# and choosing the weaker subset is a gate decision.
if [ "${REPOKIT_AGENT_MODE:-0}" = "1" ]; then
  REPO_TOOLING_HYGIENE_FALLBACK=""
fi

if [ "${REPO_TOOLING_HYGIENE_FALLBACK:-}" != "1" ] && \
   command -v check-merge-conflict >/dev/null 2>&1; then
  exec python3 -E -s "$HERE/../file_hygiene.py" --repo . \
    --range "$RANGE" --mode primary
fi

echo "file hygiene: pre-commit-hooks not installed — stdlib fallback" \
     "(subset; install the pinned package for the full check set)"
exec python3 -E -s "$HERE/../file_hygiene.py" --repo . \
  --range "$RANGE" --mode fallback
