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
The binaries the local checks call are declared in
**`tools/dependencies.txt`** — the dependency manifest (name, kind,
version, release checksums for binaries). It is the single source of
truth environments pin from: when a check gains an external
dependency, the manifest entry lands in the same change, and
environments that compare the manifest against what they installed
(devbox does, at session start) surface any drift instead of letting
the check silently skip.

## The one hard rule — and your pattern list

Personal pattern lists (the gate's blocked names, emails, paths) are
**never committed anywhere**. This repo contains only the mechanism that
consumes them. To make yours:

1. Run **`repo-kit setup`** — a short questionnaire (your emails,
   domains, handles, infrastructure names; it detects your username for
   the machine-path patterns) that writes the file for you, correctly
   escaped and formatted. Every prompt has a matching flag, so the same
   setup is scriptable. Or, if you prefer by hand: copy
   `templates/patterns.example.txt` to **`patterns.txt` in the root
   of your local tooling checkout** and replace the examples with your
   own values (the template's comments say what to include).
2. That's the whole setup. The tooling repo ships a `.gitignore` that
   ignores `patterns.txt`, so it **cannot be committed or pushed** from
   the tooling checkout, and because it is untracked, updating the
   tooling (`git pull`) can never overwrite it. The local checks find it
   automatically at that location; `GATE_PATTERNS_FILE` can point
   elsewhere if you prefer.
3. For CI, paste the file's contents into a secret named `GATE_PATTERNS`
   in each project repo. Same list, second home — the secret is the CI
   mirror, the local file is the original.

Never put `patterns.txt` inside a *project* repo, even ignored: the list
is a map of what you want hidden, and project repos are the things being
published. If a check ever reports "pattern tier skipped", the list
wasn't found — the check prints how many patterns it loaded when it
works, so a silent no-op can't masquerade as a pass.

**Inside devbox the list never enters the container at all.** When
`REPOKIT_PATTERNS_URL` + `REPOKIT_PATTERNS_TOKEN` are set (dev-agent
sets them per session), `ci_checks.py` runs in *service mode*: it
POSTs the content it would have scanned — commit messages plus the
diff — to a scan service on the host, which answers pass/fail plus
category names only. Unreachable service, timeout, or a host with no
patterns configured all fail **closed** (the push is blocked; pushing
from a host terminal, where the list is readable directly, is the
escape hatch). With the variables absent, local behaviour is
unchanged.

## Layout

- `tools/` — renderer + gate checks + `checks/` dispatcher scripts
  (pattern/format, gitleaks, file hygiene, shellcheck) +
  `dependencies.txt`, the external-tool dependency manifest
- `.github/workflows/` — the reusable workflows projects call
- `templates/` — in-repo seeds + thin caller workflows + gitignore block
- `hooks/` — the pre-push dispatcher
- `install/repo-kit` — the installer (`init` / `hooks` / `setup` / `env`)

File hygiene uses the maintained **pre-commit-hooks** package where it
is installed (pinned in the dev environment / CI), falling back to a
stdlib subset (`tools/file_hygiene.py`) so the check degrades instead of
vanishing. No check in this toolkit ever downloads anything at run time:
binaries are baked into the environment with pinned versions, and a
missing binary means a loud skip, never a fetch.

`repo-kit env` prints a tooling status report (which tools are present,
at what version; whether patterns.txt exists) and, with
`--hooks-root DIR`, installs/refreshes hooks in every git repo under
DIR. It is idempotent and fail-open, designed to be called from a dev
container's entrypoint so a fresh container starts with hooks already in
place.

Versioning: git tags (`v0.1.0`, …). Caller workflows pin a tag; the
renderer and checks are stdlib-only Python, no dependencies.
