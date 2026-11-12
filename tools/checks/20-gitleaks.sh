#!/bin/sh
# Check: gitleaks secret scan over the outgoing range. Skips (loudly) if
# the gitleaks binary is not installed — a skip is a known gap, and CI
# still runs the full-history scan as the backstop.
set -u
RANGE="${1:-HEAD~1..HEAD}"
if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks not installed — secret scan SKIPPED locally (CI runs it)."
  echo "Install gitleaks >= 8.30.2 to close this gap."
  exit 0
fi
exec gitleaks git --redact --log-opts="$RANGE" .
