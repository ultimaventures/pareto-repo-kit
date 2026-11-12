#!/bin/sh
# Check: language-agnostic file hygiene over the outgoing range
# (conflict markers / invalid JSON / oversized blobs hard-fail;
# trailing whitespace / missing final newline warn). Stdlib only.
set -u
HERE=$(dirname "$0")
RANGE="${1:-HEAD~1..HEAD}"
exec python3 "$HERE/../file_hygiene.py" --repo . --range "$RANGE"
