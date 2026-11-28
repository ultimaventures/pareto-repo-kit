# Agent mode — enforced integration snippets

> Anything the agent's shell can set, the agent can fake.
> Enforcement configuration has to come from somewhere the agent
> can't write.

The pattern service's endpoint + token, handed to an agent with a
"please use this" instruction, enforce **nothing**: an agent that
can install a hook can also remove it, and an agent blocked by the
scan is exactly the agent with a motive to try. These snippets are
the other mode — repo-kit's **agent mode**: the hook installed
where the agent **cannot write**, and every gate-critical setting
read from supervisor-written files, never from the agent's shell.
(This is the shape devbox uses in production.)

Two ways to place the hook, and they are NOT equivalent:

- **Model A — an installed copy** (`repo-kit hooks` copies the
  dispatcher into the repo's `.git/hooks/` with the tooling path
  baked in): the hook file lives in a directory the agent can
  normally write, so Model A is **detection-only** — the gate
  runs, and tampering is something a supervisor can notice
  (devbox fingerprints `.git/config` and the hook files), not
  something the layout prevents.
- **Model B — the mounted hooks directory** (`core.hooksPath`
  pinned at the read-only mount): the hook the agent's git
  executes is a file it cannot edit, delete or replace. This is
  the prevention model, and the one these snippets build.

## The pieces

- `docker-compose.snippet.yml` — mounts the tooling checkout's
  `hooks/` and `tools/` into the agent's container **read-only**,
  at the *same absolute path* they have on the host (so a
  host-written `core.hooksPath` resolves verbatim inside). It
  injects NO gate configuration into the container's environment:
  in agent mode the gate doesn't read the shell — the service
  coordinates live in `hooks/enforced.env` inside the mount.
- `gitconfig.snippet` — the `core.hooksPath` setting that points
  git at the mounted hooks, with notes on where it must live to
  stay outside the agent's control.

## Every input to the gate — the inventory

When the claim is "the agent can't influence the gate", these are
the inputs to the gate process, each crossed off (v0.4.4 — the
round that stopped locking only the configuration we had thought
of and audited the ambient state too):

| Input | Agent-mode state |
|---|---|
| Service URL/token, check profile | `hooks/enforced.env` only (supervisor-written, 0640) |
| Pattern lists | the checkout root (never mounted) or the service |
| Tooling location | baked at install, or derived from the hook's own path — never from the environment |
| `PATH` (which git/python3/gitleaks runs) | pinned by the dispatcher to a trusted value; a supervisor whose tools live elsewhere sets `REPOKIT_TRUSTED_PATH` in `enforced.env` |
| `PYTHONPATH`/`PYTHONSTARTUP`/`PYTHONHOME`, loader vars (`LD_*`/`DYLD_*`) | scrubbed by the dispatcher before anything runs; the pattern check also invokes `python3 -E -s` |
| Every `GIT_*` variable (`GIT_DIR`, `GIT_CONFIG*`, `GIT_EXEC_PATH`, …) | scrubbed by the dispatcher — git's own environment is an input too |
| Every `GITLEAKS_*` variable (incl. `GITLEAKS_CONFIG_TOML`, a whole config in an env var) | scrubbed by the dispatcher |
| The scanned repo's `.gitleaks.toml` / `.gitleaksignore` / inline `gitleaks:allow` | the scan runs on the tooling's own `tools/gitleaks.toml` + empty ignore dir + `--ignore-gitleaks-allow`; a repo-root `.gitleaksignore` fails the check closed |
| The owner override (`.git/repokit-override.json`) | never honoured in agent mode — it is a file in agent-writable `.git` |
| The per-repo disable list (repo git config) | ignored, and its presence reported |
| Git attributes — a committed `.gitattributes`, `.git/info/attributes`, or an attributes file named by config | CLOSED, not gated (v0.4.7): the pattern tier classifies files by CONTENT (a NUL in the first 8 KiB) and extracts from blobs, so suppression cannot blind it; and gitleaks is always invoked with `--text` in its log-opts, which overrides the diff attribute from every source (verified on the real 8.30.1 — committed attributes and info/attributes both become visible; the v0.4.5–0.4.6 push-level guard was retired once no scanner was left blind) |
| `$HOME/.gitconfig` and the XDG git config (the GLOBAL config) | the dispatcher points `GIT_CONFIG_GLOBAL` at `/dev/null` in agent mode — the agent's own git config no longer reaches git at all |
| The SYSTEM gitconfig (`/etc/gitconfig`) | deliberately left readable (owner ruling, v0.4.5): in the container model it is root-owned, so the non-root agent cannot write it, and the supervisor's own hook pin may live there. `GIT_CONFIG_NOSYSTEM` is NOT set — it never touched the committed-attributes route in the first place |
| `TMPDIR` / temp files | the dispatcher keeps the push ranges in a shell variable, never on disk — no gate state transits a directory a same-uid process could truncate |
| The shell that runs the hook | must be a POSIX `sh` that actually executes the script; a supervisor image whose shell swallows hooks (e.g. `SHELLOPTS=noexec` under bash-as-sh) has no gate at all — repo-kit cannot detect its own non-execution; keep the image's `/bin/sh` ordinary |
| The push's ref lines on the hook's stdin, and local remote-tracking refs | the ref lines ARE the push — git supplies them and they name what is being published, not configuration. Remote-tracking refs are NOT trusted as scan boundaries in agent mode (v0.4.8): they live in agent-writable `.git`, so a new ref's whole history is scanned instead of trusting a boundary the agent could have moved |
| Repo-local git config (`.git/config`) as a class | agent-writable by construction; the gate takes no gate-critical setting from it in agent mode — settings come from `enforced.env`, and the one repo-config input the gate would otherwise read (the per-repo disable list) is ignored and reported (above). Git-level pins that matter (`core.hooksPath`) are set by the supervisor outside the repo — see the checklist |
| Working-tree contents of the scanned repo | not an input (v0.4.8): hygiene and shellcheck grade the range's committed blobs, extracted from the object store, so uncommitted working-tree state can neither launder a committed file nor block a clean push |
| The checks themselves, the service, CI | supervisor / host / GitHub — outside the agent's reach by the checklist below |

## Supervisor checklist

1. The tooling checkout lives **outside** every path the agent
   can write. The agent gets read-only mounts (or a root-owned
   copy) of `hooks/` and `tools/` only — never the checkout root,
   never the pattern lists.
2. `core.hooksPath` is set by the supervisor (host-written repo
   config before the session starts, or a global/system config on
   a read-only mount) — never by the agent, never as a setup step
   the agent performs.
3. **Agent mode is on**: run `repo-kit enforce on` on the host
   BEFORE the mounts are made. It creates `hooks/enforced.marker`
   (the mode switch) and `hooks/enforced.env` (profile +
   service coordinates) in the checkout — both ride into the
   container inside the read-only hooks/ mount, where the agent
   can read but never change them. While the marker exists:
   - the gate takes its service coordinates and profile ONLY
     from `hooks/enforced.env` — shell variables naming a
     service are ignored, so a fake service on localhost
     satisfies nothing;
   - pattern lists resolve only from the checkout root (which
     is not mounted — so in practice the service is the source);
   - "no pattern source at all" fails CLOSED instead of going
     through personal mode's coverage-gap decision (which, with
     no terminal, also fails the push — but asks first when a
     terminal is present);
   - the per-repo disable list (`repo-kit checks --off`) is
     ignored, and a missing gitleaks binary fails closed under
     the security profile this stack runs.
   The pattern-service launcher (`repo-kit pattern-service`, run
   host-side) refreshes `hooks/enforced.env` with each run's URL
   and token — run it after `enforce on`, and the file the agent
   sees always names the live service.
4. The pattern service runs on the host under the supervisor's
   control (`repo-kit pattern-service`, bound per the service
   docstring's EXPOSURE ladder, or the supervisor's own lifecycle
   running `tools/pattern_service.py`).
5. Nothing about the hook's location comes from the environment:
   an installed copy has the tooling path baked in, and a raw
   mounted copy derives it from its own location. (Pre-v0.4.3
   the `REPO_TOOLING_HOME` variable could redirect the hook at a
   fake checkout; it is no longer read at all.)
6. The tools the gate runs (git, python3, gitleaks) live on the
   dispatcher's trusted PATH — `/usr/local/bin:/usr/bin:/bin:/
   opt/homebrew/bin` by default. If yours live elsewhere, set
   `REPOKIT_TRUSTED_PATH` in `hooks/enforced.env` (the file is
   supervisor-written, so the gate may believe it). The gitleaks
   binary in particular must be the supervisor's: in agent mode
   the scan runs it with the tooling's own configuration
   (`tools/gitleaks.toml`), never the scanned repo's.
7. Use a SEPARATE tooling checkout for the container mount.
   Your own checkout stays in personal mode; `repo-kit enforce
   on` refuses to flip the checkout it lives in without `--yes`
   for exactly this reason.
8. The agent runs NON-ROOT, and nothing on the dispatcher's
   trusted PATH is writable by it. The ambient-state design
   assumes exactly this: a root agent can overwrite the pinned
   `git` itself, and no environment scrub survives that —
   root-in-its-own-container is an unsupported configuration,
   stated plainly rather than half-defended. The agent's uid
   must also be able to READ `hooks/enforced.env` (0640): run
   the container as the file's owner or group (`--user`), or
   the gate fails closed saying the file is UNREADABLE — by
   design, never silently.

## Residual bypasses — stated plainly

- `git push --no-verify` skips any hook. Mitigations: the CI gate +
  ruleset re-check every pull request, so relaxed-tier content
  cannot *merge* even if pushed; and a supervisor that fingerprints
  the agent's `.git/config` and hook files against the last accepted
  state (devbox does this) makes tampering visible rather than
  silent.
- An agent can rewrite its own repo-local git config. Same
  mitigations: pin what you can outside its reach, fingerprint the
  rest, and let CI be the merge-time backstop.
- The strict tier is never checked in CI (its list must never leave
  the host), so for strict-tier content the local stack above is
  the whole defence. That is the deliberate trade: the strict list
  exists for identifiers that must not be *transmitted* anywhere,
  and the enforced local hook is what guards them.
- Out of scope by design: an agent running in the user's own
  terminal, logged in as the user. It shares the user's authority,
  so no hook arrangement enforces against it — that setup runs in
  personal mode, where the gate catches accidents and makes no
  stronger claim.

Enforcement here is a guardrail with tamper evidence, not a prison:
with all five pieces above in place it stops accidents, and it
makes casual circumvention fail closed rather than slip through —
an environment that simply lacks the service coordinates no longer
waves pushes through (that was true until v0.4.2, and the claim is
scoped to the assembled stack, not to any single piece).
Deliberate circumvention it makes loud, via the fingerprints and
the CI backstop. If your threat model needs more than that, the
agent should not hold push credentials at all — give it a
read/write working tree and let the supervisor push.
