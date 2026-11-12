#!/bin/sh
# Check: language-agnostic file hygiene over the outgoing range.
#
# Primary implementation: the maintained `pre-commit-hooks` package
# (the pre-commit project's official hooks — check-merge-conflict,
# check-json/yaml/toml, check-added-large-files, detect-private-key,
# trailing-whitespace-fixer, end-of-file-fixer), pinned wherever it is
# installed (dev image / CI). This toolkit does not hand-roll what a
# maintained library already does.
#
# Fallback: if the package is not installed, tools/file_hygiene.py
# (stdlib-only subset: conflict markers, JSON validity, oversized
# blobs hard; whitespace/final-newline warn) runs instead, so the check
# degrades instead of disappearing. REPO_TOOLING_HYGIENE_FALLBACK=1
# forces the fallback (used by tests).
#
# Tiers match the rest of the gate: structural problems hard-fail;
# whitespace/final-newline are warnings. The fixers may repair the
# working-tree copy — that is reported as a warning, since the push
# itself still carries the unfixed content.
set -u
HERE=$(dirname "$0")
RANGE="${1:-HEAD~1..HEAD}"

if [ "${REPO_TOOLING_HYGIENE_FALLBACK:-}" != "1" ] && \
   command -v check-merge-conflict >/dev/null 2>&1; then
  FILES=$(git diff --name-only "$RANGE" -- . | while IFS= read -r f; do
    [ -f "$f" ] && printf '%s\n' "$f"
  done)
  if [ -z "$FILES" ]; then
    echo "file hygiene (pre-commit-hooks): no changed files"
    exit 0
  fi
  fail=0
  # --assume-in-merge is REQUIRED: without it check-merge-conflict is a
  # no-op outside an in-progress merge, and this check exists to catch
  # markers that were committed, which is exactly the not-in-merge case.
  # shellcheck disable=SC2086
  check-merge-conflict --assume-in-merge $FILES || fail=1
  # shellcheck disable=SC2086
  check-added-large-files --maxkb 500 $FILES || fail=1
  # shellcheck disable=SC2086
  detect-private-key $FILES || fail=1
  for f in $FILES; do
    case "$f" in
      *.json) check-json "$f" || fail=1 ;;
      *.yml|*.yaml) check-yaml "$f" || fail=1 ;;
      *.toml) check-toml "$f" || fail=1 ;;
    esac
  done
  # shellcheck disable=SC2086
  if ! trailing-whitespace-fixer $FILES >/dev/null 2>&1; then
    echo "::warning::File hygiene: trailing whitespace found (working" \
         "tree auto-fixed; the push still carries it — re-commit)." \
         "(Reminder only.)"
  fi
  # shellcheck disable=SC2086
  if ! end-of-file-fixer $FILES >/dev/null 2>&1; then
    echo "::warning::File hygiene: a file lacked a final newline" \
         "(working tree auto-fixed; the push still carries it)." \
         "(Reminder only.)"
  fi
  if [ "$fail" -ne 0 ]; then
    echo "file hygiene (pre-commit-hooks): hard failure(s) above"
    exit 1
  fi
  echo "file hygiene (pre-commit-hooks): clean"
  exit 0
fi

echo "file hygiene: pre-commit-hooks not installed — stdlib fallback" \
     "(subset; install the pinned package for the full check set)"
exec python3 "$HERE/../file_hygiene.py" --repo . --range "$RANGE"
