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

- [ ] ns-1 · Publish v0.1.0: create the public repo, push the prepared
  history and the v0.1.0 tag together, add the GATE_PATTERNS repository
  secret, enable Pages (source: GitHub Actions), then apply the setup
  standard to this repo with `repo-kit provision` (dogfooding the tool
  on itself)
- [ ] ns-2 · On the first live CI run, re-verify the pinned zizmor and
  actionlint versions/asset names in the reusable gate workflow (they
  could not be live-verified from the build environment)
  blocked-by: ns-1
- [ ] ns-3 · First consumer: set up the Pareto EasyMount Kit for Rclone
  repo with `repo-kit provision` + the thin caller workflows and confirm
  gate + dashboard go green end to end
  blocked-by: ns-1

## Open decisions

Tracked here until decided; a decision lands as an ADR in `adr/` and is
removed from this list in the same commit.

- [ ] dec-1 · Machine identity for agent pushes: a GitHub App (1-hour
  installation tokens, distinct `app[bot]` actor) vs a dedicated machine
  user account (simpler, long-lived credentials). Until one exists,
  agents holding the owner's personal access token authenticate AS the
  owner, so `repo-kit provision` Variant B (required approvals) is
  unenforceable and Variant A semantics apply
