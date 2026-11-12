#!/bin/sh
# Check: ShellCheck on shell files changed in the range (by extension or
# shebang). Skips loudly if shellcheck is not installed — the binary is
# pinned in the dev environment / CI, never fetched at hook time.
set -u
RANGE="${1:-HEAD~1..HEAD}"
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck not installed — shell lint SKIPPED locally (CI or" \
       "the dev image provides it)."
  exit 0
fi
FILES=""
for f in $(git diff --name-only "$RANGE" -- .); do
  [ -f "$f" ] || continue
  case "$f" in
    *.sh|*.bash) FILES="$FILES $f" ;;
    *) head -1 "$f" 2>/dev/null | grep -qE '^#!.*\b(ba|z|k)?sh\b' && \
       FILES="$FILES $f" ;;
  esac
done
if [ -z "$FILES" ]; then
  echo "shellcheck: no shell files in range"
  exit 0
fi
echo "shellcheck: linting$FILES"
# shellcheck disable=SC2086
exec shellcheck $FILES
