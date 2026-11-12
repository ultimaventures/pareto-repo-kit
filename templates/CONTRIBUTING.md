# Contributing

## Commit messages are the project's public activity feed

The status dashboard is generated from this repo's commit log, so commit
**subjects** are written for an audience:

- Start with a type prefix: `feat:`, `fix:`, `docs:`, `adr:`, `test:`,
  `refactor:`, `chore:`, `build:`, `ci:` — e.g. `feat: kit-mount status
  shows cache age`.
- Keep the subject short and self-contained; put detail in the body.
- If the commit completes a plan step, cite its ID: `feat: mount smoke
  test (ns-4)`. The dashboard counts a step as done when a commit cites
  its ID (or when its box is ticked in `PLAN.md`) — no separate update
  needed.
- A decision lands as an ADR file in `adr/` with an `adr:` commit, and
  the decided item is removed from `PLAN.md`'s open-decisions list in the
  same commit.

## The gate

Two tiers of automated checks run on every PR/push:

- **Hard failures** (block merge): secret formats (gitleaks) and blocked
  personal patterns in commit messages or diffs — instance data such as
  real account/remote names, personal email addresses, machine paths.
  Public history cannot be unpublished, so these are strict. If you hit a
  false positive, say so in the PR; allowlisting is a maintainer decision
  with a one-line reason recorded.
- **Warnings** (never block): a subject without a type prefix, or a
  change that cites no plan step — reminders that list the currently open
  steps so you can check in five seconds whether one applies.

Personal configuration is git-ignored by design: repos ship example
templates (e.g. `config/settings.example.yaml`), and real config files
never leave your machine.

## Local pre-push

Installed by `repo-kit` (from the repo-tooling repo). The hook runs the
same checks as CI — plus gitleaks, if installed — before anything leaves
your machine. Point it at your private pattern list (never committed):

    export GATE_PATTERNS_FILE=/path/to/your/local/patterns-ongoing.txt
