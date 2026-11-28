#!/usr/bin/env python3
"""The coverage-gap decision, shared by every check
(v0.4.7, round 6 items 5 and 6).

A COVERAGE GAP is a check that cannot run at all — a
missing pattern list, a missing scanner binary. (Content a
scanner cannot *see* is no longer in this class: the gate's
scanners are attribute-immune by construction or by
invocation, so there is nothing left to ask about there.)
In personal mode a gap is decided here, once:

- REPOKIT_ALLOW_COVERAGE_GAPS=1 in the environment is the
  owner's explicit choice to proceed — honoured only on
  this personal-mode path (agent mode never calls here,
  and the dispatcher scrubs the variable besides).
- With a terminal, the run STOPS and asks on /dev/tty (a
  pre-push hook's stdin is git's ref-line pipe, never the
  keyboard). The question has a 60-second timeout — no
  answer counts as no — and a backgrounded process (not the
  terminal's foreground group) counts as no-terminal: its
  read would stop the job with SIGTTIN instead of failing
  cleanly.
- Without a terminal (CI, GUI clients) the gap fails
  closed, with the remedy in the message where the log
  reader will find it.

Answers are memoized per push in the file named by
REPOKIT_GAP_STATE (created and removed by the dispatcher),
keyed by the gap's summary: `git push --all` runs the
checks once per range, and the same gap must not ask
twice — while a DIFFERENT gap still asks on its own.
Everything printed is escaped for control characters: the
summaries can contain filenames from pushed commits, and a
crafted name must not be able to fake or bury the [y/N]
line (round 6 item 6).

Used two ways: ci_checks imports coverage_gap_ok; the
shell checks execute this file with (summary, remedy) as
argv — exit 0 proceeds, exit 1 stops.
"""

import hashlib
import os
import select
import sys

TIMEOUT_SECONDS = 60


def _clean(text):
    """Escape every non-printable / non-ASCII character, the
    same scheme repo-kit's override output uses, so a
    crafted filename cannot inject terminal controls."""
    return "".join(
        ch if 32 <= ord(ch) < 127 else
        f"\\x{ord(ch):02x}" if ord(ch) < 256 else
        f"\\u{ord(ch):04x}" for ch in text)


def _tty():
    """Open /dev/tty for asking, or return None when there
    is no terminal to ask on: the device does not open (no
    controlling terminal), GitHub Actions (no-terminal by
    rule — a run must never block on input), or this process
    group is not the terminal's foreground group (a
    backgrounded push)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return None
    try:
        tty = open("/dev/tty", "r+b", buffering=0)
    except OSError:
        return None
    try:
        if os.tcgetpgrp(tty.fileno()) != os.getpgrp():
            tty.close()
            return None
    except OSError:
        pass  # no tcgetpgrp on this platform: proceed
    return tty


def _memo(key):
    path = os.environ.get("REPOKIT_GAP_STATE")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.rstrip("\n").partition(" ")
                if k == key:
                    return v == "y"
    except OSError:
        pass
    return None


def _memo_save(key, answer):
    path = os.environ.get("REPOKIT_GAP_STATE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key} {'y' if answer else 'n'}\n")
    except OSError:
        pass


def coverage_gap_ok(summary, remedy):
    """True when the run may proceed despite the gap."""
    summary = _clean(summary)
    remedy = _clean(remedy)
    if os.environ.get("REPOKIT_ALLOW_COVERAGE_GAPS") == "1":
        print("coverage gap accepted for this push via "
              f"REPOKIT_ALLOW_COVERAGE_GAPS: {summary}")
        return True
    key = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]
    memo = _memo(key)
    if memo is not None:
        print(f"coverage gap already answered this push "
              f"({'proceeding' if memo else 'stopping'}): "
              f"{summary}")
        return memo
    tty = _tty()
    if tty is None:
        print("::error::Pre-public gate: coverage gap with "
              "no terminal to confirm on — failing closed. "
              f"{summary} {remedy}")
        print("Pre-public gate: coverage gap with no "
              "terminal to confirm on — failing closed. "
              f"{summary} {remedy}")
        return False
    answer = False
    try:
        tty.write(f"COVERAGE GAP — {summary} Continue "
                  "anyway? [y/N] ".encode("utf-8"))
        # The WHOLE answer read lives under the deadline
        # (v0.4.8, pass 3): the old code bounded only the
        # select() wait for the first byte, then called
        # readline() — a partial line (bytes, no newline)
        # blocked the push indefinitely.
        import time
        deadline = time.monotonic() + TIMEOUT_SECONDS
        buf = bytearray()
        # Track WHY the loop ended (v0.4.8 fix pass, secondary
        # review item 6): a newline-less buffer is evaluated
        # ONLY on EOF — at the deadline it is not an answer,
        # however complete it looks.
        deadline_expired = False
        saw_eof = False
        while b"\n" not in buf:
            left = deadline - time.monotonic()
            if left <= 0:
                deadline_expired = True
                break
            ready, _, _ = select.select([tty], [], [], left)
            if not ready:
                deadline_expired = True
                break
            chunk = os.read(tty.fileno(), 1)
            if not chunk:
                saw_eof = True
                break
            buf.extend(chunk)
        if b"\n" not in buf and (deadline_expired
                                 or not saw_eof or not buf):
            # Deadline/timeout with a partial (or empty) buffer,
            # or an empty buffer at EOF: no answer counts as no,
            # per this module's contract. Only EOF (saw_eof, not
            # deadline_expired) with a non-empty newline-less
            # buffer falls through and is evaluated below.
            print("coverage gap: no answer within "
                  f"{TIMEOUT_SECONDS}s — treating as no. "
                  f"{remedy}")
            _memo_save(key, False)
            return False
        line = bytes(buf).decode("utf-8", "replace")
        answer = line.strip().lower() in ("y", "yes")
    except OSError:
        # Never fail silently (v0.4.8, pass 3): a terminal that
        # errors mid-question is a no, WITH the reason and the
        # remedy where the user can see them.
        print("coverage gap: the terminal could not be read — "
              f"treating as no. {summary} {remedy}")
        _memo_save(key, False)
        return False
    finally:
        tty.close()
    _memo_save(key, answer)
    if answer:
        print("coverage gap confirmed by the user for this "
              "push — proceeding.")
    else:
        print("push stopped — the coverage gap was not "
              f"confirmed. {remedy}")
    return answer


if __name__ == "__main__":
    _summary = (sys.argv[1] if len(sys.argv) > 1
                else "a check cannot run")
    _remedy = sys.argv[2] if len(sys.argv) > 2 else ""
    sys.exit(0 if coverage_gap_ok(_summary, _remedy) else 1)
