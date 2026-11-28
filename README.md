# Pareto Easy Repo Kit

The Pareto idea applied to repository setup: the small set of steps that
gets a new repo most of the benefit — a public status dashboard generated
from the repo's own history, a plan/decision-record format, a two-tier
pre-public gate, and one-command GitHub provisioning. One repository,
several ways of being consumed — pick per piece, nothing forces the
others. The toolkit's command is `repo-kit` (see `install/repo-kit`);
the checkout folder may be named anything.

## What do you want to do?

**You never copy folders out of this repo by hand.** Every capability
is one `repo-kit` command (run from anywhere — it finds its own files
inside this checkout) or, for CI, one workflow line:

| Goal | Run |
|---|---|
| Scaffold a new project repo — plan/ADR seed files, thin caller workflows, the `.gitignore` tooling block, pre-push hooks | `repo-kit init [REPO]` |
| Build (or rebuild) your private pattern list | `repo-kit setup` |
| Install/refresh the pre-push checks in one repo | `repo-kit hooks [REPO]` |
| Switch one check off (or back on) for one repo — the escape hatch when a check can't run in your environment | `repo-kit checks [REPO] [--off gitleaks] [--on gitleaks]` |
| Remove everything repo-kit installed in one repo (hook, `.gitignore` block, unmodified template workflows) and report what was left | `repo-kit uninstall [REPO]` |
| Report this machine's tooling status (built for a dev container's entrypoint; `--hooks-root DIR` also installs hooks in every repo under DIR) | `repo-kit env [--hooks-root DIR]` |
| Turn **agent mode** on/off for this tooling checkout (or report which mode it's in) — the mode for a containerized agent with its own identity; supervisors run this, agents never do | `repo-kit enforce [on\|off]` |
| Bring a public GitHub repo up to the setup standard — repo settings, security features, the `main` branch ruleset | `repo-kit provision OWNER/REPO [--variant A\|B] [--dry-run]` |
| Build a review packet for everything committed since the last one — to hand to any reviewer | `repo-kit packet` (memory: `--history`, `--rollback N`, `--reset`; custom start: `--base REF`) |
| Use the dashboard + gate in CI, installing nothing locally | A thin caller workflow ending in `uses: <owner>/pareto-repo-kit/.github/workflows/gate.yml@v0.4.8` |

## The pieces

| Piece | What it is | How a project gets it |
|---|---|---|
| **Dashboard** | `tools/render_dashboard.py` — builds a status page (index.html + STATE.md) from the project's commit log, `adr/`, `PLAN.md` and `project.public.yaml`. Nothing else is read. | CI: the reusable workflow renders and publishes to GitHub Pages |
| **Gate checks** | `tools/ci_checks.py` + gitleaks — hard-fail on secrets and blocked personal patterns in messages/diffs; warn-only on commit format and uncited plan steps | CI: the reusable workflow; locally: the pre-push dispatcher runs the same script |
| **Plan & ADR formats** | `PLAN.md` (steps with stable IDs + checkboxes) and `adr/ADR-NNN.json` records | Copied into the project once by `repo-kit init`; then they belong to the project |
| **Hooks** | `hooks/pre-push` — a dispatcher that runs every executable in `tools/checks/`; adding a tool to the toolkit = adding one script there | `repo-kit hooks` (also part of `init`) |

## Consuming, in practice

- **CI is a library call.** A project's `.github/workflows/gate.yml` is
  ~15 lines ending in `uses: <owner>/pareto-repo-kit/.github/workflows/gate.yml@v0.4.8`.
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
- **Review packets live in a local-only `review/` folder.** `repo-kit
  packet` builds one self-contained markdown packet per review round
  (commit list, stats, diff, full changed-file texts) into
  `review/packets/`; reviewer feedback is dropped in `review/feedback/`.
  A nested `review/.gitignore` — seeded by `init`, created on demand by
  `packet` — keeps the whole folder out of every commit and clone:
  packets are working material, not project content. The packet memory
  (the last 10 packet endpoints) lives in the repo's `.git`, so it is
  per-clone and never committed either. Each generated packet is
  pattern-scanned against your list BEFORE it is written, and a
  match refuses the packet — nothing is written, copied or
  recorded until the matched content is removed.
- **GitHub-side settings are applied by `repo-kit provision`.** Repo
  settings, security toggles and branch rulesets cannot live in a
  file, so provision applies the setup standard to `OWNER/REPO`
  through the `gh` CLI: squash-only merges, auto-delete head branches,
  secret scanning + push protection, Dependabot, CodeQL default setup,
  private vulnerability reporting, Actions hardening, the
  `default-branch` ruleset from `templates/rulesets/`, and the
  `GATE_PATTERNS` secret (uploaded from your `patterns-relaxed.txt`;
  if you don't have one yet, provision offers to run `repo-kit setup`
  first), and — when the repo contains a dashboard workflow — the
  GitHub Pages source set to GitHub Actions. Two ruleset
  variants: **A** (solo repo — required approvals 0) and **B** (an
  agent or outside developer holds a write token — 1 approval + Code
  Owner review; pass `--bypass-user-id` with your numeric GitHub user
  id to sit on the bypass list in pull-request mode). It is
  idempotent — an existing ruleset of the same name is updated in
  place — so re-running it doubles as an audit. `--dry-run` prints
  every command and payload and executes nothing.

Machine-level binaries (gitleaks, future linters/formatters) are **not**
distributed here — they are pinned and installed by the development
environment (e.g. a dev container image) like any other dev tool. This
repo distributes the *wiring*: scripts, workflows, templates, hooks.
The binaries the kit pins are declared in
**`tools/dependencies.txt`** — the dependency manifest (name, kind,
version, release checksums for binaries), covering the local
checks' tools and the CI gate's installs alike. It is the single
source of truth for those pins: when a check gains an external
dependency, the manifest entry lands in the same change; the gate
workflow keeps verified inline copies of its pins (a workflow must
be self-contained at its tag) and `tools/check_pins.py` fails any
change where a copy disagrees with the manifest — or where a
workflow, template or README pin disagrees with `VERSION`; and
environments that compare the manifest against what they installed
(devbox does, at session start) surface any drift instead of letting
the check silently skip.

## Two modes: personal and agent

The kit has exactly two modes, because it serves two different
situations and pretending otherwise is how gates get fake
enforcement:

- **Personal mode** (the default; every fresh install): you are
  developing, possibly with an AI assistant working *as you* in
  your own terminal. The gate is your guardrail: it catches
  accidents — an agent about to commit your email address is
  stopped because the check is the path of least resistance, not
  because it couldn't go around it. Configuration may come from
  your shell, and the escape hatches (`checks --off`, `override`)
  work. A check that *cannot run* — no pattern list, a missing
  scanner — is never a silent skip: it is a coverage gap, which
  asks you on the terminal and, with no terminal, fails the push
  with the remedy in the message (see *An ordinary personal-mode
  push* below). Nothing here may make your own development
  harder. Honest limit: an
  agent running as you, with your credentials and write access
  to everything, **cannot** be enforced against — and this kit
  does not pretend otherwise.
- **Agent mode** (opt-in, via `repo-kit enforce on`): a
  containerized agent with its **own identity**, developing in
  an environment its supervisor built. There the gate takes no
  gate-critical input from the shell at all — anything the
  agent's shell can set, the agent can fake — and reads its
  service coordinates and profile only from supervisor-written
  files inside the (read-only) tooling mount. It also pins its
  own PATH and scrubs the ambient environment (every `GIT_*`
  and `GITLEAKS_*` variable, the Python interpreter variables,
  the dynamic-loader variables), because the gate's tools run
  on that state too. "No pattern source" fails closed. The
  supervisor turns agent mode on when building the container;
  the agent never can. **Keep the checkouts separate**: your
  own checkout stays personal; make a second checkout for the
  container mount and flip that one — `repo-kit enforce on`
  refuses to flip the checkout it lives in without `--yes`,
  because that checkout's own pushes would silently become
  agent-mode pushes. See **Agent mode** below.

### An ordinary personal-mode push, step by step

The map of the common path — what actually happens when you push
in personal mode, and nothing else:

1. You run `git push`. Git calls the installed pre-push hook (the
   dispatcher) once, handing it the refs being pushed on standard
   input.
2. Per ref, the dispatcher works out the commits that are actually
   new to that remote — for a brand-new branch, what your
   remote-tracking refs say the server doesn't have yet. The
   ranges are kept in a shell variable, never a temp file.
3. For each range it runs the four checks in order — patterns +
   format reminders, gitleaks, file hygiene, shellcheck — each
   with its standard input closed, so no check can eat the ref
   lines meant for the next range. The checks grade the range's
   **committed blobs**, not your working tree: uncommitted edits
   neither launder a bad commit nor block a good one.
4. A hard failure (a pattern match, a secret, a lint error in a
   committed shell script) fails the push. Warnings — commit
   format, plan-step reminders — never do.
5. A check that **cannot run** — no pattern list, gitleaks or
   shellcheck missing — is a *coverage gap*, not a skip: on a
   terminal it asks, per gap, whether to proceed (the answer
   lasts for that push only); with no terminal it fails the push
   and prints the remedy, including the one-push escape
   (`REPOKIT_ALLOW_COVERAGE_GAPS=1 git push …`) and the permanent
   per-repo one (`repo-kit checks --off <check>`). If every check
   that would run is disabled by your own `checks --off` choices,
   that's your configuration, not a gap, and the push proceeds;
   if checks exist but *none ran at all* for a reason that isn't
   your configuration, the push fails — zero checks running is
   never a pass.
6. A pattern false positive goes through `repo-kit override`
   (above): it shows you the matches, and a granted allowance is
   bound to the commit set you approved and consumed when the
   pattern tier applies it.

That is the whole path. Nothing phones home, nothing writes
outside the repo's `.git` and the checks' own output.

## The one hard rule — and your pattern list

Personal pattern lists (the gate's blocked names, emails, paths) are
**never committed anywhere**. This repo contains only the mechanism that
consumes them. To make yours:

1. Run **`repo-kit setup`** — a short questionnaire (your emails,
   domains, handles, infrastructure names; it detects your username for
   the machine-path patterns) that writes the file for you, correctly
   escaped and formatted. Every prompt has a matching flag, so the same
   setup is scriptable. Or, if you prefer by hand: copy
   `templates/patterns.example.txt` to **`patterns-relaxed.txt` in the
   root of your local tooling checkout** and replace the examples with
   your own values (the template's comments say what to include).
2. That's the whole setup. The tooling repo ships a `.gitignore` that
   ignores `patterns-relaxed.txt`, so it **cannot be committed or
   pushed** from the tooling checkout, and because it is untracked,
   updating the tooling (`git pull`) can never overwrite it. The local
   checks find it automatically at that location; `GATE_PATTERNS_FILE`
   can point elsewhere if you prefer.
3. For CI, **`repo-kit provision` uploads the file as the
   `GATE_PATTERNS` secret** in each project repo (offering to run
   setup first if you don't have the file yet). Same list, second
   home — the secret is the CI mirror, the local file is the original.
4. Optional: a **`patterns-strict.txt`** beside it — a stricter list
   you keep by hand, holding only the *extra* patterns. Local checks
   load both files and union them, so the strict file never replaces
   the relaxed one and the two can't drift apart when you edit one.
   It never leaves your machine: provision only ever uploads the
   relaxed list.

**False positives: what the check reports, and the ladder.** The
check's output never shows the pattern text or the matched content,
so it is safe for an agent to relay. A match is reported as **tier +
pattern number + location**: the number is the pattern's position
among the pattern lines of its file (count the non-comment lines of
`patterns-relaxed.txt` or `patterns-strict.txt` to find it), and the
location is the commit (for a message match) or the file and line
within the diff — locations come from the diff itself, which anyone
who can see the pull request can already see. When a match is
genuinely harmless — sample data, a third party's public address —
the resolution depends on where it failed:

- **Locally, strict-list matches** (your local-only list, which CI
  never sees): the owner runs **`repo-kit override`** in the repo,
  from an interactive terminal. It shows the matches grouped per
  pattern with counts — the first five per pattern, with an offer,
  right there in the flow, to expand any pattern's full list
  before you decide (`repo-kit override --all` prints the matches
  unprompted, up to 200 per pattern); the only place matches are
  ever shown. Approving
  grants a one-push allowance scoped to exactly the patterns
  approved and bound to the exact commit set you were shown,
  expiring in 10 minutes and consumed at the moment the pattern
  tier applies it — a different commit set under the same tip
  does not spend it. That clears a strict false positive end to end. The
  interactive-terminal requirement is a **speed bump, not a security
  boundary**: it keeps agents and scripts from granting an override
  in the normal course of things, but anything running as you with
  write access to a repo's `.git` could forge the allowance or
  bypass the local hooks entirely. The hard gate is CI. There is
  no log file of matches for anyone to go read — the command
  re-derives them on the spot, on the owner's machine.
- **Anywhere, content that shouldn't be there**: fix the content at
  the reported location and re-push. This is the normal path, and
  the local pre-push hook usually catches it before CI ever runs.
- **In CI, relaxed-list matches**: a local override does *not*
  apply — CI checkouts carry no allowance. Narrow the pattern in
  `patterns-relaxed.txt`, re-run `repo-kit provision` to update the
  `GATE_PATTERNS` secret, and re-run the check; or, on a
  **variant-B** ruleset, the repo owner can bypass-merge the pull
  request. (Variant A has no bypass actor on purpose: it is the
  variant where agents authenticate as the owner, so a bypass would
  be agent-reachable and would defeat the gate. Its remaining
  escape is a deliberate ruleset edit by the owner.)

Never put a pattern list inside a *project* repo, even ignored: the
list is a map of what you want hidden, and project repos are the things
being published. If a check ever reports "pattern tier skipped", the
list wasn't found — the check prints how many patterns it loaded when
it works, so a silent no-op can't masquerade as a pass. In **agent
mode** (`repo-kit enforce on` — see "Two modes" above), "no pattern
source at all" is instead a hard failure: an enforced gate that
checks nothing has not passed. Personal installs never see that
failure; the marker that switches modes is an untracked file a
supervisor creates on the machine, never one shipped in this repo.

**Getting out.** A gate you cannot leave is a gate people rip
out by hand, so leaving is a feature: `repo-kit uninstall`
removes everything repo-kit installed in a repo (the hook — only
when it is verifiably ours — the `.gitignore` block, workflow
files still identical to their templates) and reports what it
deliberately left; `repo-kit checks --off <name>` switches one
check off for one repo when a check cannot run in your
environment (honored in personal installs; agent mode ignores
the list and says so at push time); and `repo-kit enforce off`
turns agent mode off for a tooling checkout. One broken piece
should never lock you out of your own repository.

**Upgrading from before v0.3.0?** The wizard-built list was renamed
from `patterns.txt` to `patterns-relaxed.txt`. The old file is never
read — and since v0.4.8 nothing warns about it either (the
compatibility shims were deleted once every known install had
migrated). One rename — `mv patterns.txt patterns-relaxed.txt` in
your tooling checkout — is the whole migration; until you do it,
the pattern tier has no list, and says so as a coverage gap.

**Inside devbox the list never enters the container at all.** When
service coordinates are in effect, `ci_checks.py` runs in *service
mode*: it POSTs the content it would have scanned — commit messages
plus the added lines of each commit's diff — to a scan service on
the host, which answers pass/fail plus category names only.
Unreachable service, timeout, or a host with no patterns configured
all fail **closed** (the push is blocked; pushing from a host
terminal, where the list is readable directly, is the escape
hatch). Where the coordinates come from depends on the mode: in
personal mode, from your own shell (`REPOKIT_PATTERNS_URL` +
`REPOKIT_PATTERNS_TOKEN`, e.g. exported by your session launcher);
in agent mode, only from the supervisor-written
`hooks/enforced.env` — the pattern-service launcher refreshes that
file on every launch, and the shell is never consulted for it.
With no service in effect the local list is used instead — and in
agent mode, with neither service nor list, the check fails closed
rather than skipping.

**Running the service.** Since v0.4.0 the service ships with
repo-kit (`tools/pattern_service.py`, stdlib-only), so it versions
with the client and reads the same two list files with the same
rules — no hand-rolled copies to drift. Start it on the host with

```
repo-kit pattern-service            # containers: see the bind ladder below
```

For a container client, bind by the service docstring's EXPOSURE
ladder, stopping at the first rung that reaches: loopback first
(on Docker Desktop / OrbStack, `host.docker.internal` usually
proxies to a loopback-bound host service), then the specific
bridge/gateway address on Linux, and `--host 0.0.0.0` — every
interface, LAN included — only when neither works.

It prints the three environment variables a personal-mode client
needs; in agent mode it instead writes them into
`hooks/enforced.env` (above), where the gate reads them.
Everything else is the normal hook the checkout already has —
there is no separate agent-side integration to build. For an
agent-mode harness, the instruction block to paste into its
configuration is:

> Pattern checks in this environment are configured by the
> supervisor and run through a scan service on the host. Push
> normally — the pre-push hook runs the scan for you. If the hook
> reports the pattern service is unavailable, STOP and tell the
> owner; do not change environment variables to get around it,
> edit the hook, or push from another route. The service being
> down blocks pushes on purpose.

Supervisors with their own session lifecycle (devbox's dev-agent)
can run `tools/pattern_service.py` directly under the
`REPOKIT_PATTERN_*` environment its docstring documents — port,
counts file, instance name, and the owner's oversize-allowance
record all carry over from the devbox original.

**Agent mode.** The rule the whole mode rests on:

> Anything the agent's shell can set, the agent can fake.
> Enforcement configuration has to come from somewhere the agent
> can't write.

So in agent mode the gate believes only supervisor-written files:
whether agent mode is on (`hooks/enforced.marker`), where the
pattern service lives and which profile runs
(`hooks/enforced.env`), and pattern lists only from the tooling
checkout root. The shell is ignored for all of it — there is no
"the file wins a tie", because the shell is never asked. The
ambient process state gets the same treatment (v0.4.4): the
dispatcher pins its PATH and scrubs every `GIT_*`/`GITLEAKS_*`
variable, the Python interpreter variables and the loader
variables before anything runs, the secret scanner runs on a
gitleaks configuration that ships in the tooling (never the
scanned repo's own), and the owner override — a file in the
repo's `.git` — is a personal-mode feature the agent gate never
honours. The pattern tier also classifies every file by its
own bytes rather than git's attribute-aware labels and extracts
content from blobs, and gitleaks always runs with `--text` in
its log-opts — so git attributes cannot suppress a file from
any scanner in the gate, in either mode (v0.4.7; a v0.4.5-era
push-level guard against suppressed diffs was retired once no
scanner was left blind). The full inventory of everything that
can influence the gate, crossed off line by line, is in
`templates/agent-enforced/README.md`. To build an agent-mode
environment:

1. Run `repo-kit enforce on` on the host, in a checkout made
   for the container (not your personal one — the command
   refuses its own checkout without `--yes`) — it creates the
   marker and the env file. (The pattern-service launcher
   refreshes the env file's service coordinates on every launch
   after that.)
2. Mount (or root-own) the tooling checkout's `hooks/` and
   `tools/` read-only in the agent's environment, at the same
   absolute path as on the host — marker and env file included,
   so the mode the supervisor set is the mode the agent gets.
   Never mount the checkout root — the pattern lists live there.
3. Pin `core.hooksPath` to that read-only hooks directory from
   outside the agent's control: supervisor-written repo config,
   or a global/system gitconfig on a read-only mount.
4. Run the pattern service on the host under the supervisor's
   control.

Ready-made pieces live in `templates/agent-enforced/` (a compose
snippet, a gitconfig snippet, and a supervisor checklist with the
residual bypasses stated plainly — `--no-verify` and config
tampering exist; the backstops are tamper-evidence/fingerprinting
and the CI gate, which re-checks every pull request so
relaxed-tier content cannot merge even if pushed). This is the
shape devbox uses in production.

Scope, stated plainly: agent mode is for a **containerized agent
with a separate identity**. An agent running in your own terminal,
logged in as you, shares your authority — repo-kit runs there in
personal mode, catches accidents, and cannot be made
circumvention-proof without locking you out of your own machine.
The kit doesn't try.

One personal-mode exception to "the gate only advises": a
**coverage gap** — a check that cannot run at all. No pattern
list, no gitleaks or shellcheck binary. A warning line scrolls
past and the gap ships, so the check stops and asks on the
terminal; with no terminal (CI, GUI clients) it fails closed
and the message names the next steps — install the tool,
create the list, disable that check for this repo
(`repo-kit checks --off …`), or proceed once, explicitly, with
`REPOKIT_ALLOW_COVERAGE_GAPS=1 git push …`. Advisory warnings
(commit format, plan reminders) still never block. In CI the
same failure lands in the log; a same-repo run with an empty
pattern secret is fixed by `repo-kit provision`, while fork
and Dependabot pull requests — which cannot read the secret
at all — stay warning-only by the owner's ruling, via the
workflow's dedicated opt-out.

Content a scanner cannot *see* is not a gap any more: the
gate's scanners are attribute-immune. The pattern tier
classifies files by their bytes and extracts content from
blobs, and gitleaks is always invoked with `--text`, which
overrides a diff-suppressing git attribute (`-diff`,
`binary`) from every source — a committed `.gitattributes`,
`.git/info/attributes`, or a config-named attributes file —
so a suppressed file is scanned like any other, locally and
in CI, on either ruleset variant. That is the standing rule
for any scanner added later: internal checks extract content
from blobs; external diff consumers are invoked in their
attribute-defeating mode. No raw attributed diff is ever a
scanner's input.

## Layout

- `tools/` — renderer + gate checks + `checks/` dispatcher scripts
  (pattern/format, gitleaks, file hygiene, shellcheck) +
  `pattern_service.py`, the host-side scan service +
  `dependencies.txt`, the external-tool dependency manifest
- `.github/workflows/` — the reusable workflows projects call
- `templates/` — in-repo seeds + thin caller workflows + gitignore
  block + `rulesets/` (the `default-branch` ruleset JSON provision
  posts: variant A / variant B) + `agent-enforced/` (the enforced
  agent-integration snippets)
- `hooks/` — the pre-push dispatcher
- `install/repo-kit` — the installer (`init` / `hooks` / `setup` /
  `env` / `provision` / `override` / `pattern-service` / `packet`)

File hygiene uses the maintained **pre-commit-hooks** package where it
is installed (pinned in the dev environment / CI), falling back to a
stdlib subset (`tools/file_hygiene.py`) so the check degrades instead of
vanishing. No check in this toolkit ever downloads anything at run time:
binaries are baked into the environment with pinned versions, and a
missing binary is a coverage gap — the push asks on the terminal
and fails closed without one — never a fetch, never a silent skip.

`repo-kit env` prints a tooling status report (which tools are present,
at what version; which pattern list is in force) and, with
`--hooks-root DIR`, installs/refreshes hooks in every git repo under
DIR. It is idempotent and fail-open, designed to be called from a dev
container's entrypoint so a fresh container starts with hooks already in
place.

Versioning: git tags (`v0.1.0`, …). Caller workflows pin a tag; the
renderer and checks are stdlib-only Python, no dependencies.
