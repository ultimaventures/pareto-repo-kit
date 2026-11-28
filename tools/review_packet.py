#!/usr/bin/env python3
"""
review_packet.py -- Build a review packet from a git commit range.

A review packet is one self-contained markdown file holding everything a
reviewer needs to review a range of commits without access to the repo:
the commit list, per-commit stats, the unified diff, and the full text of
each changed file, under a character budget. Packets are reviewer-neutral:
the same packet can go to any reviewer, human or AI.

    repo-kit packet                 # packet for everything since the last one
    repo-kit packet --history       # show the remembered packet endpoints
    repo-kit packet --rollback 1    # forget the last packet (e.g. never sent)

How the range is chosen:
  The tool remembers the commit each of the last 10 packets ended at (a
  stack stored in .git/repo-kit-packet-state.json -- inside .git, so it is
  never committed). A normal run starts where the last packet ended.
  --base REF overrides the start for that one packet. --rollback N rewinds
  the memory N packets so the next packet re-covers that range. --reset
  clears the memory entirely; the next packet then covers only the most
  recent commit (HEAD~1). A state file that exists but cannot be parsed
  is a loud error, never a silent empty stack.

Where packets live:
  By default, review/packets/ inside the repo, next to a review/feedback/
  folder for incoming reviewer feedback. The whole review/ folder is
  local-only: a nested review/.gitignore (created on demand, seeded by
  `repo-kit init`) keeps it out of every commit and clone. BEFORE the
  packet is written, copied or recorded, the assembled text is scanned
  against the pattern lists (the same discovery as the gate:
  $GATE_PATTERNS_FILE, else patterns-strict.txt AND
  patterns-relaxed.txt at the tooling root, unioned). A match REFUSES
  the packet: nothing is written, copied or recorded, the reviewed
  stack does not advance, and the tool exits 1 -- the scan names the
  pattern (tier + per-file number) and the packet line only, never
  the matched text. The scan also fails closed (v0.4.8 fix pass,
  secondary review major 1): when a discovered list yields loader
  errors, or yields no patterns at all, the packet is REFUSED the
  same way -- a scan that could not load its patterns cannot vouch
  for the packet. (When no list is discovered at all, the scan is
  skipped with a notice, as before: a skip is not a pass.)

Stdlib only -- nothing to install. Works anywhere git + python3 exist.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from gate_common import (  # noqa: E402
    EMPTY_TREE, discover_pattern_files, load_patterns)

STATE_NAME = "repo-kit-packet-state.json"
STACK_CAP = 10

REVIEW_GITIGNORE = """\
# Review packets and reviewer feedback are local-only working material:
# generated from this repo's commits, reviewed elsewhere, never committed.
*
!.gitignore
"""

REVIEW_PROMPT = """\
You are reviewing a code change. Be blunt, specific, and concrete.

Check for:
1. Bugs -- logic errors, off-by-one mistakes, wrong assumptions, mishandled
   null/None/empty values, race conditions, resource leaks.
2. Security issues -- injection, exposed secrets, broken auth, unsafe
   deserialization, path traversal.
3. Edge cases -- empty input, huge input, error paths, concurrency.
4. Anything likely to break in production.

For each issue give: the file and line, what is wrong, and a concrete fix.
If a file looks fine, say so in one line -- do not invent problems.
End with a verdict: safe to merge / needs fixes / needs rework.
"""

LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".mjs": "javascript", ".cjs": "javascript", ".go": "go",
    ".rs": "rust", ".java": "java", ".kt": "kotlin", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".sh": "bash", ".bash": "bash",
    ".zsh": "bash", ".sql": "sql", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".xml": "xml", ".html": "html",
    ".css": "css", ".scss": "scss", ".md": "markdown", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
}

STATUS_LABELS = {"A": "added", "M": "modified", "D": "deleted",
                 "R": "renamed", "T": "type-changed"}


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True,
                          text=True, errors="replace")


def git(args, cwd):
    p = sh(["git", *args], cwd)
    if p.returncode != 0:
        raise RuntimeError("git %s failed:\n%s"
                           % (" ".join(args), p.stderr.strip()))
    return p.stdout


def short(sha):
    return sha[:7]


# ---------------------------------------------------------------- state

def _is_commit(repo, sha):
    p = sh(["git", "cat-file", "-t", sha], repo)
    return p.returncode == 0 and p.stdout.strip() == "commit"


def load_stack(gitdir, repo):
    """Return the stack. Entries are dicts:
    {"sha": ..., "packet": filename-or-None, "ts": iso-or-None},
    oldest first, newest last, at most STACK_CAP entries.

    A state file that exists but cannot be parsed is a LOUD error
    (v0.4.8, secondary review pass 3): up to v0.4.7 corruption was
    silently treated as an empty stack, forgetting the reviewed
    history — the next packet then started at HEAD~1 and the
    unreviewed range in between was covered by no packet, ever.
    Entries naming commits the repo no longer has (rewritten
    history) are still dropped quietly: that is a normal event
    with a visible consequence (the next packet re-covers more),
    not corruption."""
    path = os.path.join(gitdir, STATE_NAME)
    stack = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data["stack"]
            if not isinstance(entries, list):
                raise ValueError("'stack' is not a list")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise RuntimeError(
                "packet state file %s is unreadable or corrupt "
                "(%s) — refusing to guess the reviewed history. "
                "Fix it, or delete it and accept that the next "
                "packet starts from HEAD~1 (or pass --base "
                "explicitly)." % (path, exc))
        for item in entries:
            if isinstance(item, str):
                item = {"sha": item, "packet": None, "ts": None}
            if isinstance(item, dict) and item.get("sha") \
                    and _is_commit(repo, item["sha"]):
                stack.append(item)
    return stack[-STACK_CAP:]


def save_stack(gitdir, stack):
    """Write the stack atomically (tmp + rename): a crash mid-write
    must not leave the truncated file load_stack now refuses."""
    path = os.path.join(gitdir, STATE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"stack": stack[-STACK_CAP:]}, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def commit_line(repo, sha):
    out = git(["log", "-1", "--format=%cs %s", sha], repo).strip()
    return out or short(sha)


# ------------------------------------------------------------- patterns

def find_pattern_files():
    """Every pattern list in force, strict first: $GATE_PATTERNS_FILE
    (possibly several os.pathsep-joined paths), else the tooling
    root's patterns-strict.txt and patterns-relaxed.txt — the one
    discovery rule, gate_common's (v0.4.8 consolidation)."""
    tooling = os.path.dirname(os.path.dirname(
        os.path.realpath(__file__)))
    return discover_pattern_files(
        tooling, os.environ.get("GATE_PATTERNS_FILE"))


def scan_packet(text):
    """Pattern-scan the assembled packet — called BEFORE the packet
    is written, copied or recorded (v0.4.8, secondary review
    pass 3: up to v0.4.7 the scan ran after all three, so the only
    check before sharing happened once the packet was already on
    disk, on the clipboard, and marked reviewed).

    Returns the hits as (tier, number, packet_line), with the
    pattern identity in the GATE's terms — gate_common's loader
    numbers each pattern by its position among the pattern lines
    of its own file, exactly as the gate reports it. Never prints
    matched text.

    Returns None for a refusal by scan failure (v0.4.8 fix pass,
    secondary review major 1 + nit B): loader errors, or no
    patterns loaded from a non-empty paths list, mean the scan
    cannot vouch for the packet, so the caller must refuse it —
    [] is a clean scan only, and the no-paths skip above keeps
    returning [] as before."""
    paths = find_pattern_files()
    if not paths:
        print("Pattern scan: skipped -- no pattern list found "
              "($GATE_PATTERNS_FILE unset, no patterns-strict.txt or "
              "patterns-relaxed.txt at the tooling root). A skip is "
              "not a pass.")
        return []
    patterns, errors = load_patterns(paths)
    print("Pattern scan: %d pattern(s) loaded from %s."
          % (len(patterns), " + ".join(paths)))
    if errors:
        print("Pattern scan: %d pattern(s) could not be compiled "
              "-- refusing the packet: a pattern that cannot be "
              "compiled cannot check the packet, so the scan "
              "cannot vouch for it. Fix the pattern list and "
              "re-run." % len(errors))
        return None
    if not patterns:
        print("Pattern scan: no patterns loaded from %s "
              "-- refusing the packet: the scan cannot vouch "
              "for the packet with no patterns in force. Fix "
              "the pattern list and re-run."
              % " + ".join(paths))
        return None
    lines = text.splitlines()
    hits = []
    for pat in patterns:
        for lineno, line in enumerate(lines, 1):
            if pat["rx"].search(line):
                hits.append((pat["tier"], pat["number"], lineno))
                break
    if hits:
        print("Pattern scan: %d pattern(s) matched: %s"
              % (len(hits), ", ".join(
                  "%s #%d at packet line %d" % h for h in hits)))
    else:
        print("Pattern scan: no matches.")
    return hits


def changed_files(base, head, repo):
    """Return [(status, old_path, new_path)] for base..head. Status in AMDRT."""
    out = git(["diff", "--name-status", "-z", "-M",
               "%s..%s" % (base, head)], repo)
    entries, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        token = parts[i]
        if not token:
            i += 1
            continue
        if token[0] == "R":  # R100<old><new>
            entries.append(("R", parts[i + 1], parts[i + 2]))
            i += 3
        else:
            entries.append((token[0], parts[i + 1], parts[i + 1]))
            i += 2
    return entries


def file_text_at(head, path, repo):
    p = sh(["git", "show", "%s:%s" % (head, path)], repo)
    if p.returncode != 0 or "\0" in p.stdout:
        return None  # submodule, or binary
    return p.stdout


def lang_for(path):
    return LANG_BY_EXT.get(os.path.splitext(path)[1].lower(), "")


def copy_to_clipboard(text):
    if sys.platform == "darwin":
        cmds = [["pbcopy"]]
    elif sys.platform == "win32":
        cmds = [["clip"]]
    else:
        cmds = []
        for c in (["wl-copy"],
                  ["xclip", "-selection", "clipboard"],
                  ["xsel", "--clipboard", "--input"]):
            if shutil.which(c[0]):
                cmds = [c]
                break
        if not cmds:
            return False
    try:
        p = subprocess.run(cmds[0], input=text, capture_output=True,
                           text=True, errors="replace", timeout=15)
        return p.returncode == 0
    except Exception:
        return False


def resolve_base(args, repo, stack, head):
    base = None
    if args.base:
        base = git(["rev-parse", "--verify", args.base], repo).strip()
    elif stack:
        base = stack[-1]["sha"]
    if not base:
        p = sh(["git", "rev-parse", "--verify", "HEAD~1"], repo)
        base = p.stdout.strip() if p.returncode == 0 else EMPTY_TREE
    if base != EMPTY_TREE:
        p = sh(["git", "merge-base", "--is-ancestor", base, "HEAD"], repo)
        if p.returncode != 0:
            raise RuntimeError(
                "base %s is not an ancestor of HEAD (history was "
                "rewritten?). Pass --base explicitly or use --reset."
                % short(base))
    return base


def per_commit_stats(base, head, repo):
    """Return [(sha, subject, stat_text)] oldest-first for base..head."""
    shas = git(["rev-list", "--reverse", "%s..%s" % (base, head)],
               repo).strip().split()
    out = []
    for sha in shas:
        subject = git(["log", "--format=%s", "-n", "1", sha], repo).strip()
        stat = git(["show", "--stat", "--format=", sha], repo).strip()
        out.append((short(sha), subject, stat))
    return out


def diff_block(text, title):
    return "\n## %s\n\n```diff\n%s\n```\n" % (title, text.rstrip())


def _diff_sections(diff):
    """Split a unified diff into per-file sections, each starting
    with its 'diff --git' line. Returns [(path, section_text)] —
    the unit the budget allocation keeps or drops whole."""
    sections = []
    current = []
    path = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                sections.append((path, "".join(current)))
            current = [line]
            m = re.match(r"diff --git a/(.*) b/(.*)",
                         line.rstrip("\n"))
            path = m.group(2) if m else "?"
        else:
            current.append(line)
    if current:
        sections.append((path, "".join(current)))
    return sections


def assemble(repo, repo_name, branch, base, head, commits, commit_stats,
             entries, diff, full, unc_diff, budget):
    notes = []
    h = ["# Code review packet\n"]
    h.append("- Repo: `%s` (branch `%s`)" % (repo_name, branch))
    if base != head:
        h.append("- Range: `%s..%s`" % (short(base), short(head)))
        if commits:
            h.append("- Commits:")
            h.extend("  - " + c for c in commits.splitlines())
    h.append("- Generated: %s UTC"
             % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    h.append("- Tree: `%s` (`git rev-parse HEAD^{tree}` -- the reviewed "
             "tree; compare with the pushed side's tree to confirm "
             "reviewed == pushed)"
             % git(["rev-parse", "HEAD^{tree}"], repo).strip())
    h.append("\n## Instructions for the reviewer\n\n"
             + REVIEW_PROMPT.rstrip() + "\n")
    if commit_stats:
        h.append("\n## Per-commit stats\n")
        for sha, subject, stat in commit_stats:
            h.append("### `%s` %s\n\n```\n%s\n```" % (sha, subject, stat))
    if entries:
        h.append("\n## Changed files (%d)\n" % len(entries))
        for st, old, new in entries:
            if st == "R":
                h.append("- renamed `%s` -> `%s`" % (old, new))
            else:
                h.append("- %s `%s`" % (STATUS_LABELS.get(st, st), new))
    header = "\n".join(h) + "\n"

    # Budget allocation (v0.4.8, secondary review pass 3): the
    # surface that can be COMPLETE takes first claim. When the
    # whole diff fits, it goes first — a delta packet exists to
    # show what changed — and full texts fill what remains. When
    # the diff alone exceeds the budget (a full-history packet),
    # full texts take first claim instead: the final tree is that
    # packet's substance, and a fitted diff still shows the shape
    # of the history. Either way, the fitted surface is truncated
    # only at FILE boundaries (whole per-file sections kept or
    # dropped, with the dropped files named in the packet), and
    # no per-file size cap excludes a file the budget could hold.
    # Up to v0.4.7 the diff took the whole budget first and was
    # cut MID-STREAM — a half-shown file diff reads as complete —
    # while a 20k per-file cap excluded large files from full
    # text regardless of the remaining budget, so the tool
    # structurally could not produce a complete packet for a
    # large range.
    def build_full(budget_left):
        parts = ["\n## Full file contents\n"]
        omitted = []
        for path, text, reason in full:
            if text is None:
                omitted.append((path, reason))
                continue
            block = ("\n### `%s`\n\n```%s\n%s\n```\n"
                     % (path, lang_for(path), text.rstrip()))
            if len(block) <= budget_left:
                parts.append(block)
                budget_left -= len(block)
            else:
                omitted.append((path, "over character budget"))
        if omitted:
            note = ("\n_Full text omitted for: "
                    + ", ".join("`%s` (%s)" % (p, r)
                                for p, r in omitted)
                    + " -- review from the diff above._\n")
            if len(note) > budget_left:
                note = ("\n_Full text omitted for %d file(s) "
                        "-- review from the diff above._\n"
                        % len(omitted))
            parts.append(note)
            budget_left -= len(note)
        return "".join(parts), budget_left

    def build_body(budget_left):
        body = ""
        for title, text in blocks:
            whole = diff_block(text, title)
            if len(whole) <= budget_left:
                body += whole
                budget_left -= len(whole)
                continue
            sections = _diff_sections(text)
            fence = len("\n## %s\n\n```diff\n\n```\n" % title)
            kept, dropped, used = [], [], fence
            for spath, stext in sections:
                if used + len(stext) <= budget_left:
                    kept.append(stext)
                    used += len(stext)
                else:
                    dropped.append(spath)
            if kept:
                body += diff_block("".join(kept), title)
                budget_left -= used
            if dropped:
                # Charge the note against the budget too
                # (v0.4.8 fix pass, secondary review item 18):
                # build_full deducts its omitted-note; this one
                # used to ride free, so the packet could exceed
                # the stated budget by the note's length.
                note = ("\n_%s omitted for: %s -- over the "
                        "character budget; where a file's full "
                        "text appears below, that is its review "
                        "surface._\n"
                        % (title, ", ".join("`%s`" % p
                                            for p in dropped)))
                body += note
                budget_left -= len(note)
                notes.append("%s for %d file(s) omitted from "
                             "the packet (over budget): %s"
                             % (title.lower(), len(dropped),
                                ", ".join(dropped)))
        return body, budget_left

    blocks = [(title, text) for title, text in (
        ("Diff", diff),
        ("Uncommitted changes (not yet committed)", unc_diff))
        if text.strip()]
    remaining = budget - len(header)
    if remaining < 0:
        notes.append("packet header alone exceeds the character "
                     "budget; no diff or file text included")
        remaining = 0
    diff_len = sum(len(diff_block(text, title))
                   for title, text in blocks)
    if blocks and diff_len <= remaining:
        body, remaining = build_body(remaining)
        full_text, remaining = build_full(remaining)
    else:
        full_text, remaining = build_full(remaining)
        body, remaining = build_body(remaining)

    return header + body + full_text, notes


def ensure_review_dirs(repo):
    """Create review/packets + review/feedback and the nested
    .gitignore that keeps the whole folder local-only."""
    review = os.path.join(repo, "review")
    os.makedirs(os.path.join(review, "packets"), exist_ok=True)
    os.makedirs(os.path.join(review, "feedback"), exist_ok=True)
    gi = os.path.join(review, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write(REVIEW_GITIGNORE)
    return review


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a review packet: one self-contained markdown "
                    "file covering a commit range (commit list, stats, "
                    "diff, full changed-file texts), ready to hand to "
                    "any reviewer.")
    ap.add_argument("--repo", default=".",
                    help="repo path (default: current directory)")
    ap.add_argument("--base", default=None,
                    help="start the packet at this ref instead of where "
                         "the last packet ended (any commit, tag or "
                         "branch; applies to this packet only)")
    ap.add_argument("--out", default=None,
                    help="output file (default: a timestamped file "
                         "under review/packets/ in the repo; '-' = stdout)")
    ap.add_argument("--copy", dest="copy", action="store_true", default=True,
                    help="copy packet to clipboard (default on)")
    ap.add_argument("--no-copy", dest="copy", action="store_false",
                    help="do not copy to clipboard")
    ap.add_argument("--budget", type=int, default=100_000,
                    help="max packet size in characters (default 100000)")
    ap.add_argument("--include-uncommitted", action="store_true",
                    help="also include uncommitted working-tree changes")
    ap.add_argument("--history", action="store_true",
                    help="list the remembered packet endpoints "
                         "(oldest first) and exit")
    ap.add_argument("--rollback", type=int, default=None, metavar="N",
                    help="rewind the memory by N packets and exit, so "
                         "the next packet re-covers what those packets "
                         "covered (for a packet generated but never sent)")
    ap.add_argument("--reset", action="store_true",
                    help="forget all remembered packet endpoints and "
                         "exit; the next packet covers only the most "
                         "recent commit")
    args = ap.parse_args(argv)

    ops = sum([bool(args.history), args.rollback is not None,
               bool(args.reset)])
    if ops > 1:
        print("error: --history, --rollback and --reset are mutually "
              "exclusive -- pick one.", file=sys.stderr)
        return 2

    try:
        repo = git(["rev-parse", "--show-toplevel"], args.repo).strip()
    except RuntimeError:
        raise SystemExit("error: not inside a git repository")

    gitdir = git(["rev-parse", "--absolute-git-dir"], args.repo).strip()
    state_path = os.path.join(gitdir, STATE_NAME)
    head = git(["rev-parse", "HEAD"], repo).strip()
    branch = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                repo).stdout.strip()
    stack = load_stack(gitdir, repo)

    if args.history:
        if not stack:
            print("No packets recorded yet -- the memory is empty. "
                  "The next packet starts from the most recent commit "
                  "(HEAD~1), or from --base if given.")
            return 0
        print("Remembered packet endpoints (oldest first; the next "
              "packet starts after #%d):" % len(stack))
        for i, item in enumerate(stack, 1):
            extra = "  [%s]" % item["packet"] if item.get("packet") else ""
            print("  #%d  %s  %s%s"
                  % (i, short(item["sha"]), commit_line(repo, item["sha"]),
                     extra))
        return 0

    if args.rollback is not None:
        n = args.rollback
        if n < 1:
            print("error: --rollback needs a count of at least 1.",
                  file=sys.stderr)
            return 2
        if not stack:
            print("error: nothing to roll back -- no packets are "
                  "recorded for this repo.", file=sys.stderr)
            return 2
        if n > len(stack):
            print("error: cannot roll back %d packet(s) -- only %d "
                  "endpoint(s) are remembered (oldest: %s)."
                  % (n, len(stack), short(stack[0]["sha"])),
                  file=sys.stderr)
            return 2
        dropped = stack[-n:]
        stack = stack[:-n]
        save_stack(gitdir, stack)
        print("Rolled back %d packet(s): %s"
              % (n, ", ".join(short(d["sha"]) for d in dropped)))
        if stack:
            print("The next packet will start after %s (%s)."
                  % (short(stack[-1]["sha"]),
                     commit_line(repo, stack[-1]["sha"])))
        else:
            print("The memory is now empty -- the next packet starts "
                  "from the most recent commit (HEAD~1), or from "
                  "--base if given.")
        return 0

    if args.reset:
        p = os.path.join(gitdir, STATE_NAME)
        if os.path.exists(p):
            os.remove(p)
        print("Forgot all remembered packet endpoints. The next packet "
              "starts from the most recent commit (HEAD~1), or from "
              "--base if given.")
        return 0

    base = resolve_base(args, repo, stack, head)
    unc_diff = (git(["diff", "--no-color", "-M", "HEAD"], repo)
                if args.include_uncommitted else "")

    if base == head and not unc_diff.strip():
        print("Nothing new since the last review packet. "
              "(--rollback N to re-cover a previous range, --reset to "
              "forget the memory, --base REF for a custom range)")
        return 0

    commits = (git(["log", "--oneline", "%s..%s" % (base, head)], repo).strip()
               if base != head else "")
    commit_stats = (per_commit_stats(base, head, repo) if base != head else [])
    diff = (git(["diff", "--no-color", "-M", "%s..%s" % (base, head)], repo)
            if base != head else "")
    entries = changed_files(base, head, repo) if base != head else []

    full = []
    for st, old, new in entries:
        if st == "D":
            full.append((new, None, "deleted"))
            continue
        text = file_text_at(head, new, repo)
        if text is None:
            full.append((new, None, "binary or submodule"))
        else:
            # No per-file size cap (v0.4.8): whether a file's full
            # text is included is decided by the budget allocation
            # in assemble(), not by an absolute cap that excluded
            # a file regardless of the budget remaining.
            full.append((new, text, None))

    packet, notes = assemble(repo, os.path.basename(repo), branch, base,
                             head, commits, commit_stats, entries, diff,
                             full, unc_diff, args.budget)

    # The pattern scan runs HERE — on the assembled text, before
    # the packet is written, copied or recorded (v0.4.8). A match
    # refuses the packet outright: the packet exists to be shared,
    # and a packet carrying listed content must not reach disk,
    # the clipboard, or the reviewed stack. A scan failure refuses
    # it too (v0.4.8 fix pass, secondary review major 1):
    # scan_packet returns None when it could not vouch for the
    # packet, and its message above names the cause.
    scan = scan_packet(packet)
    if scan is None:
        print("Packet REFUSED: the pattern scan could not vouch "
              "for the packet (cause named above). Nothing was "
              "written, copied or recorded, and the reviewed "
              "stack did not advance. Fix the pattern list and "
              "re-run.")
        return 1
    if scan:
        print("Packet REFUSED: the assembled packet contains "
              "pattern matches (named above). Nothing was written, "
              "copied or recorded, and the reviewed stack did not "
              "advance. Remove the matched content (or narrow the "
              "range) and re-run.")
        return 1

    default_out = args.out is None
    if default_out:
        review = ensure_review_dirs(repo)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        span = ("%s-to-%s" % (short(base), short(head))
                if base != head else "working-tree")
        out = os.path.join(review, "packets",
                           "packet-%s-%s.md" % (stamp, span))
    else:
        out = args.out
    if out == "-":
        sys.stdout.write(packet)
    else:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(packet)

    if base != head:
        if not stack or stack[-1]["sha"] != head:
            stack.append({
                "sha": head,
                "packet": (os.path.basename(out)
                           if out != "-" else None),
                "ts": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
            })
            save_stack(gitdir, stack)

    copied = copy_to_clipboard(packet) if args.copy else False

    n_commits = len(commits.splitlines()) if commits else 0
    print("Review packet ready: %s (%s chars, %d changed files)"
          % (out if out != "-" else "stdout", f"{len(packet):,}",
             len(entries)))
    print("Range %s..%s on %s (%d commit(s))"
          % (short(base), short(head), branch, n_commits))
    if default_out:
        print("Stored under review/packets/ -- local-only, covered by "
              "review/.gitignore; reviewer feedback goes in "
              "review/feedback/.")
    for n in notes:
        print("Note: %s" % n)
    if copied:
        print("Clipboard: copied.")
    elif args.copy:
        print("Clipboard: not copied (no clipboard tool found) -- "
              "use the file.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        sys.exit(1)
