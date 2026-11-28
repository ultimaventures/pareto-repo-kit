# Plan — next steps and open decisions

This file is the project's public plan. The status dashboard is generated
from it (plus the commit log and `adr/`), so keep it current **in the same
commit as the work itself**:

- A step is `- [ ] ns-N · text` while open, `- [x] ns-N · text` when done.
- A blocked step carries an indented `blocked-by: ns-X` (or `dec-N`) line.
- Citing a step ID in a commit subject — `feat: mount smoke test (ns-4)` —
  makes the dashboard count the step as done, no separate update needed.
- New steps get the next free `ns-N` ID. IDs are never reused.

## Next steps

- [x] ns-1 · Publish v0.1.0: create the public repo, push the prepared
  history and the v0.1.0 tag together, add the GATE_PATTERNS repository
  secret, enable Pages (source: GitHub Actions), then apply the setup
  standard to this repo with `repo-kit provision` (dogfooding the tool
  on itself)
  (Done 2026-11-15 — the repo is public with v0.1.0 and v0.2.0
  tagged; the step sat unticked through three releases and was
  reconciled in v0.4.8 on the owner's evidence.)
- [x] ns-2 · On the first live CI run, re-verify the pinned zizmor and
  actionlint versions/asset names in the reusable gate workflow (they
  could not be live-verified from the build environment)
  blocked-by: ns-1
  (Done — the first live gate run verified both pins (zizmor
  1.11.0 / actionlint 1.7.7, verified live 2026-11-17); reconciled
  in v0.4.8. v0.4.8 also moves zizmor to its verified release
  binary, hash-pinned in tools/dependencies.txt.)
- [ ] ns-3 · First consumer: set up the Pareto EasyMount Kit for Rclone
  repo with `repo-kit provision` + the thin caller workflows and confirm
  gate + dashboard go green end to end
  blocked-by: ns-1
- [x] ns-4 · Review packets in repo-kit: `repo-kit packet` (the packet
  generator, folded in from the standalone script, reviewer-neutral),
  packets + feedback in a gitignored in-repo `review/` folder seeded by
  `init`, pattern scan on each generated packet, and the last-reviewed
  memory as a stack of the last 10 endpoints with `--history` and
  `--rollback N`
- [x] ns-5 · Pattern lists renamed and wired into provision: the
  wizard-built list is `patterns-relaxed.txt`; an optional hand-kept
  `patterns-strict.txt` ADDS its patterns to the relaxed list for
  local checks (union — the two files can't drift) and is never
  uploaded; `repo-kit provision` sets the GATE_PATTERNS secret from
  the relaxed list (offering `repo-kit setup` when none exists,
  `--skip-secret` to opt out); `repo-kit override` gives false
  positives an owner-only escape hatch (interactive-terminal-only —
  a speed bump, not a security boundary; shows the exact matches
  the check output never prints, grants a one-push allowance bound
  to HEAD + range, 10-minute expiry, consumed on use); provision
  also sets the GitHub Pages source to GitHub Actions when the
  repo contains a dashboard workflow (`--skip-pages` opts out),
  removing the last manual repo setting
- [ ] ns-6 · Exercise the so-far staged-only paths on a real project:
  `repo-kit init` from an empty folder and the `repo-kit setup`
  wizard, end to end, by a real user run
- [x] ns-7 · v0.3.0 review fix round: the check no longer prints raw
  pattern text (matches are reported as tier + pattern number +
  location — file/line in the diff, commit for messages); the
  override shows ALL matches per pattern (counts + capped excerpts,
  terminal-sanitized), scans exactly as the check does, and grants
  an allowance scoped to the approved patterns and bound to the
  range's resolved SHAs; the false-positive ladder is documented
  (local override is local-tier only; CI relaxed matches resolve
  via content fix, pattern narrowing + re-provision, or a
  variant-B owner bypass — variant A deliberately has no bypass);
  the "agents cannot grant" wording is corrected to speed-bump
  language everywhere; a legacy `patterns.txt` with no renamed list
  triggers a loud warning (never a fallback read); a repo's root
  commit is now pattern-checked (empty-tree base); provision
  distinguishes 404 from other API errors in the Pages probe,
  refuses to upload an empty pattern list, and collects step
  failures instead of aborting before Pages; a non-compiling
  pattern fails the gate closed with the file and line named;
  `--minutes` is validated (1–60); the packet scan compiles
  patterns case-insensitively like the gate; the warnings tier no
  longer crashes on a repo without a PLAN.md (found by the fix
  round's own scenario tests)
- [x] ns-8 · Ship the host pattern service IN repo-kit
  (`tools/pattern_service.py`, v0.4.0): upstreamed from devbox's
  hand-rolled copy so the service versions with the client and can
  never drift from it again — same tiered lists and loader rules,
  same wire protocol (the service accepts both the X-Repokit-Token
  and legacy X-Devbox-Token headers; the client sends both during
  the transition), counts audit and the owner oversize allowance
  carried over under `REPOKIT_PATTERN_*` env names; the new
  `repo-kit pattern-service` launcher prints the three-variable
  client environment, and the README gains the running-the-service
  section with the copy-paste harness instruction block. Devbox
  keeps only its session wiring (port pool, token, pid/claim,
  egress) and launches this service from the tooling checkout —
  write-up delivered to the devbox project 2026-11-17. Also fixed
  en route: `_effective_range` substituted HEAD for an
  unresolvable base's range head, silently widening scans to
  commits outside the requested range (found by ns-8's end-to-end
  service tests; the root-commit case it was built for still
  scans correctly)
- [x] ns-9 · Enforced agent integration (v0.4.1): the pattern
  service ships in two explicit tiers — the voluntary tier
  (endpoint + token + instruction block) and the enforced tier
  (hook installed where the agent cannot write). The
  `repo-kit pattern-service` launcher now prints the honesty note
  (a URL + token alone enforces nothing); the README gains an
  "Enforcing it" section with the four-step recipe; and
  `templates/agent-enforced/` ships the installable pieces —
  compose snippet (read-only same-path mounts of hooks/ + tools/,
  supervisor-injected env), gitconfig snippet (hooksPath placement
  rules), and a supervisor checklist with the residual bypasses
  (--no-verify, config tampering) and their backstops
  (fingerprinting, CI gate) stated plainly. Raised by the owner
  2026-11-17: an agent blocked by the scan is exactly the agent
  with a motive to remove the hook
- [x] ns-10 · Fail-open fix round (v0.4.2), from the external
  review of the v0.3.1–v0.4.1 packet (all items verified against
  source first; disposition in review/feedback/): pre-push reads
  the stdin ref lines and scans exactly the commits each ref
  update carries (gate.yml's first-push fallback scans the whole
  history); ci_checks fails CLOSED on git errors instead of
  reading them as "nothing to check"; scanning is per-commit
  (diff-tree / log -p) in the local check, the override, and the
  service payload — add-then-remove inside a range no longer
  evades; an enforced install (new hooks/enforced.marker, or the
  security profile) hard-fails with no pattern source, and a
  missing gitleaks under the marker fails too; CI fails on an
  empty GATE_PATTERNS secret (fork PRs excepted, maintainer-side
  enforcement noted), the tooling pin moves to v0.4.2 in both the
  reusable workflow default and the caller template, and the
  gitleaks/actionlint downloads are checksum-verified; the
  pattern service refuses to start over an unparseable pattern
  (reversing ns-8's skip-and-serve), reserves scan-cap slots
  under the lock, throttles 403 counts writes and caps the peer
  list, closes error responses (with the header), reads bodies
  under an overall deadline, and matches per line with a 32 KiB
  line cap; the bind guidance is a ladder (loopback → bridge
  address → 0.0.0.0); the gitconfig snippet teaches the
  installed-copy model and the compose snippet injects
  REPO_TOOLING_HOME; provision reads gh's HTTP status
  structurally instead of matching "404" in stderr;
  _diff_location no longer drifts on ++/-- content lines; the
  CI-ladder discriminator is GITHUB_ACTIONS, not CI;
  REPOKIT_PATTERN_FILES is newline-joined. Existing repos need a
  `repo-kit hooks` refresh to pick up the new dispatcher. Added
  on the owner's lockout concern before the packet went out (same
  round): `repo-kit uninstall` removes what init installed in a
  repo (our hook only, the .gitignore block, unmodified template
  workflows, override state, disable list) and reports what it
  deliberately leaves; `repo-kit checks --off/--on` maintains a
  per-repo disabled-check list (git config
  repokit.disabled-checks) that the dispatcher honors in
  voluntary installs and ignores — loudly — under the enforced
  marker; and the marker's fail-closed was narrowed back off the
  gitleaks check (missing scanner fails closed only under the
  security profile, where the environment guarantees it), so a
  voluntary install can't lock its owner out over a missing tool

- [x] ns-11 · Two-mode restructure + round-3 fixes (v0.4.3), from
  the external review of the v0.4.2 packet and the owner's design
  ruling that followed it (disposition in review/feedback/): the
  kit now has exactly two modes — PERSONAL (default; the gate is
  the user's own guardrail, shell config is believed, escape
  hatches work, nothing fails closed over a missing list) and
  AGENT (opt-in via the new `repo-kit enforce on|off`, for a
  containerized agent with a separate identity). In agent mode
  the gate takes NO gate-critical input from the shell: the
  dispatcher never reads REPO_TOOLING_HOME (baked path, or
  derived from its own location in raw mode), and ci_checks
  takes service coordinates + profile only from the
  supervisor-written hooks/enforced.env (refreshed by each
  pattern-service launch) and pattern lists only from the
  checkout root. hooks/enforced.marker is UNTRACKED + gitignored
  — a fresh clone is a personal install; gate.yml's fork-path
  marker removal is gone with it. Also in this round: the
  service matches long lines in overlapping 32 KiB windows
  (nothing past column 32768 goes unchecked); scanning covers
  ADDED lines only (-U0 --text, local + service payload), so a
  commit removing a leaked value no longer blocks itself; the
  owner override binds to HEAD + pattern set, not the push range
  (hook-path regression-tested this time); gate.yml treats an
  empty EVENT_BEFORE like a first push (no more green "..HEAD"
  jobs), warns unmissably on fork/Dependabot PRs (the pattern
  check did NOT run — merging accepts it unchecked; the
  fail-closed alternative was considered and rejected by the
  owner, approvals stay a documented ruleset opt-in), and
  provision sets GATE_PATTERNS for the Dependabot app too;
  new-ref ranges use rev-list --boundary (one range per boundary
  commit) instead of a date-ordered tail pick, and the zero-SHA
  sentinel is length-matched (SHA-256 repos); the service checks
  its scan cap BEFORE consuming the owner's oversize allowance
  and flushes audit tallies on any exit (SIGTERM included); the
  disabled-checks loop no longer globs. Docs: README gains the
  two-mode section and an Agent mode section carrying the rule
  ("anything the agent's shell can set, the agent can fake");
  the agent-enforced templates label Model A detection-only and
  drop all environment injection. Existing repos need a
  `repo-kit hooks` refresh for the new dispatcher.
- [x] ns-12 · Ambient-state hardening + scanning corrections
  (v0.4.4), from the external review of the v0.4.3 packet
  (disposition in review/feedback/) and the owner's rulings on
  it (item 7 = the FULL contract change; the environment scrub
  covers every GIT_* variable, not a named subset): agent mode
  now also pins PATH (default system + Homebrew, overridable
  via REPOKIT_TRUSTED_PATH in enforced.env) and scrubs the
  ambient environment — all GIT_*/GITLEAKS_*, PYTHON*, LD_*/
  DYLD_* — before anything runs; the pattern check invokes
  python3 -E -s; gitleaks in agent mode runs on the tooling's
  own tools/gitleaks.toml + empty ignore dir +
  --ignore-gitleaks-allow, and a repo-root .gitleaksignore fails
  the check closed (third-party testing showed the ignore-path
  flag adds a source in some modes rather than replacing the
  root lookup); the owner override is never honoured in agent
  mode (the allowance is agent-forgeable in .git — the
  disposition's own catch); file PATHS are scanned again (the
  added-lines change had dropped them — round-4 blocker 2);
  --text is gone — binary files scan as printable runs of the
  new blob, capped per file (round-4 blocker 3, the reviewer's
  own suggestion reversed); the override binds to the PUSHED
  tip, not HEAD; `enforce on` refuses its own checkout without
  --yes and takes the service token from env/stdin, never argv;
  enforced.env is written 0600; the hook and ci_checks parse
  enforced.env identically; service payloads dedupe merge-diff
  repeats. Item 7 as the full contract change: every check
  consumes a rev-list expression — a multi-boundary push is one
  "<tip> --not <boundaries>" expression naming exactly the new
  commits, and hygiene/shellcheck/ci_checks enumerate commits
  and union their files instead of diffing endpoints. The
  agent-enforced README gains the gate-input inventory, crossed
  off line by line. Existing repos need a `repo-kit hooks`
  refresh for the new dispatcher.
- [x] ns-13 · Decide from the bytes (v0.4.5), from the external
  review of the v0.4.4 packet (round 5; disposition in
  review/feedback/) and the owner's rulings on it (the
  attribute guard is built, precise form; GIT_CONFIG_NOSYSTEM
  is NOT adopted — it never touched the committed-attributes
  route, and the system config is root-owned in the container
  model): the pattern tier classifies every file by CONTENT
  (a NUL in the first 8 KiB), never by git's attribute-aware
  label — a -diff/binary attribute can no longer route a text
  file into the weak path; text sections come from a --text
  diff FILTERED by that classification, so raw binary never
  re-enters as payload; binary runs add UTF-16LE/BE decodings
  and stream across the whole blob through ONE
  `git cat-file --batch` process (bounded windows, a per-file
  extracted-text cap that fails closed in agent mode) instead
  of whole-blob captures; paths come from a NUL-delimited
  enumeration, exact for non-ASCII and spaced names; the
  ATTRIBUTE GUARD fails an agent-mode push that suppresses a
  text file's diff (personal mode warns) — the condition is
  gated because every diff-reading tool is blind to such a
  file, not just gitleaks. The dispatcher points
  GIT_CONFIG_GLOBAL at /dev/null, scrubs
  REPO_TOOLING_HYGIENE_FALLBACK (the hygiene script ignores it
  in agent mode too), keeps push ranges in a shell variable
  instead of a TMPDIR temp file, and says UNREADABLE out loud
  when enforced.env cannot be read; enforced.env is written
  0640 and before the marker; hygiene/shellcheck fail on a bad
  rev-list instead of passing on an empty list. Deferred as a
  named follow-up: diff-tree --stdin batching (the win is
  confined to thousand-commit first pushes). Existing repos
  need a `repo-kit hooks` refresh for the new dispatcher.
- [x] ns-14 · Coverage gaps stop and ask (v0.4.6), from the
  owner's ruling 2026-11-18 (a personal-mode warning that
  scrolls past ships the gap): the COVERAGE-GAP class — no
  pattern list, gitleaks or shellcheck missing, a text file
  whose diff is attribute-suppressed, a binary whose run
  extraction hit the cap — now interrupts in personal mode.
  With a terminal the check asks on /dev/tty (the hook's
  stdin is git's ref-line pipe) and anything but an explicit
  yes stops the push; without one (CI, GUI clients) it fails
  closed with the remedy in the message — install the tool,
  create the list, remove the attribute, `repo-kit checks
  --off <name>` for this repo, or
  REPOKIT_ALLOW_COVERAGE_GAPS=1 for one push (personal mode
  only; the dispatcher scrubs it in agent mode). Agent mode
  and the security profile already failed closed on the
  pattern cases and now do on missing binaries under every
  profile. Advisory warnings (commit format, plan reminders,
  hygiene fixers) still never block — the line is "a scanner
  did not run, or content could not be scanned", stated in
  the README. The hygiene stdlib-fallback notice is
  deliberately NOT in the class: a reduced scanner ran, by
  design. Existing repos need a `repo-kit hooks` refresh
  for the new dispatcher.
- [x] ns-15 · Round-6 fixes (v0.4.7): the fork/Dependabot CI
  regression is reverted via REPOKIT_PATTERN_GAP_OK (set
  only by gate.yml's fork/Dependabot branch, honored only
  when GITHUB_ACTIONS=true, scoped to the no-pattern-source
  decision); CI's gitleaks step now runs on the tooling's
  config/ignore flags and fails on a caller-root
  .gitleaksignore. The suppressed-diff gap is CLOSED, not
  flagged (owner ruling, reversing the round-5 guard on the
  round-6 evidence): every gitleaks invocation carries
  --text in its log-opts — verified on the real 8.30.1 to
  defeat committed .gitattributes AND .git/info/attributes
  (GIT_ATTR_SOURCE still missed the latter) — and the
  attribute guard is retired; the standing rule is that no
  scanner consumes a raw attributed diff (internal checks
  extract from blobs, external diff tools run with --text).
  The coverage-gap decision now lives in ONE helper
  (tools/coverage_gap.py): 60s timeout, backgrounded pushes
  count as no-terminal, control-character sanitization,
  answers memoized per push via a dispatcher-owned state
  file; shellcheck enumerates before it may call a missing
  binary a gap; the dispatcher banners a set
  REPOKIT_ALLOW_COVERAGE_GAPS. Reviewer's closing rule
  adopted for future work: before adding a fail-closed path,
  list the legitimate non-interactive callers and give each
  a deliberate answer. Existing repos need a `repo-kit
  hooks` refresh for the new dispatcher.
- [x] ns-16 · Secondary-review rework round (v0.4.8): the
  clean-room full-history review of v0.4.7 (six passes) plus its
  complexity review, dispositioned in full (review/feedback/
  2026-11-19-*), owner rulings 2026-11-19. In-repo test suite
  (tests/) + a CI job running it on the PR checkout; the
  fail-open closures (hygiene/shellcheck scan the range's blobs,
  not the working tree; the extraction cap surfaces again;
  review_packet scans before it writes/stamps/copies and can
  include large files; the pattern service refuses short bodies;
  the dispatcher fails when zero checks ran, feeds checks
  /dev/null, and in agent mode ignores local remote-tracking
  boundaries for new refs; no silent range widening; provision's
  missing secret is a failed step; the override binds to the
  approved commit set); a supervisor-side grant for the service
  oversize allowance; the override display package (--all, an
  inline show-all offer before the grant prompt, a narrowing
  nudge naming the pattern file past 5 matches); one shared
  plumbing module (pattern loading, tier, discovery, range
  enumeration, EMPTY_TREE, git helper, enforced.env grammar)
  consumed by every check; legacy shims deleted (devbox is
  undeployed and will adopt agent mode as-is — owner ruling);
  pins unified behind a mechanical pins-agree check; README
  reconciled + a personal-mode push map; variant B CODEOWNERS
  seeded/reported. Structural splits and keep/delete decisions
  on speculative machinery are deferred to after alpha
  (complexity review timing, owner-endorsed); new-machinery
  freeze until then. Review path: secondary reviewer only,
  iterated to clean, then the primary reviewer (owner ruling).
- [ ] ns-17 · v0.4.8 review path (split out of ns-16 in the
  fix pass, secondary review item 13 — ns-16's build scope is
  done and ticked; the round is not finished while it is still
  being reviewed): the final consolidated secondary review of
  v0.4.8 (5 majors, 14 minors, 2 nits; dispositioned in full,
  review/feedback/2026-11-19-v048-disposition-secondary-final.md)
  is fixed in one pass on top of 6cc1ef3, folded into v0.4.8
  before merge; a delta packet against 6cc1ef3 (changed files
  in full, plus the fix PR's tests-job output) goes to the
  SECONDARY reviewer only — first message carrying the
  message-count header (owner protocol, 2026-11-19) — iterated
  to a clean confirmation; only then does the round go to the
  primary reviewer.
- [ ] ns-18 · PROPOSAL (logged 2026-11-25 at the owner's
  direction, cross-posted from the local-agent-harness chat;
  stream-of-consciousness framing, changes welcome; NO build
  authorized and no decision needed now): add a code-safety
  (SAST / dangerous-sink) check tier to the gate. Surfaced via
  the Canopii Trust Index's grading of JevStyle (17/100, F) in
  the main chat — the catches were code-level: `eval()` on
  tool-derived strings, SSRF sinks (fetching tool-supplied
  URLs without allow-listing), unsafe patterns generally.
  Canopii is MCP-server-scoped, so it is the reference for
  WHAT to check, not the tool to adopt. The gap, verified
  against the current stack: local checks are 10-pattern-
  and-format (custom regex list), 20-gitleaks (committed
  secrets), 30-file-hygiene (pre-commit-hooks package),
  40-shellcheck; CI adds zizmor + actionlint for Actions
  YAML. Gitleaks hunts secrets and the pattern tier hunts
  literal text — nothing parses code structure or follows
  data into dangerous sinks (eval/exec, SSRF, unsafe
  deserialization, `shell=True`, pickle loads). Candidate
  tools for the spike: Semgrep (the standard open-source
  SAST; LGPL-2.1 CLI), Opengrep (its community fork),
  Bandit (Apache-2.0, Python-only, much simpler). Conditions
  any adoption must meet, from the kit's standing rules and
  hard lessons: (1) scan the committed RANGE/blobs, not the
  working tree (the v0.4.7 review finding against the
  hygiene/shellcheck tiers); (2) ruleset vendored and pinned
  in the kit — never pulled from a live registry at run time
  (the no-downloads-at-runtime rule; binaries pinned with
  checksums as usual); (3) a thin wrapper in the existing
  dispatcher shape, loud skip when the tool is missing — a
  skip is never a pass; (4) the owner's standing
  tool-evaluation spike before adoption — history /
  reputation / CVE check on the finalists, INCLUDING the
  rules' licensing (Semgrep's rules licensing has wrinkles
  worth checking for a kit that may be commercialized);
  (5) enforcement at the established gate points (pre-push +
  CI), same as the other tiers.

## Open decisions

Tracked here until decided; a decision lands as an ADR in `adr/` and is
removed from this list in the same commit.

- [ ] dec-1 · Machine identity for agent pushes: a GitHub App (1-hour
  installation tokens, distinct `app[bot]` actor) vs a dedicated machine
  user account (simpler, long-lived credentials). Until one exists,
  agents holding the owner's personal access token authenticate AS the
  owner, so `repo-kit provision` Variant B (required approvals) is
  unenforceable and Variant A semantics apply
- [ ] dec-2 · Platform scope: GitHub-only, or GitHub/GitLab-agnostic?
  Logged 2026-11-17 as a curiosity, NOT a direction — do not build
  toward it unprompted. Inventory at logging time: the local core
  (hooks, checks, pattern lists, override, packet tool, setup, init
  scaffolding, dashboard renderer) is already platform-agnostic
  (pure git); the GitHub-specific half is `provision` (gh CLI +
  GitHub API) and the CI layer (Actions workflows, ruleset
  templates, zizmor/actionlint). An agnostic shape would keep the
  local core and add per-platform provision backends + CI templates
  (GitLab: protected branches/approval rules, CI/CD variables,
  GitLab CI YAML, Pages jobs) — moderate, not a rewrite
