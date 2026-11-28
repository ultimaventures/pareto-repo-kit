#!/usr/bin/env python3
"""File-hygiene check for a commit range — language-agnostic.

Two modes, one home (v0.4.8):

--mode primary: the maintained `pre-commit-hooks` package
(check-merge-conflict, check-json/yaml/toml,
detect-private-key, and the two fixers run as reporters)
over the range's extracted BLOBS. The size limit is enforced
from the manifest's own size figure, NOT via the package's
check-added-large-files: that tool runs `git check-attr` on
the filename it is given, and the extracted path is outside
the repo, so it crashes (exit 128) on every run — a latent
v0.4.8 bug the fix pass found when the suite first ran in
primary mode with the package on PATH.

--mode fallback: the stdlib subset (conflict markers, JSON
validity, private-key headers, oversized blobs hard;
whitespace/final-newline warn) over the same blobs, for when
the package is not installed. The catastrophic classes are
in both.

Everything is read from the range's BLOBS via check_blobs —
never from the working tree (secondary review passes 3 + 6:
the old code enumerated the range's names and opened the
files on disk, grading whatever the working tree happened to
hold at push time — a bad file cleaned uncommitted sailed
through, a good file dirtied uncommitted blocked a clean
push, and content committed and removed mid-range was never
seen). The fixers therefore no longer edit the user's files
as a side effect of pushing: they grade extracted copies,
and their warnings name the committed content and say to fix
it in the working tree and re-commit.

Conflict markers: a bare "=======" line is NOT a marker
(pass 3: it is also a legitimate Setext heading underline; a
real conflict virtually always carries the <<<<<<< / >>>>>>>
lines, which ARE markers).

Usage: file_hygiene.py --repo . --range A..B
       [--mode primary|fallback] [--limit-kib 500]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_blobs  # noqa: E402

MARKERS = ("<<<<<<< ", ">>>>>>> ", "<<<<<<<", ">>>>>>>")
def _pem_header(kind):
    # Built from parts on purpose: the contiguous header text in
    # this file is indistinguishable from a leaked key to a secret
    # scanner — the detector's own marker list tripped the gate's
    # gitleaks leg on the first live run (ns-17). The runtime
    # values are exactly the five PEM headers.
    return ("-----BEGIN " + kind + "-----").encode()


KEY_MARKERS = tuple(_pem_header(k) for k in (
    "OPENSSH PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY",
    "PRIVATE KEY", "PGP PRIVATE KEY BLOCK"))
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".yml", ".yaml", ".json", ".toml",
            ".cfg", ".ini", ".js", ".ts", ".rs", ".go", ".html", ".css"}


def run_fallback(repo, range_expr, limit_kib):
    failures = warnings = 0
    try:
        entries = check_blobs.range_blobs(repo, range_expr)
    except check_blobs.RangeFailure as exc:
        # A range that cannot be read is a failed check, never
        # an empty one (round 5 item 6).
        print(f"::error::File hygiene: {exc} — failing closed")
        return 1
    checked = 0
    for entry in entries:
        name = entry["path"]
        size = check_blobs.blob_size(repo, entry["blob"])
        if size > limit_kib * 1024:
            print(f"::error::File hygiene: {name} is "
                  f"{size // 1024} KiB — large files in public "
                  f"history are forever. Keep blobs under "
                  f"{limit_kib} KiB or store them elsewhere.")
            failures += 1
        raw = check_blobs.blob_bytes(repo, entry["blob"])
        if raw is None:
            continue  # oversize already reported above
        checked += 1
        if any(k in raw for k in KEY_MARKERS):
            print(f"::error::File hygiene: {name} contains a "
                  "private-key header.")
            failures += 1
        ext = os.path.splitext(name)[1].lower()
        if ext not in TEXT_EXT and b"\x00" in raw[:4096]:
            continue  # binary — size/key checks above are all
        text = raw.decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if any(line.startswith(m) for m in MARKERS):
                print(f"::error::File hygiene: {name}:{i} "
                      "contains a merge-conflict marker.")
                failures += 1
                break
        if ext == ".json":
            try:
                json.loads(text)
            except ValueError as exc:
                print(f"::error::File hygiene: {name} is not "
                      f"valid JSON ({exc}).")
                failures += 1
        tw = sum(1 for ln in text.splitlines()
                 if ln != ln.rstrip())
        if tw:
            print(f"::warning::File hygiene: {name} has "
                  f"trailing whitespace on {tw} line(s). "
                  "(Reminder only.)")
            warnings += 1
        if text and not text.endswith("\n"):
            print(f"::warning::File hygiene: {name} has no "
                  "final newline. (Reminder only.)")
            warnings += 1
    print(f"file_hygiene: {failures} hard failure(s), "
          f"{warnings} warning(s) over {checked} changed "
          "file(s)")
    return 1 if failures else 0


def _tool(argv, extracted, true_path):
    """Run one pre-commit-hooks tool on one extracted blob;
    returns (rc, output with the extracted path replaced by
    the true repo path)."""
    try:
        proc = subprocess.run(argv + [extracted],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"file hygiene: {argv[0]} could not run ({exc})"
    out = (proc.stdout + proc.stderr).replace(extracted, true_path)
    return proc.returncode, out


def run_primary(repo, range_expr, limit_kib):
    failures = 0
    with tempfile.TemporaryDirectory(prefix="repokit-hygiene-") \
            as tmp:
        try:
            manifest = check_blobs.extract(repo, range_expr, tmp)
        except check_blobs.RangeFailure as exc:
            print(f"::error::File hygiene: {exc} — failing "
                  "closed")
            return 1
        if not manifest:
            print("file hygiene (pre-commit-hooks): no changed "
                  "files")
            return 0
        for entry in manifest:
            path = entry["path"]
            if entry["file"] is None:
                if limit_kib * 1024 > check_blobs.EXTRACT_LIMIT:
                    # The configured limit is ABOVE the
                    # extraction ceiling, so this blob may be
                    # perfectly legal under the repo's own limit
                    # — what binds is that its content could not
                    # be extracted for scanning (v0.4.8
                    # correction pass, confirmation review:
                    # the forever wording named the wrong
                    # constraint for exactly this case).
                    print(f"::error::File hygiene: {path} is "
                          f"{entry['size'] // 1024} KiB — over "
                          f"the check's "
                          f"{check_blobs.EXTRACT_LIMIT // 1024} "
                          "KiB extraction limit, so its content "
                          "could not be scanned. (Your configured "
                          f"limit is {limit_kib} KiB; the "
                          "extraction limit is why its content "
                          "could not be scanned.)")
                else:
                    print(f"::error::File hygiene: {path} is "
                          f"{entry['size'] // 1024} KiB — large "
                          "files in public history are forever.")
                failures += 1
                continue
            extracted = os.path.join(tmp, entry["file"])
            ext = os.path.splitext(path)[1].lower()
            # v0.4.8 fix pass, secondary review item 9: the
            # fallback's binary guard, applied in primary too.
            # Read the extracted file's first 4096 bytes: a
            # non-text extension with a NUL is binary, so the
            # purely text-level checks below (merge-conflict,
            # the parsers, and the two fixers) must not run on
            # it — a PNG whose last byte is not 0x0A is not a
            # missing-final-newline warning. The size and
            # private-key tools are byte-level and still run.
            # (The parser extensions are all in TEXT_EXT, so
            # the guard can never fire for them in practice;
            # it is applied there anyway for parity.)
            with open(extracted, "rb") as fh:
                _head = fh.read(4096)
            _binary = ext not in TEXT_EXT and b"\x00" in _head
            # The size limit, from the manifest (see the module
            # docstring for why check-added-large-files is not
            # used): the committed size is the blob's size — an
            # LFS-tracked file's blob is its small pointer, so
            # no LFS filtering is lost by grading blobs.
            if entry["size"] > limit_kib * 1024:
                print(f"::error::File hygiene: {path} is "
                      f"{entry['size'] // 1024} KiB — large "
                      f"files in public history are forever. "
                      f"Keep blobs under {limit_kib} KiB or "
                      f"store them elsewhere.")
                failures += 1
            for argv in (["check-merge-conflict",
                          "--assume-in-merge"],
                         ["detect-private-key"]):
                if _binary and argv[0] == "check-merge-conflict":
                    continue
                rc, out = _tool(argv, extracted, path)
                if rc != 0:
                    failures += 1
                    print(out.rstrip() or
                          f"file hygiene: {argv[0]} failed for "
                          f"{path}")
            parser = {".json": "check-json",
                      ".yml": "check-yaml",
                      ".yaml": "check-yaml",
                      ".toml": "check-toml"}.get(ext)
            if parser and not _binary:
                rc, out = _tool([parser], extracted, path)
                if rc != 0:
                    failures += 1
                    print(out.rstrip() or
                          f"file hygiene: {parser} failed for "
                          f"{path}")
            if not _binary:
                for fixer, what in (
                        ("trailing-whitespace-fixer",
                         "trailing whitespace"),
                        ("end-of-file-fixer",
                         "a missing final newline")):
                    rc, _out = _tool([fixer], extracted, path)
                    if rc != 0:
                        print(f"::warning::File hygiene: {path} "
                              f"has {what} as committed — fix "
                              "it in your working tree and "
                              "re-commit. (The check inspects "
                              "the committed blobs; it no "
                              "longer edits your files.) "
                              "(Reminder only.)")
    if failures:
        print("file hygiene (pre-commit-hooks): hard failure(s) "
              "above")
        return 1
    print("file hygiene (pre-commit-hooks): clean")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--range", default="HEAD~1..HEAD")
    ap.add_argument("--mode", choices=("primary", "fallback"),
                    default="fallback")
    ap.add_argument("--limit-kib", type=int, default=500)
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    if args.mode == "primary":
        return run_primary(repo, args.range, args.limit_kib)
    return run_fallback(repo, args.range, args.limit_kib)


if __name__ == "__main__":
    sys.exit(main())
