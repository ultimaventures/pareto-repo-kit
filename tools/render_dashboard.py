#!/usr/bin/env python3
"""Public project dashboard renderer.

Builds the project's public status dashboard (index.html) and a STATE.md
summary from ONLY what is inside this repository:

  * project.public.yaml  — publishable project facts (flat file)
  * git log              — the activity feed (commit SUBJECTS only)
  * adr/*.json           — decision records
  * PLAN.md              — next steps + open decisions (checkbox format)

Nothing outside the repo is read. Stdlib only, deterministic: same repo
state in, same dashboard out. Run by CI (.github/workflows/dashboard.yml)
on every push to main; can also be run locally:

    python3 tools/render_dashboard.py [--repo .] [--out-dir _site]
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys

COMMIT_TYPES = ("feat", "fix", "docs", "adr", "test", "refactor", "chore",
                "build", "ci", "perf", "style", "revert")
SUBJECT_RX = re.compile(
    r"^(?P<type>" + "|".join(COMMIT_TYPES) + r")"
    r"(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s*(?P<rest>.*)$")
STEP_RX = re.compile(r"^- \[(?P<done>[ x])\] (?P<id>ns-\d+|dec-\d+) · (?P<text>.*)$")
CONT_RX = re.compile(r"^\s+(?P<text>\S.*)$")
BLOCKED_RX = re.compile(r"blocked-by:\s*(\S+)")


def parse_public_yaml(path):
    """Tiny parser for the flat public descriptor (no nested maps)."""
    data = {}
    lines = open(path, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in (">-", ">"):
            buf = []
            while i < len(lines) and (lines[i].startswith("  ") or
                                      not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            val = " ".join(buf).strip()
        data[key] = val.strip("'\"")
    return data


def git_log(repo, limit=30):
    out = subprocess.run(
        ["git", "-C", repo, "log", f"-{limit}",
         "--format=%ad%x00%s%x00%h", "--date=short"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    commits = []
    if out.returncode != 0:
        return commits
    for line in out.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) != 3:
            continue
        date, subject, sha = parts
        m = SUBJECT_RX.match(subject)
        commits.append({
            "date": date, "subject": subject, "sha": sha,
            "type": m.group("type") if m else "other",
            "rest": m.group("rest") if m else subject,
            "scope": m.group("scope") if m else None,
        })
    return commits


def parse_plan(path):
    """PLAN.md -> {'Next steps': [steps], 'Open decisions': [steps]}"""
    sections = {}
    current = None
    step = None
    for line in open(path, encoding="utf-8").read().splitlines():
        h = re.match(r"^## (.+)$", line)
        if h:
            current = h.group(1).strip()
            sections.setdefault(current, [])
            step = None
            continue
        m = STEP_RX.match(line)
        if m and current:
            step = {"id": m.group("id"), "done": m.group("done") == "x",
                    "text": m.group("text").strip(), "blocked_by": None}
            sections[current].append(step)
            continue
        c = CONT_RX.match(line)
        if c and step is not None:
            text = c.group("text").strip()
            b = BLOCKED_RX.search(text)
            if b:
                step["blocked_by"] = b.group(1)
            else:
                step["text"] += " " + text
    return sections


def parse_adrs(repo):
    adr_dir = os.path.join(repo, "adr")
    adrs = []
    if not os.path.isdir(adr_dir):
        return adrs
    for name in sorted(os.listdir(adr_dir)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(adr_dir, name),
                          encoding="utf-8") as fh:
                    adrs.append(json.load(fh))
            except (ValueError, OSError):
                continue
    return adrs


def esc(text):
    return html.escape(str(text or ""))


CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial,
  sans-serif; margin: 0; background: #f6f4ec; color: #1d2a22; }
@media (prefers-color-scheme: dark) { body { background: #14201a;
  color: #e6e2d5; } }
main { max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 1.9rem; margin: 0 0 4px; }
.tagline { font-size: 1.05rem; opacity: .85; margin: 0 0 6px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
  background: #2e5d43; color: #fff; font-size: .8rem; text-transform:
  uppercase; letter-spacing: .04em; }
section { background: rgba(127,127,127,.08); border: 1px solid
  rgba(127,127,127,.25); border-radius: 12px; padding: 16px 20px;
  margin: 18px 0; }
h2 { font-size: 1.15rem; margin: 0 0 10px; }
ul { margin: 0; padding-left: 20px; }
li { margin: 6px 0; }
.step-id, .adr-id { font-weight: 600; }
.blocked { color: #b3541e; font-weight: 600; font-size: .85em; }
.done { opacity: .55; text-decoration: line-through; }
.commit { font-size: .92rem; }
.commit .date { opacity: .6; margin-right: 8px; }
.ctype { display: inline-block; min-width: 52px; font-weight: 600;
  font-size: .8em; text-transform: uppercase; }
footer { opacity: .6; font-size: .85rem; margin-top: 24px; }
a { color: inherit; }
"""


CITE_RX = re.compile(r"\((ns-\d+|dec-\d+)\)")


def cited_steps(commits):
    """Step IDs cited in any commit subject — a citation completes a step."""
    cited = {}
    for c in commits:
        for m in CITE_RX.finditer(c["subject"]):
            cited.setdefault(m.group(1), c["sha"])
    return cited


def render_html(meta, commits, plan, adrs, cited=None):
    cited = cited or {}
    parts = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, "
             "initial-scale=1\">",
             f"<title>{esc(meta.get('name', 'Project'))} — status</title>",
             f"<style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>{esc(meta.get('name', 'Project'))}</h1>")
    if meta.get("tagline"):
        parts.append(f"<p class=\"tagline\">{esc(meta['tagline'])}</p>")
    if meta.get("status"):
        parts.append(f"<p><span class=\"badge\">{esc(meta['status'])}</span></p>")
    if meta.get("summary"):
        parts.append(f"<p>{esc(meta['summary'])}</p>")

    for section in ("Next steps", "Open decisions"):
        steps = plan.get(section, [])
        if not steps:
            continue
        parts.append(f"<section><h2>{esc(section)}</h2><ul>")
        for s in steps:
            done = s["done"] or s["id"] in cited
            cls = "done" if done else ""
            blocked = (f" <span class=\"blocked\">blocked by "
                       f"{esc(s['blocked_by'])}</span>"
                       if s["blocked_by"] and not done else "")
            via = (f" <span style=\"opacity:.6\">(via commit "
                   f"{esc(cited[s['id']])})</span>"
                   if not s["done"] and s["id"] in cited else "")
            parts.append(
                f"<li class=\"{cls}\"><span class=\"step-id\">{esc(s['id'])}</span>"
                f" · {esc(s['text'])}{blocked}{via}</li>")
        parts.append("</ul></section>")

    if commits:
        parts.append("<section><h2>Recent activity</h2><ul>")
        for c in commits:
            parts.append(
                f"<li class=\"commit\"><span class=\"date\">{esc(c['date'])}</span>"
                f"<span class=\"ctype\">{esc(c['type'])}</span>"
                f"{esc(c['rest'])}</li>")
        parts.append("</ul></section>")

    if adrs:
        parts.append("<section><h2>Decision records</h2><ul>")
        for a in adrs:
            parts.append(
                f"<li><span class=\"adr-id\">{esc(a.get('id'))}</span> · "
                f"{esc(a.get('title'))} "
                f"<span style=\"opacity:.6\">({esc(a.get('status'))}, "
                f"{esc(a.get('date'))})</span></li>")
        parts.append("</ul></section>")

    parts.append("<footer>Generated by CI from this repository's commit log, "
                 "PLAN.md and adr/ — nothing outside the repo feeds this "
                 "page.</footer>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def render_state_md(meta, commits, plan, adrs, cited=None):
    cited = cited or {}
    lines = [f"# {meta.get('name', 'Project')} — status", ""]
    if meta.get("status"):
        lines.append(f"Status: **{meta['status']}**")
    if meta.get("summary"):
        lines += ["", meta["summary"]]
    for section in ("Next steps", "Open decisions"):
        steps = plan.get(section, [])
        if steps:
            lines += ["", f"## {section}", ""]
            for s in steps:
                done = s["done"] or s["id"] in cited
                box = "x" if done else " "
                extra = (f" (blocked by {s['blocked_by']})"
                         if s["blocked_by"] and not done else "")
                lines.append(f"- [{box}] {s['id']} · {s['text']}{extra}")
    if adrs:
        lines += ["", "## Decision records", ""]
        for a in adrs:
            lines.append(f"- {a.get('id')} · {a.get('title')} "
                         f"({a.get('status')}, {a.get('date')})")
    if commits:
        lines += ["", "## Recent activity", ""]
        for c in commits[:15]:
            lines.append(f"- {c['date']} · {c['subject']}")
    lines += ["", "_Generated by CI from this repository._", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out-dir", default="_site")
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    meta = parse_public_yaml(os.path.join(repo, "project.public.yaml"))
    commits = git_log(repo, limit=500)
    cited = cited_steps(commits)
    plan = parse_plan(os.path.join(repo, "PLAN.md"))
    adrs = parse_adrs(repo)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "index.html"), "w",
              encoding="utf-8") as fh:
        fh.write(render_html(meta, commits[:30], plan, adrs, cited))
    with open(os.path.join(args.out_dir, "STATE.md"), "w",
              encoding="utf-8") as fh:
        fh.write(render_state_md(meta, commits, plan, adrs, cited))
    print(f"dashboard rendered: {len(commits)} commits, "
          f"{sum(len(v) for v in plan.values())} plan items, "
          f"{len(adrs)} ADRs -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
