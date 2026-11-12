# repo-tooling

A small development toolkit shared across projects: a public status
dashboard generated from a repo's own history, a plan/decision-record
format, and a two-tier pre-public gate. One repository, three ways of
being consumed — pick per piece, nothing forces the others.

## The pieces

| Piece | What it is | How a project gets it |
|---|---|---|
| **Dashboard** | `tools/render_dashboard.py` — builds a status page (index.html + STATE.md) from the project's commit log, `adr/`, `PLAN.md` and `project.public.yaml`. Nothing else is read. | CI: the reusable workflow renders and publishes to GitHub Pages |
| **Gate checks** | `tools/ci_checks.py` + gitleaks — hard-fail on secrets and blocked personal patterns in messages/diffs; warn-only on commit format and uncited plan steps | CI: the reusable workflow; locally: the pre-push dispatcher runs the same script |
| **Plan & ADR formats** | `PLAN.md` (steps with stable IDs + checkboxes) and `adr/ADR-NNN.json` records | Copied into the project once by `repo-kit init`; then they belong to the project |
| **Hooks** | `hooks/pre-push` — a dispatcher that runs every executable in `tools/checks/`; adding a tool to the toolkit = adding one script there | `repo-kit hooks` (also part of `init`) |

## Consuming, in practice

- **CI is a library call.** A project's `.github/workflows/gate.yml` is
  ~15 lines ending in `uses: <owner>/repo-tooling/.github/workflows/gate.yml@v0.1.0`.
  Pin a version like any dependency; bump deliberately.
- **In-repo files are copied, not synced.** `repo-kit init` copies only
  `templates/` content (plus the thin caller workflows) into the target
  repo — never the tools, hooks or workflows machinery. It refuses to
  overwrite project content, and merges the tooling's `.gitignore` rules
  as a marker-delimited block inside the project's single `.gitignore`
  (git allows one `.gitignore` per directory; the block is how managed
  rules coexist with the project's own — re-running init refreshes just
  the block).
- **Local checks are installed once per repo** by `repo-kit`, and read
  the tooling *checkout* at push time — so when the toolkit gains a
  linter or formatter check (a new script in `tools/checks/`), existing
  repos pick it up when their tooling checkout updates. No reinstall.

Machine-level binaries (gitleaks, future linters/formatters) are **not**
distributed here — they are pinned and installed by the development
environment (e.g. a dev container image) like any other dev tool. This
repo distributes the *wiring*: scripts, workflows, templates, hooks.

## The one hard rule

Personal pattern lists (the gate's blocked names, emails, paths) are
**never** in this repo or any project repo. CI receives them as a
repository secret (`GATE_PATTERNS`); local hooks read them from a local
file (`GATE_PATTERNS_FILE`). This repo contains only the mechanism that
consumes them.

## Layout

- `tools/` — renderer + gate checks + `checks/` dispatcher scripts
- `.github/workflows/` — the reusable workflows projects call
- `templates/` — in-repo seeds + thin caller workflows + gitignore block
- `hooks/` — the pre-push dispatcher
- `install/repo-kit` — the installer (`init` / `hooks`)

Versioning: git tags (`v0.1.0`, …). Caller workflows pin a tag; the
renderer and checks are stdlib-only Python, no dependencies.
