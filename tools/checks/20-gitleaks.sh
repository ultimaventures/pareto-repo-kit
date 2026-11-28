#!/bin/sh
# Check: gitleaks secret scan over the outgoing range. If the gitleaks
# binary is not installed: in AGENT MODE, or under the security
# profile, the environment is supervisor-built and guarantees the
# scanner, so its absence means the environment is broken and the
# check fails closed (v0.4.2). In PERSONAL mode a missing scanner is
# a COVERAGE GAP (v0.4.6, owner ruling — the code below has done
# this since; an earlier version of this header still described the
# pre-v0.4.6 "skips loudly" behaviour): the shared helper
# (tools/coverage_gap.py) asks on the terminal, and with no terminal
# the check fails with the remedy in the message — install the
# scanner, accept the gap for one push with
# REPOKIT_ALLOW_COVERAGE_GAPS=1, or disable the check for this repo
# with `repo-kit checks --off gitleaks`. CI runs the full-history
# scan regardless, as the backstop.
#
# AGENT MODE (v0.4.4, reviewer round 4 item 1): gitleaks otherwise
# takes configuration from the repo being scanned — a .gitleaks.toml
# or .gitleaksignore the agent wrote, or inline gitleaks:allow
# comments in its code. In agent mode the scan therefore runs ONLY
# on the supervisor's configuration, which ships in the tooling
# checkout: --config names tools/gitleaks.toml ([extend]
# useDefault, so the default rules apply and the repo's own config
# is not loaded), --gitleaks-ignore-path names the tooling's empty
# ignore directory, and --ignore-gitleaks-allow makes inline allow
# comments inert. Third-party testing against gitleaks 8.30 found
# the ignore-path flag ADDS a source in some scan modes rather
# than replacing the repo-root lookup, so a .gitleaksignore at the
# scanned repo's root additionally fails this check closed in
# agent mode: the supervisor blesses suppressions by putting them
# in the supervisor config, never by the scanned repo's say-so.
# The dispatcher's environment scrub has already removed every
# GITLEAKS_* variable (GITLEAKS_CONFIG and GITLEAKS_CONFIG_TOML
# are alternate config channels). In personal mode none of this
# applies — the user's own repo config is theirs to use.
#
# ATTRIBUTE IMMUNITY (v0.4.7, round 6 item 3): every invocation
# below passes --text in its log-opts. A file whose diff is
# suppressed by git attributes (-diff / binary, from ANY source
# — a committed .gitattributes, .git/info/attributes, or a
# config-named attributes file) is otherwise invisible to a
# diff-reading scanner. --text overrides the diff attribute
# from every source; verified on the real gitleaks 8.30.1
# (all three routes become visible; GIT_ATTR_SOURCE, the
# alternative, still missed info/attributes). This is the
# gate's standing rule for external diff consumers — internal
# checks take the other door and extract content from blobs
# (tools/ci_checks.py), so no scanner in the gate reads an
# attributed diff.
set -u
RANGE="${1:-HEAD~1..HEAD}"
if ! command -v gitleaks >/dev/null 2>&1; then
  if [ "${REPOKIT_AGENT_MODE:-0}" = "1" ] || \
     [ "${REPOKIT_CHECK_PROFILE:-}" = "security" ]; then
    echo "gitleaks not installed — secret scan CANNOT run, and" >&2
    echo "agent mode or the security profile is in use: this" >&2
    echo "environment is expected to provide the scanner. Failing" >&2
    echo "closed; the supervisor must install gitleaks >= 8.30.1 here." >&2
    exit 1
  fi
  # Personal mode: a skipped secret scan is a COVERAGE GAP
  # (round 6, owner ruling 2026-11-18) — a printed notice
  # scrolls past and ships the gap, so ask on the terminal
  # (/dev/tty: the hook's stdin is git's ref-line pipe), and
  # fail closed where there is none (CI, GUI clients), with
  # the next steps in the message.
  if [ "${REPOKIT_ALLOW_COVERAGE_GAPS:-}" = "1" ]; then
    echo "gitleaks not installed — secret scan SKIPPED, accepted" \
         "for this push via REPOKIT_ALLOW_COVERAGE_GAPS."
    exit 0
  fi
  # Probe by OPENING the device in a subshell: [ -r /dev/tty ]
  # The decision itself lives in the shared helper
  # (tools/coverage_gap.py — terminal prompt with timeout,
  # per-push memoization, sanitized output; round 6 item 5).
  HERE=$(dirname "$0")
  if python3 "$HERE/../coverage_gap.py" \
      "gitleaks is not installed — the secret scan cannot run." \
      "Next steps: install gitleaks >= 8.30.1 (e.g. brew install gitleaks), or if it is installed, put its directory on PATH. To skip this check permanently for THIS repo: repo-kit checks --off gitleaks. To proceed despite the gap for one push: REPOKIT_ALLOW_COVERAGE_GAPS=1 git push …"; then
    exit 0
  fi
  exit 1
fi
if [ "${REPOKIT_AGENT_MODE:-}" = "1" ]; then
  if [ -f "./.gitleaksignore" ]; then
    echo "gitleaks: a .gitleaksignore sits at this repo's root and this" >&2
    echo "install is in AGENT mode. The scanned repo does not get to" >&2
    echo "suppress findings; the supervisor moves any legitimate" >&2
    echo "suppressions into the tooling's gitleaks config. Failing" >&2
    echo "closed." >&2
    exit 1
  fi
  exec gitleaks git --redact --log-opts="$RANGE --text" \
    --config "$REPOKIT_TOOLING_HOME/tools/gitleaks.toml" \
    --gitleaks-ignore-path "$REPOKIT_TOOLING_HOME/tools/gitleaks-ignore" \
    --ignore-gitleaks-allow .
fi
exec gitleaks git --redact --log-opts="$RANGE --text" .
