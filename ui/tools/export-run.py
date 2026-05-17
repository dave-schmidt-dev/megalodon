#!/usr/bin/env python3
"""
Megalodon — export a mission run as a single static HTML page.

Reads the project's protocol files (STATUS.md, TASKS.md, HISTORY.md,
.mission-events, README.md, findings/*.md, claims/) and renders them as a
single self-contained HTML document with embedded CSS. No external assets,
no JavaScript — suitable for archival under `.archive/<utc>--<slug>/`.

S-8 deliverable per TASKS.md cross-pool. Built standalone (no server.py
dependency) so it can be run against archived mission directories.

Usage:
    python ui/tools/export-run.py
    python ui/tools/export-run.py --project-root /path/to/megalodon
    python ui/tools/export-run.py --output /path/to/output.html
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Dataclasses (minimal mirror of server.py types — deliberately decoupled)
# ---------------------------------------------------------------------------


@dataclass
class LaneRow:
    lane: str
    agent: Optional[str]
    state: str
    last_utc: Optional[str]
    notes: str


@dataclass
class Task:
    id: str
    phase: str
    lane_tag: str
    description: str
    state: str
    claimer: Optional[str] = None
    claim_utc: Optional[str] = None
    done_utc: Optional[str] = None


@dataclass
class Finding:
    filename: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    title: Optional[str] = None
    body_md: str = ""


@dataclass
class HistoryEntry:
    utc: str
    agent: str
    lane: str
    task: str
    finding_filename: str
    severity: str


@dataclass
class PhaseEvent:
    utc: str
    from_phase: str
    to_phase: str
    by_agent: str
    reason: str


# ---------------------------------------------------------------------------
# Parsers — minimal duplicated subset of server.py; deliberate to keep this
# tool dependency-free.
# ---------------------------------------------------------------------------


def parse_status(project_root: Path) -> list[LaneRow]:
    path = project_root / "STATUS.md"
    rows: list[LaneRow] = []
    if not path.exists():
        return rows
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and "Lane" in line and "Agent" in line:
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if not in_table or not line.startswith("|"):
            in_table = in_table and line.startswith("|")
            if not in_table:
                continue
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        lane = cells[0]
        if lane.upper() not in (
            "AUDIT", "ARCHITECT", "BACKEND", "FRONTEND", "TEST", "META"
        ):
            continue
        agent = None if cells[1] in ("unclaimed", "—", "") else cells[1]
        rows.append(
            LaneRow(
                lane=lane,
                agent=agent,
                state=cells[2],
                last_utc=cells[3] if cells[3] not in ("—", "") else None,
                notes=" | ".join(cells[4:]) if len(cells) > 4 else "",
            )
        )
    return rows


_TASK_RE = re.compile(
    r"^- \[(?P<bracket>[^\]]+)\] \[(?P<lane>[^\]]+)\] `(?P<id>[^`]+)` — (?P<desc>.+)$"
)
_PHASE_RE = re.compile(r"^## (?P<phase>PHASE [^\n]+)")


def parse_tasks(project_root: Path) -> list[Task]:
    path = project_root / "TASKS.md"
    if not path.exists():
        return []
    tasks: list[Task] = []
    current_phase = "UNKNOWN"
    for line in path.read_text(encoding="utf-8").splitlines():
        mp = _PHASE_RE.match(line.strip())
        if mp:
            current_phase = mp.group("phase")
            continue
        m = _TASK_RE.match(line.strip())
        if not m:
            continue
        bracket = m.group("bracket").strip()
        state = "open"
        claimer = None
        claim_utc = None
        done_utc = None
        if bracket.startswith("claimed:"):
            state = "claimed"
            parts = bracket[len("claimed:"):].split("@", 1)
            if len(parts) == 2:
                claimer = parts[0].strip()
                claim_utc = parts[1].strip()
        elif bracket.startswith("done:"):
            state = "done"
            parts = bracket[len("done:"):].split("@", 1)
            if len(parts) == 2:
                claimer = parts[0].strip()
                done_utc = parts[1].strip()
        tasks.append(
            Task(
                id=m.group("id").strip(),
                phase=current_phase,
                lane_tag=m.group("lane").strip(),
                description=m.group("desc").strip(),
                state=state,
                claimer=claimer,
                claim_utc=claim_utc,
                done_utc=done_utc,
            )
        )
    return tasks


_HISTORY_RE = re.compile(
    r"^(?P<utc>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z) \| (?P<agent>[^|]+) \| (?P<lane>[^|]+) \| (?P<task>[^|]+) \| (?P<filename>[^|]+) \| (?P<severity>.+)$"
)


def parse_history(project_root: Path) -> list[HistoryEntry]:
    path = project_root / "HISTORY.md"
    if not path.exists():
        return []
    out: list[HistoryEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HISTORY_RE.match(line.strip())
        if not m:
            continue
        out.append(
            HistoryEntry(
                utc=m.group("utc"),
                agent=m.group("agent").strip(),
                lane=m.group("lane").strip(),
                task=m.group("task").strip(),
                finding_filename=m.group("filename").strip(),
                severity=m.group("severity").strip(),
            )
        )
    return out


_MISSION_EVENT_RE = re.compile(
    r"^(?P<utc>[\d\-T:]+Z) (?P<from>[A-Z\-]+)->(?P<to>[A-Z\-]+) by (?P<by>[^ ]+) — (?P<reason>.+)$"
)


def parse_mission_events(project_root: Path) -> list[PhaseEvent]:
    path = project_root / ".mission-events"
    if not path.exists():
        return []
    out: list[PhaseEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _MISSION_EVENT_RE.match(line.strip())
        if not m:
            continue
        out.append(
            PhaseEvent(
                utc=m.group("utc"),
                from_phase=m.group("from"),
                to_phase=m.group("to"),
                by_agent=m.group("by"),
                reason=m.group("reason"),
            )
        )
    return out


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_finding(path: Path) -> Finding:
    """Lightweight YAML frontmatter split — handles the convention used in this
    project without requiring PyYAML."""
    text = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm_text = m.group(1)
        body = m.group(2)
        # Hand-roll: lines of `key: value` (no nested structures used in this project)
        for ln in fm_text.splitlines():
            if ":" in ln and not ln.startswith(" ") and not ln.startswith("-"):
                k, v = ln.split(":", 1)
                frontmatter[k.strip()] = v.strip()
    title = None
    for ln in body.splitlines():
        if ln.startswith("# "):
            title = ln[2:].strip()
            break
    return Finding(filename=path.name, frontmatter=frontmatter, title=title, body_md=body)


def list_findings(project_root: Path) -> list[Finding]:
    findings_dir = project_root / "findings"
    if not findings_dir.is_dir():
        return []
    return [parse_finding(p) for p in sorted(findings_dir.glob("*.md"))]


def current_mission_status(project_root: Path) -> str:
    path = project_root / "README.md"
    if not path.exists():
        return "UNKNOWN"
    m = re.search(r"\*\*Current:\s*([A-Z\-]+)", path.read_text(encoding="utf-8"))
    return m.group(1) if m else "UNKNOWN"


# ---------------------------------------------------------------------------
# Minimal markdown → HTML (intentionally tiny — enough for finding bodies)
# ---------------------------------------------------------------------------


def md_to_html(text: str) -> str:
    """Render a SUBSET of markdown: headers, bullets, code fences, bold, inline code, paragraphs.

    Deliberately small — for archival rendering. Full fidelity is not the goal.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_code = False
    code_lang = ""
    in_list = False
    para: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            joined = " ".join(para).strip()
            if joined:
                out.append(f"<p>{_inline_md(joined)}</p>")
            para = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_para(); close_list()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                code_lang = line[3:].strip() or ""
                out.append(f'<pre><code class="lang-{html.escape(code_lang)}">')
                in_code = True
            continue
        if in_code:
            out.append(html.escape(raw) + "\n")
            continue
        if not line.strip():
            flush_para(); close_list()
            continue
        if line.startswith("# "):
            flush_para(); close_list()
            out.append(f"<h2>{_inline_md(line[2:].strip())}</h2>")
            continue
        if line.startswith("## "):
            flush_para(); close_list()
            out.append(f"<h3>{_inline_md(line[3:].strip())}</h3>")
            continue
        if line.startswith("### "):
            flush_para(); close_list()
            out.append(f"<h4>{_inline_md(line[4:].strip())}</h4>")
            continue
        if line.startswith("- ") or line.startswith("* "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(line[2:].strip())}</li>")
            continue
        # Treat as paragraph fragment
        close_list()
        para.append(line)
    flush_para(); close_list()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _inline_md(text: str) -> str:
    text = html.escape(text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    return text


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


CSS = r"""
:root {
  --bg: #0d1117;
  --fg: #c9d1d9;
  --muted: #6e7681;
  --accent: #58a6ff;
  --panel: #161b22;
  --border: #30363d;
  --green: #3fb950;
  --amber: #d29922;
  --red: #f85149;
  --purple: #bc8cff;
}
* { box-sizing: border-box; }
html, body { background: var(--bg); color: var(--fg); margin: 0; padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px; line-height: 1.5; }
main { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
h2 { font-size: 18px; margin-top: 32px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
h3 { font-size: 16px; margin-top: 24px; color: var(--accent); }
h4 { font-size: 14px; margin-top: 18px; color: var(--purple); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: var(--panel); padding: 1px 5px; border-radius: 3px; font-family: SF Mono, Menlo, monospace; font-size: 12px; }
pre { background: var(--panel); padding: 12px; overflow-x: auto; border-radius: 4px; border: 1px solid var(--border); }
pre code { background: transparent; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
.muted { color: var(--muted); font-size: 12px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; }
.sev-BLOCKING { background: rgba(248,81,73,0.2); color: var(--red); }
.sev-MAJOR { background: rgba(210,153,34,0.2); color: var(--amber); }
.sev-MINOR { background: rgba(63,185,80,0.15); color: var(--green); }
.sev-NIT, .sev-DELTA, .sev-INFO { background: rgba(110,118,129,0.2); color: var(--muted); }
.state-done { color: var(--green); }
.state-claimed { color: var(--amber); }
.state-open { color: var(--muted); }
.state-working { color: var(--accent); }
.state-idle { color: var(--green); }
.state-BLOCKED, .state-STALE { color: var(--red); }
.phase-pill { display: inline-block; padding: 2px 8px; border-radius: 12px; background: var(--panel); border: 1px solid var(--border); margin-right: 4px; font-size: 11px; }
.finding-card { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; margin: 12px 0; }
.finding-card details summary { cursor: pointer; padding: 6px 0; }
.finding-card details[open] summary { border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.timeline { border-left: 2px solid var(--border); margin-left: 8px; padding-left: 18px; }
.timeline-item { position: relative; padding: 8px 0; }
.timeline-item::before { content: ""; position: absolute; left: -25px; top: 14px; width: 8px; height: 8px; background: var(--accent); border-radius: 50%; }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 4px 12px; margin: 8px 0; }
.kv dt { color: var(--muted); }
.banner { background: var(--panel); border-left: 3px solid var(--accent); padding: 12px 16px; margin: 12px 0; border-radius: 0 4px 4px 0; }
.toc { background: var(--panel); padding: 12px 16px; border-radius: 4px; border: 1px solid var(--border); margin-bottom: 24px; }
.toc ul { margin: 0; padding-left: 20px; }
"""


def render_html(project_root: Path) -> str:
    lanes = parse_status(project_root)
    tasks = parse_tasks(project_root)
    history = parse_history(project_root)
    events = parse_mission_events(project_root)
    findings = list_findings(project_root)
    mission_status = current_mission_status(project_root)
    current_phase = events[-1].to_phase if events else "INIT"
    export_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Group tasks by phase
    phases_seen: list[str] = []
    tasks_by_phase: dict[str, list[Task]] = {}
    for t in tasks:
        if t.phase not in tasks_by_phase:
            tasks_by_phase[t.phase] = []
            phases_seen.append(t.phase)
        tasks_by_phase[t.phase].append(t)

    findings_by_lane: dict[str, list[Finding]] = {}
    for f in findings:
        lane = f.frontmatter.get("lane", "UNKNOWN")
        findings_by_lane.setdefault(lane, []).append(f)

    parts: list[str] = []
    parts.append(
        f"<!DOCTYPE html><html lang=en><head><meta charset=utf-8>"
        f"<title>Megalodon — mission archive @ {html.escape(export_utc)}</title>"
        f"<style>{CSS}</style></head><body><main>"
    )
    parts.append(
        f"<h1>Megalodon — mission archive</h1>"
        f'<div class="banner">'
        f'<span class="phase-pill">Phase: {html.escape(current_phase)}</span>'
        f'<span class="phase-pill">Status: {html.escape(mission_status)}</span>'
        f'<span class="phase-pill">Exported: {html.escape(export_utc)}</span>'
        f'<span class="phase-pill">{len(findings)} findings · {len(history)} history entries · {len(events)} phase events</span>'
        f"</div>"
    )

    # Table of contents
    parts.append(
        '<nav class="toc"><strong>Contents</strong><ul>'
        '<li><a href="#lanes">Lane status</a></li>'
        '<li><a href="#phases">Phase timeline</a></li>'
        '<li><a href="#tasks">Task queue</a></li>'
        '<li><a href="#findings">Findings</a></li>'
        '<li><a href="#history">History log</a></li>'
        '</ul></nav>'
    )

    # Lane status
    parts.append('<h2 id="lanes">Lane status</h2>')
    parts.append(
        "<table><thead><tr><th>Lane</th><th>Agent</th><th>State</th><th>Last UTC</th><th>Notes</th></tr></thead><tbody>"
    )
    for r in lanes:
        state_class = _state_class(r.state)
        parts.append(
            f"<tr><td>{html.escape(r.lane)}</td>"
            f"<td>{html.escape(r.agent or '—')}</td>"
            f"<td class='{state_class}'>{html.escape(r.state)}</td>"
            f"<td>{html.escape(r.last_utc or '—')}</td>"
            f"<td>{html.escape(r.notes)}</td></tr>"
        )
    parts.append("</tbody></table>")

    # Phase timeline
    parts.append('<h2 id="phases">Phase timeline</h2><div class="timeline">')
    for ev in events:
        parts.append(
            f'<div class="timeline-item">'
            f'<strong>{html.escape(ev.from_phase)} → {html.escape(ev.to_phase)}</strong> '
            f'<span class="muted">at {html.escape(ev.utc)} by {html.escape(ev.by_agent)}</span><br>'
            f'<span class="muted">{html.escape(ev.reason)}</span>'
            f'</div>'
        )
    parts.append("</div>")

    # Task queue per phase
    parts.append('<h2 id="tasks">Task queue</h2>')
    for phase in phases_seen:
        ts = tasks_by_phase[phase]
        done = sum(1 for t in ts if t.state == "done")
        claimed = sum(1 for t in ts if t.state == "claimed")
        open_count = sum(1 for t in ts if t.state == "open")
        parts.append(
            f"<h3>{html.escape(phase)}</h3>"
            f'<div class="muted">{done} done · {claimed} claimed · {open_count} open of {len(ts)} total</div>'
        )
        parts.append(
            "<table><thead><tr><th>State</th><th>Task ID</th><th>Lane</th><th>Description</th><th>Claimer @ UTC</th></tr></thead><tbody>"
        )
        for t in ts:
            stamp = t.done_utc or t.claim_utc or "—"
            parts.append(
                f"<tr><td class='state-{t.state}'>{html.escape(t.state)}</td>"
                f"<td><code>{html.escape(t.id)}</code></td>"
                f"<td>{html.escape(t.lane_tag)}</td>"
                f"<td>{html.escape(t.description[:200])}{'…' if len(t.description) > 200 else ''}</td>"
                f"<td>{html.escape(t.claimer or '—')} @ {html.escape(stamp)}</td></tr>"
            )
        parts.append("</tbody></table>")

    # Findings index + bodies
    parts.append('<h2 id="findings">Findings</h2>')
    for lane in sorted(findings_by_lane):
        fs = findings_by_lane[lane]
        parts.append(f"<h3>Lane {html.escape(str(lane))} — {len(fs)} findings</h3>")
        for f in fs:
            severity = f.frontmatter.get("severity", "—")
            fm = f.frontmatter
            anchor = f.filename.replace(".", "-")
            parts.append(
                f'<div class="finding-card" id="{html.escape(anchor)}">'
                f'<details>'
                f'<summary>'
                f'<span class="tag sev-{html.escape(severity)}">{html.escape(severity)}</span> '
                f'<strong>{html.escape(f.title or f.filename)}</strong> '
                f'<span class="muted">{html.escape(f.filename)}</span>'
                f'</summary>'
                f'<dl class="kv">'
                f'<dt>Agent</dt><dd>{html.escape(str(fm.get("agent", "—")))}</dd>'
                f'<dt>Task</dt><dd><code>{html.escape(str(fm.get("task", "—")))}</code></dd>'
                f'<dt>UTC</dt><dd>{html.escape(str(fm.get("utc", "—")))}</dd>'
                f'<dt>Artifact</dt><dd>{html.escape(str(fm.get("artifact", "—"))[:200])}</dd>'
                f'</dl>'
                f"<div>{md_to_html(f.body_md)}</div>"
                f'</details>'
                f'</div>'
            )

    # History log
    parts.append('<h2 id="history">History log</h2>')
    parts.append(
        "<table><thead><tr><th>UTC</th><th>Agent</th><th>Lane</th><th>Task</th><th>Finding</th><th>Severity</th></tr></thead><tbody>"
    )
    for h in history:
        parts.append(
            f"<tr><td>{html.escape(h.utc)}</td>"
            f"<td>{html.escape(h.agent)}</td>"
            f"<td>{html.escape(h.lane)}</td>"
            f"<td><code>{html.escape(h.task)}</code></td>"
            f"<td>{html.escape(h.finding_filename[:80])}</td>"
            f"<td><span class='tag sev-{html.escape(h.severity)}'>{html.escape(h.severity)}</span></td></tr>"
        )
    parts.append("</tbody></table>")

    parts.append(
        f'<footer class="muted" style="margin-top:48px;border-top:1px solid var(--border);padding-top:12px">'
        f"Generated by <code>ui/tools/export-run.py</code> (S-8) at {html.escape(export_utc)}. "
        f"Static archive — no external assets."
        f"</footer></main></body></html>"
    )
    return "".join(parts)


def _state_class(state: str) -> str:
    if state.startswith("working"):
        return "state-working"
    if state == "STALE-RECLAIMED":
        return "state-STALE"
    return f"state-{state.split(':')[0]}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="Path to the Megalodon project root (default: parent of ui/tools/)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the HTML file. Default: <project>/.archive/<utc>--export/index.html",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write HTML to stdout instead of a file",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        sys.stderr.write(f"ERROR: project root not found: {project_root}\n")
        return 1

    html_text = render_html(project_root)

    if args.stdout:
        sys.stdout.write(html_text)
        return 0

    if args.output:
        out_path = Path(args.output).resolve()
    else:
        utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%MZ")
        out_path = project_root / ".archive" / f"{utc}--export" / "index.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")
    print(f"Wrote {len(html_text):,} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
