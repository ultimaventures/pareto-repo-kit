#!/usr/bin/env python3
"""gate_common.py -- the ONE home for the gate's shared plumbing.

v0.4.8 consolidation (secondary review, complexity pass): up to
v0.4.7 this plumbing lived in four to six copies — pattern loading
in four implementations with different error semantics, tier
derivation in three (one already diverged), pattern-file discovery
in three, range enumeration in five, the EMPTY_TREE constant in
four files, a git() helper in five variants — and every serious
review finding of the round was a place where a correct decision
had been implemented in one copy and merely asserted of the
others. The copies are gone; this module is the only
implementation, and the in-repo suite (tests/run_tests.sh) pins
the behaviour.

Contents:
  EMPTY_TREE            the git empty-tree object id (SHA-1 form)
  EMPTY_TREE_SHA256     the git empty-tree object id (SHA-256 form)
  GitFailure            a git failure the gate must not paper over
  git()                 the gate's git runner (timeout, fail-closed)
  tier_for_path()       pattern-list tier from the file name
  load_patterns()       the one pattern loader
  discover_pattern_files()  the one pattern-list discovery rule
  effective_range()     range validation/normalisation
  range_commits()       a range's commit set, oldest first
  range_tip()           the commit a range publishes at its tip
  parse_enforced_env()  the Python half of the enforced.env grammar

Where the old copies disagreed, the choice made (recorded so the
next reader does not have to re-derive it):
  * Tier fallback: the file's basename stem, or "patterns" when
    the stem is empty (the ci_checks form; the service's bare-stem
    form could yield an empty tier name).
  * A pattern file is read as UTF-8; a file that cannot be read
    or decoded is an ERROR entry (path, 0) — never a silent
    replacement-character read (the service's old form, which
    could mangle a pattern into never matching) and never a
    traceback (the check's old form). Callers that fail closed on
    loader errors keep doing so.
  * Patterns are deduplicated by text across files; the FIRST
    file wins the (tier, number) identity (the check's form; the
    service used to compile duplicates a second time — same
    matches, wasted probes).
  * Discovery: $GATE_PATTERNS_FILE (os.pathsep-joined) when set,
    else the tooling root's patterns-strict.txt +
    patterns-relaxed.txt, existing files only. Whether the
    environment is believed at all is the CALLER's decision —
    agent mode passes env_value=None on purpose — and stays
    visible at the call site.
  * git(): check=False returns "" on failure (the gate helper's
    long-standing contract: an empty range is a legitimate
    answer); check=True raises GitFailure; a timeout ALWAYS
    raises GitFailure.

Deliberately NOT consolidated:
  * The dispatcher's shell parser for enforced.env (hooks/
    pre-push): the hook is POSIX shell and cannot import this
    module; it implements the same documented grammar, and the
    suite's grammar-parity case pins the two together.
  * check_blobs' byte-level git runner: blob enumeration parses
    NUL-delimited BYTES so paths with newlines or non-UTF-8
    bytes survive exactly; it raises its own RangeFailure. Its
    commit enumeration DOES delegate here (see check_blobs).
  * review_packet's and repo-kit's local subprocess helpers:
    user-facing CLI tools with their own error contracts
    (RuntimeError / ""-on-failure). Their pattern loading, tier
    derivation, discovery and EMPTY_TREE DO come from here —
    those carry gate semantics; a CLI's process plumbing does
    not.

Stdlib only.
"""

import os
import re
import shlex
import subprocess

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
# The SHA-256 empty tree (v0.4.8 fix pass, secondary review
# item 11): on a SHA-256 repo `git hash-object` derives this id,
# not the SHA-1 form above, so effective_range's sentinel test
# must recognise both, plus both all-zero lengths.
EMPTY_TREE_SHA256 = \
    "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"
EMPTY_TREE_SENTINELS = (EMPTY_TREE, EMPTY_TREE_SHA256,
                        "0" * 40, "0" * 64)


class GitFailure(Exception):
    """A git command the gate depends on failed. Distinct from an
    empty result: an empty range is a legitimate answer, a failed
    git is NO answer — and a gate that cannot read the commits must
    fail closed, never report "nothing to check" (v0.4.2, reviewer
    round 2 item 2: bind-mounted container repos hit git's
    "dubious ownership" refusal, which the old swallow-everything
    helper turned into a green gate)."""


def git(repo, *args, check=False, timeout=120):
    """Run git in repo. Every call carries a timeout (v0.4.8,
    secondary review pass 2): a wedged git (a stale lock, a hung
    credential helper, an NFS stall) must fail the gate closed,
    never hang a push forever."""
    try:
        out = subprocess.run(["git", "-C", repo] + list(args),
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitFailure(
            f"git {' '.join(args[:2])} timed out after {timeout}s "
            f"in {repo} — failing closed rather than hanging")
    if out.returncode != 0:
        if check:
            raise GitFailure(
                f"git {' '.join(args[:2])} failed in {repo}: "
                f"{out.stderr.strip()[:300]}")
        return ""
    return out.stdout


def tier_for_path(path):
    """The tier a patterns file belongs to, from its name:
    patterns-relaxed.txt -> "relaxed", patterns-strict.txt ->
    "strict" (the gate workflow writes the CI secret to a file named
    patterns-relaxed.txt for exactly this reason); any other file is
    its own tier, named by its basename."""
    base = os.path.basename(path or "")
    if base == "patterns-relaxed.txt":
        return "relaxed"
    if base == "patterns-strict.txt":
        return "strict"
    return os.path.splitext(base)[0] or "patterns"


def load_patterns(paths, compile=True):
    """Union of every patterns file in paths, in order, deduplicated
    by pattern text (a pattern present in both the strict and relaxed
    lists counts once). Missing files are skipped.

    Returns (pats, errors): each pat is a dict {raw, rx, tier,
    number, path} where number is the pattern's 1-based position
    among the pattern lines of ITS OWN file (comments and blanks
    don't count) — the owner can find it by counting pattern lines
    in that file. rx is the compiled pattern (case-insensitive,
    matching every consumer), or None when compile=False (callers
    that only display patterns, e.g. `repo-kit override`).

    An error is a (path, lineno) pair naming a line that does not
    compile — never the pattern text; lineno 0 means the file
    itself could not be read or decoded. Callers fail closed on
    errors: a dead pattern must never silently become a pass."""
    pats = []
    errors = []
    seen = set()
    for path in paths or []:
        if not path or not os.path.exists(path):
            continue
        tier = tier_for_path(path)
        number = 0
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            errors.append((path, 0))
            continue
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            number += 1
            if line in seen:
                continue
            seen.add(line)
            rx = None
            if compile:
                try:
                    rx = re.compile(line, re.IGNORECASE)
                except re.error:
                    errors.append((path, lineno))
                    continue
            pats.append({"raw": line, "rx": rx, "tier": tier,
                         "number": number, "path": path})
    return pats, errors


def discover_pattern_files(tooling_root, env_value=None):
    """The pattern files in force: $GATE_PATTERNS_FILE when the
    caller passes its value (os.pathsep-joined; the loaders skip
    missing files), else the tooling root's patterns-strict.txt
    and patterns-relaxed.txt — BOTH when both exist; local checks
    union their patterns, so the strict file is purely additive
    and the two can never drift apart."""
    if env_value:
        return [p for p in env_value.split(os.pathsep) if p]
    found = []
    for name in ("patterns-strict.txt", "patterns-relaxed.txt"):
        cand = os.path.join(tooling_root, name)
        if os.path.exists(cand):
            found.append(cand)
    return found


def effective_range(repo, range_):
    """The range to actually scan. HEAD~1..HEAD on a root commit
    names no commits (HEAD~1 does not exist) and the git helper
    would silently return nothing — the very first commit must be
    checked, not skipped: scan the range's own head against the
    empty tree. (The head must be preserved exactly: substituting
    HEAD would silently widen the scan to commits the caller never
    asked about — caught by the v0.4.0 end-to-end service tests.)

    A rev-list EXPRESSION (v0.4.4, round 4 item 7 — the full
    contract change) passes through untouched: "<tip> --not <b1>
    <b2>" already names an exact commit set, and a bare tip names
    its whole history; the endpoint surgery below only applies to
    the A..B form.

    v0.4.8 (secondary review pass 2): an unresolvable base is no
    longer silently replaced with the empty tree. That rewrite
    existed for ONE legitimate case — HEAD~1..HEAD on a root
    commit, where the base cannot resolve because the head has
    no parent — and it also fired for a typo'd, unfetched or
    shallow-clone base, silently widening the scan to all of
    history (and, for the override, blessing a commit set the
    owner never saw). Now: the empty-tree form is used only when
    the base IS the empty-tree/zero sentinel, or when the head
    is a root commit; any other unresolvable endpoint fails
    closed with the reason."""
    if not range_ or " --not " in range_ or ".." not in range_:
        return range_
    left, _, right = (range_ or "").partition("..")
    if not left:
        return range_  # "..B" is git's own empty-range shorthand
    right = right or "HEAD"
    # Both empty-tree ids and both zero lengths are sentinels
    # (v0.4.8 fix pass, secondary review item 11). The returned
    # form keeps the existing SHA-1 EMPTY_TREE constant so
    # downstream consumers keep working: range_commits reduces
    # an empty-tree range to its head by testing the RETURNED
    # form for startswith(EMPTY_TREE), which still triggers.
    if left in EMPTY_TREE_SENTINELS:
        return f"{EMPTY_TREE}..{right}"
    if left and git(repo, "rev-parse", "--verify", "-q",
                    left + "^{commit}").strip():
        if not git(repo, "rev-parse", "--verify", "-q",
                   right + "^{commit}").strip():
            raise GitFailure(
                f"range {range_!r}: the head {right!r} does not "
                "resolve to a commit in this repo — failing "
                "closed rather than guessing")
        return range_
    # The base does not resolve. Legitimate only when the head
    # is a root commit (HEAD~1..HEAD on a first commit).
    if git(repo, "rev-parse", "--verify", "-q",
           right + "^{commit}").strip() and not git(
            repo, "rev-parse", "--verify", "-q",
            right + "^{commit}^").strip():
        return f"{EMPTY_TREE}..{right}"
    raise GitFailure(
        f"range {range_!r}: the base {left!r} does not resolve "
        "to a commit in this repo and the head is not a root "
        "commit — failing closed rather than silently widening "
        "the scan to all of history (the pre-v0.4.8 behaviour). "
        "Fetch the missing history or fix the range.")


def range_commits(repo, range_):
    """The full SHAs a range expression names, oldest first —
    the same derivation the pattern check scans by (effective
    range, with the empty-tree base reduced to its head for
    git log)."""
    effective = effective_range(repo, range_)
    log_range = effective.partition("..")[2] \
        if effective.startswith(EMPTY_TREE) else effective
    out = git(repo, "log", *shlex.split(log_range),
              "--format=%H", "--reverse", check=True)
    return [line.strip() for line in out.splitlines()
            if line.strip()]


def range_tip(repo, range_):
    """The commit a range expression will publish at its tip,
    resolved to a full SHA: for "A..B" the B side; for a rev-list
    expression ("<tip> --not ...") or a bare tip, the first token.
    The override binds to THIS commit (v0.4.4, round 4 item 4):
    up to v0.4.3 it compared against HEAD, so pushing
    `other:main` from an unrelated checkout state applied an
    allowance to commits the owner never reviewed."""
    token = (range_ or "HEAD").split()[0]
    tip = token.rsplit("..", 1)[-1] if ".." in token else token
    return git(repo, "rev-parse", "--verify", "-q",
               f"{tip}^{{commit}}").strip()


def parse_enforced_env(text):
    """Parse hooks/enforced.env text into a dict — THE grammar,
    shared with the dispatcher's shell parser (hooks/pre-push):
    KEY=VALUE lines; blank lines and #-comments skipped; leading
    whitespace allowed; the value is stripped of surrounding
    whitespace and of ONE pair of MATCHING surrounding quotes;
    duplicate keys resolve LAST-wins. Spaces around the `=` are
    allowed (v0.4.8 fix pass, secondary review item 10): both
    `KEY =value` and `KEY= value` — and `KEY = value` — parse
    exactly as `KEY=value` does, because the key and the value
    are each stripped. The dispatcher implements the same
    grammar in shell (it cannot import this module); the
    suite's grammar-parity case pins the two together."""
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] \
                and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


if __name__ == "__main__":
    # A small CLI for the shell checks (tools/checks/*.sh), so the
    # discovery rule lives here and not in shell:
    #   gate_common.py pattern-files TOOLING_ROOT
    #       print the discovered pattern files, os.pathsep-joined
    #       (empty output when no list exists)
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "pattern-files":
        print(os.pathsep.join(
            discover_pattern_files(sys.argv[2], None)))
    else:
        print("usage: gate_common.py pattern-files TOOLING_ROOT",
              file=sys.stderr)
        raise SystemExit(2)
