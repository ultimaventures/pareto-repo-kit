# Decision records (ADRs) — format

One JSON file per decision in `adr/`, named `ADR-NNN.json`, numbered in
the order decisions are made and never renumbered. Copy
`ADR-000-template.json` to start a new one.

Fields:

- `id` — `ADR-NNN`, matches the filename.
- `title` — the decision in a few words.
- `date` — the day it was decided (`YYYY-MM-DD`).
- `status` — `accepted` normally; `superseded by ADR-NNN` when a later
  decision replaces it. Superseded records are edited to point forward,
  never deleted — the history of *why* is the value.
- `context` — the situation, constraints, and options considered.
- `decision` — the ruling, stated as a checkable rule.
- `consequences` — costs, benefits, deferrals, and the revisit rule: what
  evidence would reopen this decision.

Rules:

- An ADR records a decision that was actually made, by the person with
  the standing to make it. Proposals and open questions live in
  `PLAN.md`'s open-decisions list until they are decided.
- ADRs are public writing from their first commit: no instance data (real
  hostnames, account names, personal context). The gate scans them like
  everything else.
- The commit that adds an ADR uses the `adr:` prefix and removes the
  decided item from `PLAN.md` in the same commit.
