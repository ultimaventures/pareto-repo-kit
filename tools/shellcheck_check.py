#!/usr/bin/env python3
"""ShellCheck over the range's BLOBS (v0.4.8, secondary
review pass 6).

Up to v0.4.7 the check enumerated the range's file names and
linted the WORKING TREE's files — a committed syntax error
cleaned up uncommitted sailed through, and the NUL-delimited
enumeration was piped through `tr '\\0' '\\n'`, so a filename
containing a newline split into fragments and was silently
skipped. This driver extracts the range's blobs via
check_blobs and lints the extracted copies; names survive
end to end (the manifest is JSON), and the shebang test reads
the blob's own first line. dash and ash shebangs count (pass
6: the old regex missed them).

Modes:
  --list   print the number of shell blobs in the range
           (the check script decides the missing-binary
           coverage gap from this, BEFORE needing the binary)
  (run)    lint each shell blob; output names the true repo
           path; exit 1 on any lint failure, 2 when the range
           cannot be read or a shell-extension blob is over
           check_blobs' extraction limit (item 19 below).
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_blobs  # noqa: E402

# v0.4.8 fix pass, secondary review item 2: no zsh in either
# set — shellcheck cannot lint zsh (SC1071 with a shebang,
# SC2148 without, both exit 1 with no author-side fix). ksh
# stays: shellcheck supports it.
SHELL_EXT = {".sh", ".bash", ".ksh"}
SHEBANG_RX = re.compile(rb"^#!.*\b(bash|ksh|dash|ash|sh)\b")


class OverLimitShell(Exception):
    """A shell-extension blob over check_blobs' extraction
    limit (v0.4.8 fix pass, secondary review item 19)."""


def _is_shell(entry, first_line):
    ext = os.path.splitext(entry["path"])[1].lower()
    if ext in SHELL_EXT:
        return True
    return bool(first_line and SHEBANG_RX.match(first_line))


def _shell_entries(repo, range_expr, tmp):
    manifest = check_blobs.extract(repo, range_expr, tmp)
    picked = []
    for entry in manifest:
        if entry["file"] is None:
            # v0.4.8 fix pass, secondary review item 19:
            # shellness-by-extension needs only the path, so
            # an over-limit entry with a shell extension is a
            # hard failure, not a silent skip — agreeing with
            # file_hygiene's treatment of the same condition.
            # Without a shell extension, shellness would need
            # the shebang (i.e. the content), so those entries
            # stay skipped.
            ext = os.path.splitext(entry["path"])[1].lower()
            if ext in SHELL_EXT:
                raise OverLimitShell(
                    f"{entry['path']} is a shell file over "
                    f"the {check_blobs.EXTRACT_LIMIT} byte "
                    f"({check_blobs.EXTRACT_LIMIT // (1024 * 1024)}"
                    f" MiB) extraction limit "
                    f"({entry.get('size', '?')} bytes) — "
                    "cannot lint it, failing closed")
            continue
        first = b""
        with open(os.path.join(tmp, entry["file"]), "rb") as fh:
            first = fh.readline()
        if _is_shell(entry, first):
            picked.append(entry)
    return picked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", required=True)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    with tempfile.TemporaryDirectory(prefix="repokit-shell-") \
            as tmp:
        try:
            picked = _shell_entries(repo, args.range, tmp)
        except (check_blobs.RangeFailure,
                OverLimitShell) as exc:
            # Item 19: this covers --list too — _shell_entries
            # raises before any count is printed, so an
            # over-limit shell file is never silently
            # uncounted.
            print(f"shellcheck: {exc} — failing closed",
                  file=sys.stderr)
            return 2
        if args.list:
            print(len(picked))
            return 0
        if not picked:
            print("shellcheck: no shell files in range")
            return 0
        fail = 0
        for entry in picked:
            extracted = os.path.join(tmp, entry["file"])
            print(f"shellcheck: linting {entry['path']}")
            proc = subprocess.run(
                ["shellcheck", extracted], capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=120)
            out = (proc.stdout + proc.stderr).replace(
                extracted, entry["path"])
            if out.strip():
                print(out.rstrip())
            if proc.returncode != 0:
                fail = 1
        return fail


if __name__ == "__main__":
    sys.exit(main())
