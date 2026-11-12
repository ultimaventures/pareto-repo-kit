#!/usr/bin/env python3
"""File-hygiene check for a commit range — language-agnostic, stdlib only.

HARD failures:
  * merge-conflict markers left in a changed file
  * a changed .json file that no longer parses
  * an added blob larger than the limit (default 500 KiB — big files in
    public history are forever)

WARNINGS (never fail):
  * trailing whitespace in changed text files
  * a changed text file with no final newline

Usage: file_hygiene.py --repo . --range A..B [--limit-kib 500]
"""

import argparse
import json
import os
import subprocess
import sys

MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".yml", ".yaml", ".json", ".toml",
            ".cfg", ".ini", ".js", ".ts", ".rs", ".go", ".html", ".css"}


def git(repo, *args, binary=False):
    out = subprocess.run(["git", "-C", repo] + list(args),
                         capture_output=True,
                         text=not binary,
                         encoding=None if binary else "utf-8",
                         errors=None if binary else "replace")
    return out.stdout if out.returncode == 0 else (b"" if binary else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", default="HEAD~1..HEAD")
    ap.add_argument("--limit-kib", type=int, default=500)
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    failures = warnings = 0

    names = git(repo, "diff", "--name-only", args.range).split()
    for name in names:
        path = os.path.join(repo, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        raw = open(path, "rb").read()
        if len(raw) > args.limit_kib * 1024:
            print(f"::error::File hygiene: {name} is "
                  f"{len(raw) // 1024} KiB — large files in public "
                  f"history are forever. Keep blobs under "
                  f"{args.limit_kib} KiB or store them elsewhere.")
            failures += 1
        if ext not in TEXT_EXT and b"\x00" in raw[:4096]:
            continue  # binary — size check above is all that applies
        text = raw.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if any(line.startswith(m) for m in MARKERS):
                print(f"::error::File hygiene: {name}:{i} contains a "
                      "merge-conflict marker.")
                failures += 1
                break
        if ext == ".json":
            try:
                json.loads(text)
            except ValueError as exc:
                print(f"::error::File hygiene: {name} is not valid "
                      f"JSON ({exc}).")
                failures += 1
        tw = sum(1 for ln in text.splitlines() if ln != ln.rstrip())
        if tw:
            print(f"::warning::File hygiene: {name} has trailing "
                  f"whitespace on {tw} line(s). (Reminder only.)")
            warnings += 1
        if text and not text.endswith("\n"):
            print(f"::warning::File hygiene: {name} has no final "
                  "newline. (Reminder only.)")
            warnings += 1

    print(f"file_hygiene: {failures} hard failure(s), "
          f"{warnings} warning(s) over {len(names)} changed file(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
