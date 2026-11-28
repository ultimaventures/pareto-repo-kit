#!/usr/bin/env bash
# RepoKit scenario/regression suite — IN the repo (v0.4.8, secondary
# review pass 1 blocker 2): the rounds before this one ran suites
# from scratch space and lost them; this file is the durable form,
# and CI runs it on the PR checkout (.github/workflows/self-gate.yml,
# "tests" job).
#
# Usage: tests/run_tests.sh [SECTION ...]   (default: all sections)
# Sections: patterns firstpush worktree override agent service
#           packet setup units dashboard merges pins fixpass
#
# Every case builds scratch repos/toolings under a mktemp dir and
# pushes to a local bare remote — no network, no real pattern list.
# gitleaks: if the real binary is absent, a fake exit-0 gitleaks is
# put on PATH for pushes (the cases that test its ABSENCE manage
# their own PATH). shellcheck cases SKIP if shellcheck is absent.
set -u
SRC=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d /tmp/repokit-tests.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0; SKIP=0; FAILED_CASES=""
FAKEBIN="$WORK/fakebin"; mkdir -p "$FAKEBIN"
if ! command -v gitleaks >/dev/null 2>&1; then
  printf '#!/bin/sh\nexit 0\n' > "$FAKEBIN/gitleaks"; chmod +x "$FAKEBIN/gitleaks"
fi
export PATH="$FAKEBIN:$PATH"
HAVE_SHELLCHECK=0; command -v shellcheck >/dev/null 2>&1 && HAVE_SHELLCHECK=1

ok()   { PASS=$((PASS+1)); echo "ok   - $1"; }
bad()  { FAIL=$((FAIL+1)); FAILED_CASES="$FAILED_CASES $1"; echo "FAIL - $1"; }
skip() { SKIP=$((SKIP+1)); echo "skip - $1"; }
# expect_rc <want:0|1> <case> <rc>
expect_rc() { if [ "$1" = "0" ] && [ "$3" -eq 0 ]; then ok "$2"; elif [ "$1" = "1" ] && [ "$3" -ne 0 ]; then ok "$2"; else bad "$2 (rc=$3, wanted $1)"; fi }
# expect_grep <case> <file> <pattern>
expect_grep() { if grep -q -- "$3" "$2" 2>/dev/null; then ok "$1"; else bad "$1 (pattern '$3' not in output)"; fi }
expect_no_grep() { if grep -q -- "$3" "$2" 2>/dev/null; then bad "$1 (pattern '$3' unexpectedly in output)"; else ok "$1"; fi }

# new_tooling DIR — a fixture tooling checkout with test patterns.
new_tooling() {
  mkdir -p "$1"
  cp -r "$SRC/hooks" "$SRC/tools" "$SRC/install" "$SRC/templates" "$1/"
  cp "$SRC/VERSION" "$1/"
  rm -rf "$1/tools/__pycache__"
  printf 'swordfish\n@example\\.test\n' > "$1/patterns-relaxed.txt"
}
# install_hook REPO TOOLING — bake the dispatcher like install_hooks does.
install_hook() {
  sed "s|__TOOLING_HOME__|$2|g" "$2/hooks/pre-push" > "$1/.git/hooks/pre-push"
  chmod +x "$1/.git/hooks/pre-push"
}
# new_repo DIR TOOLING — repo + bare remote + hook; prints nothing.
new_repo() {
  git init -q -b main "$1"; git init -q --bare "$1-remote.git"
  git -C "$1" config user.email t@t; git -C "$1" config user.name t
  git -C "$1" remote add origin "$1-remote.git"
  install_hook "$1" "$2"
}
commit() { # REPO MSG
  git -C "$1" add -A; git -C "$1" commit -qm "$2"
}
# push REPO [REFSPEC] — no controlling terminal; output in $OUT, rc returned.
OUT="$WORK/last-push.out"
push() {
  local repo=$1 ref=${2:-main}
  setsid git -C "$repo" push origin "$ref" >"$OUT" 2>&1
  return $?
}
# push_pty REPO INPUT — push under a pty, feeding INPUT on stdin lines.
push_pty() {
  local repo=$1 input=$2
  printf '%s' "$input" | script -qec "git -C $repo push origin main" /dev/null >"$OUT" 2>&1
  return $?
}

section_patterns() {
  local T="$WORK/t-pat" R="$WORK/r-pat"
  new_tooling "$T"; new_repo "$R" "$T"
  echo clean > "$R/a.txt"; commit "$R" init
  push "$R"; expect_rc 0 "patterns: clean first push passes" $?
  echo "the word is swordfish" > "$R/b.txt"; commit "$R" add-b
  push "$R"; expect_rc 1 "patterns: pattern in content blocks" $?
  expect_grep "patterns: block names the tier" "$OUT" "relaxed"
  git -C "$R" reset -q --hard HEAD~1
  mkdir -p "$R/docs"; echo x > "$R/docs/swordfish-notes.md"; commit "$R" add-path
  push "$R"; expect_rc 1 "patterns: pattern in file path blocks" $?
  git -C "$R" reset -q --hard HEAD~1
  echo x > "$R/José García.md"; commit "$R" add-jose
  push "$R"; expect_rc 0 "patterns: non-ASCII filename, clean content, passes" $?
  echo "contact me@example.test" > "$R/c.txt"; commit "$R" add-mail
  push "$R"; expect_rc 1 "patterns: second pattern blocks" $?
  git -C "$R" reset -q --hard HEAD~1
  # UTF-16LE pattern inside a binary blob blocks (blob extraction).
  python3 - "$R/bin.dat" <<'EOF'
import sys
data = b"\x00\x01\x02" + "swordfish".encode("utf-16-le") + b"\x00" * 64
open(sys.argv[1], "wb").write(data)
EOF
  commit "$R" add-bin
  push "$R"; expect_rc 1 "patterns: UTF-16LE pattern in binary blob blocks" $?
  git -C "$R" reset -q --hard HEAD~1
  # (If a regression let that push through, re-sync the remote so the
  # next case is a fast-forward, not a non-fast-forward rejection.)
  setsid git -C "$R" push --force origin main >/dev/null 2>&1
  # Extraction cap: a binary whose extracted text exceeds the cap.
  # Personal mode, no terminal: the cap is a coverage gap -> fail.
  python3 - "$R/big.dat" <<'EOF'
import sys
buf = bytearray(b"\x00A" * 4)
while len(buf) < 400 * 1024:
    buf += b"\x00" + bytes([65 + (len(buf) % 26)])
open(sys.argv[1], "wb").write(bytes(buf))
EOF
  commit "$R" add-big
  push "$R"; expect_rc 1 "patterns: capped binary, no terminal, fails closed" $?
  expect_grep "patterns: cap failure explains itself" "$OUT" "cap"
  REPOKIT_ALLOW_COVERAGE_GAPS=1 push "$R"
  expect_rc 0 "patterns: capped binary + explicit gap acceptance passes" $?
}

section_firstpush() {
  local T="$WORK/t-fp" R="$WORK/r-fp"
  new_tooling "$T"; new_repo "$R" "$T"
  # Root commit carries a hygiene failure (conflict marker) — the
  # first push of a new repo must run ALL checks on the empty-tree
  # range and catch it (secondary review passes 5/6).
  printf 'a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> side\n' > "$R/conflicted.txt"
  echo hello > "$R/a.txt"; commit "$R" root
  push "$R"; expect_rc 1 "firstpush: conflict marker in root commit blocks" $?
  # Amend the root clean: a marker fixed in a FOLLOW-UP commit still
  # publishes (every commit is public), so the clean case must be a
  # clean history, not a cleaned-up one.
  printf 'a\n' > "$R/conflicted.txt"
  git -C "$R" add -A; git -C "$R" commit -q --amend -m root-clean
  push "$R"; expect_rc 0 "firstpush: clean root commit passes all checks" $?
  if [ "$HAVE_SHELLCHECK" = 1 ]; then
    printf '#!/bin/sh\nfoo( bar\n' > "$R/bad.sh"; commit "$R" bad-shell
    push "$R"; expect_rc 1 "firstpush: shell syntax error in range blocks" $?
    git -C "$R" reset -q --hard HEAD~1
  else
    skip "firstpush: shell syntax (no shellcheck)"
  fi
}

section_worktree() {
  local T="$WORK/t-wt" R="$WORK/r-wt"
  new_tooling "$T"; new_repo "$R" "$T"
  echo clean > "$R/a.txt"; commit "$R" init; push "$R" >/dev/null 2>&1
  if [ "$HAVE_SHELLCHECK" = 1 ]; then
    # Committed BAD, working tree cleaned afterwards: the check must
    # grade the COMMIT, so the push blocks (pass 6 blocker 1).
    printf '#!/bin/sh\nfoo( bar\n' > "$R/s.sh"; commit "$R" bad-shell
    printf '#!/bin/sh\necho clean\n' > "$R/s.sh"   # uncommitted "fix"
    push "$R"; expect_rc 1 "worktree: committed-bad/working-clean still blocks" $?
    git -C "$R" checkout -q -- "$R/s.sh" 2>/dev/null; git -C "$R" reset -q --hard HEAD~1
    # Committed CLEAN, working tree dirtied afterwards: the push must
    # NOT fail for content that is not in it.
    printf '#!/bin/sh\necho fine\n' > "$R/s.sh"; commit "$R" good-shell
    printf '#!/bin/sh\nfoo( bar\n' > "$R/s.sh"       # uncommitted breakage
    push "$R"; expect_rc 0 "worktree: committed-clean/working-dirty passes" $?
    git -C "$R" checkout -q -- "$R/s.sh"
  else
    skip "worktree: shell cases (no shellcheck)"
  fi
  # A private-key-shaped blob committed and REMOVED before the tip:
  # hygiene must still see the intermediate commit's blob.
  printf -- '-----BEGIN %s PRIVATE KEY-----\nfake\n-----END %s PRIVATE KEY-----\n' 'OPENSSH' 'OPENSSH' > "$R/key.pem"
  commit "$R" add-key
  git -C "$R" rm -q "$R/key.pem"; commit "$R" remove-key
  push "$R"; expect_rc 1 "worktree: key added+removed mid-range still blocks" $?
  git -C "$R" reset -q --hard HEAD~2
  # A filename containing a newline must not silently evade checks.
  if [ "$HAVE_SHELLCHECK" = 1 ]; then
    printf '#!/bin/sh\nfoo( bar\n' > "$R/$(printf 'evil\nname.sh')"; commit "$R" newline-name
    push "$R"; expect_rc 1 "worktree: newline-named bad shell script blocks" $?
    git -C "$R" reset -q --hard HEAD~1
  else
    skip "worktree: newline name (no shellcheck)"
  fi
}

RK() { python3 "$SRC/install/repo-kit" "$@"; }

section_override() {
  local T="$WORK/t-ovr" R="$WORK/r-ovr"
  new_tooling "$T"; new_repo "$R" "$T"
  echo clean > "$R/a.txt"; commit "$R" init; push "$R" >/dev/null 2>&1
  local root; root=$(git -C "$R" rev-parse HEAD)
  # One pattern, three matches: grant via pty, push passes, consumed.
  printf 'swordfish one\nswordfish two\nswordfish three\n' > "$R/fp.txt"
  commit "$R" false-positive
  printf 'override\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T" /dev/null >"$OUT" 2>&1
  expect_grep "override: grant lists the pattern" "$OUT" "swordfish"
  push "$R"; expect_rc 0 "override: push passes under the allowance" $?
  # Consumed on use: the allowance file is gone, and a push that
  # RE-SCANS the same commits (a fresh remote that has none of
  # them) blocks — the one-push allowance does not apply twice.
  # (Pushing the same tip to a second ref on the SAME remote is
  # not a second use: those commits are already on that server,
  # so the scan set is empty and there is nothing to apply.)
  if [ -e "$R/.git/repokit-override.json" ]; then bad "override: allowance file consumed by the push"; else ok "override: allowance file consumed by the push"; fi
  git init -q --bare "$R-other.git"; git -C "$R" remote add other "$R-other.git"
  setsid git -C "$R" push other main >"$OUT" 2>&1
  expect_rc 1 "override: allowance is consumed (re-scan on a fresh remote blocks)" $?
  git -C "$R" remote remove other
  git -C "$R" reset -q --hard "$root"
  # Commit-set binding (pass 5 major 2): main gains a swordfish commit
  # S (pushed bypassing the hook — fixture setup). A feature branch on
  # top of S gets an override for its own false positive F (grant
  # range S..F). Pushing F's tip onto a branch based at the ROOT makes
  # the push range {S, F} — S was never approved, so it must block.
  printf 'swordfish on main\n' > "$R/sneak.txt"; commit "$R" main-hit
  # --force: scenario 1's reset rewound local main, so the remote's
  # main (scenario 1's tip) is not an ancestor — without the force
  # this fixture push is rejected non-fast-forward and S never
  # reaches the server, which silently breaks the premise below.
  setsid git -C "$R" push --no-verify --force origin main >/dev/null 2>&1
  git -C "$R" checkout -qb feature
  printf 'swordfish false alarm\n' > "$R/fp2.txt"; commit "$R" feature-fp
  printf 'override\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T" /dev/null >"$OUT" 2>&1
  push "$R" feature; expect_rc 0 "override: approved range pushes" $?
  printf 'override\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T" /dev/null >"$OUT" 2>&1
  git -C "$R-remote.git" update-ref refs/heads/oldbase "$root"
  setsid git -C "$R" push origin feature:refs/heads/oldbase >"$OUT" 2>&1
  expect_rc 1 "override: same tip, larger unseen commit set, blocks" $?
  git -C "$R" push origin :oldbase >/dev/null 2>&1
  git -C "$R" checkout -q main; git -C "$R" reset -q --hard origin/main
  # Display package: >5 matches — capped view, nudge, --all, inline offer.
  python3 -c "
lines = ['hit swordfish number %d' % i for i in range(1, 9)]
open('$R/many.txt', 'w').write('\n'.join(lines) + '\n')"
  commit "$R" many-matches
  printf 'n\noverride\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T" /dev/null >"$OUT" 2>&1
  expect_no_grep "override: capped view hides match 8" "$OUT" "number 8"
  expect_grep "override: nudge names the pattern file" "$OUT" "patterns-relaxed.txt"
  expect_grep "override: capped view points at --all" "$OUT" "override --all"
  printf 'y\noverride\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T" /dev/null >"$OUT" 2>&1
  expect_grep "override: inline offer shows match 8 in place" "$OUT" "number 8"
  git -C "$R" reset -q --hard HEAD~1
  python3 -c "
lines = ['hit swordfish number %d' % i for i in range(1, 9)]
open('$R/many.txt', 'w').write('\n'.join(lines) + '\n')"
  commit "$R" many-matches-2
  printf 'override\n' | script -qec "python3 $SRC/install/repo-kit override $R --tooling $T --all" /dev/null >"$OUT" 2>&1
  expect_grep "override: --all shows match 8 without prompting" "$OUT" "number 8"
}

start_service() { # TOOLING PORT TOKEN ALLOWFILE
  REPOKIT_PATTERN_FILES="$1/patterns-relaxed.txt" \
  REPOKIT_PATTERN_TOKEN="$3" \
  REPOKIT_PATTERN_ALLOWANCE_FILE="$4" \
  REPOKIT_PATTERN_HOST=127.0.0.1 REPOKIT_PATTERN_PORT="$2" \
  python3 "$1/tools/pattern_service.py" \
    >"$WORK/service.log" 2>&1 &
  SVC_PID=$!
  local n=0
  while [ $n -lt 50 ]; do
    curl -s "http://127.0.0.1:$2/" >/dev/null 2>&1 && return 0
    n=$((n+1)); sleep 0.2
  done
  return 1
}
stop_service() { kill "$SVC_PID" 2>/dev/null; wait "$SVC_PID" 2>/dev/null; }

make_agent() { # REPO TOOLING PORT — enforce on against the service
  # (agent mode is a property of the TOOLING checkout, not the repo:
  # enforce takes no repo argument.)
  REPOKIT_ENFORCE_TOKEN=tok123 RK enforce on --tooling "$2" \
    --url "http://127.0.0.1:$3" </dev/null >/dev/null 2>&1
  # As supervisor, widen the dispatcher's pinned PATH to include the
  # suite's fakebin (where the fake gitleaks lives) — the supported
  # mechanism (REPOKIT_TRUSTED_PATH in enforced.env).
  printf 'REPOKIT_TRUSTED_PATH=%s:/usr/local/bin:/usr/bin:/bin\n' "$FAKEBIN" >> "$2/hooks/enforced.env"
}

section_agent() {
  local T="$WORK/t-ag" R="$WORK/r-ag" PORT=18741
  new_tooling "$T"; new_repo "$R" "$T"
  echo clean > "$R/a.txt"; commit "$R" init; push "$R" >/dev/null 2>&1
  start_service "$T" $PORT tok123 "$WORK/allowance.json" || { bad "agent: service starts"; return; }
  make_agent "$R" "$T" $PORT
  echo more > "$R/b.txt"; commit "$R" clean-2
  push "$R"; expect_rc 0 "agent: clean push passes via the service" $?
  echo swordfish > "$R/c.txt"; commit "$R" dirty
  push "$R"; expect_rc 1 "agent: pattern hit blocks via the service" $?
  git -C "$R" reset -q --hard HEAD~1
  # Forged boundary (pass 4 major): a new branch whose tip commit is
  # pre-registered as a local remote-tracking ref. Agent mode must
  # ignore local boundaries and scan the whole history.
  git -C "$R" checkout -qb nb
  echo swordfish > "$R/nb.txt"; commit "$R" nb-bad
  local badsha; badsha=$(git -C "$R" rev-parse HEAD)
  git -C "$R" update-ref refs/remotes/origin/nb "$badsha"
  push "$R" nb; expect_rc 1 "agent: forged remote-tracking boundary does not shrink the scan" $?
  git -C "$R" checkout -q main
  # enforced.env grammar parity: duplicate profile lines, LAST wins in
  # BOTH parsers. security first, full second -> hygiene (30) must run.
  local envf; envf=$(find "$T" "$R" -name "enforced.env" 2>/dev/null | head -1)
  if [ -n "$envf" ]; then
    printf 'REPOKIT_CHECK_PROFILE=full\n' >> "$envf"
    printf 'x\n<<<<<<< HEAD\ny\n' > "$R/conf.txt"; commit "$R" conflict
    push "$R"; expect_rc 1 "agent: duplicate profile lines resolve identically (last wins, full)" $?
    git -C "$R" reset -q --hard HEAD~1
  else
    bad "agent: enforced.env located"
  fi
  stop_service
  # Zero checks run is a failed push in agent mode (pass 4 major).
  local T2="$WORK/t-ag0" R2="$WORK/r-ag0" PORT2=18742
  new_tooling "$T2"; rm -f "$T2"/tools/checks/*.sh; new_repo "$R2" "$T2"
  echo clean > "$R2/a.txt"; commit "$R2" init
  start_service "$T2" $PORT2 tok123 "$WORK/allowance0.json" || { bad "agent0: service starts"; return; }
  make_agent "$R2" "$T2" $PORT2
  push "$R2"; expect_rc 1 "agent: zero checks running fails the push" $?
  stop_service
}

section_service() {
  local T="$WORK/t-svc" PORT=18743
  new_tooling "$T"
  start_service "$T" $PORT tok123 "$WORK/allowance-svc.json" || { bad "service: starts"; return; }
  # Short body: declare more than is sent — must NOT come back ok.
  local shortres
  shortres=$(python3 - "$PORT" <<'PYEOF'
import http.client, json, sys
port = int(sys.argv[1])
body = json.dumps({"chunks": ["clean text"]}).encode()
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
conn.putrequest("POST", "/scan")
conn.putheader("X-Repokit-Token", "tok123")
conn.putheader("Content-Type", "application/json")
conn.putheader("Content-Length", str(len(body) + 500))
conn.endheaders()
conn.send(body)  # then close without sending the rest
try:
    resp = conn.getresponse(); data = resp.read()
    print("SHORT_OK" if (b'"ok": true' in data or b'"ok":true' in data) else "SHORT_REFUSED")
except Exception:
    print("SHORT_REFUSED")
conn.close()
PYEOF
)
  if [ "$shortres" = "SHORT_REFUSED" ]; then ok "service: truncated body is not a pass"; else bad "service: truncated body is not a pass"; fi
  stop_service
}

section_packet() {
  local T="$WORK/t-pk" R="$WORK/r-pk"
  new_tooling "$T"; new_repo "$R" "$T"
  mkdir -p "$R/review/packets"
  echo clean > "$R/a.txt"; commit "$R" init
  local base head
  base=$(git -C "$R" rev-parse HEAD)
  echo more > "$R/b.txt"; commit "$R" second; head=$(git -C "$R" rev-parse HEAD)
  GATE_PATTERNS_FILE="$T/patterns-relaxed.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$base" --no-copy >"$OUT" 2>&1
  expect_rc 0 "packet: clean packet generates" $?
  local state; state=$(find "$R/.git" \( -name "*packet-state*" -o -name "*last-reviewed*" \) -type f 2>/dev/null | head -1)
  local before=""; [ -n "$state" ] && before=$(cat "$state")
  echo "swordfish inside" > "$R/leak.txt"; commit "$R" leak
  GATE_PATTERNS_FILE="$T/patterns-relaxed.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$head" --no-copy >"$OUT" 2>&1
  expect_rc 1 "packet: packet containing a pattern hit is refused" $?
  if [ -n "$state" ]; then
    if [ "$(cat "$state")" = "$before" ]; then ok "packet: refused packet does not advance the reviewed stack"; else bad "packet: refused packet does not advance the reviewed stack"; fi
  else
    bad "packet: state file located"
  fi
  # A file larger than the old 20k per-file cap appears in FULL.
  git -C "$R" reset -q --hard HEAD~1
  python3 -c "
lines = ['line %05d filler' % i for i in range(2000)]
lines.append('TAILMARKER-UNIQUE')
open('$R/big.txt', 'w').write('\n'.join(lines) + '\n')"
  commit "$R" bigfile
  GATE_PATTERNS_FILE="$T/patterns-relaxed.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$head" --no-copy >"$OUT" 2>&1
  # Newest packet in the scratch repo (portably: BSD find has
  # no -printf, and shellcheck rightly distrusts parsing ls).
  local pkt="" f
  for f in "$R"/review/packets/*.md; do
    [ -e "$f" ] || continue
    if [ -z "$pkt" ] || [ "$f" -nt "$pkt" ]; then pkt=$f; fi
  done
  if [ -n "$pkt" ] && grep -q "TAILMARKER-UNIQUE" "$pkt"; then ok "packet: >20k file included in full"; else bad "packet: >20k file included in full"; fi
}

section_setup() {
  local T="$WORK/t-su"
  mkdir -p "$T"; cp -r "$SRC/install" "$SRC/tools" "$SRC/hooks" "$SRC/templates" "$T/"; cp "$SRC/VERSION" "$T/"; rm -rf "$T/tools/__pycache__"
  RK setup --tooling "$T" </dev/null >"$OUT" 2>&1
  expect_rc 1 "setup: closed stdin aborts instead of answering for the user" $?
  if [ -e "$T/patterns-relaxed.txt" ]; then bad "setup: aborted run writes no pattern list"; else ok "setup: aborted run writes no pattern list"; fi
  RK setup --tooling "$T" --no-gmail --email a@b.c --domain example.com --handle bob --name "Bob Smith" >"$OUT" 2>&1
  expect_rc 0 "setup: flags alone complete non-interactively" $?
  if [ -f "$T/patterns-relaxed.txt" ]; then
    expect_no_grep "setup: --no-gmail is honoured" "$T/patterns-relaxed.txt" "@gmail"
    local mode; mode=$(stat -c %a "$T/patterns-relaxed.txt" 2>/dev/null || stat -f %Lp "$T/patterns-relaxed.txt")
    if [ "$mode" = "600" ]; then ok "setup: pattern list written 0600"; else bad "setup: pattern list written 0600 (got $mode)"; fi
  else
    bad "setup: flags-alone run writes the list"
  fi
}

section_units() {
  local res
  res=$(python3 - "$SRC" <<'PYEOF'
import argparse, importlib.machinery, importlib.util, sys
src = sys.argv[1]
loader = importlib.machinery.SourceFileLoader("repokit", src + "/install/repo-kit")
spec = importlib.util.spec_from_loader("repokit", loader)
rk = importlib.util.module_from_spec(spec); loader.exec_module(rk)
ns = argparse.Namespace(variant="A", required_check=None, bypass_user_id=123)
try:
    rs = rk._load_ruleset(src, ns)
    print("RULESET_A_ACCEPTED" if "bypass_actors" in rs else "RULESET_A_REFUSED")
except Exception:
    print("RULESET_A_REFUSED")
PYEOF
)
  if [ "$res" = "RULESET_A_REFUSED" ]; then ok "units: variant A refuses a bypass actor"; else bad "units: variant A refuses a bypass actor"; fi
  setsid python3 "$SRC/tools/coverage_gap.py" "gap summary X" "remedy Y" </dev/null >"$OUT" 2>&1
  expect_rc 1 "units: coverage_gap with no terminal fails closed" $?
  expect_grep "units: coverage_gap failure prints the remedy" "$OUT" "remedy Y"
  # Unresolvable range base fails closed, never silently widened.
  local T="$WORK/t-un" R="$WORK/r-un"
  new_tooling "$T"; new_repo "$R" "$T"
  echo swordfish-old > "$R/old.txt"; commit "$R" old-hit
  echo clean > "$R/new.txt"; commit "$R" clean
  python3 "$T/tools/ci_checks.py" --repo "$R" --range "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef..HEAD" >"$OUT" 2>&1
  expect_rc 1 "units: unresolvable base fails" $?
  expect_grep "units: failure names the range problem" "$OUT" "range"
}

section_dashboard() {
  local R="$WORK/r-dash"
  git init -q -b main "$R"; git -C "$R" config user.email t@t; git -C "$R" config user.name t
  printf -- '- [ ] ns-99 · cited step\n' > "$R/PLAN.md"
  git -C "$R" add -A; git -C "$R" commit -qm "feat: thing (ns-99)"
  # 510 further commits via fast-import — the citation falls outside
  # any 500-commit window (pass 3 major).
  python3 - "$R" <<'PYEOF'
import subprocess, sys
base = subprocess.run(["git", "-C", sys.argv[1], "rev-parse", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
stream = []
for i in range(1, 512):
    # fast-import grammar: 'from' comes AFTER the message data,
    # not before author/committer (a misplaced 'from' aborts the
    # whole import — and an earlier draft of this fixture did
    # exactly that, leaving a 1-commit repo in which the citation
    # was trivially inside any window: the case passed vacuously).
    from_line = ("from %s\n" % base) if i == 1 else ""
    stream.append(
        "commit refs/heads/main\n"
        "author T <t@t> 1700000000 +0000\n"
        "committer T <t@t> 1700000000 +0000\n"
        "data 5\nbulk\n" + from_line)
p = subprocess.run(["git", "-C", sys.argv[1], "fast-import", "--quiet"],
                   input="".join(stream).encode())
sys.exit(p.returncode)
PYEOF
  # Guard the guard: the case only discriminates if the history
  # really is deeper than the old 500-commit window.
  local ncommits; ncommits=$(git -C "$R" rev-list --count HEAD)
  if [ "$ncommits" -gt 501 ]; then ok "dashboard: fixture history is deeper than 500 commits ($ncommits)"; else bad "dashboard: fixture history is deeper than 500 commits (got $ncommits)"; fi
  local res
  res=$(python3 - "$SRC" "$R" <<'PYEOF'
import importlib.util, sys
src, repo = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("rd", src + "/tools/render_dashboard.py")
rd = importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)
cited = rd.cited_steps(repo) if hasattr(rd, "cited_steps") else set()
print("CITED" if "ns-99" in cited else "NOT_CITED")
PYEOF
)
  if [ "$res" = "CITED" ]; then ok "dashboard: citation survives past 500 commits"; else bad "dashboard: citation survives past 500 commits"; fi
}

section_merges() {
  local T="$WORK/t-mg" R="$WORK/r-mg"
  new_tooling "$T"; new_repo "$R" "$T"
  echo base > "$R/a.txt"; commit "$R" init; push "$R" >/dev/null 2>&1
  git -C "$R" checkout -qb side; echo side > "$R/s.txt"; commit "$R" side
  git -C "$R" checkout -q main; echo main > "$R/m.txt"; commit "$R" mainwork
  git -C "$R" merge -q --no-edit side >/dev/null 2>&1
  push "$R"; expect_rc 0 "merges: clean merge passes" $?
  # Evil merge: the pattern exists in NEITHER parent — it is created
  # by the conflict resolution, so only the merge's combined diff
  # can catch it.
  git -C "$R" checkout -qb topic; echo third > "$R/t.txt"; commit "$R" topic
  git -C "$R" checkout -q main; echo other > "$R/t.txt"; commit "$R" other
  git -C "$R" merge topic >/dev/null 2>&1 || true
  echo swordfish > "$R/t.txt"; git -C "$R" add "$R/t.txt"; git -C "$R" commit -qm "merge topic (evil)"
  push "$R"; expect_rc 1 "merges: evil merge (pattern only in combined diff) blocks" $?
}

section_pins() {
  python3 "$SRC/tools/check_pins.py" >"$OUT" 2>&1
  expect_rc 0 "pins: the tree's pins agree" $?
  # Copy WITH dotfiles (v0.4.8 fix pass, item 4): the old case's
  # `cp -r "$SRC"/*` never copied .github/, so the copied
  # checker exited 1 on missing files regardless of the VERSION
  # change — the case passed even with the VERSION comparison
  # deleted. Now the failure must NAME the VERSION disagreement.
  local C="$WORK/pincopy"; mkdir -p "$C"
  cp -r "$SRC"/. "$C"/ 2>/dev/null; rm -rf "$C/.git" "$C/review" "$C/tools/__pycache__"
  echo "9.9.9" > "$C/VERSION"
  python3 "$C/tools/check_pins.py" >"$OUT" 2>&1
  expect_rc 1 "pins: a drifted VERSION is caught" $?
  expect_grep "pins: the drift failure names the VERSION disagreement" "$OUT" "gate reusable default"
  # self-gate.yml scope (item 4): perturbing one of its inline
  # hashes — verified nowhere before the fix pass — must fail
  # and name self-gate.
  local C2="$WORK/pincopy2"; mkdir -p "$C2"
  cp -r "$SRC"/. "$C2"/ 2>/dev/null; rm -rf "$C2/.git" "$C2/review" "$C2/tools/__pycache__"
  sed -i 's/8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198/0000000000000000000000000000000000000000000000000000000000000000/' "$C2/.github/workflows/self-gate.yml"
  python3 "$C2/tools/check_pins.py" >"$OUT" 2>&1
  expect_rc 1 "pins: a drifted self-gate hash is caught" $?
  expect_grep "pins: the self-gate failure names self-gate" "$OUT" "self-gate"
  # Arch rule (v0.4.8 correction pass, confirmation review):
  # any manifest hash is a legitimate pin per download line;
  # the amd64 hash must be among them. Replace ONE of
  # self-gate's two shellcheck amd64 hashes with the manifest's
  # arm64 hash: the unified checker accepts it. (On beb437f
  # this false-failed — the self-gate check there demanded
  # EVERY hash equal amd64, while gate.yml was checked by
  # first match only; the two files now share one rule.)
  local C3="$WORK/pincopy3"; mkdir -p "$C3"
  cp -r "$SRC"/. "$C3"/ 2>/dev/null; rm -rf "$C3/.git" "$C3/review" "$C3/tools/__pycache__"
  python3 - "$C3/.github/workflows/self-gate.yml" <<'PYEOF'
import sys
p = sys.argv[1]
text = open(p).read()
amd = "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"
arm = "12b331c1d2db6b9eb13cfca64306b1b157a86eb69db83023e261eaa7e7c14588"
assert text.count(amd) == 2, text.count(amd)
open(p, "w").write(text.replace(amd, arm, 1))
PYEOF
  python3 "$C3/tools/check_pins.py" >"$OUT" 2>&1
  expect_rc 0 "pins: an arm64 hash beside the amd64 one is accepted (arch rule)" $?
}

section_fixpass() {
  # Regression cases for the v0.4.8 fix pass (secondary review
  # items 1-19 + nits, disposition 2026-11-19). Each case is built
  # to FAIL on the pre-fix code: the review named the old
  # section_pins drift case — which passed vacuously — as the
  # counterexample of the opposite.
  local T="$WORK/t-fx" R="$WORK/r-fx"
  new_tooling "$T"; new_repo "$R" "$T"
  echo clean > "$R/a.txt"; commit "$R" init
  local base head; base=$(git -C "$R" rev-parse HEAD)
  echo more > "$R/b.txt"; commit "$R" second; head=$(git -C "$R" rev-parse HEAD)

  # Item 1 + nit B: the packet scan refuses on loader errors and
  # on zero patterns from a discovered list; the loaded count
  # excludes compile errors.
  GATE_PATTERNS_FILE="/nonexistent-xyz.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$base" --no-copy >"$OUT" 2>&1
  expect_rc 1 "fixpass: packet with a nonexistent pattern list is refused" $?
  printf '# only a comment\n' > "$WORK/comments.txt"
  GATE_PATTERNS_FILE="$WORK/comments.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$base" --no-copy >"$OUT" 2>&1
  expect_rc 1 "fixpass: packet with a comments-only pattern list is refused" $?
  printf '[unclosed\nswordfish\n' > "$WORK/badpat.txt"
  GATE_PATTERNS_FILE="$WORK/badpat.txt" \
    python3 "$T/tools/review_packet.py" --repo "$R" --base "$base" --no-copy >"$OUT" 2>&1
  expect_rc 1 "fixpass: packet with an uncompilable pattern is refused" $?
  expect_grep "fixpass: loaded count excludes the broken pattern" "$OUT" "1 pattern(s) loaded"

  # Items 18 + 11 + nit A + 8: python-level units in one harness.
  python3 - "$T" "$R" "$WORK" <<'PYEOF' >"$OUT" 2>&1
import importlib.util, json, os, subprocess, sys
T, R, WORK = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(T, "tools"))
import gate_common

def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(T, "tools", name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m

fails = []
def check(name, cond):
    print(("ok   - " if cond else "FAIL - ") + name)
    if not cond: fails.append(name)

# Item 18: build_body charges its dropped-files note against
# the budget. The charge is observable ONLY through a surface
# that consumes budget after the note inside build_body, so
# the construction is: two diff blocks, in assemble's
# full-texts-first branch (the summed whole blocks exceed the
# budget, so build_full runs first; the full list here is
# empty and claims nothing). Block 1 ("Diff", 3 sections of
# 200) truncates: two sections kept, c.txt dropped, the
# dropped-files note printed. Block 2 ("Uncommitted changes",
# one 120-char section carrying UMARKER) is then fitted
# against what remains. The budget leaves exactly 190 chars
# after block 1's kept sections: the uncommitted block (177
# whole) fits in 190, so pre-fix -- where the note rode free
# -- UMARKER is in the packet; post-fix the note (127) is
# deducted first, leaving 63, the block no longer fits and
# its only section is dropped, so UMARKER must be absent.
# (The first version of this case aimed the note at a full
# text in the diff-first branch, where build_body can never
# truncate and no note exists; it passed on both trees.)
rp = load("review_packet")
entries18 = [("M", "a.txt", "a.txt")]
hdr_pkt, _ = rp.assemble(R, "r", "main", "x", "y", "", [],
                         entries18, "", [], "", 10**9)
header_len = len(hdr_pkt) - len("\n## Full file contents\n")
def section(path, size, marker=None):
    line = "diff --git a/%s b/%s\n" % (path, path)
    fill = size - len(line) - 2
    body = "+" + "x" * fill + "\n"
    if marker:
        body = "+" + marker + "x" * (fill - len(marker)) + "\n"
    return line + body
diff = "".join(section(p, 200) for p in ("a.txt", "b.txt", "c.txt"))
unc = section("u.txt", 120, marker="UMARKER")
fence = len("\n## Diff\n\n```diff\n\n```\n")
budget = header_len + fence + 400 + 190
pkt, _ = rp.assemble(R, "r", "main", "x", "y", "", [],
                     entries18, diff, [], unc, budget)
check("fixpass: budget charges the dropped-files note (item 18)",
      "UMARKER" not in pkt)

# Item 11: a SHA-256 repo's empty-tree range is a sentinel, not
# an unresolvable base.
sha_repo = os.path.join(WORK, "sha256-repo")
subprocess.run(["git", "init", "-q", "--object-format=sha256", sha_repo], check=True)
subprocess.run(["git", "-C", sha_repo, "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", sha_repo, "config", "user.name", "t"], check=True)
open(os.path.join(sha_repo, "f.txt"), "w").write("x\n")
subprocess.run(["git", "-C", sha_repo, "add", "-A"], check=True)
subprocess.run(["git", "-C", sha_repo, "commit", "-qm", "root"], check=True)
# A SECOND commit (v0.4.8 correction pass, confirmation review):
# with a single root commit the case passed VACUOUSLY on the
# pre-fix code — a root-commit head takes the legitimate
# unresolvable-base path, which also returns the empty-tree
# form. The production failure needs a NON-root head, where
# the pre-fix sentinel test misses the SHA-256 forms and the
# range fails closed instead.
open(os.path.join(sha_repo, "g.txt"), "w").write("y\n")
subprocess.run(["git", "-C", sha_repo, "add", "-A"], check=True)
subprocess.run(["git", "-C", sha_repo, "commit", "-qm", "second"], check=True)
try:
    out = gate_common.effective_range(
        sha_repo, gate_common.EMPTY_TREE_SHA256 + "..HEAD")
    ok11 = out.startswith(gate_common.EMPTY_TREE + "..")
except Exception:
    ok11 = False
try:
    out0 = gate_common.effective_range(sha_repo, "0" * 64 + "..HEAD")
    ok11 = ok11 and out0.startswith(gate_common.EMPTY_TREE + "..")
except Exception:
    ok11 = False
check("fixpass: sha256 empty-tree + 64-zero ranges are sentinels (item 11)", ok11)

# Nit A: an override whose commits entry is present but not a
# list reads as stale, never as valid tip-only.
cc = load("ci_checks")
gitdir = subprocess.run(["git", "-C", R, "rev-parse", "--absolute-git-dir"],
                        capture_output=True, text=True).stdout.strip()
# The head must be the range's REAL tip (v0.4.8 correction
# pass, confirmation review): the original fixture wrote
# "head": "x", so _override_status exited via the head-mismatch
# branch on BOTH trees and never evaluated the malformed
# commits entry — the case was vacuous. With the real tip,
# pre-fix reads "valid" (silent tip-only downgrade) and the
# fixed code reads "stale".
real_head = subprocess.run(["git", "-C", R, "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
data = {"head": real_head, "patterns": ["swordfish"],
        "commits": "notalist", "expires": 9999999999}
with open(os.path.join(gitdir, "repokit-override.json"), "w") as fh:
    json.dump(data, fh)
state, _why = cc._override_status(R, "HEAD~1..HEAD")
check("fixpass: malformed commits entry reads as stale (nit A)", state == "stale")
os.remove(os.path.join(gitdir, "repokit-override.json"))

# Item 8: identical bytes at a .txt and a .json path produce
# BOTH manifest entries under (blob, extension) dedupe.
cb = load("check_blobs")
repo8 = os.path.join(WORK, "dedupe-repo")
subprocess.run(["git", "init", "-q", "-b", "main", repo8], check=True)
subprocess.run(["git", "-C", repo8, "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", repo8, "config", "user.name", "t"], check=True)
open(os.path.join(repo8, "same.txt"), "w").write('{"k": 1}\n')
open(os.path.join(repo8, "same.json"), "w").write('{"k": 1}\n')
subprocess.run(["git", "-C", repo8, "add", "-A"], check=True)
subprocess.run(["git", "-C", repo8, "commit", "-qm", "root"], check=True)
head8 = subprocess.run(["git", "-C", repo8, "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
paths = sorted(e["path"] for e in cb.range_blobs(repo8, head8))
check("fixpass: (blob, extension) dedupe keeps .txt and .json (item 8)",
      paths == ["same.json", "same.txt"])

# Q2 (correction pass, confirmation review): the repo-kit
# module docstring must document EVERY subcommand main()
# registers. On beb437f it said "nine jobs" and had no entry
# lines for uninstall, checks or env, though all three are
# registered — item 12 recurring in miniature, invisible to
# the item-12 grep (which only greps the OLD wrong phrases).
# Names are extracted from the parser construction itself,
# and each must appear as an entry line ("    repo-kit <name>"
# at line start — prose mentions do not count).
import ast, re as _re
rk_src = open(os.path.join(T, "install", "repo-kit"),
              encoding="utf-8").read()
rk_doc = ast.get_docstring(ast.parse(rk_src)) or ""
names = set(_re.findall(r'add_parser\("([a-z][a-z-]*)"\)', rk_src))
for _m in _re.finditer(r"for name in \(([^)]*)\)", rk_src):
    names |= set(_re.findall(r'"([a-z][a-z-]*)"', _m.group(1)))
if 'argv[0] == "packet"' in rk_src:
    names.add("packet")
missing = [n for n in sorted(names)
           if not _re.search(r"^    repo-kit %s( |$)"
                             % _re.escape(n), rk_doc, _re.M)]
check("fixpass: docstring documents every subcommand (Q2; "
      "registered: %s)" % ",".join(sorted(names)),
      not missing and "twelve jobs" in rk_doc)
sys.exit(1 if fails else 0)
PYEOF
  expect_rc 0 "fixpass: python units (items 18, 11, 8, nit A, Q2)" $?
  grep -E "^(ok|FAIL)" "$OUT" | while IFS= read -r line; do echo "  $line"; done
  if grep -q "^FAIL" "$OUT"; then bad "fixpass: python units detail (see above)"; fi

  # Item 10: enforced.env grammar parity — the SHELL parser now
  # accepts whitespace around = like the Python one. Extract
  # _env_value from the dispatcher and drive it directly.
  local FAKET="$WORK/faketooling"; mkdir -p "$FAKET/hooks"
  printf 'REPOKIT_CHECK_PROFILE= full\nREPOKIT_FOO =bar\n' > "$FAKET/hooks/enforced.env"
  sed -n '/^_env_value()/,/^}/p' "$T/hooks/pre-push" > "$WORK/env_value.sh"
  local shval pyval
  shval=$(TOOLING_HOME="$FAKET" sh -c '. "$1"; _env_value REPOKIT_CHECK_PROFILE' _ "$WORK/env_value.sh")
  if [ "$shval" = "full" ]; then ok "fixpass: shell _env_value strips leading value space (item 10)"; else bad "fixpass: shell _env_value strips leading value space (item 10) (got '$shval')"; fi
  shval=$(TOOLING_HOME="$FAKET" sh -c '. "$1"; _env_value REPOKIT_FOO' _ "$WORK/env_value.sh")
  if [ "$shval" = "bar" ]; then ok "fixpass: shell _env_value accepts KEY =value (item 10)"; else bad "fixpass: shell _env_value accepts KEY =value (item 10) (got '$shval')"; fi
  pyval=$(python3 -c "
import sys; sys.path.insert(0, '$T/tools')
import gate_common
env = gate_common.parse_enforced_env(open('$FAKET/hooks/enforced.env').read())
print(env.get('REPOKIT_CHECK_PROFILE', '') + '/' + env.get('REPOKIT_FOO', ''))")
  if [ "$pyval" = "full/bar" ]; then ok "fixpass: python parser reads the same values (item 10)"; else bad "fixpass: python parser reads the same values (item 10) (got '$pyval')"; fi

  # Q1 (correction pass, confirmation review): quote parity.
  # The reviewer could not confirm from the packet whether the
  # shell _env_value strips one pair of matching surrounding
  # quotes as parse_enforced_env does. It does (the case
  # statement in _env_value) — this case PINS that behaviour
  # against both parsers; it passes on beb437f too, by design:
  # it exists so the shared-grammar claim is tested, not just
  # asserted.
  printf 'REPOKIT_CHECK_PROFILE="security"\n' > "$FAKET/hooks/enforced.env"
  shval=$(TOOLING_HOME="$FAKET" sh -c '. "$1"; _env_value REPOKIT_CHECK_PROFILE' _ "$WORK/env_value.sh")
  if [ "$shval" = "security" ]; then ok "fixpass: shell _env_value strips matching quotes (Q1)"; else bad "fixpass: shell _env_value strips matching quotes (Q1) (got '$shval')"; fi
  pyval=$(python3 -c "
import sys; sys.path.insert(0, '$T/tools')
import gate_common
env = gate_common.parse_enforced_env(open('$FAKET/hooks/enforced.env').read())
print(env.get('REPOKIT_CHECK_PROFILE', ''))")
  if [ "$pyval" = "security" ]; then ok "fixpass: python parser strips matching quotes (Q1)"; else bad "fixpass: python parser strips matching quotes (Q1) (got '$pyval')"; fi

  # Item 6: a partial line at the coverage-gap deadline is NOT
  # an answer. Pty harness: feed 'y' with no newline, deadline 2s.
  python3 - "$T" <<'PYEOF' >"$OUT" 2>&1
import os, pty, subprocess, sys, termios, time
child = ("import sys; sys.path.insert(0, %r)\n"
         "import coverage_gap\n"
         "coverage_gap.TIMEOUT_SECONDS = 2\n"
         "print('RESULT', coverage_gap.coverage_gap_ok('s', 'r'), flush=True)\n"
         % (os.path.join(sys.argv[1], "tools"),))
master, slave = pty.openpty()
# RAW mode on the slave: in canonical mode the line discipline
# never delivers a newline-less 'y' at all, so both the broken
# and fixed code answer no and the case cannot discriminate —
# the exposure needs a non-canonical terminal (the review's own
# recalibration of item 6).
_attrs = termios.tcgetattr(slave)
_attrs[3] = _attrs[3] & ~termios.ICANON & ~termios.ECHO
termios.tcsetattr(slave, termios.TCSANOW, _attrs)
def preexec():
    os.setsid()
    import fcntl
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
p = subprocess.Popen([sys.executable, "-c", child], stdin=slave,
                     stdout=slave, stderr=slave, preexec_fn=preexec)
os.close(slave)
os.write(master, b"y")
out = b""; deadline = time.time() + 15
while time.time() < deadline:
    try:
        chunk = os.read(master, 4096)
    except OSError:
        break
    if not chunk: break
    out += chunk
    if b"RESULT" in out: break
p.kill()
print("PARTIAL_NO" if b"RESULT False" in out else "PARTIAL_YES")
PYEOF
  expect_grep "fixpass: partial line at the deadline counts as no (item 6)" "$OUT" "PARTIAL_NO"

  # Item 7: truncated-body accounting — the refusal tallies as
  # truncated and does NOT burn a scan slot.
  local T7="$WORK/t-svc7" PORT7=18744
  new_tooling "$T7"
  REPOKIT_PATTERN_FILES="$T7/patterns-relaxed.txt" \
  REPOKIT_PATTERN_TOKEN=tok123 \
  REPOKIT_PATTERN_ALLOWANCE_FILE="$WORK/allow7.json" \
  REPOKIT_PATTERN_COUNTS_FILE="$WORK/counts7.json" \
  REPOKIT_PATTERN_HOST=127.0.0.1 REPOKIT_PATTERN_PORT=$PORT7 \
  python3 "$T7/tools/pattern_service.py" >"$WORK/service7.log" 2>&1 &
  SVC_PID=$!
  local n7=0; while [ $n7 -lt 50 ]; do curl -s "http://127.0.0.1:$PORT7/" >/dev/null 2>&1 && break; n7=$((n7+1)); sleep 0.2; done
  python3 - "$PORT7" <<'PYEOF'
import http.client, json, sys
port = int(sys.argv[1])
body = json.dumps({"chunks": ["clean text"]}).encode()
conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
conn.putrequest("POST", "/scan")
conn.putheader("X-Repokit-Token", "tok123")
conn.putheader("Content-Type", "application/json")
conn.putheader("Content-Length", str(len(body) + 500))
conn.endheaders(); conn.send(body)
conn.close()  # close at once: the server sees EOF mid-body —
              # that is what makes the body truncated (holding
              # the socket open would leave it waiting to read)
PYEOF
  sleep 0.5; stop_service
  python3 - "$WORK/counts7.json" <<'PYEOF' >"$OUT" 2>&1
import json, sys
try:
    c = json.load(open(sys.argv[1]))
    print("TRUNC_OK" if c.get("truncated", 0) >= 1 and c.get("scans", 0) == 0 else "TRUNC_BAD %s" % c)
except Exception as e:
    print("TRUNC_BAD %s" % e)
PYEOF
  expect_grep "fixpass: truncated body tallies and burns no scan (item 7)" "$OUT" "TRUNC_OK"

  # Items 2 + 9 + 19: shell-tier behaviour via pushes.
  local T2="$WORK/t-fx2" R2="$WORK/r-fx2"
  new_tooling "$T2"; new_repo "$R2" "$T2"
  echo clean > "$R2/a.txt"; commit "$R2" init; push "$R2" >/dev/null 2>&1
  if [ "$HAVE_SHELLCHECK" = 1 ]; then
    printf '#!/bin/zsh\nfoo( bar\n' > "$R2/bad.zsh"; commit "$R2" bad-zsh
    push "$R2"; expect_rc 0 "fixpass: a .zsh file is not collected for shellcheck (item 2)" $?
    git -C "$R2" reset -q --hard HEAD~1
    # The case above PUSHED, so rewind the remote too — a local
    # reset alone makes the next push non-fast-forward (the
    # section_patterns re-sync pattern).
    setsid git -C "$R2" push --force origin main >/dev/null 2>&1
    printf '#!/bin/zsh\nfoo( bar\n' > "$R2/zshtool"; commit "$R2" bad-zsh-shebang
    push "$R2"; expect_rc 0 "fixpass: a zsh-shebang file is not collected (item 2)" $?
    git -C "$R2" reset -q --hard HEAD~1
    setsid git -C "$R2" push --force origin main >/dev/null 2>&1
    # Item 19: a .sh blob over the 32 MiB extraction limit fails
    # the shellcheck tier with a reason, never a silent skip.
    python3 -c "
open('$R2/big.sh', 'wb').write(b'#!/bin/sh\necho x\n' + b'# pad\n' * (6 * 1024 * 1024))"
    commit "$R2" big-shell
    push "$R2"; expect_rc 1 "fixpass: over-limit .sh blob fails the tier (item 19)" $?
    expect_grep "fixpass: over-limit failure names the reason" "$OUT" "shellcheck"
    # The push case alone cannot discriminate item 19 (hygiene
    # fails the same blob on size grounds either way) — drive
    # the shellcheck driver directly: pre-fix it silently
    # counts zero shell files and exits 0.
    python3 "$SRC/tools/shellcheck_check.py" --repo "$R2" --range "HEAD~1..HEAD" >"$OUT" 2>&1
    if [ $? -eq 2 ]; then ok "fixpass: shellcheck driver exit is 2 with the reason (item 19)"; else bad "fixpass: shellcheck driver exit is 2 with the reason (item 19)"; fi
    expect_grep "fixpass: driver failure names the size limit (item 19)" "$OUT" "MiB"
    git -C "$R2" reset -q --hard HEAD~1
  else
    skip "fixpass: shell-tier cases (no shellcheck)"
  fi
  if command -v end-of-file-fixer >/dev/null 2>&1; then
    # Item 9: a binary blob whose last byte is not a newline
    # earns NO missing-final-newline warning in primary mode.
    python3 -c "
open('$R2/img.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 4 + b'\x00\x01')"
    commit "$R2" add-png
    push "$R2"; expect_rc 0 "fixpass: binary blob pushes clean (item 9)" $?
    expect_no_grep "fixpass: no final-newline warning for a binary blob (item 9)" "$OUT" "missing final newline"
    git -C "$R2" reset -q --hard HEAD~1
    # Item 9 at DRIVER level (correction pass, confirmation
    # review): the push case above is confounded on the
    # pre-fix tree — on 6cc1ef3 its failure mixes the
    # 543b8fc check-added-large-files crash with the warning.
    # This case drives file_hygiene.py directly over the
    # binary's range. Measured on 6cc1ef3 (correction-pass
    # fail-before run): the case fails on BOTH assertions —
    # the rc assertion via the crash's hard failure, and the
    # no-warning assertion via the warning itself (the fixer
    # runs and warns, the guard being absent) — so the
    # warning assertion isolates item 9 even there, and on
    # any tree without the crash both assertions isolate it.
    local R9="$WORK/r-fx9"
    new_repo "$R9" "$T2"
    echo clean > "$R9/a.txt"; commit "$R9" init
    python3 -c "
open('$R9/img.png', 'wb').write(b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 4 + b'\x00\x01')"
    commit "$R9" add-png
    python3 "$SRC/tools/file_hygiene.py" --repo "$R9" --range "HEAD~1..HEAD" --mode primary >"$OUT" 2>&1
    expect_rc 0 "fixpass: hygiene driver is clean on a binary blob (item 9)" $?
    expect_no_grep "fixpass: hygiene driver gives no final-newline warning for a binary (item 9)" "$OUT" "missing final newline"
  else
    skip "fixpass: binary fixer case (no pre-commit-hooks on PATH)"
  fi

  # Item 7 wording (correction pass, the 543b8fc edge): a blob
  # over the 32 MiB extraction limit, with a configured limit
  # ABOVE it, must name the extraction limit as the binding
  # constraint — not the "large files are forever" wording,
  # which describes a blob over the CONFIGURED limit. (No
  # pre-commit-hooks needed: the over-limit entry is never
  # extracted, so no tool runs.)
  local R7="$WORK/r-fx7"
  new_repo "$R7" "$T2"
  echo clean > "$R7/a.txt"; commit "$R7" init
  python3 -c "
open('$R7/big.bin', 'wb').write(b'x' * (33 * 1024 * 1024))"
  commit "$R7" add-big
  python3 "$SRC/tools/file_hygiene.py" --repo "$R7" --range "HEAD~1..HEAD" --mode primary --limit-kib 40000 >"$OUT" 2>&1
  expect_rc 1 "fixpass: over-extraction-limit blob still fails (item 7 wording)" $?
  expect_grep "fixpass: the failure names the extraction limit (item 7 wording)" "$OUT" "extraction limit"
  expect_no_grep "fixpass: the failure does not use the forever wording (item 7 wording)" "$OUT" "large files in public history are forever"
  # Same message, blob over BOTH limits (50 MiB, configured
  # 40000 KiB): the parenthetical must not call the extraction
  # limit "the binding constraint" — both bind; extraction is
  # why the content could not be scanned (confirmation-review
  # nit on the correction pass, explicitly non-blocking).
  python3 -c "
open('$R7/big2.bin', 'wb').write(b'x' * (50 * 1024 * 1024))"
  commit "$R7" add-bigger
  python3 "$SRC/tools/file_hygiene.py" --repo "$R7" --range "HEAD~1..HEAD" --mode primary --limit-kib 40000 >"$OUT" 2>&1
  expect_rc 1 "fixpass: over-both-limits blob still fails (item 7 wording)" $?
  expect_grep "fixpass: over-both message names why it could not be scanned" "$OUT" "extraction limit is why its content"
  expect_no_grep "fixpass: over-both message does not claim a single binding constraint" "$OUT" "the binding"

  # Item 15: a FAILED boundary rev-list falls back to a
  # whole-history scan with a notice — drive the installed hook
  # directly with a bogus tip sha (rev-list cannot succeed).
  local bogus=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef zeros=0000000000000000000000000000000000000000
  ( cd "$R" && printf 'refs/heads/x %s refs/heads/y %s\n' "$bogus" "$zeros" | ./.git/hooks/pre-push origin "$R-remote.git" ) >"$OUT" 2>&1
  expect_rc 1 "fixpass: failed boundary computation does not pass silently (item 15)" $?
  expect_grep "fixpass: boundary failure prints its notice (item 15)" "$OUT" "boundary computation"

  # Item 5 / choice A: the shared CI pattern-setup helper's
  # three branches, at shell level.
  local H="$SRC/tools/ci_pattern_setup.sh"
  ( GATE_PATTERNS="swordfish" EVENT_NAME=pull_request IS_FORK=false IS_DEPENDABOT=false sh -c '. "$1"; echo "FILE=$GATE_PATTERNS_FILE"' _ "$H" ) >"$OUT" 2>&1
  expect_grep "fixpass: helper writes the list when the secret is present (item 5)" "$OUT" "FILE=/tmp/patterns-relaxed.txt"
  ( GATE_PATTERNS="" EVENT_NAME=pull_request IS_FORK=true IS_DEPENDABOT=false sh -c '. "$1"; echo "GAP=$REPOKIT_PATTERN_GAP_OK"' _ "$H" ) >"$OUT" 2>&1
  expect_grep "fixpass: helper opts out for a fork PR (item 5)" "$OUT" "GAP=1"
  expect_grep "fixpass: helper warning is the ruled wording (item 5)" "$OUT" "did NOT run on this pull request"
  ( GATE_PATTERNS="" EVENT_NAME=pull_request IS_FORK=false IS_DEPENDABOT=true sh -c '. "$1"; echo "GAP=$REPOKIT_PATTERN_GAP_OK"' _ "$H" ) >"$OUT" 2>&1
  expect_grep "fixpass: helper opts out for a Dependabot PR (item 5)" "$OUT" "GAP=1"
  ( GATE_PATTERNS="" EVENT_NAME=pull_request IS_FORK=false IS_DEPENDABOT=false sh -c '. "$1"' _ "$H" ) >"$OUT" 2>&1
  expect_rc 1 "fixpass: helper fails a same-repo PR with no secret (item 5)" $?
  expect_grep "fixpass: helper error names the empty secret (item 5)" "$OUT" "::error::"

  # Items 3 + 16: init surface — CODEOWNERS locations, fresh
  # init, linked worktree hooks, dangling-marker .gitignore.
  local T3="$WORK/t-fx3" RI="$WORK/r-init"
  new_tooling "$T3"
  git init -q -b main "$RI"; git -C "$RI" config user.email t@t; git -C "$RI" config user.name t
  mkdir -p "$RI/.github"; printf '* @someone\n' > "$RI/.github/CODEOWNERS"
  RK init "$RI" --tooling "$T3" >"$OUT" 2>&1
  if [ -e "$RI/CODEOWNERS" ]; then bad "fixpass: init does not seed over .github/CODEOWNERS (item 3)"; else ok "fixpass: init does not seed over .github/CODEOWNERS (item 3)"; fi
  local RI2="$WORK/r-init2"
  git init -q -b main "$RI2"; git -C "$RI2" config user.email t@t; git -C "$RI2" config user.name t
  RK init "$RI2" --tooling "$T3" >"$OUT" 2>&1
  if [ -f "$RI2/CODEOWNERS" ]; then ok "fixpass: fresh init seeds CODEOWNERS (item 16)"; else bad "fixpass: fresh init seeds CODEOWNERS (item 16)"; fi
  if [ -f "$RI2/PLAN.md" ] && [ -x "$RI2/.git/hooks/pre-push" ]; then ok "fixpass: fresh init installs content + hook (item 16)"; else bad "fixpass: fresh init installs content + hook (item 16)"; fi
  # Dangling begin marker: the user's own lines survive init.
  local RI3="$WORK/r-init3"
  git init -q -b main "$RI3"; git -C "$RI3" config user.email t@t; git -C "$RI3" config user.name t
  printf '# >>> repo-tooling\nmy-own-line.txt\n' > "$RI3/.gitignore"
  RK init "$RI3" --tooling "$T3" >"$OUT" 2>&1
  expect_grep "fixpass: dangling-marker init keeps the user's lines (item 16)" "$RI3/.gitignore" "my-own-line.txt"
  expect_grep "fixpass: dangling-marker init completes the block (item 16)" "$RI3/.gitignore" "# <<< repo-tooling"
  # Hooks in a LINKED worktree land in the common dir.
  local RI4="$WORK/r-init4" WT="$WORK/wt-init4"
  git init -q -b main "$RI4"; git -C "$RI4" config user.email t@t; git -C "$RI4" config user.name t
  echo x > "$RI4/a.txt"; git -C "$RI4" add -A; git -C "$RI4" commit -qm init
  git -C "$RI4" worktree add -q "$WT" >/dev/null 2>&1
  RK hooks "$WT" --tooling "$T3" >"$OUT" 2>&1
  local common; common=$(git -C "$WT" rev-parse --git-common-dir)
  if [ -f "$common/hooks/pre-push" ]; then ok "fixpass: worktree hooks land in the common dir (item 16)"; else bad "fixpass: worktree hooks land in the common dir (item 16)"; fi

  # Item 14: recovery commands survive a broken gate_common.
  local T4="$WORK/t-fx4"
  mkdir -p "$T4"; cp -r "$SRC/install" "$SRC/tools" "$SRC/hooks" "$SRC/templates" "$T4/"; cp "$SRC/VERSION" "$T4/"; rm -rf "$T4/tools/__pycache__"
  printf '\ndef broken(:\n' >> "$T4/tools/gate_common.py"
  python3 "$T4/install/repo-kit" enforce off --tooling "$T4" >"$OUT" 2>&1
  expect_rc 0 "fixpass: enforce off survives a broken gate_common (item 14)" $?
  python3 "$T4/install/repo-kit" checks "$R" --tooling "$T4" >"$OUT" 2>&1
  expect_rc 0 "fixpass: checks survives a broken gate_common (item 14)" $?
  python3 "$T4/install/repo-kit" env --tooling "$T4" >"$OUT" 2>&1
  expect_rc 1 "fixpass: a gate_common-dependent command fails (item 14)" $?
  expect_grep "fixpass: the failure names gate_common (item 14)" "$OUT" "gate_common"
  expect_no_grep "fixpass: the failure is not a traceback (item 14)" "$OUT" "Traceback"

  # Item 17: override allowance is written atomically at 0600;
  # enforce on preserves hand-added supervisor lines.
  local T5="$WORK/t-fx5" R5="$WORK/r-fx5"
  new_tooling "$T5"; new_repo "$R5" "$T5"
  echo clean > "$R5/a.txt"; commit "$R5" init; push "$R5" >/dev/null 2>&1
  printf 'swordfish\n' > "$R5/fp.txt"; commit "$R5" fp
  printf 'override\n' | script -qec "python3 $SRC/install/repo-kit override $R5 --tooling $T5" /dev/null >"$OUT" 2>&1
  local omode; omode=$(stat -c %a "$R5/.git/repokit-override.json" 2>/dev/null || stat -f %Lp "$R5/.git/repokit-override.json")
  if [ "$omode" = "600" ]; then ok "fixpass: override allowance written 0600 (item 17)"; else bad "fixpass: override allowance written 0600 (item 17) (got $omode)"; fi
  if [ -e "$R5/.git/repokit-override.json.tmp" ]; then bad "fixpass: no override tmp file left behind (item 17)"; else ok "fixpass: no override tmp file left behind (item 17)"; fi
  printf 'REPOKIT_TRUSTED_PATH=/opt/bin:/usr/bin:/bin\nREPOKIT_CHECK_PROFILE=security\n' > "$T5/hooks/enforced.env"
  RK enforce on --tooling "$T5" --profile full </dev/null >"$OUT" 2>&1
  expect_grep "fixpass: enforce on preserves REPOKIT_TRUSTED_PATH (item 17)" "$T5/hooks/enforced.env" "REPOKIT_TRUSTED_PATH=/opt/bin"
  expect_grep "fixpass: enforce on updates the profile it owns (item 17)" "$T5/hooks/enforced.env" "REPOKIT_CHECK_PROFILE=full"

  # Item 12: the stale phrases are gone from the live summary
  # surfaces (the two verified-not-stale hits in tools/checks
  # are out of scope and not grepped here).
  if grep -nE "warnings only|six jobs|skip loudly|skip-with-a-notice|may skip" \
      "$SRC/README.md" "$SRC/install/repo-kit" "$SRC/tools/ci_checks.py" \
      "$SRC/templates/agent-enforced/README.md" >"$OUT" 2>&1; then
    bad "fixpass: stale summary phrases reconciled (item 12)"
  else
    ok "fixpass: stale summary phrases reconciled (item 12)"
  fi
  # Item 13: ns-17 exists, unticked, carrying the review path.
  expect_grep "fixpass: PLAN ns-17 is open and carries the review path (item 13)" "$SRC/PLAN.md" "^- \[ \] ns-17"
}

main() {
  local sections="$*"
  [ -z "$sections" ] && sections="patterns firstpush worktree override agent service packet setup units dashboard merges pins fixpass"
  local s
  for s in $sections; do "section_$s"; done
  echo "----"
  echo "passed: $PASS  failed: $FAIL  skipped: $SKIP"
  [ -n "$FAILED_CASES" ] && echo "failed cases:$FAILED_CASES"
  [ "$FAIL" -eq 0 ]
}
main "$@"
