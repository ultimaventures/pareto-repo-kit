# ci_pattern_setup.sh -- the CI pattern-source decision, in ONE
# place (v0.4.8 fix pass, secondary review item 5 / owner choice A).
#
# SOURCED, never executed: the caller's run block does
#     . <tooling>/tools/ci_pattern_setup.sh
# so the exports below land in the step's own shell. Both call
# sites use it -- the reusable gate (.github/workflows/gate.yml,
# sourcing from its pinned .repo-tooling checkout) and this
# repo's dogfood job (.github/workflows/self-gate.yml
# this-prs-gate, sourcing from the PR checkout). Up to the fix
# pass, gate.yml carried the decision inline and self-gate
# carried only the first branch, so a fork PR to this repo ran
# red on a coverage gap the reusable handles by design.
#
# Inputs (env, set by the caller's env: block -- values arrive
# via env, never ${{ }} expansion inside a run block, per the
# template-injection rule):
#   GATE_PATTERNS   the secret's content ("" when unavailable --
#                   always the case for fork PRs, which cannot
#                   read repository secrets)
#   EVENT_NAME      github.event_name
#   IS_FORK         "true" when a PR's head repo is a fork
#   IS_DEPENDABOT   "true" when the actor is dependabot[bot]
#
# Outcomes -- exactly one:
#   secret present:  the list is written to
#                    /tmp/patterns-relaxed.txt (named on purpose:
#                    the check derives the reported tier from the
#                    filename, and the secret's source is the
#                    relaxed list) and GATE_PATTERNS_FILE is
#                    exported pointing at it.
#   secret empty, fork or Dependabot PR:  the tier genuinely
#                    cannot run here (Dependabot PRs read a
#                    SEPARATE secret store; provision now sets
#                    the secret for the Dependabot app too, so a
#                    Dependabot run landing here means the repo
#                    predates that or opted out). Failing the
#                    contributor for it would break the standard
#                    open-source flow (v0.4.2 item 4; Dependabot
#                    added v0.4.3). The warning is worded to be
#                    unmissable by whoever merges: only
#                    write-access holders can merge, so the
#                    maintainer is the gate, and they should know
#                    exactly what did not run (v0.4.3, round 3
#                    item 6c -- the fail-closed alternative was
#                    considered and rejected by the owner:
#                    per-contribution maintainer work is a
#                    non-starter, and required approvals break a
#                    solo owner's self-merge on variant A;
#                    approvals remain a documented opt-in in the
#                    ruleset). REPOKIT_PATTERN_GAP_OK=1 is
#                    exported: ci_checks would otherwise treat
#                    the missing list as a coverage gap and fail
#                    the run (v0.4.6); this dedicated opt-out
#                    restores the owner-ruled warning-only
#                    behaviour for exactly this branch (v0.4.7,
#                    round 6 item 1). It is honored only when
#                    GITHUB_ACTIONS=true and only for the
#                    no-pattern-source decision.
#   secret empty, anything else:  provisioning lapsed -- an
#                    ::error:: is printed and the step exits 1.
#                    Until v0.4.2 the tier then silently did not
#                    run and the job went green.
# shellcheck shell=sh
if [ -n "${GATE_PATTERNS:-}" ]; then
  printf '%s\n' "$GATE_PATTERNS" > /tmp/patterns-relaxed.txt
  export GATE_PATTERNS_FILE=/tmp/patterns-relaxed.txt
elif [ "${EVENT_NAME:-}" = "pull_request" ] && { [ "${IS_FORK:-}" = "true" ] || [ "${IS_DEPENDABOT:-}" = "true" ]; }; then
  echo "::warning::The personal-pattern check did NOT run on this pull request (the private pattern list is unavailable to fork and Dependabot pull requests). Merging accepts this PR unchecked against the private list."
  export REPOKIT_PATTERN_GAP_OK=1
else
  echo "::error::GATE_PATTERNS secret is empty — the personal-pattern tier would not run. Run 'repo-kit provision' to set it from patterns-relaxed.txt."
  exit 1
fi
