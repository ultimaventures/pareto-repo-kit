#!/usr/bin/env python3
"""check_pins.py -- verify that every copy of a pinned value agrees.

The kit's recurring drift bug (secondary review, pass 1 major 3 +
complexity pass): a version or tool pin lives in several files —
VERSION, the two reusable workflows' defaults, the two caller
templates, the README's examples, the dependency manifest and the
gate workflow's hard-coded download hashes — and the copies drift
(the dashboard reusable sat at v0.1.0 through three releases).
Each value has ONE home where it is changed (VERSION for the kit
version, tools/dependencies.txt for external tools); the other
copies exist because their files must be self-contained (a
workflow at a tag cannot read the manifest). This script is the
mechanical agreement check: run it before tagging a release —
and in CI (self-gate) on every PR — and a drifted copy fails
loudly instead of shipping.

Exit codes: 0 = every pin agrees; 1 = a disagreement (or a pin
location missing entirely, which is drift that already happened).

Stdlib only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_common import EMPTY_TREE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(label, found, expected):
    if found == expected:
        print(f"ok   - {label}: {found}")
    else:
        FAILURES.append(label)
        print(f"FAIL - {label}: found {found!r}, expected "
              f"{expected!r}")


def read(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def one(rel, pattern, label, expected):
    text = read(rel)
    if text is None:
        check(label, "<file missing>", "<present>")
        return
    m = re.search(pattern, text)
    check(label, m.group(1) if m else "<no match>", expected)


def check_hashes(rel, text, name, file_pat, hashes):
    """Every inline sha256 for one tool's download in one workflow.

    Arch rule (v0.4.8 correction pass, confirmation review): a
    workflow may pin either architecture's archive — gate.yml and
    self-gate.yml both download the amd64 asset today, but an
    arm64 line is a legitimate pin, not drift. So: every collected
    hash must be one of the manifest's hashes for the tool, at
    least one hash must be present, and the amd64 hash must be
    among them (both workflows fetch the amd64 asset). Up to the
    correction pass the two files were checked inconsistently —
    gate.yml by first match only, self-gate.yml by requiring
    EVERY hash to equal the amd64 hash, which would false-fail
    the day self-gate gains an arm64 download line.
    """
    label = f"{name} sha256 ({rel} vs manifest)"
    found = re.findall(file_pat, text)
    if not found:
        check(label, "<no match>", "<present>")
        return
    allowed = set(hashes.values())
    bad = [h for h in found if h not in allowed]
    if bad:
        check(label, bad, f"one of {sorted(allowed)}")
    elif hashes.get("amd64") not in found:
        check(label, found,
              "the manifest hashes, including amd64 "
              + hashes.get("amd64", "<none in manifest>"))
    else:
        print(f"ok   - {label}: {found}")


def manifest():
    """name -> (version, {arch: sha256}) from dependencies.txt."""
    out = {}
    text = read("tools/dependencies.txt") or ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        name, _kind, version, shas = parts[:4]
        hashes = dict(kv.split("=", 1) for kv in shas.split(";")
                      if "=" in kv)
        out[name] = (version, hashes)
    return out


def main():
    version = (read("VERSION") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"FAIL - VERSION file: {version!r} is not X.Y.Z")
        return 1
    tag = "v" + version

    # The kit version: reusable defaults + caller templates + README.
    one(".github/workflows/gate.yml",
        r'tooling_ref:[\s\S]*?default:\s*"(v[\d.]+)"',
        "gate reusable default", tag)
    one(".github/workflows/dashboard.yml",
        r'tooling_ref:[\s\S]*?default:\s*"(v[\d.]+)"',
        "dashboard reusable default", tag)
    one("templates/workflows/gate.yml",
        r"workflows/gate\.yml@(v[\d.]+)", "gate caller template", tag)
    one("templates/workflows/dashboard.yml",
        r"workflows/dashboard\.yml@(v[\d.]+)",
        "dashboard caller template", tag)
    readme = read("README.md") or ""
    readme_pins = sorted(set(re.findall(
        r"workflows/(?:gate|dashboard)\.yml@(v[\d.]+)", readme)))
    check("README pin examples", readme_pins, [tag])

    # External tools: the manifest is the home; gate.yml's inline
    # copies (needed for the workflow to be self-contained) must
    # agree with it.
    man = manifest()
    gate = read(".github/workflows/gate.yml") or ""
    for name, url_pat, file_pat in (
            ("gitleaks", r"gitleaks/releases/download/v([\d.]+)/",
             r'echo "([0-9a-f]{64})  /tmp/gitleaks\.tgz"'),
            ("zizmor", r"zizmor/releases/download/v([\d.]+)/",
             r'echo "([0-9a-f]{64})  /tmp/zizmor\.tgz"'),
            ("actionlint", r"actionlint/releases/download/v([\d.]+)/",
             r'echo "([0-9a-f]{64})  /tmp/actionlint\.tgz"')):
        if name not in man:
            check(f"{name} manifest row", "<missing>", "<present>")
            continue
        version_want, hashes = man[name]
        m = re.search(url_pat, gate)
        check(f"{name} version (gate.yml vs manifest)",
              m.group(1) if m else "<no match>", version_want)
        check_hashes(".github/workflows/gate.yml", gate, name,
                     file_pat, hashes)

    # self-gate.yml's inline copies (v0.4.8 fix pass, item 4):
    # the dogfood workflow hard-codes the gitleaks and shellcheck
    # downloads too, and up to the fix pass no check opened the
    # file — its three hashes were verified nowhere. (The pattern
    # is file-agnostic: version URL + the sha256 echo line.)
    self_gate = read(".github/workflows/self-gate.yml")
    if self_gate is None:
        check("self-gate.yml", "<file missing>", "<present>")
    else:
        for name, url_pat, file_pat in (
                ("gitleaks", r"gitleaks/releases/download/v([\d.]+)/",
                 r'echo "([0-9a-f]{64})  /tmp/gitleaks\.tgz"'),
                ("shellcheck",
                 r"shellcheck/releases/download/v([\d.]+)/",
                 r'echo "([0-9a-f]{64})  '
                 r'/tmp/shellcheck\.tar\.xz"')):
            if name not in man:
                check(f"{name} manifest row", "<missing>", "<present>")
                continue
            version_want, hashes = man[name]
            m = re.search(url_pat, self_gate)
            check(f"{name} version (self-gate.yml vs manifest)",
                  m.group(1) if m else "<no match>", version_want)
            check_hashes(".github/workflows/self-gate.yml",
                         self_gate, name, file_pat, hashes)

    # The empty-tree id: gate_common is the home; the shell
    # dispatcher's copy (it cannot import the module) must agree.
    # The hook's literal is the SHA-1 form; the pattern accepts
    # either hash length so the check itself is not SHA-1-only
    # (v0.4.8 fix pass, item 11) — agreement with gate_common's
    # SHA-1 constant is what is being verified.
    hook = read("hooks/pre-push") or ""
    m = re.search(r"EMPTY_TREE=([0-9a-f]{40,64})", hook)
    check("EMPTY_TREE (hooks/pre-push vs gate_common)",
          m.group(1) if m else "<no match>", EMPTY_TREE)

    if FAILURES:
        print(f"check_pins: {len(FAILURES)} pin(s) disagree — "
              "fix the copies to match the home value (VERSION "
              "for the kit version, tools/dependencies.txt for "
              "external tools, tools/gate_common.py for "
              "EMPTY_TREE); do not edit this script to bless a "
              "drift.")
        return 1
    print("check_pins: all pins agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
