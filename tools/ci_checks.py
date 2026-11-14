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

SERVICE MODE (devbox): when REPOKIT_PATTERNS_URL and
REPOKIT_PATTERNS_TOKEN are both set, the pattern list never enters
this environment at all. The content this tier would have scanned
locally — commit subjects/bodies plus the diff for the range — is
POSTed to the host-side pattern service, which answers pass/fail
plus category NAMES only (never pattern text, never matched text).
An unreachable service, a timeout, or an unparseable reply FAILS
CLOSED: a push that cannot be pattern-checked is a blocked push.
With the variables absent, local behaviour is exactly as above.

SECURITY PROFILE (devbox review instance): when
REPOKIT_CHECK_PROFILE=security, only the pattern tier runs — the
subject-format and plan-citation warning blocks are skipped
entirely. The warnings never block anything, but a review session
pushing feedback has no use for development-habit reminders, and
its pushes should carry exactly the security checks and no noise.
Any other profile value (or none) keeps full behaviour; the
pre-push dispatcher is the layer that fails closed on an unknown
profile value.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

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


def _human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def pattern_service_scan(repo, range_, commits):
    """Service-mode pattern check (see the module docstring).

    Returns (status, categories, size): status is "ok" | "fail" |
    "unavailable"; categories is a list of category names; size is
    the byte size the service measured for an oversize ("too-
    large") refusal, else None. The payload is the same content
    the local tier scans: every commit subject/body in the range,
    then the range's unified diff.
    """
    url = os.environ["REPOKIT_PATTERNS_URL"].rstrip("/") + "/scan"
    token = os.environ["REPOKIT_PATTERNS_TOKEN"]
    parts = [subject + "\n" + body for _sha, subject, body in commits]
    parts.append(git(repo, "diff", range_))
    payload = "\n".join(parts).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"X-Devbox-Token": token,
                 "Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # A JSON error body still carries category names (e.g. a 413
        # "too-large", which also carries the measured "size");
        # anything else is an unavailable service.
        try:
            data = json.loads(e.read().decode("utf-8"))
        except Exception:
            return ("unavailable", [], None)
        cats = data.get("categories") if isinstance(data, dict) else None
        size = data.get("size") if isinstance(data, dict) else None
        if cats and cats != ["forbidden"]:
            return ("fail", [str(c) for c in cats],
                    size if isinstance(size, int) else None)
        return ("unavailable", [], None)
    except Exception:
        return ("unavailable", [], None)
    if isinstance(data, dict) and data.get("ok"):
        return ("ok", [], None)
    cats = data.get("categories") if isinstance(data, dict) else None
    if cats:
        return ("fail", [str(c) for c in cats], None)
    return ("unavailable", [], None)


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

    # Service mode takes precedence: with the service variables set,
    # the pattern list is on the host by design and must not be read
    # locally even if a copy happens to be visible.
    service_handled = False
    if os.environ.get("REPOKIT_PATTERNS_URL") and \
            os.environ.get("REPOKIT_PATTERNS_TOKEN"):
        service_handled = True
        status, categories, size = pattern_service_scan(
            repo, args.range, commits)
        if status == "ok":
            print("ci_checks: pattern service: OK (the pattern list "
                  "stayed on the host)")
        elif status == "fail":
            for cat in categories:
                failures += 1
                if cat == "no-patterns-configured":
                    annotate(
                        "error",
                        "pattern service has no patterns configured "
                        "— push blocked; add patterns.txt on the host "
                        "or push from a Mac terminal")
                elif cat == "scan-limit":
                    annotate(
                        "error",
                        "pattern service scan limit reached for "
                        "this session — push blocked; end the "
                        "session (the service resets with it) or "
                        "push from a Mac terminal")
                elif cat == "too-large":
                    measured = (f"{_human_size(size)}, limit 1 MiB"
                                if isinstance(size, int)
                                else "over the 1 MiB limit")
                    annotate(
                        "error",
                        f"Pattern scan too large ({measured}). If "
                        "this push is legitimate, on the Mac run: "
                        "dev-agent --allow-big-push — then retry "
                        "the push within 10 minutes. The allowance "
                        "covers ONE oversize scan, up to 32 MiB.")
                else:
                    annotate(
                        "error",
                        f"pattern check failed (category: {cat}) — "
                        "the matching pattern list lives on the host "
                        "and is never shown here")
        else:
            failures += 1
            annotate(
                "error",
                "pattern service unavailable — push blocked; push "
                "from a Mac terminal or fix the service")

    pats = [] if service_handled else load_patterns(args.patterns)
    if pats:
        print(f"ci_checks: pattern list loaded ({len(pats)} patterns)")
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
    elif not service_handled:
        print("ci_checks: no pattern list available (GATE_PATTERNS_FILE "
              "unset) — personal-pattern tier skipped")

    # Warnings tier — skipped ENTIRELY under the security profile
    # (see the module docstring): the pattern tier above is the
    # whole job there.
    if os.environ.get("REPOKIT_CHECK_PROFILE") != "security":
        cited = any(STEP_CITE_RX.search(s + b) for _, s, b in commits)
        plan_changed = "PLAN.md" in git(repo, "diff",
                                       "--name-only", args.range)
        for sha, subject, _ in commits:
            if not SUBJECT_RX.match(subject):
                annotate(
                    "warning",
                    f"Commit format: '{subject}' ({sha}) doesn't start "
                    "with a type prefix. Subjects become the public "
                    "activity feed — use e.g. 'feat: …', 'fix: …', "
                    "'docs: …', 'adr: …'. (Reminder only — this never "
                    "blocks a merge.)")
        if not cited and not plan_changed:
            plan = parse_plan(os.path.join(repo, "PLAN.md"))
            open_steps = [s for s in plan.get("Next steps", [])
                          if not s["done"]]
            listing = "; ".join(f"{s['id']} {s['text'][:60]}…"
                                for s in open_steps[:5])
            annotate(
                "warning",
                "Plan check: no commit in this range cites a plan "
                "step and PLAN.md wasn't updated. If this work "
                "completes a step, add its ID to a commit subject — "
                "e.g. 'feat: mount smoke test (ns-4)' — or tick it "
                f"in PLAN.md. Open steps: {listing}. (Reminder "
                "only — this never blocks a merge.)")

    if failures:
        print(f"ci_checks: {failures} hard failure(s)")
        return 1
    print("ci_checks: no hard failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
