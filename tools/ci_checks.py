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
    ci_checks.py --range origin/main..HEAD [--patterns FILE ...]

The patterns files default to $GATE_PATTERNS_FILE (which may hold
several paths joined by os.pathsep — the pre-push wrapper passes the
strict and relaxed lists together; their patterns are UNIONED, never
either/or). --patterns may be repeated. With no patterns file, the
pattern tier cannot run — a COVERAGE GAP (round 6): in agent mode
(hooks/enforced.marker present in the tooling checkout) or under
the security profile that is a hard failure, and in personal mode
the run asks on the terminal and fails closed without one (see
coverage_gap.coverage_gap_ok): an enforced gate that checks nothing has not
passed, and a personal gate that checks nothing must not pass
silently either (secrets scanning is gitleaks' job in the same
workflow; this tier covers personal patterns in messages and
diffs).

TWO MODES (v0.4.3): personal mode is the default — the gate is the
user's own guardrail and its configuration may come from the user's
shell. AGENT mode (the marker, created by `repo-kit enforce on`,
normally by the supervisor who builds an agent's container) means
the process being gated must not be able to influence the gate: in
agent mode this script takes its service coordinates ONLY from the
supervisor-written hooks/enforced.env inside the tooling checkout,
ignores $GATE_PATTERNS_FILE / --patterns (pattern lists resolve
only from the checkout root), and ignores the shell's
REPOKIT_PATTERNS_URL / _TOKEN / profile. Anything the agent's shell
can set, the agent can fake; agent mode only believes files the
agent cannot write.

OWNER OVERRIDE: when the pattern tier would fail on what the owner
judges a false positive, `repo-kit override` (interactive terminal
only — a deliberate speed bump, not a security boundary) can write a
one-push allowance into the repo's .git directory, bound to the
exact commit set the owner approved and to the specific patterns
approved, expiring in minutes. The check re-binds at scan time:
the allowance applies only when the range actually being scanned
names that same commit set (and tip). Legacy allowances granted
before the commit set was recorded carry no commit set and still
bind by tip alone. In AGENT mode the override is never
honoured at all: the allowance file lives in the agent-writable
.git, so an agent could forge one; agent mode's escape hatches
are the supervisor's, not the repo's (round 4 disposition).
This script honours a valid allowance by skipping ONLY those
patterns for that one run, prints that it did so, and consumes the
allowance at the moment the pattern tier applies it. CI checkouts
have no allowance file, so CI is never affected — a relaxed-list
false positive that reaches CI is resolved
by fixing the content, narrowing the pattern (and re-provisioning
the secret), or an owner bypass merge where the ruleset provides
one (variant B); see the README's false-positive ladder.

REPORTING: this script NEVER prints pattern text or matched content.
A match is reported as tier + pattern number (the pattern's position
among the pattern lines of its file — the owner maps number to
pattern locally, e.g. via `repo-kit override`) plus a location: the
commit for a message match, or the file and line within the diff
for a diff match. Locations derive from the diff itself, so the
output stays safe to relay.

SERVICE MODE: when a service URL and token are in effect (from the
shell in personal mode, from hooks/enforced.env in agent mode),
the pattern list never enters this environment at all. The content
this tier would have scanned locally — commit subjects/bodies plus
the ADDED lines of each commit's diff — is POSTed to the host-side
pattern service, which answers pass/fail plus category NAMES only
(never pattern text, never matched text). An unreachable service,
a timeout, or an unparseable reply FAILS CLOSED: a push that
cannot be pattern-checked is a blocked push. With no service in
effect, local behaviour is exactly as above.

SECURITY PROFILE: when the profile in effect is "security", only
the pattern tier runs — the subject-format and plan-citation
warning blocks are skipped entirely. The warnings never block
anything, but a review session pushing feedback has no use for
development-habit reminders, and its pushes should carry exactly
the security checks and no noise. Any other profile value (or
none) keeps full behaviour; the pre-push dispatcher is the layer
that fails closed on an unknown profile value.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coverage_gap import coverage_gap_ok  # noqa: E402
from render_dashboard import SUBJECT_RX, parse_plan  # noqa: E402
from gate_common import (  # noqa: E402
    EMPTY_TREE, GitFailure, discover_pattern_files, git,
    load_patterns, parse_enforced_env)
from gate_common import (  # noqa: E402
    effective_range as _effective_range,
    range_commits as _range_commits,
    range_tip as _range_tip,
    tier_for_path as _tier_for_path)

STEP_CITE_RX = re.compile(r"\((ns-\d+|dec-\d+)\)")

def _tooling_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _agent_mode():
    """(agent_mode, enforced_env) for this install (v0.4.3).

    Agent mode is on when hooks/enforced.marker exists in the
    tooling checkout — an untracked file created by
    `repo-kit enforce on`, in an enforced deployment written by
    the supervisor into what becomes a read-only mount. The
    returned dict is the parsed hooks/enforced.env
    (KEY=VALUE lines, supervisor-written): in agent mode it is
    the ONLY source of the service coordinates and the check
    profile — the shell's environment is never consulted for
    them, because anything the agent's shell can set, the agent
    can fake."""
    root = _tooling_root()
    if not os.path.exists(os.path.join(root, "hooks",
                                       "enforced.marker")):
        return False, {}
    env = {}
    path = os.path.join(root, "hooks", "enforced.env")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                # THE grammar lives in gate_common (the Python
                # half; the dispatcher's shell parser implements
                # the same rules — v0.4.8, pass 4).
                env = parse_enforced_env(fh.read())
        except PermissionError:
            # Say it plainly (round 5 item 5): an unreadable
            # enforced.env must never read as "no settings" —
            # the defaults it would fall back to are not the
            # supervisor's.
            print("ci_checks: hooks/enforced.env exists but is "
                  "UNREADABLE (permissions) — the supervisor's "
                  "settings cannot be applied; failing closed. "
                  "Fix the file's permissions or the container's "
                  "user.")
            raise SystemExit(1)
    return True, env

def _profile(agent, agent_env):
    """The check profile in effect: in agent mode, from the
    supervisor's file (default security); in personal mode, from
    the user's shell (default full)."""
    if agent:
        return agent_env.get("REPOKIT_CHECK_PROFILE", "security")
    return os.environ.get("REPOKIT_CHECK_PROFILE", "full")

def annotate(kind, msg):
    print(f"::{kind}::{msg}")
    print(msg)

def _allowance_path(repo):
    """Where the owner override allowance lives for this repo
    (its .git directory — per-clone, never committed)."""
    git_dir = git(repo, "rev-parse", "--git-dir").strip()
    if not git_dir:
        return None
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(repo, git_dir)
    return os.path.join(git_dir, "repokit-override.json")

_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\t]{8,}")
_TEXT_RUN = re.compile(r"[\x20-\x7e\t]{8,}")
_CLASSIFY_HEAD = 8192  # a NUL in the first 8 KiB of a blob
# classifies it binary — decided from the BYTES, never from
# git's attribute-aware labels (round 5 blocker 1: an agent-
# controlled .gitattributes entry of -diff / binary made git
# call a text file binary, and the weak runs path hid it).
_EXTRACT_CAP = 256 * 1024  # per-file cap on extracted run text.
# Hitting it SURFACES (v0.4.8, secondary review pass 2): the
# caller learns the file was capped and decides — agent mode
# fails closed, personal mode asks via the coverage-gap
# protocol. (v0.4.7's docstring claimed the reclassification to
# "a resource bound, not a gate" without wiring the signal
# anywhere; the extractor set the flag and nobody read it.)
_READ_WINDOW = 1024 * 1024  # blob reads stream in 1 MiB
# windows; a whole blob is never held in RAM (round 5 item 4).

class _BlobBatch:
    """One `git cat-file --batch` process for a whole scan:
    blob reads for classification and run extraction stream
    through it in bounded windows (round 5 item 4 — the old
    whole-blob capture put a 2 GiB blob's bytes in RAM to use
    a 1 MiB slice). The protocol is strict request/response:
    every response is consumed to its trailing LF, drained in
    full even when the caller stops early, or the stream
    desyncs for every later query."""

    def __init__(self, repo):
        self.proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch", "-z"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self._buf = bytearray()
        # Per-response deadline (v0.4.8, pass 2): the batch
        # process is long-lived, so the one-shot subprocess
        # timeouts elsewhere cannot cover it — a wedged
        # cat-file must fail the gate closed, not hang the push.
        self._deadline_s = 120

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _ask(self, rev):
        self.proc.stdin.write(rev.encode("utf-8") + b"\x00")
        self.proc.stdin.flush()

    def _fill(self, want):
        """Buffer at least `want` bytes (or hit EOF), waiting at
        most the per-response deadline. Raw os.read under a
        select() deadline — a buffered reader could already hold
        the bytes while select reports the fd idle."""
        import select
        fd = self.proc.stdout.fileno()
        end = time.monotonic() + self._deadline_s
        while len(self._buf) < want:
            left = end - time.monotonic()
            if left <= 0:
                self.proc.kill()
                raise GitFailure(
                    "git cat-file --batch did not answer within "
                    f"{self._deadline_s}s — failing closed")
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            block = os.read(fd, 65536)
            if not block:
                return  # EOF; callers raise on short reads
            self._buf.extend(block)

    def _read_exact(self, n):
        self._fill(n)
        if len(self._buf) < n:
            raise GitFailure("git cat-file --batch ended "
                             "mid-blob")
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _readline(self):
        while True:
            idx = self._buf.find(b"\n")
            if idx >= 0:
                out = bytes(self._buf[:idx + 1])
                del self._buf[:idx + 1]
                return out
            before = len(self._buf)
            self._fill(before + 1)
            if len(self._buf) == before:
                raise GitFailure("git cat-file --batch ended "
                                 "unexpectedly")

    def _response_size(self):
        """The announced blob size, or None when the object is
        missing. A missing object's reply echoes the request —
        which may itself contain newlines (exotic filenames) —
        so resync to the end of its '… missing' line."""
        line = self._readline()
        text = line.decode("utf-8", "replace").rstrip("\n")
        parts = text.split(" ")
        if len(parts) >= 3 and parts[1] in (
                "blob", "commit", "tree", "tag"):
            try:
                return int(parts[2])
            except ValueError:
                pass
        guard = 0
        while not text.endswith(" missing") and guard < 3:
            text = self._readline().decode(
                "utf-8", "replace").rstrip("\n")
            guard += 1
        return None

    def peek(self, rev, limit):
        """The first `limit` bytes of the blob at rev (the rest
        is drained unread), or None when it does not exist —
        a file deleted by the commit has no blob to scan."""
        self._ask(rev)
        size = self._response_size()
        if size is None:
            return None
        want = min(size, limit)
        data = self._read_exact(want)
        left = size - want
        while left > 0:
            step = min(left, _READ_WINDOW)
            self._read_exact(step)
            left -= step
        self._read_exact(1)  # the response's trailing LF
        return data

    def stream(self, rev, on_window):
        """Feed the blob at rev to on_window(bytes) -> bool in
        bounded windows; a False return stops the FEEDING but
        never the draining. Returns the blob size, or None
        when the object is missing."""
        self._ask(rev)
        size = self._response_size()
        if size is None:
            return None
        remaining = size
        active = True
        while remaining > 0:
            block = self._read_exact(
                min(remaining, _READ_WINDOW))
            remaining -= len(block)
            if active and on_window(block) is False:
                active = False
        self._read_exact(1)
        return size

class _RunExtractor:
    """Printable-run extraction over a streamed binary blob
    (round 5 blocker 1's binary leg): ASCII runs as before,
    plus UTF-16LE and UTF-16BE decodings — a pattern stored
    UTF-16 (Windows tooling, document formats) was invisible
    to the ASCII-only pass, as was anything past the old
    first-MiB cutoff: windows now cover the WHOLE blob, with
    carry-over so a run crossing a seam is not split, up to
    _EXTRACT_CAP extracted characters per file.

    v0.4.8 (secondary review pass 2) closes two coverage holes
    in the seam/alignment handling: each UTF-16 decoding runs
    at BOTH byte parities (a UTF-16 string starting at an odd
    offset was decoded as garbage at the old offset-0-only
    alignment), and a trailing printable fragment SHORTER than
    the 8-character run minimum is carried across the seam
    too (a 5+5 split of a 10-character run was dropped whole,
    because neither half ever matched the run regex)."""

    def __init__(self):
        self.runs = []
        self.capped = False
        self._total = 0
        self._a_tail = b""
        self._le_tail = b""
        self._be_tail = b""
        # Phase-shifted UTF-16 streams: the one-byte dummy head
        # makes every decode pair start one byte later, so text
        # sitting at odd offsets is seen aligned. The carry
        # mechanics in _feed_text preserve the phase across
        # windows.
        self._le2_tail = b"\x00"
        self._be2_tail = b"\x00"

    def _emit(self, text):
        self.runs.append(text)
        self._total += len(text)
        if self._total > _EXTRACT_CAP:
            self.capped = True

    def _feed_bytes(self, tail, data):
        """ASCII runs over tail+data; returns the unterminated
        trailing run as the new tail (bounded: an over-long run
        is emitted in pieces — patterns are short). A trailing
        printable fragment too short to be a run yet (< 8) is
        carried as well: it may complete across the seam."""
        buf = tail + data
        matches = list(_PRINTABLE_RUN.finditer(buf))
        new_tail = b""
        reached_end = False
        for i, m in enumerate(matches):
            if i == len(matches) - 1 and \
                    m.end() == len(buf) and not self.capped:
                reached_end = True
                run = m.group(0)
                if len(run) > 65536:
                    self._emit(run[:-65536].decode("ascii"))
                    run = run[-65536:]
                new_tail = run
                continue
            self._emit(m.group(0).decode("ascii"))
        if not reached_end and not self.capped:
            # The printable suffix after the last non-printable
            # byte: if it is a fragment (< 8), no emitted match
            # can contain it (a run covering it would BE it), so
            # holding it is exact, not heuristic.
            i = len(buf)
            while i > 0 and (buf[i - 1] == 0x09 or
                             0x20 <= buf[i - 1] <= 0x7e):
                i -= 1
            suffix = buf[i:]
            if 0 < len(suffix) < 8:
                new_tail = suffix
        return new_tail

    def _feed_text(self, tail, data, encoding):
        """UTF-16 runs: decode tail+data (one odd trailing byte
        carried undecoded), extract runs from the text, carry
        the unterminated trailing run as text. A trailing
        fragment shorter than the run minimum is carried too
        (see _feed_bytes)."""
        raw = tail + data
        carry = raw[-1:] if len(raw) % 2 else b""
        text = raw[:len(raw) - len(carry)].decode(
            encoding, "ignore")
        matches = list(_TEXT_RUN.finditer(text))
        new_tail = ""
        reached_end = False
        for i, m in enumerate(matches):
            if i == len(matches) - 1 and \
                    m.end() == len(text) and not self.capped:
                reached_end = True
                run = m.group(0)
                if len(run) > 65536:
                    self._emit(run[:-65536])
                    run = run[-65536:]
                new_tail = run
                continue
            self._emit(m.group(0))
        if not reached_end and not self.capped:
            i = len(text)
            while i > 0 and (text[i - 1] == "\t" or
                             " " <= text[i - 1] <= "~"):
                i -= 1
            suffix = text[i:]
            if 0 < len(suffix) < 8:
                new_tail = suffix
        # The text carry rides back as re-encoded bytes so one
        # code path (feed) owns the seam state.
        return new_tail.encode(encoding, "ignore") + carry \
            if new_tail else carry

    def feed(self, data):
        if self.capped:
            return False
        self._a_tail = self._feed_bytes(self._a_tail, data)
        self._le_tail = self._feed_text(
            self._le_tail, data, "utf-16-le")
        self._be_tail = self._feed_text(
            self._be_tail, data, "utf-16-be")
        self._le2_tail = self._feed_text(
            self._le2_tail, data, "utf-16-le")
        self._be2_tail = self._feed_text(
            self._be2_tail, data, "utf-16-be")
        return not self.capped

    def finish(self):
        if self._a_tail:
            self._emit(self._a_tail.decode("ascii"))
            self._a_tail = b""
        for tail, enc in ((self._le_tail, "utf-16-le"),
                          (self._be_tail, "utf-16-be"),
                          (self._le2_tail, "utf-16-le"),
                          (self._be2_tail, "utf-16-be")):
            if len(tail) > 1:
                text = tail[:-1].decode(enc, "ignore") \
                    if len(tail) % 2 else \
                    tail.decode(enc, "ignore")
                for m in _TEXT_RUN.finditer(text):
                    self._emit(m.group(0))

def _unquote_git_path(token):
    """Undo git's C-style quoting of a ---/+++ path token
    ("b/Jos\\303\\251…" -> b/José…); an unquoted token passes
    through minus the trailing TAB git appends when a name
    contains a space (round 5 blocker 2)."""
    if not token.startswith('"'):
        return token[:-1] if token.endswith("\t") else token
    out = bytearray()
    i = 1
    while i < len(token) and token[i] != '"':
        ch = token[i]
        if ch == "\\" and i + 1 < len(token):
            nxt = token[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(token) and j < i + 4 and \
                        token[j] in "01234567":
                    j += 1
                out.append(int(token[i + 1:j], 8))
                i = j
                continue
            simple = {"n": b"\n", "t": b"\t", '"': b'"',
                      "\\": b"\\"}
            out.extend(simple.get(nxt, nxt.encode("utf-8")))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return bytes(out).decode("utf-8", "replace")

def _diff_sections(diff):
    """Split a -p diff into per-file sections keyed by the b/
    target path (quoting undone), plus the list of sections
    whose target could not be determined — those are scanned
    unattributed rather than dropped (round 5 blocker 2)."""
    sections = {}
    unmapped = []
    current = []

    def flush():
        if not current:
            return
        target = None
        deleted = False
        for line in current:
            if line.startswith("+++ "):
                token = line[4:].rstrip("\n")
                if token == "/dev/null":
                    deleted = True
                else:
                    raw = _unquote_git_path(token)
                    target = raw[2:] if raw.startswith("b/") \
                        else None
                break
        if deleted:
            return
        text = "".join(current)
        if target:
            sections[target] = text
        else:
            unmapped.append(text)

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") or \
                line.startswith("diff --combined "):
            flush()
            current = [line]
        elif current:
            current.append(line)
    flush()
    return sections, unmapped

def _commit_chunks(repo, sha, blobs=None):
    """Everything the pattern tier scans for one commit, as
    (text, path, kind) chunks, plus the commit's diff text
    (for location reporting).

    Three chunk kinds:

    - "added": per-hunk added lines of the commit's own diff
      (see _added_chunks for why added lines only). The diff
      is produced with --text so an attribute can never hide
      a hunk, and each section is FILTERED by our own content
      classification: sections of binary-classified files are
      dropped (their content arrives as runs instead), so
      --text never re-admits raw binary as payload.
    - "path": every file path the commit touches, from a
      NUL-delimited --name-only enumeration — never quoted,
      never mangled (v0.4.4 round 4 item 2 restored paths;
      round 5 blocker 2 made their source exact: the +++ line
      is quoted for non-ASCII names and tab-terminated for
      names with spaces, so a pattern inside José_García.pdf
      was never scanned).
    - "binary": the printable runs of each BINARY-classified
      file's new blob — classification by content (a NUL in
      the first 8 KiB), never by git's attribute-aware label
      (round 5 blocker 1) — ASCII and UTF-16LE/BE, streamed
      across the whole blob (round 5 item 4).

    Whether a file is text is decided from its bytes, never
    from git's attribute-aware labels — attributes are the
    pushed content's own claim about itself, and a scanner
    that trusts them imports the author's say-so (round 5
    blocker 1).

    Returns (chunks, diff, capped): capped lists the paths of
    binary files whose extracted text hit _EXTRACT_CAP — their
    tails were NOT scanned, and the caller decides what that
    means (v0.4.8: agent mode fails closed on them, personal
    mode routes them through the coverage-gap decision)."""
    own_batch = blobs is None
    if own_batch:
        blobs = _BlobBatch(repo)
    try:
        return _commit_chunks_inner(repo, sha, blobs)
    finally:
        if own_batch:
            blobs.close()

def _commit_chunks_inner(repo, sha, blobs):
    # A merge contributes only its COMBINED diff (-c): the content
    # it actually introduces (conflict resolutions, evil merges).
    # Its per-parent diffs (-m, the pre-v0.4.4 behaviour) re-scan
    # everything the merged branch carries — content that is
    # either scanned as its own commits when those commits are
    # new, or already public when they are not (the multi-
    # boundary false positive of round 4 item 7, in merge form).
    # The ENUMERATION stays -m (a superset: every name any parent
    # diff touches) — paths are cheap, and the merge publishes
    # them on this branch.
    parents = git(repo, "rev-list", "--parents", "-n", "1",
                  sha).split()
    merge_flag = ["-c"] if len(parents) > 2 else []
    diff = git(repo, "diff-tree", "--root", "-p", "--text",
               *merge_flag, "-r", "-U0", "--no-commit-id", sha,
               check=True)
    enum = git(repo, "diff-tree", "--root", "-m", "-r", "-z",
               "--name-only", "--no-commit-id", sha, check=True)
    paths = []
    for name in enum.split("\x00"):
        if name and name not in paths:
            paths.append(name)
    sections, unmapped = _diff_sections(diff)
    chunks = []
    capped = []
    for path in paths:
        head = blobs.peek(f"{sha}:{path}", _CLASSIFY_HEAD)
        if head is None:
            sections.pop(path, None)
            continue  # deleted here: nothing is published
        chunks.append((path, path, "path"))
        if b"\x00" not in head:
            section = sections.pop(path, None)
            if section is not None:
                chunks.extend(
                    (c, path, "added")
                    for c in _added_chunks(section))
        else:
            sections.pop(path, None)  # content arrives as runs
            extractor = _RunExtractor()
            blobs.stream(f"{sha}:{path}", extractor.feed)
            extractor.finish()
            if extractor.capped:
                capped.append(path)
            chunks.extend(
                (run, path, "binary") for run in extractor.runs)
    # Sections no enumerated path claimed (an exotic name whose
    # +++ token did not resolve) are scanned unattributed —
    # never silently dropped.
    known = set(paths)
    for path, section in sections.items():
        if path not in known:
            chunks.extend(
                (c, None, "added") for c in _added_chunks(section))
    for section in unmapped:
        chunks.extend(
            (c, None, "added") for c in _added_chunks(section))
    return chunks, diff, capped

def _changed_files(repo, commits):
    """The union of file names touched by the given commits
    (per-commit, so it works for every range form — a rev-list
    expression has no single diff endpoint pair)."""
    names = set()
    for sha, _s, _b in commits:
        out = git(repo, "diff-tree", "--root", "-m", "-r", "-z",
                  "--first-parent", "--name-only",
                  "--no-commit-id", sha)
        names.update(n for n in out.split("\x00") if n)
    return names

def _consume_override(repo):
    """Spend the allowance file. Called only at the moment the
    pattern tier actually applies an override (v0.4.8) — see
    _override_status."""
    path = _allowance_path(repo)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

def _override_status(repo, range_):
    """The owner override allowance for this repo, if usable.

    Returns (state, detail): state is "valid" (detail = the
    allowance dict — NOT yet consumed; _run consumes it via
    _consume_override at the moment the tier applies it),
    "stale" (a file exists but does not apply; detail says
    why) or "none". The allowance lives in the repo's .git
    directory — per-clone, never committed, never present
    in CI.

    The binding is the approved COMMIT SET plus the approved
    pattern set (v0.4.8, secondary review pass 5 major 2):
    grants record the exact commits that were scanned and
    approved (install/repo-kit writes "commits"), and a push
    whose range names any other set does not apply the
    allowance — up to v0.4.7 only the pushed TIP was compared,
    so the same tip pushed onto an older base swept commits
    the owner never saw under the approval. Allowances granted
    by older versions carry no commit set (they live at most
    an hour); those still bind by tip alone. The tip is the
    range's own tip (see _range_tip), never the checkout's
    HEAD (round 4 item 4).
    """
    path = _allowance_path(repo)
    if not path or not os.path.exists(path):
        return "none", ""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        head = _range_tip(repo, range_)
        if int(data["expires"]) < int(time.time()):
            why = "expired"
        elif data.get("head") != head:
            why = "granted for a different commit than the one " \
                  "being pushed (an override binds to the pushed " \
                  "tip, not to whatever HEAD happens to be)"
        elif isinstance(data.get("commits"), list) and \
                set(data["commits"]) != set(
                    _range_commits(repo, range_)):
            why = "the push contains commits the override was " \
                  "not granted for (it binds to the approved " \
                  "commit set, not just the tip — re-run " \
                  "`repo-kit override` to review the full set)"
        elif "commits" in data and \
                not isinstance(data.get("commits"), list):
            # Present but wrong-shaped is NOT the deliberate
            # legacy case (absent key, tip-only binding above):
            # it reads as stale, like a corrupt file (v0.4.8
            # fix pass, secondary review nit A).
            why = "the override's 'commits' entry is present " \
                  "but is not a list — the allowance is " \
                  "malformed and does not apply; re-run " \
                  "`repo-kit override` to grant a fresh one"
        else:
            return "valid", data
    except GitFailure:
        raise
    except Exception as exc:  # noqa: BLE001 — a corrupt allowance
        why = f"unreadable ({exc})"  # must never become a free pass
    return "stale", why

def _added_chunks(diff):
    """The added content of one commit's -U0 patch, as a list of
    per-hunk strings (added lines only, the leading '+' stripped).

    Only added lines are ever scanned (v0.4.3, reviewer round 3
    item 5): context and removed lines are not content the commit
    adds — up to v0.4.2 the whole patch text was scanned, so a
    commit that REMOVED a leaked value matched its own '-' line
    and blocked the cleanup. Per-hunk chunks keep a multi-line
    pattern from matching across a hunk gap.

    Combined (merge) diffs: a hunk header carries one '@' per
    parent plus one ("@@@ " for two parents), and each content
    line carries one prefix character per parent. Only the
    all-'+' lines are content the merge itself adds; a line
    added relative to one parent but identical in another is
    that parent's branch content, scanned as its own commit when
    it is new (v0.4.4 — see _commit_chunks)."""
    chunks = []
    current = None
    in_header = False
    parents = 1
    for line in diff.splitlines():
        if line.startswith("diff --git ") or \
                line.startswith("diff --combined "):
            if current:
                chunks.append("\n".join(current))
            current = None
            in_header = True
            parents = 1
            continue
        if line.startswith("@@"):
            k = len(line) - len(line.lstrip("@"))
            if k >= 2:
                in_header = False
                parents = k - 1
                if current:
                    chunks.append("\n".join(current))
                current = []
                continue
        if in_header:
            continue
        if current is not None and \
                line.startswith("+" * parents):
            current.append(line[parents:])
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c]

def _diff_location(diff, rx):
    """First location in a unified diff whose ADDED line matches
    rx, as (filename, new_line_number_or_None, kind): kind is
    "added", or ("the diff", None, "multi-line") when the pattern
    matches added content but no single line (it spans lines).
    Only the location is ever reported — never the line's
    content. Removed and header lines are never reported: they
    are not scanned (see _added_chunks)."""
    filename = None
    new_line = None
    # The ---/+++ file headers appear between the "diff --git" line
    # and the first hunk; after that, a content line can itself
    # start with ++ (e.g. the added line "++i;") and must be
    # treated as content, or every later line number in the hunk
    # drifts (v0.4.2, reviewer round 2 low item).
    in_header = False
    for line in diff.splitlines():
        if line.startswith("diff --combined "):
            # A merge's combined-diff section: the +++ line below
            # still names the file.
            filename = None
            new_line = None
            in_header = True
            continue
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            filename = parts[1] if len(parts) > 1 else None
            new_line = None
            in_header = True
            continue
        if in_header and line.startswith("+++ "):
            # The exact path, quoting undone — the scanner holds
            # it already via the -z enumeration, and a raw token
            # here misreports non-ASCII names (v0.4.8, pass 2:
            # this parser used to re-derive the path WITHOUT the
            # unquoting helper the section splitter uses).
            raw = _unquote_git_path(line[4:].rstrip("\n"))
            if raw.startswith("b/"):
                filename = raw[2:]
            continue
        if line.startswith("@@"):
            in_header = False
            m = re.search(r"\+(\d+)", line)
            new_line = int(m.group(1)) if m else None
            continue
        if in_header:
            continue
        if not in_header and line.startswith("+"):
            if rx.search(line[1:]):
                return (filename or "the diff", new_line, "added")
            if new_line is not None:
                new_line += 1
            continue
    return ("the diff", None, "multi-line")

def _human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"

def pattern_service_scan(repo, commits, material, url, token):
    """Service-mode pattern check (see the module docstring).

    Returns (status, categories, size): status is "ok" | "fail" |
    "unavailable"; categories is a list of category names; size is
    the byte size the service measured for an oversize ("too-
    large") refusal, else None. The payload is the same content
    the local tier scans: every commit subject/body in the range,
    then each commit's scanned chunks — added lines, file paths,
    and binary files' printable runs (per-commit,
    never the net diff — content added in one commit and removed
    in a later one still publishes; and added lines only, so a
    cleanup commit that removes a value is not itself flagged;
    v0.4.2 round 2 item 5 + v0.4.3 round 3 item 5). The url/token
    are passed in by the caller, which resolved them from the
    shell (personal mode) or from hooks/enforced.env (agent
    mode) — this function never reads the environment itself.
    """
    url = url.rstrip("/") + "/scan"
    parts = [subject + "\n" + body for _sha, subject, body in commits]
    for sha, _s, _b in commits:
        chunks = material[sha][0]
        parts.extend(text for text, _p, _k in chunks)
    # Dedupe identical parts (round-4 low): the same content can
    # arrive via several commits (cherry-picks, reverts and
    # re-applies), and duplicates only inflate the payload
    # toward the service's size cap.
    seen = set()
    unique = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    payload = "\n".join(unique).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"X-Repokit-Token": token,
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
    try:
        return _run(argv)
    except GitFailure as exc:
        # Fail closed: the gate could not read the commits it exists
        # to check (v0.4.2, reviewer round 2 item 2).
        print(f"ci_checks: {exc}")
        print("ci_checks: failing closed — a check that cannot run "
              "is a failed check, not a passed one.")
        return 1

def _run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", default="HEAD~1..HEAD")
    ap.add_argument("--patterns", action="append", default=None,
                    help="patterns file (repeatable); default: "
                         "$GATE_PATTERNS_FILE, which may hold several "
                         "os.pathsep-joined paths — all are unioned")
    args = ap.parse_args(argv)
    agent, agent_env = _agent_mode()
    profile = _profile(agent, agent_env)
    if profile not in ("full", "security"):
        # Validate here too (v0.4.8, pass 2): the dispatcher
        # checks the profile, but CI calls this script directly
        # (gate.yml), bypassing the dispatcher — an unknown
        # profile must fail closed, not silently behave like
        # whichever branch an != comparison falls into.
        print(f"ci_checks: unknown check profile {profile!r} "
              "(expected 'full' or 'security') — failing closed")
        return 2
    if agent:
        # Agent mode: pattern lists resolve ONLY from the tooling
        # checkout root; the shell's GATE_PATTERNS_FILE and the
        # --patterns flag are not believed (either could be
        # pointed at a dummy file by the process being gated).
        root = _tooling_root()
        pattern_paths = discover_pattern_files(root, None)
        svc_url = agent_env.get("REPOKIT_PATTERNS_URL", "")
        svc_token = agent_env.get("REPOKIT_PATTERNS_TOKEN", "")
    else:
        if args.patterns is None:
            env = os.environ.get("GATE_PATTERNS_FILE", "")
            pattern_paths = [p for p in env.split(os.pathsep) if p]
        else:
            pattern_paths = args.patterns
        svc_url = os.environ.get("REPOKIT_PATTERNS_URL", "")
        svc_token = os.environ.get("REPOKIT_PATTERNS_TOKEN", "")
    repo = os.path.abspath(args.repo)
    failures = 0

    scan_range = _effective_range(repo, args.range)
    # git log needs commit endpoints; with an empty-tree base the
    # log side is the range's own head (the whole history up to it),
    # never a substituted HEAD.
    log_range = scan_range.partition("..")[2] \
        if scan_range.startswith(EMPTY_TREE) else scan_range
    # The range may be a rev-list expression with spaces (the
    # multi-boundary form); it is dispatcher-generated (SHAs and
    # fixed keywords only), so word-splitting it into argv is safe
    # and required — git log would read the whole string as one
    # revision name otherwise.
    log = git(repo, "log", *shlex.split(log_range),
              "--format=%H%x00%s%x00%b%x1e", check=True)
    commits = []
    for chunk in log.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        sha, _, rest = chunk.partition("\x00")
        subject, _, body = rest.partition("\x00")
        # Full SHAs (v0.4.8, pass 2): the 12-char truncation was
        # also used as a revision in later git calls, where a
        # collision or a future format change turns display
        # shorthand into a wrong answer. Display sites slice.
        commits.append((sha.strip(), subject.strip(), body))
    if not commits:
        print("ci_checks: no commits in range — nothing to check")
        return 0

    # The chunk build serves the pattern route in effect. The
    # SERVICE route needs every commit's chunks at once (one
    # payload), so it materialises them here — bounded by the
    # service's own payload cap, which refuses oversize scans.
    # The LOCAL route builds per commit inside its scan loop
    # and releases each commit's chunks before the next, so a
    # long range never holds the whole range's text in RAM at
    # once (v0.4.8, pass 2: there was no aggregate bound).
    capped = []
    service_handled = False
    if svc_url and svc_token:
        # Service mode takes precedence: with service coordinates
        # in effect, the pattern list is on the host by design
        # and must not be read locally even if a copy happens to
        # be visible.
        service_handled = True
        with _BlobBatch(repo) as blobs:
            material = {sha: _commit_chunks(repo, sha, blobs)
                        for sha, _s, _b in commits}
        capped = [(sha, p) for sha, (_c, _d, caps)
                  in material.items() for p in caps]
        status, categories, size = pattern_service_scan(
            repo, commits, material, svc_url, svc_token)
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
                        "— push blocked; add patterns-strict.txt or "
                        "patterns-relaxed.txt on the host or push "
                        "from a Mac terminal")
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
                        "this push is legitimate, the supervisor "
                        "runs: repo-kit allow-oversize — then retry "
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

    # Owner override (local runs only — service mode answers to the
    # host, and CI checkouts carry no allowance file). A valid
    # allowance is consumed and skips ONLY the patterns the owner
    # approved; every other pattern still runs. A stale one is
    # reported and ignored.
    override_note = None
    if agent and not service_handled:
        # AGENT mode never honours the override (round-4
        # disposition catch): the allowance is a file in the
        # agent-writable .git, so the process being gated could
        # forge one. It is a personal-mode feature, like
        # `repo-kit checks --off`. Say so when one is present —
        # never silently, never applied.
        _apath = _allowance_path(repo)
        if _apath and os.path.exists(_apath):
            print("ci_checks: an owner override file exists but is "
                  "IGNORED in agent mode — overrides are a "
                  "personal-mode feature; the supervisor controls "
                  "this gate.")
    elif not service_handled:
        state, detail = _override_status(repo, args.range)
        if state == "valid":
            override_note = detail
        elif state == "stale":
            print("ci_checks: an owner override exists but does not "
                  f"apply ({detail}) — pattern tier runs as normal")
    pats, pat_errors = ([], []) if service_handled else \
        load_patterns(pattern_paths)
    for path, lineno in pat_errors:
        failures += 1
        annotate(
            "error",
            f"Pre-public gate: pattern list {path} line {lineno} "
            "does not compile — the pattern tier cannot run with a "
            "broken pattern, so this check fails closed. Fix the "
            "line (counting every line, comments included) and "
            "re-run.")
    # There is deliberately NO attribute guard here any more
    # (round 6 item 3; owner ruling 2026-11-18). v0.4.5–0.4.6
    # gated the push itself when a text file's diff was
    # attribute-suppressed, because diff-reading tools could
    # not see the file. The gap is now CLOSED instead of
    # flagged: this tier extracts content from blobs, and
    # gitleaks is invoked with --text, which overrides the
    # diff attribute from every source (verified on the real
    # 8.30.1: committed .gitattributes AND .git/info/
    # attributes both become visible; GIT_ATTR_SOURCE, the
    # alternative, still missed info/attributes). With no
    # scanner left blind, there is nothing to ask about —
    # suppression is now merely a display preference.
    approved = set()
    if override_note:
        approved = {p for p in override_note.get("patterns", [])
                    if isinstance(p, str)}
    skipped = [p for p in pats if p["raw"] in approved]
    active = [p for p in pats if p["raw"] not in approved]
    if override_note and skipped and not pat_errors:
        # Consumption happens HERE (v0.4.8, pass 2) — the moment
        # the tier actually applies the allowance — not at
        # validation time: a run that failed earlier for an
        # unrelated reason (a pattern file that does not compile,
        # a range that would not resolve) used to burn the
        # one-push allowance without it ever being used.
        _consume_override(repo)
        names = ", ".join(f"{p['tier']} #{p['number']}"
                          for p in skipped)
        print("ci_checks: owner override consumed (granted "
              f"{override_note.get('granted_at', '?')}, bound to commit "
              f"{str(override_note.get('head', ''))[:12]}) — "
              f"skipping approved pattern(s): {names}. All other "
              "patterns still run.")
    if active:
        print(f"ci_checks: pattern list loaded ({len(pats)} patterns "
              f"from {len(pattern_paths)} file(s))")
        tier_before = failures
        matched = []
        # Each commit's OWN chunks, not the range's net diff:
        # content added in one commit and removed in a later one
        # leaves the net diff clean while every intermediate
        # commit stays public on the branch (v0.4.2, reviewer
        # round 2 item 5). Chunks are built per commit inside
        # the loop and released before the next commit (the
        # aggregate bound, v0.4.8); each pattern is reported
        # once, at its first matching commit, as before.
        chunk_reported = set()
        with _BlobBatch(repo) as blobs:
            for sha, subject, body in commits:
                chunks, diff, cap = _commit_chunks(
                    repo, sha, blobs)
                capped.extend((sha, p) for p in cap)
                for p in active:
                    if p["rx"].search(subject + "\n" + body):
                        failures += 1
                        matched.append(p)
                        annotate(
                            "error",
                            f"Pre-public gate: commit {sha[:12]} "
                            f"message matches blocked personal "
                            f"pattern {p['tier']} #{p['number']}. "
                            "Commit messages are permanent and "
                            "become this project's public activity "
                            "feed. Rewrite the message and "
                            "re-push.")
                for p in active:
                    key = (p["tier"], p["number"], p["raw"])
                    if key in chunk_reported:
                        continue
                    hit = next((c for c in chunks
                                if p["rx"].search(c[0])), None)
                    if hit:
                        failures += 1
                        matched.append(p)
                        chunk_reported.add(key)
                        _text, cpath, ckind = hit
                        if ckind == "path":
                            spot = f"file path {cpath}"
                        elif ckind == "binary":
                            spot = ("printable text in binary "
                                    f"file {cpath}")
                        else:
                            fname, lineno, kind = \
                                _diff_location(diff, p["rx"])
                            where = fname if lineno is None else \
                                f"{fname} line {lineno}"
                            spot = f"{kind} line in {where}"
                        annotate(
                            "error",
                            f"Pre-public gate: commit {sha[:12]} "
                            "adds content matching blocked "
                            f"personal pattern {p['tier']} "
                            f"#{p['number']} ({spot}). Remove "
                            "the instance data before this can "
                            "merge — public history cannot be "
                            "unpublished, and later removing it "
                            "in the same branch does not help: "
                            "every commit is published.")
        if failures > tier_before:
            tiers = {p["tier"] for p in matched}
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(
                    "ci_checks: FALSE POSITIVE? This run is CI, "
                    "where local overrides do not apply. The ladder: "
                    "(1) fix the content at the location above; "
                    "(2) if the pattern itself is too broad, narrow "
                    "it in patterns-relaxed.txt and re-run "
                    "`repo-kit provision` to update the GATE_PATTERNS "
                    "secret, then re-run this check; (3) on a "
                    "variant-B ruleset the repo owner can bypass-"
                    "merge the pull request. Pattern numbers count "
                    "the pattern lines of patterns-relaxed.txt, in "
                    "order — the owner can map them locally.")
            elif tiers == {"strict"}:
                print(
                    "ci_checks: FALSE POSITIVE? These matches are "
                    "against your local-only strict list, which CI "
                    "never sees — an owner override clears them end "
                    "to end. Run `repo-kit override` in this repo "
                    "from an interactive terminal: it shows the "
                    "exact matches (never printed here, so this "
                    "output stays safe to relay) and can grant a "
                    "one-push allowance scoped to exactly the "
                    "patterns and commits you approve — 10 "
                    "minutes, bound to that commit set, consumed "
                    "when the tier applies it. "
                    "The interactive-terminal requirement is a "
                    "speed bump, not a security boundary. For a "
                    "recurring false positive, narrow the pattern "
                    "instead.")
            else:
                print(
                    "ci_checks: FALSE POSITIVE? Run `repo-kit "
                    "override` in this repo from an interactive "
                    "terminal to see the exact matches (never "
                    "printed here, so this output stays safe to "
                    "relay). Note the ladder: an override only "
                    "clears a match locally — a relaxed-list match "
                    "will still fail in CI. For a relaxed false "
                    "positive that must merge, narrow the pattern "
                    "in patterns-relaxed.txt and re-run `repo-kit "
                    "provision` to update the CI secret, or (on a "
                    "variant-B ruleset) the owner can bypass-merge. "
                    "Strict-list matches never reach CI and an "
                    "override clears those end to end.")
    elif not service_handled and not pat_errors:
        if pats and not active:
            print("ci_checks: every loaded pattern was covered by "
                  "the owner override — pattern tier skipped for "
                  "this run")
        else:
            # No pattern source at all: no service in effect and no
            # usable list. In personal mode that is a notice; in
            # AGENT mode (or under the security profile, the user's
            # own harness choice) it must be a hard failure —
            # otherwise the whole tier evaporates when the gated
            # process's environment simply lacks the variables
            # (v0.4.2, reviewer round 2 item 3; modes, v0.4.3).
            if agent or profile == "security":
                failures += 1
                annotate(
                    "error",
                    "Pre-public gate: this install is in AGENT "
                    "MODE (hooks/enforced.marker) or the security "
                    "profile, but no pattern source is available — "
                    "hooks/enforced.env names no pattern service "
                    "and no pattern list exists in the tooling "
                    "checkout. Failing closed: an enforced gate "
                    "with no patterns checks nothing. The "
                    "supervisor must provide the service (via "
                    "hooks/enforced.env) or the list; a personal "
                    "install turns agent mode off with "
                    "`repo-kit enforce off`.")
            elif (os.environ.get("REPOKIT_PATTERN_GAP_OK")
                    == "1"
                    and os.environ.get("GITHUB_ACTIONS")
                    == "true"):
                # The workflow's fork/Dependabot branch
                # (round 6 item 1): the secret is unavailable
                # to those runs BY PLATFORM DESIGN, and the
                # owner ruled warning-only for them (round 3
                # item 6c) — the workflow prints the warning;
                # this dedicated opt-out, honored only in CI
                # and only for this branch of the decision,
                # keeps the tier's absence from failing the
                # run. It does not extend to any other gap.
                print("ci_checks: pattern list unavailable "
                      "to this run (fork/Dependabot opt-out) "
                      "— personal-pattern tier skipped")
            else:
                # Personal mode (round 6): a skipped tier is a
                # coverage gap, and a printed notice ships it
                # silently — so this asks on a terminal and
                # fails closed without one (see
                # coverage_gap.coverage_gap_ok).
                if not coverage_gap_ok(
                        "no pattern list is available, so "
                        "the personal-pattern tier will not "
                        "run at all.",
                        "To fix: create patterns-relaxed.txt "
                        f"at the tooling checkout root "
                        f"({_tooling_root()}), or set "
                        "GATE_PATTERNS_FILE to a list (in CI, "
                        "set the GATE_PATTERNS secret — "
                        "`repo-kit provision` does this). To "
                        "skip the pattern check permanently "
                        "for THIS repo: repo-kit checks --off "
                        "patterns. To proceed despite the gap "
                        "for one push: "
                        "REPOKIT_ALLOW_COVERAGE_GAPS=1 git "
                        "push …"):
                    failures += 1

    # Extraction-cap surfacing (v0.4.8, secondary review pass 2):
    # a binary whose extracted text hit the cap was scanned only
    # up to the cap and passed silently in BOTH modes — the
    # extractor set its flag and, since v0.4.7 removed the report
    # plumbing, no caller ever read it. The cap is a gate input
    # again, with the v0.4.5 semantics: agent mode fails closed
    # per capped file; personal mode routes the set through ONE
    # coverage-gap decision for the push.
    if capped:
        if agent:
            for sha, path in capped:
                failures += 1
                annotate(
                    "error",
                    f"Pre-public gate: binary file {path} "
                    f"(commit {sha[:12]}) produced more "
                    f"extracted text than the "
                    f"{_human_size(_EXTRACT_CAP)} cap — content "
                    "past the cap was NOT scanned, and agent mode "
                    "fails closed on unscanned content. Split "
                    "the file so it extracts under the cap.")
        elif not coverage_gap_ok(
                f"{len(capped)} binary file(s) (first: "
                f"{capped[0][1]}) exceeded the pattern scan's "
                f"extraction cap of "
                f"{_human_size(_EXTRACT_CAP)} of extracted text "
                "per file, so part of their content will not "
                "be scanned.",
                "To fix: split the file or reduce its printable "
                "content so it extracts under the cap. To "
                "proceed despite the gap for one push: "
                "REPOKIT_ALLOW_COVERAGE_GAPS=1 git push …"):
            failures += 1

    # Warnings tier — skipped ENTIRELY under the security profile
    # (see the module docstring): the pattern tier above is the
    # whole job there.
    if profile != "security":
        cited = any(STEP_CITE_RX.search(s + b) for _, s, b in commits)
        plan_changed = "PLAN.md" in _changed_files(repo, commits)
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
            # A repo without PLAN.md (init not run there) must not
            # crash the warnings tier — warnings never block, and a
            # traceback here would fail the whole gate for the wrong
            # reason.
            plan_path = os.path.join(repo, "PLAN.md")
            plan = parse_plan(plan_path) if os.path.exists(
                plan_path) else {}
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
