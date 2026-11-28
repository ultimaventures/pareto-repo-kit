#!/usr/bin/env python3
"""Range -> blob extraction, shared by the hygiene and
shellcheck checks (v0.4.8, secondary review passes 3 + 6).

Up to v0.4.7 both checks enumerated the range's file NAMES and
then read the WORKING TREE's bytes for them — so they graded
whatever happened to be on disk at push time: a bad file cleaned
up uncommitted sailed through, a good file dirtied uncommitted
blocked a clean push, and content committed and removed
mid-range was never seen at all. This module is the single
place that turns a range expression into the range's actual
blobs: it enumerates commits (strict — a failed rev-list is an
error, never an empty list), takes each commit's changed names
from a NUL-delimited diff-tree, resolves each name to its blob
in THAT commit's tree, dedupes by (blob, extension), and
(for the CLI) extracts each distinct blob to a scratch file.

Consumers read blob bytes, never working-tree files. Names
with newlines or non-ASCII bytes survive end to end: the
enumeration is -z throughout and the manifest is JSON.

Dedupe is by (blob, extension) (v0.4.8 fix pass, secondary
review item 8): consumers' checks are path-classified, so
the same bytes under a different extension are a different
check subject — identical content first seen at a .txt path
must still be checked under its .json path — while same-
extension duplicates still dedupe once.

The range -> commits step itself is gate_common's (the one
range implementation, v0.4.8 consolidation); this module adds
the blob layer on top of it.

CLI:
  check_blobs.py --repo . --range RANGE --out DIR
      Extract into DIR; write DIR/manifest.json:
      [{file, path, commit, blob, size, too_large}] — "file"
      is the extracted file's name inside DIR (None when the
      blob was too large to extract), "path" the true repo
      path, "commit" the first commit in the range carrying
      this blob under this path.
  Exit codes: 0 ok; 2 the range could not be read (callers
  fail closed).
"""

import json
import os
import re
import subprocess
import sys

from gate_common import GitFailure  # noqa: E402
from gate_common import range_commits as _common_range_commits  # noqa: E402

EXTRACT_LIMIT = 32 * 1024 * 1024  # per-blob extraction ceiling


class RangeFailure(Exception):
    pass


def _git(repo, *args):
    out = subprocess.run(["git", "-C", repo] + list(args),
                         capture_output=True, timeout=120)
    if out.returncode != 0:
        raise RangeFailure(
            f"git {' '.join(args[:2])} failed: "
            f"{out.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return out.stdout


def range_commits(repo, range_expr):
    """The range's commits, oldest first — delegated to
    gate_common (the one range implementation: effective-range
    validation included, so an unresolvable base fails here too
    rather than yielding an empty list). GitFailure is
    translated to this module's RangeFailure, the error its
    callers already handle. This module's other git work stays
    on the byte-level _git above: blob enumeration parses
    NUL-delimited bytes so paths survive exactly."""
    try:
        return _common_range_commits(repo, range_expr)
    except GitFailure as exc:
        raise RangeFailure(str(exc))


def commit_blobs(repo, sha):
    """(path, blob_sha) for every name the commit touches, the
    blob taken from the commit's OWN tree (for a merge, the
    result tree). Deleted names have no blob and are absent."""
    names_raw = _git(repo, "diff-tree", "--root", "-m", "-r",
                     "--first-parent", "-z", "--name-only",
                     "--no-commit-id", sha)
    names = [n.decode("utf-8", "replace")
             for n in names_raw.split(b"\x00") if n]
    if not names:
        return []
    tree_raw = _git(repo, "ls-tree", "-r", "-z", sha)
    blobs = {}
    for entry in tree_raw.split(b"\x00"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == b"blob":
            blobs[path.decode("utf-8", "replace")] = \
                parts[2].decode("ascii")
    return [(name, blobs[name]) for name in names if name in blobs]


def range_blobs(repo, range_expr):
    """Distinct blobs of the range: [{path, commit, blob}] —
    deduped by (blob, extension), extension lowercased from
    the path (v0.4.8 fix pass, secondary review item 8).
    Hygiene's checks are path-classified, so identical bytes
    under a different extension are a different check subject
    and get their own entry (reported under the first path
    seen for that (blob, extension) pair); identical content
    under the same extension is still checked once."""
    seen = {}
    for sha in range_commits(repo, range_expr):
        for path, blob in commit_blobs(repo, sha):
            ext = os.path.splitext(path)[1].lower()
            key = (blob, ext)
            if key not in seen:
                seen[key] = {"path": path, "commit": sha,
                             "blob": blob}
    return list(seen.values())


def blob_bytes(repo, blob, limit=EXTRACT_LIMIT):
    """The blob's bytes, or None when it exceeds the limit."""
    size = int(_git(repo, "cat-file", "-s", blob).strip())
    if size > limit:
        return None
    return _git(repo, "cat-file", "blob", blob)


def blob_size(repo, blob):
    return int(_git(repo, "cat-file", "-s", blob).strip())


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _extract_name(index, path):
    base = path.rsplit("/", 1)[-1] or "file"
    base = _SAFE.sub("_", base)[-80:]
    return f"{index:04d}__{base}"


def extract(repo, range_expr, out_dir):
    entries = range_blobs(repo, range_expr)
    manifest = []
    for i, entry in enumerate(entries):
        size = blob_size(repo, entry["blob"])
        name = None
        too_large = size > EXTRACT_LIMIT
        if not too_large:
            data = _git(repo, "cat-file", "blob", entry["blob"])
            name = _extract_name(i, entry["path"])
            with open(os.path.join(out_dir, name), "wb") as fh:
                fh.write(data)
        manifest.append({**entry, "file": name, "size": size,
                         "too_large": too_large})
    with open(os.path.join(out_dir, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return manifest


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    try:
        manifest = extract(os.path.abspath(args.repo),
                           args.range, args.out)
    except RangeFailure as exc:
        print(f"check_blobs: {exc}", file=sys.stderr)
        return 2
    print(f"check_blobs: {len(manifest)} distinct blob(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
