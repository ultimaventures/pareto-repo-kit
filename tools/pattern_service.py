#!/usr/bin/env python3
"""pattern_service — the host-side half of repo-kit's pattern gate.

WHY THIS EXISTS
  The pattern check scans outgoing commits against the owner's
  personal pattern lists (patterns-relaxed.txt + patterns-strict.txt
  in the tooling checkout: emails, handles, hostnames, paths — a map
  of exactly what must never reach a public repo). When the code
  being checked was written by an agent — in a container, a harness,
  or any environment the list must stay hidden from — the list
  cannot be present locally at all. So the check splits: the client
  (tools/ci_checks.py, driven by the normal pre-push hook) sends the
  commit text OUT to this service, which runs on the owner's machine
  where the lists live, and answers with pass/fail plus tier NAMES
  only. Pattern text, matched text, and diff content are never
  returned and never logged. The agent needs exactly three
  environment variables (REPOKIT_PATTERNS_URL, REPOKIT_PATTERNS_TOKEN,
  REPOKIT_CHECK_PROFILE=security); `repo-kit pattern-service` starts
  this service and prints them.

  This service ships WITH repo-kit (since v0.4.0) so it can never
  drift from the client: one loader, one list format, one protocol,
  versioned together. It supersedes the hand-rolled copy the devbox
  project carried before v0.4.0.

PROTOCOL
  POST /scan
    Header:  X-Repokit-Token: <token>
    Body:    text/plain — commit subjects/bodies + the unified diff
    Reply:   JSON only — {"ok": true}
                      or {"ok": false, "categories": ["relaxed", ...]}
             ("categories" carries TIER names — the only vocabulary
             this service ever answers in)
    Errors:  bad/absent token -> 403 "forbidden"; body > 1 MiB ->
             413 "too-large" with a "size" field (measured bytes);
             scan cap reached -> 429 "scan-limit"; non-POST -> 405.
  If NO patterns are loaded (files absent) the service still runs
  and answers every scan {"ok": false, "categories":
  ["no-patterns-configured"]} — the client fails closed, so a
  missing list can never masquerade as a clean pass.

OWNER OVERSIZE ALLOWANCE
  A legitimate push can exceed 1 MiB (an initial import, a vendored
  tree). A flat refusal there teaches bypass habits, so the OWNER
  can authorise ONE oversize scan: an allowance record (JSON:
  {"instance": ..., "expires": <epoch>, "ceiling": <bytes>}) placed
  at REPOKIT_PATTERN_ALLOWANCE_FILE — a path the checked environment
  cannot write. An oversize request that finds a live record FOR
  THIS INSTANCE (REPOKIT_PATTERN_INSTANCE) is accepted up to the
  record's ceiling; the record is deleted BEFORE the scan runs
  (one-shot even if the scan then flags), and the use is tallied in
  the counts as "owner_allowed". An over-ceiling, expired,
  instance-mismatched or malformed record changes nothing, and an
  over-ceiling refusal does NOT consume the record. The allowance
  never raises the scan lifetime cap.

AUDIT COUNTS
  The service keeps counts — scans, flagged scans, per-tier
  tallies, distinct peer addresses, over-limit refusals,
  truncated-body refusals, owner-authorised oversize scans, and
  bad-token 403s (a prober
  without the token leaves a trace too). Content-free by
  construction: numbers, tier names, addresses only. When
  REPOKIT_PATTERN_COUNTS_FILE is set, counts are written there as
  JSON, atomically (temp file + rename), at startup and after every
  scan, so a supervisor can read them at any moment. (The
  unauthenticated 403 path tallies in memory and writes at most
  every few seconds, and the peer list is capped — a prober's loop
  must not become a disk-write amplifier; v0.4.2 item 7b.) The counts are
  how probing shows up: the list is protected from casual leaks, not
  from probing (a client holding the token can oracle-test
  candidates) — the lifetime cap bounds the probing per service run
  and the counts expose it after the fact.

PATTERN LISTS (the same files and rules the local check uses)
  Loaded from REPOKIT_PATTERN_FILES (newline-separated paths;
  the launcher passes the tooling checkout's
  patterns-relaxed.txt and, when present, patterns-strict.txt).
  One regex per line, matched case-insensitively; blank lines and
  #-comments are skipped; the tier is derived from the filename
  (exactly patterns-relaxed.txt → "relaxed", exactly
  patterns-strict.txt → "strict", anything else → its basename
  stem — the same rule the local check uses). A pattern that does not compile is FATAL: the
  service refuses to start, naming the file and line (v0.4.2,
  reviewer round 2 item 6 — patterns compile once at startup, so
  failing there costs nothing, while the old skip-and-serve
  behaviour answered ok:true for content the dead pattern existed
  to catch; the local check made the same choice in v0.3.1).
  Matching is per LINE, probed in overlapping 32 KiB windows: the
  patterns this system carries are single-line identifiers, and
  whole-text matching let one pathological line (a minified
  bundle) or a catastrophic pattern stall every handler thread
  (item 7a); the windows keep every character of a long line
  checked without an unbounded probe (round 3 item 2). A pattern
  written to span lines will match in the local check but not
  here — do not write those.

ENVIRONMENT
  REPOKIT_PATTERN_TOKEN        the token (required — the service
                               refuses to run ungated)
  REPOKIT_PATTERN_FILES        newline-separated pattern list
                               paths
  REPOKIT_PATTERN_PORT         listen port (default 18741)
  REPOKIT_PATTERN_HOST         bind address (default 127.0.0.1;
                               for containers, work down the
                               EXPOSURE ladder before 0.0.0.0)
  REPOKIT_PATTERN_COUNTS_FILE  optional audit-counts JSON path
  REPOKIT_PATTERN_INSTANCE     instance name; an allowance record
                               is honoured only on an exact match
  REPOKIT_PATTERN_ALLOWANCE_FILE
                               optional owner oversize-allowance
                               record path; unset = no allowance can
                               ever be honoured

EXPOSURE, honestly: the default bind is loopback, which same-host
agents can reach and nothing else can. When a container must reach
the service, work down this ladder and stop at the first rung that
works (v0.4.2, reviewer round 2 item 8 — the old text pushed the
last rung first): (1) loopback — on Docker Desktop and OrbStack,
host.docker.internal usually reaches a loopback-bound host service
through the runtime's proxy, so try the default bind first;
(2) the specific bridge/gateway address (on Linux, the docker0 or
host-gateway IP) — reachable by the container, not by the LAN;
(3) 0.0.0.0 — every interface, LAN included, for the service's
lifetime. The mitigations are the per-run token (required
on every request, compared as bytes so a malformed header earns a
clean 403, never an exception), a scan-only surface (no write, no
exec, no file access beyond the patterns loaded at startup),
tier-only replies, a 1 MiB body cap, a 100-scan lifetime cap, a
10-second socket timeout (slow clients cannot hold handler
threads), and a lifetime bounded by the owner's terminal or the
supervising session. It logs nothing but counts to stderr.
"""

import hmac
import json
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gate_common import load_patterns  # noqa: E402

MAX_BODY = 1 * 1024 * 1024  # 1 MiB
MAX_SCANS = 100             # per service lifetime
SOCKET_TIMEOUT = 10         # seconds, per read
MAX_LINE = 32768            # matching window per line (item 7a)
WINDOW_OVERLAP = 1024       # window overlap: a match straddling a
                            # window seam is still seen whole, as
                            # long as the pattern itself is shorter
                            # than the overlap (round 3 item 2)


def _line_hit(rx, line):
    """True when rx matches anywhere in line, probing in
    overlapping windows of MAX_LINE characters so no part of a
    long line goes unchecked (v0.4.3). A pattern longer than
    WINDOW_OVERLAP could still straddle a seam unseen — the
    documented bound; real identifiers are far shorter."""
    if len(line) <= MAX_LINE:
        return bool(rx.search(line))
    step = MAX_LINE - WINDOW_OVERLAP
    start = 0
    while True:
        if rx.search(line[start:start + MAX_LINE]):
            return True
        if start + MAX_LINE >= len(line):
            return False
        start += step
PEER_CAP = 64               # distinct peers kept; rest are tallied
BODY_READ_DEADLINE = 30     # seconds for a whole body (item 7e)
FORBIDDEN_WRITE_INTERVAL = 5  # seconds between 403 counts writes
COUNTS_FILE = os.environ.get("REPOKIT_PATTERN_COUNTS_FILE", "")
ALLOWANCE_FILE = os.environ.get("REPOKIT_PATTERN_ALLOWANCE_FILE", "")
SERVICE_INSTANCE = os.environ.get("REPOKIT_PATTERN_INSTANCE", "")


def _pattern_file_paths():
    raw = os.environ.get("REPOKIT_PATTERN_FILES", "")
    # Newline-separated (a path may contain a colon). The
    # pre-v0.4.2 colon-separated form is no longer parsed
    # (v0.4.8 shim deletion — the only writer, the repo-kit
    # launcher, has emitted newlines since v0.4.2).
    return [p.strip() for p in raw.splitlines() if p.strip()]


PATTERNS, PATTERN_ERRORS = load_patterns(_pattern_file_paths())
TOKEN = os.environ.get("REPOKIT_PATTERN_TOKEN", "")
TOKEN_BYTES = TOKEN.encode("utf-8")

# --- Audit counts -------------------------------------------------------
# Numbers, tier names and peer addresses ONLY — never content.
# Written to COUNTS_FILE atomically after every change so a
# supervisor can surface them ("pattern service: N scans, M
# flagged") even though the service itself is stopped at teardown.
_counts_lock = threading.RLock()
_counts = {
    "scans": 0,          # token-valid scans actually performed
    "flagged": 0,        # scans that matched at least one tier
    "over_limit": 0,     # token-valid scans refused by the lifetime cap
    "owner_allowed": 0,  # oversize scans the owner's allowance let in
    "forbidden": 0,      # bad-token requests refused 403
    "truncated": 0,      # bodies refused unscanned as truncated
                         # (v0.4.8 fix pass, secondary review item 7)
    "categories": {},    # tier name -> number of scans it matched in
    "peers": [],         # distinct peer addresses (scans AND 403s),
                         # capped at PEER_CAP entries
    "peers_extra": 0,    # requests from peers beyond the cap
}

_last_forbidden_write = 0.0  # counts-file writes on the 403 path
                             # are throttled (item 7b): the tally
                             # updates in memory per request, the
                             # file at most every few seconds


def _note_peer_locked(peer):
    """Record a peer address under the counts lock, capped: the
    list holds at most PEER_CAP distinct addresses; further
    distinct peers bump peers_extra instead of growing the file
    without bound (item 7b)."""
    if not peer:
        return
    if peer in _counts["peers"]:
        return
    if len(_counts["peers"]) < PEER_CAP:
        _counts["peers"].append(peer)
    else:
        _counts["peers_extra"] += 1


def _write_counts_locked():
    if not COUNTS_FILE:
        return
    payload = dict(_counts)
    payload["peers"] = sorted(_counts["peers"])
    try:
        tmp = COUNTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, COUNTS_FILE)
    except OSError:
        pass  # audit must never break the gate itself


def write_counts():
    with _counts_lock:
        _write_counts_locked()


# --- Owner oversize allowance -------------------------------------------
# The record is written by the owner into a location the checked
# environment cannot reach, so its mere presence is the owner's
# authorisation. Everything about the check fails closed: any doubt
# (no path configured, no file, malformed JSON, wrong instance,
# expired, over the record's ceiling) means "no allowance", and only
# a fully valid record is consumed.
_allowance_warned = False


def _consume_allowance(length):
    """Try to spend the owner's one-push oversize allowance on a
    body of `length` bytes. Returns True (record deleted, use
    tallied) iff a live, matching, in-ceiling record existed."""
    global _allowance_warned
    if not ALLOWANCE_FILE or not os.path.exists(ALLOWANCE_FILE):
        return False
    try:
        with open(ALLOWANCE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        instance = str(data["instance"])
        expires = int(data["expires"])
        ceiling = int(data["ceiling"])
    except Exception:
        # A malformed record is treated as absent — noted once on
        # stderr, never an exception, never a traceback.
        if not _allowance_warned:
            _allowance_warned = True
            print("pattern-service: allowance file is malformed — "
                  "treating it as absent", file=sys.stderr)
        return False
    if instance != SERVICE_INSTANCE:
        return False
    if int(time.time()) >= expires:
        return False
    if length > ceiling:
        # Over the owner's ceiling too: refuse WITHOUT consuming —
        # a too-big attempt must not burn the one allowance.
        return False
    # Claim it. Deleting the record IS the one-shot: if the delete
    # fails because the record is already gone, a concurrent
    # request consumed it first and this one is refused; any other
    # delete failure also fails closed rather than double-spending.
    try:
        os.remove(ALLOWANCE_FILE)
    except OSError:
        return False
    with _counts_lock:
        _counts["owner_allowed"] += 1
        _write_counts_locked()
    print(f"pattern-service: owner allowance consumed for "
          f"oversize scan bytes={length}", file=sys.stderr)
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        # A client that connects and stalls must not hold this
        # handler thread (and its socket) forever: after the
        # timeout the read/write fails and the connection closes.
        self.connection.settimeout(SOCKET_TIMEOUT)

    def log_message(self, fmt, *args):  # silence the default logger
        pass

    def _json(self, code, payload, head_only=False):
        body = json.dumps(payload).encode("utf-8")
        closing = code >= 400
        if closing:
            # Error paths do not consume the request body (the 403
            # path never reads it); leaving it unread on a
            # keep-alive connection desyncs the next request's
            # parse (item 7d). Close instead — and SAY so in the
            # response headers, or the client will try to reuse
            # the connection anyway. (The only error that used to
            # close at all was 413.)
            self.close_connection = True
        self.send_response(code)
        if closing:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _read_body(self, length):
        """Read exactly `length` body bytes under an OVERALL
        deadline. The socket timeout alone is per read, so a
        client trickling one byte at a time could hold a handler
        thread indefinitely (item 7e); the deadline bounds the
        whole body. Raises TimeoutError past it (the server's
        error handler treats that as a vanished client)."""
        chunks = []
        remaining = length
        deadline = time.time() + BODY_READ_DEADLINE
        while remaining > 0:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("pattern-service: body read "
                                   "deadline exceeded")
            self.connection.settimeout(min(SOCKET_TIMEOUT, left))
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def do_GET(self):
        self._json(405, {"ok": False,
                         "categories": ["method-not-allowed"]})

    do_PUT = do_GET
    do_DELETE = do_GET

    def do_HEAD(self):
        # A HEAD response carries the headers a GET would send
        # and NO body (v0.4.8, pass 3: do_HEAD = do_GET wrote the
        # JSON body onto a HEAD response).
        self._json(405, {"ok": False,
                         "categories": ["method-not-allowed"]},
                   head_only=True)

    def _token_ok(self):
        # Compare as BYTES. BaseHTTPRequestHandler decodes headers
        # as latin-1, so a non-ASCII header byte becomes a non-ASCII
        # str — and hmac.compare_digest on str raises TypeError for
        # non-ASCII, turning a junk header into a handler exception
        # instead of a clean refusal. Encoding back to bytes via
        # latin-1 recovers the exact header bytes; the expected
        # token is compared in its UTF-8 byte form. (The legacy
        # X-Devbox-Token header name is no longer accepted —
        # v0.4.8 shim deletion: devbox is undeployed and will
        # adopt the current name when it is built.)
        if not TOKEN_BYTES:
            return False
        for name in ("X-Repokit-Token",):
            raw = self.headers.get(name)
            if raw is None:
                continue
            try:
                given = raw.encode("latin-1")
            except UnicodeEncodeError:
                continue
            if hmac.compare_digest(given, TOKEN_BYTES):
                return True
        return False

    def do_POST(self):
        if self.path != "/scan":
            self._json(404, {"ok": False, "categories": ["not-found"]})
            return
        if not self._token_ok():
            # A bad-token attempt is audit data too: count it and
            # record its peer through the same path a valid scan's
            # peer takes, so probing WITHOUT the token shows up in
            # the counts instead of leaving no trace. The counts
            # FILE write is throttled (item 7b): this path is
            # unauthenticated, and rewriting the file per request
            # made a LAN prober's cheapest loop into disk churn.
            global _last_forbidden_write
            peer = self.client_address[0] if self.client_address else ""
            with _counts_lock:
                _counts["forbidden"] += 1
                _note_peer_locked(peer)
                now = time.time()
                if now - _last_forbidden_write >= \
                        FORBIDDEN_WRITE_INTERVAL:
                    _last_forbidden_write = now
                    _write_counts_locked()
            self._json(403, {"ok": False, "categories": ["forbidden"]})
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            length = -1
        if length < 0:
            self._json(400, {"ok": False,
                             "categories": ["bad-request"]})
            return
        # Lifetime cap and the oversize allowance are decided in
        # ONE locked section (the counts lock is reentrant because
        # _consume_allowance tallies under it). The cap is checked
        # BEFORE the allowance is consumed: a scan-limit refusal
        # must not burn the owner's one-shot allowance (v0.4.3,
        # reviewer round 3 item 8 — up to v0.4.2 the allowance was
        # spent first, so a 429 destroyed it). The slot is
        # reserved in the same hold, so a body refused in this
        # block burns no scan (round 2 item 7c: check-then-increment
        # let concurrent requests overshoot the cap); a body found
        # truncated AFTER the read releases its reserved slot and
        # tallies as "truncated" instead, so a refused body burns
        # no scan there either (v0.4.8 fix pass, secondary review
        # item 7). The token lives
        # in the checked environment, so any process there can
        # oracle-test the pattern list a few candidates at a time;
        # the cap cannot remove the oracle, it bounds how much one
        # service run can be asked, and the refusal is counted. An
        # allowance admits a bigger body, never extra scans.
        decision = None
        with _counts_lock:
            if _counts["scans"] >= MAX_SCANS:
                _counts["over_limit"] += 1
                _write_counts_locked()
                decision = "limit"
            elif length > MAX_BODY and \
                    not _consume_allowance(length):
                # Tallied (v0.4.8, pass 3: oversize refusals were
                # the one refusal class counted nowhere).
                _counts["too_large"] = \
                    _counts.get("too_large", 0) + 1
                _write_counts_locked()
                decision = "too-large"
            else:
                _counts["scans"] += 1
        if decision == "limit":
            self._json(429, {"ok": False,
                             "categories": ["scan-limit"]})
            return
        if decision == "too-large":
            self._json(413, {"ok": False,
                             "categories": ["too-large"],
                             "size": length})
            return
        raw = self._read_body(length) if length else b""
        if len(raw) != length:
            # A short body is a FAILED scan, never a partial pass
            # (v0.4.8, pass 3 blocker): _read_body stops at the
            # first empty read, and the code below used to scan
            # whatever prefix arrived and could answer ok:true
            # for content the service never saw. The scan slot
            # reserved in the decision block is RELEASED here and
            # the refusal tallied as "truncated" (v0.4.8 fix
            # pass, secondary review item 7): a body refused
            # unscanned is not a scan actually performed.
            with _counts_lock:
                _counts["scans"] -= 1
                _counts["truncated"] = \
                    _counts.get("truncated", 0) + 1
                _write_counts_locked()
            print(f"pattern-service: truncated body: declared "
                  f"{length} bytes, received {len(raw)} — "
                  "refused unscanned", file=sys.stderr)
            self._json(400, {"ok": False,
                             "categories": ["truncated-body"]})
            return
        peer = self.client_address[0] if self.client_address else ""
        if not PATTERNS:
            with _counts_lock:
                _counts["flagged"] += 1
                _counts["categories"]["no-patterns-configured"] = \
                    _counts["categories"].get(
                        "no-patterns-configured", 0) + 1
                _note_peer_locked(peer)
                _write_counts_locked()
            print(f"pattern-service: scan bytes={len(raw)} "
                  "result=no-patterns-configured", file=sys.stderr)
            self._json(200, {"ok": False,
                             "categories": ["no-patterns-configured"]})
            return
        text = raw.decode("utf-8", errors="replace")
        # Per-line matching (round 2 item 7a): the patterns are
        # single-line identifiers; whole-text search let one
        # pathological line — or a catastrophic pattern — stall
        # every handler thread under the GIL. Lines are matched in
        # OVERLAPPING WINDOWS, never truncated (v0.4.3, round 3
        # item 2): up to v0.4.2 only the first MAX_LINE characters
        # of a line were probed, so an identifier past column 32768
        # of a minified or base64 line was never checked.
        found = []
        for line in text.splitlines():
            for pat in PATTERNS:
                if pat["tier"] not in found and \
                        _line_hit(pat["rx"], line):
                    found.append(pat["tier"])
        with _counts_lock:
            if found:
                _counts["flagged"] += 1
                for tier in found:
                    _counts["categories"][tier] = \
                        _counts["categories"].get(tier, 0) + 1
            _note_peer_locked(peer)
            _write_counts_locked()
        print(f"pattern-service: scan bytes={len(raw)} "
              f"tiers_matched={len(found)}", file=sys.stderr)
        if found:
            self._json(200, {"ok": False, "categories": found})
        else:
            self._json(200, {"ok": True})


class ScanServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A client that vanishes mid-exchange (a stalled connection
        # hitting the socket timeout, a peer that closed early) makes
        # the stdlib print a traceback for a connection error that is
        # not a service fault. Keep the log meaningful: swallow the
        # connection-error class, surface everything else.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    if not TOKEN:
        print("pattern-service: REPOKIT_PATTERN_TOKEN is not set — "
              "refusing to run an ungated scan service",
              file=sys.stderr)
        return 2
    if PATTERN_ERRORS:
        # Refuse to start (v0.4.2 item 6): serving with a dead
        # pattern would answer ok:true for exactly the content the
        # pattern existed to catch. Only the location is named —
        # never the pattern text.
        for path, lineno in PATTERN_ERRORS:
            if lineno:
                print(f"pattern-service: {path} line {lineno} does "
                      "not compile — refusing to start with a "
                      "pattern missing. Fix the line (counting "
                      "every line, comments included) and restart.",
                      file=sys.stderr)
            else:
                print(f"pattern-service: {path} cannot be read "
                      "(or is not valid UTF-8) — refusing to "
                      "start; serving without it would leave its "
                      "patterns unchecked.",
                      file=sys.stderr)
        return 2
    host = os.environ.get("REPOKIT_PATTERN_HOST", "127.0.0.1")
    port = int(os.environ.get("REPOKIT_PATTERN_PORT", "18741"))
    # Threading server: scans are served concurrently, so one slow
    # or stalled client can never queue-block pushes behind it
    # (together with the per-connection socket timeout).
    server = ScanServer((host, port), Handler)
    write_counts()  # an all-zero counts file proves the service ran
    tiers = {p["tier"] for p in PATTERNS}
    print(f"pattern-service: listening on {host}:{port}; "
          f"{len(PATTERNS)} pattern(s) in {len(tiers)} tier(s)"
          if PATTERNS else
          f"pattern-service: listening on {host}:{port}; NO patterns "
          "loaded (pattern-less — scans fail closed)",
          file=sys.stderr)
    # Flush the audit counts on ANY exit (v0.4.3, round 3 item 8):
    # the throttled 403 writer only persists every few seconds,
    # so a stop inside the window used to lose the tallies —
    # SIGTERM (the normal supervisor stop) bypassed even the old
    # KeyboardInterrupt path. The signal handler turns SIGTERM
    # into a normal exit so the finally runs.
    def _term(_signo, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        write_counts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
