#!/usr/bin/env python3
"""CI / pre-push checks for this repository — two tiers, by design.

HARD FAILURES (exit 1) — only for what cannot be undone once public:
  * a blocked personal pattern (from the gate pattern list) appearing in a
    commit message or in a changed file. The pattern list itself is NEVER
    in this repo: CI injects it from a repository secret, local runs point
    GATE_PATTERNS_FILE at a local copy.

WARNINGS (never fail) — for habits that keep the project readable:
  * a commit subject that doesn't follow the conventional format
    (feat:/fix:/docs:/adr:/…), with an example of the right shape;
  * a push/PR where no commit cites a plan step and PLAN.md wasn't ticked,
    reminding the author which steps are currently open.

Usage:
    ci_checks.py --range origin/main..HEAD [--patterns FILE]

The patterns file defaults to $GATE_PATTERNS_FILE. With no patterns file,
the pattern tier is skipped with a notice (secrets scanning is gitleaks'
job in the same workflow; this tier covers personal patterns in messages
and diffs).
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_dashboard import SUBJECT_RX, parse_plan  # noqa: E402

STEP_CITE_RX = re.compile(r"\((ns-\d+|dec-\d+)\)")


def git(repo, *args):
    out = subprocess.run(["git", "-C", repo] + list(args),
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return out.stdout if out.returncode == 0 else ""


def annotate(kind, msg):
    print(f"::{kind}::{msg}")
    print(msg)


def load_patterns(path):
    pats = []
    if not path or not os.path.exists(path):
        return pats
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append((line, re.compile(line, re.IGNORECASE)))
    return pats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", default="HEAD~1..HEAD")
    ap.add_argument("--patterns",
                    default=os.environ.get("GATE_PATTERNS_FILE"))
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    failures = 0

    log = git(repo, "log", args.range, "--format=%H%x00%s%x00%b%x1e")
    commits = []
    for chunk in log.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        sha, _, rest = chunk.partition("\x00")
        subject, _, body = rest.partition("\x00")
        commits.append((sha.strip()[:12], subject.strip(), body))
    if not commits:
        print("ci_checks: no commits in range — nothing to check")
        return 0

    pats = load_patterns(args.patterns)
    if pats:
        diff = git(repo, "diff", args.range)
        for sha, subject, body in commits:
            for raw, rx in pats:
                if rx.search(subject + "\n" + body):
                    failures += 1
                    annotate(
                        "error",
                        f"Pre-public gate: commit {sha} message matches a "
                        f"blocked personal pattern (/{raw}/). Commit "
                        "messages are permanent and become this project's "
                        "public activity feed. Rewrite the message and "
                        "re-push; if this is a false positive, allowlist "
                        "it in the gate config with a one-line reason.")
                    break
        for raw, rx in pats:
            if rx.search(diff):
                failures += 1
                annotate(
                    "error",
                    f"Pre-public gate: the diff in {args.range} matches a "
                    f"blocked personal pattern (/{raw}/). Remove the "
                    "instance data before this can merge — public history "
                    "cannot be unpublished.")
                break
    else:
        print("ci_checks: no pattern list available (GATE_PATTERNS_FILE "
              "unset) — personal-pattern tier skipped")

    cited = any(STEP_CITE_RX.search(s + b) for _, s, b in commits)
    plan_changed = "PLAN.md" in git(repo, "diff", "--name-only", args.range)
    for sha, subject, _ in commits:
        if not SUBJECT_RX.match(subject):
            annotate(
                "warning",
                f"Commit format: '{subject}' ({sha}) doesn't start with "
                "a type prefix. Subjects become the public activity feed — "
                "use e.g. 'feat: …', 'fix: …', 'docs: …', 'adr: …'. "
                "(Reminder only — this never blocks a merge.)")
    if not cited and not plan_changed:
        plan = parse_plan(os.path.join(repo, "PLAN.md"))
        open_steps = [s for s in plan.get("Next steps", []) if not s["done"]]
        listing = "; ".join(f"{s['id']} {s['text'][:60]}…"
                            for s in open_steps[:5])
        annotate(
            "warning",
            "Plan check: no commit in this range cites a plan step and "
            "PLAN.md wasn't updated. If this work completes a step, add "
            "its ID to a commit subject — e.g. 'feat: mount smoke test "
            f"(ns-4)' — or tick it in PLAN.md. Open steps: {listing}. "
            "(Reminder only — this never blocks a merge.)")

    if failures:
        print(f"ci_checks: {failures} hard failure(s)")
        return 1
    print("ci_checks: no hard failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
