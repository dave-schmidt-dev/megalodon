"""megalodon_ui.server — FastAPI app factory.

`make_app(*, mission_dir, config=None, port=8080)` returns an ASGI app bound
to the given mission directory. Pure factory: two calls produce two
independent apps. No module-level globals; all state in `MissionContext`
attached to `app.state.megalodon`.

This is the BACKEND P3-C deliverable per the P2.5-C plan-v2 8-step sequence.
The endpoint surface here covers the integration-test contract; the legacy
`/api/v1/*` surface in `ui/server.py` remains the live dashboard server
until the migration is complete.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig
from . import primitives
from .queue import queue_client as _qc
from .mission_config import load_mission_config
from .mission_config.schema import MissionConfig
from .mission_config.regex_builder import (
    build_task_line_re,
    build_status_row_re,
    build_phase_header_re,
)
from .constants import (
    API_CHALLENGE,
    API_CONFIG,
    API_EVENTS,
    API_FINDINGS,
    API_INJECT_TASK,
    API_MISSION_STATUS,
    API_PHASE_FLIP,
    API_RECLAIM,
    API_SIGNAL,
    API_STATE,
    SSE_STATUS_CHANGE,
    SSE_SYNC,
    STALE_THRESHOLD_SECONDS,
)
from ._v92_constants import (
    COOKIE_MAX_AGE_SECONDS,
    LIFESPAN_STARTUP_TIMEOUT_SECONDS,
    SOCKET_PATH_LIMIT_BYTES,
    TAIL_ON_CONNECT_BYTES,
)
from . import auth
from . import tmux
from .spawn import FleetSpawner, TooManySubscribersError
from .harnesses import get_adapter


# ---------------------------------------------------------------------------
# CR-4 (narrow) — v9.2 auth path gating
# ---------------------------------------------------------------------------

#: Path prefixes that ALWAYS require a valid mui_session cookie. Any method.
_V92_GATED_PATH_RE = re.compile(
    r"^/api/v1/(lane/[^/]+|__fake__|permission_prompts|activity-wall|narrative-stream|narrative|approval-rules|lanes/stale|_test)(/|$)"
)

#: Exact (method, path) pairs that require a cookie.
_V92_GATED_EXACT: frozenset[tuple[str, str]] = frozenset(
    {
        ("DELETE", "/api/v1/fleet"),
    }
)

#: Cookie name used to carry the session id after exchange.
SESSION_COOKIE_NAME = "mui_session"


# ---------------------------------------------------------------------------
# v9.4 GET /api/v1/lanes/stale — module-level cache + test-override hook
# ---------------------------------------------------------------------------

# Per-app cache keyed by id(app). Each value: {"response": dict, "computed_at": float}.
# Module-level so concurrent requests within the TTL window get a single
# computation. Keys are cleaned up lazily — they accumulate only across
# make_app() calls within a process (negligible for production; fine for tests).
_stale_cache: dict[int, dict] = {}

#: One-shot per-lane silent_seconds override, populated ONLY by the
#: ``_test/stale_override`` endpoint. Consumed once on the next
#: ``GET /api/v1/lanes/stale`` call and then cleared. Setting
#: ``_TEST_STALE_OVERRIDES["A"] = 1200.0`` makes lane "A" appear 1200 s
#: stale in the next response.
_TEST_STALE_OVERRIDES: dict[str, float] = {}

_STALE_THRESHOLD_SECONDS: float = 900.0
_STALE_CACHE_TTL_SECONDS: float = 5.0

# Regex to extract the UTC timestamp from an applier log line:
# Format: 2026-05-16T22:00:00Z | INFO | APPLIED rid=... agent=agent-xxxx lane=... ...
_APPLIER_LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z", re.MULTILINE
)
# Parse agent= field from an applier log line.
_APPLIER_LOG_AGENT_RE = re.compile(r"\bagent=(\S+)")
# Parse lane= (submitting_lane) field from an applier log line — matches lane=X NOT submitting_lane=.
_APPLIER_LOG_LANE_RE = re.compile(r"(?<!\w)lane=(\S+)")


def _stale_latest_applier_ts(mission_dir: Path, agent_id: str) -> datetime | None:
    """Return the most-recent applier-log timestamp for *agent_id*.

    Scans ``.fleet/queue-applier.log`` from the end (lines reversed) to find
    the latest entry where ``agent=<agent_id>`` appears. Returns a UTC-aware
    datetime, or None if the file is missing / no matching entry.
    """
    log_path = mission_dir / ".fleet" / "queue-applier.log"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best: datetime | None = None
    for line in text.splitlines():
        am = _APPLIER_LOG_AGENT_RE.search(line)
        if am is None or am.group(1) != agent_id:
            continue
        ts_m = _APPLIER_LOG_TS_RE.search(line)
        if ts_m is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_m.group(1) + "+00:00")
        except ValueError:
            continue
        if best is None or ts > best:
            best = ts
    return best


def _compute_stale_response(
    mission_dir: Path,
    lane_rows: list[dict[str, Any]],
    mission_config: "MissionConfig",
    permission_watcher: Any,
) -> dict:
    """Compute the full stale-lanes payload (no caching here).

    *Source priority* (all three are considered; the newest wins):
      1. status-md  — ``last_utc`` parsed from STATUS.md row.
      2. stream-log — mtime of ``.fleet/<short>.stream.log``.
      3. applier-log — newest line in ``.fleet/queue-applier.log`` matching
                       the lane's agent_id from the STATUS.md ``agent`` column.

    Tie-break: when two sources share the same second-level precision we prefer
    in order: applier-log > stream-log > status-md (i.e. last wins on a
    ``max()`` over ``(ts, source_priority)`` tuples).
    """
    now = datetime.now(timezone.utc)

    # parse_status returns "LANE-A" form; mission_config.lanes.short is "A".
    def _row_short(row: dict) -> str:
        lane = row.get("lane", "")
        return lane[len("LANE-") :] if lane.startswith("LANE-") else lane

    short_to_agent: dict[str, str] = {
        _row_short(r): r.get("agent", "") for r in lane_rows
    }

    pending_lanes: set[str] = set()
    if permission_watcher is not None:
        try:
            pending_lanes = {p.lane_short for p in permission_watcher.pending()}
        except Exception:
            pass

    stale_lanes: list[dict] = []
    for lc in mission_config.lanes:
        short = lc.short
        if not short:
            continue

        # --- source 1: STATUS.md last_utc ---
        status_ts: datetime | None = None
        row = next((r for r in lane_rows if _row_short(r) == short), None)
        if row is not None:
            raw = row.get("last_utc", "")
            try:
                status_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # --- source 2: stream-log mtime ---
        stream_ts: datetime | None = None
        stream_log = mission_dir / ".fleet" / f"{short}.stream.log"
        try:
            mtime = stream_log.stat().st_mtime
            stream_ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            pass

        # --- source 3: applier-log latest matching entry ---
        applier_ts: datetime | None = None
        agent_id = short_to_agent.get(short)
        if agent_id:
            applier_ts = _stale_latest_applier_ts(mission_dir, agent_id)

        # --- max of sources with priority tie-break ---
        # Priority order (highest last so max picks it on equal timestamps):
        # 0=status-md, 1=stream-log, 2=applier-log
        candidates: list[tuple[datetime, int, str]] = []
        if status_ts is not None:
            candidates.append((status_ts, 0, "status-md"))
        if stream_ts is not None:
            candidates.append((stream_ts, 1, "stream-log"))
        if applier_ts is not None:
            candidates.append((applier_ts, 2, "applier-log"))

        if candidates:
            best_ts, _, best_source = max(candidates, key=lambda t: (t[0], t[1]))
            silent_seconds = (now - best_ts).total_seconds()
            last_activity_source = best_source
        else:
            silent_seconds = float("inf")
            last_activity_source = "none"

        # --- test-override hook (one-shot) ---
        # Note: caller (_compute_stale_response's caller) uses the return value
        # to detect which lanes had overrides consumed and invalidate the cache.
        if short in _TEST_STALE_OVERRIDES:
            silent_seconds = _TEST_STALE_OVERRIDES.pop(short)

        pending_approval = short in pending_lanes
        is_stale = silent_seconds >= _STALE_THRESHOLD_SECONDS and not pending_approval

        if is_stale:
            stale_lanes.append(
                {
                    "lane": short,
                    "silent_seconds": silent_seconds
                    if silent_seconds != float("inf")
                    else None,
                    "pending_approval": pending_approval,
                    "last_activity_source": last_activity_source,
                }
            )

    return {
        "stale_lanes": stale_lanes,
        "checked_at_utc": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# MissionContext — per-app state attached to app.state.megalodon
# ---------------------------------------------------------------------------


@dataclass
class MissionContext:
    """Per-`make_app` instance state.

    No module globals; multiple `make_app()` calls in one process produce
    independent contexts (required for parallel pytest workers).
    """

    mission_dir: Path
    config: AppConfig
    port: int
    csrf_token: str  # mirror of config.csrf_token for fast access
    allowed_origins: tuple[str, ...]
    mission_config: MissionConfig = field(default=None)  # type: ignore[assignment]
    status_row_re: re.Pattern = field(default=None)  # type: ignore[assignment]
    task_line_re: re.Pattern = field(default=None)  # type: ignore[assignment]
    phase_header_re: re.Pattern = field(default=None)  # type: ignore[assignment]
    session_store: "auth.SessionStore" = field(default=None)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_status(
    mission_dir: Path, ctx: "MissionContext | None" = None
) -> list[dict[str, Any]]:
    """Parse STATUS.md table into a list of lane dicts.

    REPAIR-MUTATIONS-E2E-5-STATUS-VIEW: each row gets `staleness_seconds`
    (float, age since last_utc) and `is_stale` (bool, RULE-1 15min threshold).
    Consumed by FE `dashboard.js:115,187` for `data-stale` attr + band class.
    """
    path = mission_dir / "STATUS.md"
    if not path.exists():
        return []
    text = path.read_text()
    now = datetime.now(timezone.utc)
    status_re = (
        ctx.status_row_re
        if ctx is not None
        else build_status_row_re(load_mission_config(mission_dir))
    )
    rows: list[dict[str, Any]] = []
    for m in status_re.finditer(text):
        lane = m.group("lane").strip()
        if lane.lower() == "lane":
            continue
        agent = m.group("agent").strip()
        if agent.startswith("---") or agent == "":
            continue
        last_utc = m.group("last_utc").strip()
        staleness_seconds: float | None = None
        is_stale = False
        try:
            ts = datetime.fromisoformat(last_utc.replace("Z", "+00:00"))
            staleness_seconds = (now - ts).total_seconds()
            is_stale = staleness_seconds > STALE_THRESHOLD_SECONDS  # RULE-1: 15 min
        except (ValueError, AttributeError):
            pass
        rows.append(
            {
                "lane": lane,
                "agent": agent,
                "state": m.group("state").strip(),
                "last_utc": last_utc,
                "notes": m.group("notes").strip(),
                "staleness_seconds": staleness_seconds,
                "is_stale": is_stale,
            }
        )
    return rows


def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Minimal YAML frontmatter parser (sufficient for our finding files)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[3:end].strip()
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def parse_tasks(
    mission_dir: Path, ctx: "MissionContext | None" = None
) -> list[dict[str, Any]]:
    """Parse TASKS.md into a list of phase dicts.

    REPAIR-MUTATIONS-E2E-5-STATUS-VIEW: shape `[{name, tasks: [...]}]`.
    Each task dict has `id`, `lane`, `state` ("open"|"claimed"|"done"),
    `agent` (if claimed/done), `utc` (if claimed/done), `description`.
    Consumed by FE `tasks.js:417,452` via `store.get("tasks.phases")`.
    """
    path = mission_dir / "TASKS.md"
    if not path.exists():
        return []
    text = path.read_text()
    if ctx is not None:
        task_line_re = ctx.task_line_re
        phase_header_re = ctx.phase_header_re
    else:
        mc = load_mission_config(mission_dir)
        task_line_re = build_task_line_re(mc)
        phase_header_re = build_phase_header_re(mc)
    phase_headers = list(phase_header_re.finditer(text))
    phases: list[dict[str, Any]] = []
    # Build a short-code → long-name map from the mission config so task.lane
    # matches what the FE kanban buckets by (config.lanes[i].name, e.g. "AUDIT").
    short_to_name: dict[str, str] = {}
    if ctx is not None and ctx.mission_config is not None:
        for lane_cfg in ctx.mission_config.lanes:
            if lane_cfg.short:
                short_to_name[lane_cfg.short] = lane_cfg.name
    for i, hdr in enumerate(phase_headers):
        start = hdr.end()
        end = phase_headers[i + 1].start() if i + 1 < len(phase_headers) else len(text)
        section = text[start:end]
        tasks: list[dict[str, Any]] = []
        for m in task_line_re.finditer(section):
            state_block = m.group("state_block").strip()
            if state_block == "" or state_block == " ":
                state = "open"
                agent = None
                utc = None
            elif state_block.startswith("done:"):
                state = "done"
                rest = state_block[len("done:") :].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip()
                utc = utc.strip()
            elif state_block.startswith("claimed:"):
                state = "claimed"
                rest = state_block[len("claimed:") :].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip()
                utc = utc.strip()
            else:
                state = "open"
                agent = None
                utc = None
            short = m.group("lane")
            tasks.append(
                {
                    "id": m.group("task_id").strip(),
                    "lane": short_to_name.get(short, f"LANE-{short}"),
                    "state": state,
                    "agent": agent,
                    "utc": utc,
                    "description": (m.group("description") or "").strip(),
                }
            )
        phases.append({"name": hdr.group("phase").strip(), "tasks": tasks})
    return phases


def parse_findings(
    mission_dir: Path, *, include_scratch: bool = False
) -> list[dict[str, Any]]:
    """Parse findings/ directory; return list of dicts with YAML metadata."""
    findings_dir = mission_dir / "findings"
    out = []
    if not findings_dir.is_dir():
        return out
    for p in sorted(findings_dir.iterdir()):
        if not p.is_file():
            continue
        if not p.name.endswith(".md"):
            continue
        is_scratch = ".scratch" in p.name
        if is_scratch and not include_scratch:
            continue
        meta = _parse_yaml_frontmatter(p.read_text())
        meta["filename"] = p.name
        meta["scratch"] = is_scratch
        # Normalize severity field name
        if "severity" not in meta and "Severity" in meta:
            meta["severity"] = meta["Severity"]
        out.append(meta)
    return out


# ---------------------------------------------------------------------------
# Mission diagnostics surfaced via /api/v1/state.mission
# ---------------------------------------------------------------------------


_LANE_CANONICAL_RE = re.compile(r"^LANE-[A-Z]$")
_PHASE_FLIP_LOCK_DIRNAME_RE = re.compile(
    r"^(?P<from>[A-Z][A-Z0-9-]*)-to-(?P<to>[A-Z][A-Z0-9-]*)$"
)


def _detect_stuck_flip_lock(mission_dir: Path) -> dict[str, Any] | None:
    """Surface the oldest phase-flip lock directory (if any) for the FE.

    Megalodon's distributed phase-flip protocol creates a lock dir under
    `.phase-flip-locks/<FROM>-to-<TO>/` while a worker performs the flip. The
    dir is removed when the flip lands. If the worker crashes mid-flip the
    dir lingers; the operator-console renders a warning panel from this hint
    so they can manually complete or roll back.

    Returns {"from_phase", "to_phase", "lock_age_seconds"} or None.
    Multi-lock case: return the oldest lock (most concerning).
    """
    locks_dir = mission_dir / ".phase-flip-locks"
    if not locks_dir.is_dir():
        return None
    candidates: list[tuple[float, str, str]] = []
    for p in locks_dir.iterdir():
        if not p.is_dir():
            continue
        m = _PHASE_FLIP_LOCK_DIRNAME_RE.match(p.name)
        if not m:
            continue
        try:
            age = max(0.0, time.time() - p.stat().st_mtime)
        except OSError:
            continue
        candidates.append((age, m.group("from"), m.group("to")))
    if not candidates:
        return None
    candidates.sort(reverse=True)  # oldest first
    age, from_phase, to_phase = candidates[0]
    return {
        "from_phase": from_phase,
        "to_phase": to_phase,
        "lock_age_seconds": round(age, 1),
    }


def _list_claim_dirs(mission_dir: Path) -> list[dict[str, Any]]:
    """List immediate subdirs of `claims/` for the FE non-canonical panel.

    Each entry: {dirname, has_done, mtime}. `dirname` is the raw on-disk name
    (preserving Unicode like `P2-C→B`). The FE's tasks.js cross-references
    these against TASKS task ids and surfaces any dir without a matching id
    as a "non-canonical claim" — T-FX-FAILMODE-b asserts on this.
    """
    claims_dir = mission_dir / "claims"
    if not claims_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in claims_dir.iterdir():
        if not p.is_dir():
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append(
            {
                "dirname": p.name,
                "has_done": (p / "done").is_file(),
                "mtime": mtime,
            }
        )
    return out


def _parse_history_entries(mission_dir: Path) -> list[dict[str, Any]]:
    """Parse HISTORY.md into structured entries with a per-row `drift` flag.

    HISTORY format (per RULE 10 step 3):
      `<utc> | <agent> | <lane> | <task_id> | <finding> | <severity>`

    An entry is `drift: True` when EITHER:
      - the lane field doesn't match the canonical `LANE-[A-Z]` form
        (a worker wrote `F` or `FRONTEND` instead of `LANE-F`/`LANE-D`), OR
      - the task_id starts with `DRIFT-` (an explicit drift marker injected
        by `_gen.py` for the fix-medium-failure-modes fixture).

    Used by FE `renderHistoryTail` (mission.js) to flag drifted rows with a
    warning glyph + `data-drift="true"` attribute. T-FX-FAILMODE-c asserts
    exactly 3 drift rows in fix-medium-failure-modes.
    """
    history_path = mission_dir / "HISTORY.md"
    if not history_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw in history_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) < 4:
            continue
        utc = parts[0]
        agent = parts[1]
        lane = parts[2]
        task_id = parts[3]
        finding = parts[4] if len(parts) > 4 else ""
        severity = parts[5] if len(parts) > 5 else ""
        drift = not _LANE_CANONICAL_RE.match(lane) or task_id.startswith("DRIFT-")
        out.append(
            {
                "utc": utc,
                "agent": agent,
                "lane": lane,
                "task_id": task_id,
                "finding": finding,
                "severity": severity,
                "drift": drift,
            }
        )
    return out


# ---------------------------------------------------------------------------
# v9.3 dashboard payload helpers — /api/v1/state shape consumed by
# ui/static/pages/tasks.js + signals.js + mission.js.
# ---------------------------------------------------------------------------


# Canonical phase id mapping for TASKS.md section headers like
# "## PHASE 1 — PLAN". Maps the trailing word(s) (PLAN / BUILD / VERIFY /
# CHALLENGE / RUN / HEAL) onto the canonical "PHASE-<WORD>" id the FE keys
# off in store.tasks.phases. Sections that don't match (e.g.
# "CROSS-LANE / SECONDARY TASK POOL", "OPERATOR-ACCEPTANCE TASKS",
# "OPERATOR-INJECTED (live)") fall through to tasks.cross — see
# tasks.js:475 which reads (store.get("tasks.cross") || []).
_PHASE_SECTION_TAIL_RE = re.compile(
    r"PHASE\s+\d+(?:\.\d+)?\s*[—\-]\s*(?P<name>[A-Z][A-Z0-9_-]*)",
    re.IGNORECASE,
)


def _section_title_to_phase_id(title: str) -> str | None:
    """Map a TASKS.md section title to a canonical PHASE-* id, or None for cross.

    Examples:
      "PHASE 1 — PLAN"      -> "PHASE-PLAN"
      "PHASE 2 — BUILD"     -> "PHASE-BUILD"
      "PHASE 2 — CHALLENGE" -> "PHASE-CHALLENGE"
      "PHASE 3 — VERIFY"    -> "PHASE-VERIFY"
      "PHASE 3 — BUILD"     -> "PHASE-BUILD"  (v9.0 5-phase shape)
      "PHASE 5 — RUN"       -> "PHASE-RUN"
      "PHASE-PLAN"          -> "PHASE-PLAN"   (already canonical)
      "OPERATOR-ACCEPTANCE TASKS"       -> None (-> cross)
      "CROSS-LANE / SECONDARY TASK POOL"-> None (-> cross)
      "OPERATOR-INJECTED (live)"        -> None (-> cross)
    """
    t = title.strip()
    if not t:
        return None
    # Already canonical form: "PHASE-FOO".
    if t.startswith("PHASE-") and " " not in t and "/" not in t:
        return t
    m = _PHASE_SECTION_TAIL_RE.search(t)
    if m:
        return "PHASE-" + m.group("name").upper()
    return None


def parse_tasks_fe_shape(
    mission_dir: Path,
    ctx: "MissionContext | None" = None,
) -> dict[str, Any]:
    """Parse TASKS.md into the FE-facing shape `{phases: {...}, cross: [...]}`.

    Differs from `parse_tasks` (which feeds `/api/v1/tasks` as an ordered list
    of `{name, tasks}`) in two ways:

      1. Uses `ctx.mission_config.task_sections` as the section header source —
         so it picks up human-readable headers like ``## PHASE 1 — PLAN`` that
         the canonical-phase regex (``PHASE-PLAN``) misses.
      2. Returns per-task fields named per the v9.3 FE contract
         (tasks.js:21,156,440-477): ``task_id``, ``claim_state``,
         ``claim_agent``, ``claim_utc`` — *plus* legacy aliases
         (``id``, ``state``, ``agent``, ``utc``) so old consumers keep working.

    Sections whose title doesn't map to a ``PHASE-*`` id (e.g. cross-lane pool,
    operator-acceptance, operator-injected) drop into ``cross``.
    """
    path = mission_dir / "TASKS.md"
    if not path.is_file():
        return {"phases": {}, "cross": []}
    text = path.read_text()

    if ctx is not None:
        task_line_re = ctx.task_line_re
        mc = ctx.mission_config
    else:
        mc = load_mission_config(mission_dir)
        task_line_re = build_task_line_re(mc)

    # Short-code -> long lane name (e.g. "A" -> "AUDIT") so the FE kanban
    # buckets by config-declared name. Matches parse_tasks() behavior.
    short_to_name: dict[str, str] = {}
    if mc is not None:
        for lane_cfg in mc.lanes:
            if lane_cfg.short:
                short_to_name[lane_cfg.short] = lane_cfg.name

    # Build a section-header regex from the config's task_sections list, with
    # a graceful fallback to the canonical PHASE-* form (so v9.0 missions
    # where TASKS.md uses ``## PHASE-PLAN`` still parse).
    section_titles: list[str] = []
    if mc is not None and getattr(mc, "task_sections", None):
        section_titles = list(mc.task_sections)
    # Fallback: include canonical phase names so a mission whose TASKS.md
    # already uses canonical headers parses too.
    if mc is not None:
        for p in mc.phases:
            if p not in section_titles:
                section_titles.append(p)
    if not section_titles:
        return {"phases": {}, "cross": []}

    # Length-descending so e.g. "PHASE 2.5 — Plan-v2 reconciliation" wins over
    # "PHASE 2" prefix-match. Escape to handle / — ( ) literally.
    section_titles.sort(key=len, reverse=True)
    section_re = re.compile(
        r"^##\s+(?P<title>" + "|".join(re.escape(t) for t in section_titles) + r")\s*$",
        re.MULTILINE,
    )
    section_hdrs = list(section_re.finditer(text))

    phases: dict[str, list[dict[str, Any]]] = {}
    cross: list[dict[str, Any]] = []

    # Catch-all `^## ` to bound a known section when an unknown ## header
    # (e.g. ``## OPERATOR-INJECTED (live)`` not present in task_sections)
    # would otherwise let our last known section run to EOF and accidentally
    # vacuum up unrelated task lines.
    _any_h2_re = re.compile(r"^##\s+", re.MULTILINE)

    for i, hdr in enumerate(section_hdrs):
        start = hdr.end()
        if i + 1 < len(section_hdrs):
            end = section_hdrs[i + 1].start()
        else:
            end = len(text)
        # Trim to the next ## header (if any) that we don't recognize, so
        # tasks from unconfigured sections aren't merged into ours.
        next_h2 = _any_h2_re.search(text, start + 1, end)
        if next_h2:
            end = next_h2.start()
        section = text[start:end]
        title = hdr.group("title").strip()
        phase_id = _section_title_to_phase_id(title)

        for m in task_line_re.finditer(section):
            state_block = m.group("state_block").strip()
            if state_block == "" or state_block == " ":
                claim_state = "open"
                agent = None
                utc = None
            elif state_block.startswith("done:"):
                claim_state = "done"
                rest = state_block[len("done:") :].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip() or None
                utc = utc.strip() or None
            elif state_block.startswith("claimed:"):
                claim_state = "claimed"
                rest = state_block[len("claimed:") :].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip() or None
                utc = utc.strip() or None
            elif state_block.startswith("blocked:"):
                claim_state = "blocked"
                agent = None
                utc = None
            else:
                claim_state = "open"
                agent = None
                utc = None
            short = m.group("lane")
            task_id = m.group("task_id").strip()
            description = (m.group("description") or "").strip()
            lane_name = short_to_name.get(short, f"LANE-{short}")
            rec: dict[str, Any] = {
                # FE v9.3 contract (tasks.js:21,440-477)
                "task_id": task_id,
                "lane": lane_name,
                "description": description,
                "claim_state": claim_state,
                "phase": phase_id,
                # Legacy aliases — parse_tasks() shape, retained so any older
                # consumer still works.
                "id": task_id,
                "state": claim_state,
            }
            if agent is not None:
                rec["claim_agent"] = agent
                rec["agent"] = agent
            if utc is not None:
                rec["claim_utc"] = utc
                rec["utc"] = utc

            if phase_id:
                phases.setdefault(phase_id, []).append(rec)
            else:
                cross.append(rec)

    return {"phases": phases, "cross": cross}


# Signals — `<mission>/signals/LANE-X-to-LANE-Y-<UTC>.md`.
# Filename grammar: from-lane, to-lane, UTC stamp. The body is markdown free-
# form (see live LANE-D-to-LANE-C example). FE consumer ui/static/pages/
# signals.js:179-180,621-622 reads sig.from_lane, sig.to, sig.utc, sig.kind.
_SIGNAL_FILENAME_RE = re.compile(
    r"^(?P<from_lane>LANE-[A-Z0-9]+)-to-(?P<to_lane>LANE-[A-Z0-9]+)-(?P<utc>.+)\.md$"
)


def parse_signals(mission_dir: Path) -> list[dict[str, Any]]:
    """Scan `<mission>/signals/*.md`, return list of signal dicts.

    Each dict has: ``filename``, ``from_lane``, ``to_lane``, ``to`` (alias for
    signals.js), ``utc``, ``kind`` (always "SIGNAL"), ``body`` (truncated
    file contents up to 4 KB so the SSE payload stays small).

    Files that don't match the LANE-X-to-LANE-Y-UTC grammar are skipped — the
    operator-only README or stray files won't break the timeline render.
    Missing signals/ dir returns []. Read failures per-file are tolerated.
    """
    signals_dir = mission_dir / "signals"
    if not signals_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(signals_dir.iterdir()):
        if not p.is_file() or not p.name.endswith(".md"):
            continue
        m = _SIGNAL_FILENAME_RE.match(p.name)
        if not m:
            continue
        rec: dict[str, Any] = {
            "filename": p.name,
            "from_lane": m.group("from_lane"),
            "to_lane": m.group("to_lane"),
            "to": m.group("to_lane"),  # signals.js:162 reads sig.to
            "utc": m.group("utc"),
            "kind": "SIGNAL",
        }
        try:
            body = p.read_text(errors="replace")
            # Truncate to keep payload small — operator can hit the file
            # directly for the full content via /findings-style endpoint
            # if/when one is added for signals.
            rec["body"] = body[:4096]
        except OSError:
            pass
        out.append(rec)
    return out


def _read_mission_md_fields(mission_dir: Path) -> dict[str, Any]:
    """Extract `id` and `status` from MISSION.md frontmatter-ish lines.

    Looks for ``**Mission ID:** `<id>``` and ``**Status:** <STATUS>`` near the
    top of the file (whole-file scan; live MISSION.md uses these literal
    labels per v9.3-dogfood mission). Returns {} if missing.

    Tolerant: missing MISSION.md returns {}; partial matches return only the
    found keys (e.g. status without id).
    """
    path = mission_dir / "MISSION.md"
    if not path.is_file():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}
    out: dict[str, Any] = {}
    id_match = re.search(
        r"\*\*Mission ID:\*\*\s*`?(?P<id>[^`\n]+?)`?\s*$",
        text,
        re.MULTILINE,
    )
    if id_match:
        out["id"] = id_match.group("id").strip()
    status_match = re.search(r"\*\*Status:\*\*\s+(?P<status>\S+)", text)
    if status_match:
        out["status"] = status_match.group("status").strip()
    return out


def _read_mission_events_tail(
    mission_dir: Path, limit: int = 50
) -> list[dict[str, Any]]:
    """Return last `limit` entries from `<mission>/.mission-events`, newest-first.

    Format-tolerant: each line is parsed as JSON when possible (per the v9.3
    spec); free-form text lines fall back to ``{"raw": "<line>"}`` so the
    live v9.2/v9.3 file (which today writes text like
    ``2026-05-19T21:24Z INIT->PHASE-PLAN by orchestrator -- ...``) still
    surfaces in the dashboard. Missing file -> [].
    """
    events_path = mission_dir / ".mission-events"
    if not events_path.is_file():
        return []
    try:
        raw = events_path.read_text(errors="replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    tail = lines[-limit:]
    out: list[dict[str, Any]] = []
    for ln in tail:
        try:
            obj = json.loads(ln)
            if isinstance(obj, dict):
                out.append(obj)
                continue
        except (ValueError, TypeError):
            pass
        out.append({"raw": ln})
    out.reverse()  # newest-first
    return out


# ---------------------------------------------------------------------------
# Lifespan helpers
# ---------------------------------------------------------------------------


async def _df_watchdog(mission_dir: Path) -> None:
    """Background task: exit 12 if disk free < 50 MB at mission_dir.

    Runs every 60 seconds. Designed to be run as an asyncio task inside the
    lifespan context manager; cancelled on server shutdown.
    """
    while True:
        await asyncio.sleep(60)
        stat = shutil.disk_usage(mission_dir)
        if stat.free < 50 * 1024 * 1024:  # 50 MB
            print(
                f"disk free < 50MB at {mission_dir}: {stat.free} bytes",
                file=sys.stderr,
            )
            sys.exit(12)


# ---------------------------------------------------------------------------
# V9 M2 — contract validation
# ---------------------------------------------------------------------------


def _validate_contract(app: FastAPI, contract_path: Path) -> None:
    """V9 M2 — assert declared routes match registered routes.

    Raises RuntimeError if a contract-declared route isn't registered.
    Warns (non-fatal) if a registered route isn't declared. The introspect
    endpoint is excluded from both sides.
    """
    import warnings

    from .contract_loader import load_contract

    if not contract_path.exists():
        warnings.warn(
            f"api-contract.md not found at {contract_path} — skipping validation"
        )
        return

    contract = load_contract(contract_path)
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods and path.startswith("/api/v1/"):
            for method in methods:
                # HEAD is auto-added for GET; ignore.
                if method == "HEAD":
                    continue
                registered.add((method, path))

    declared = {(e["method"], e["path"]) for e in contract["endpoints"]}
    registered_filtered = {
        r for r in registered if not r[1].endswith("__contract_introspect__")
    }

    missing = declared - registered_filtered
    if missing:
        raise RuntimeError(
            f"BE contract violation: declared routes not registered: {missing}"
        )
    extras = registered_filtered - declared
    if extras:
        warnings.warn(f"Routes registered but not in contract: {extras}")


# ---------------------------------------------------------------------------
# SSE event generator (Task 4.2)
# ---------------------------------------------------------------------------


async def generate_lane_pane_stream_events(
    spawner: "FleetSpawner",
    lane: str,
    stream_log: Path,
    q: "asyncio.Queue[bytes]",
):
    """Yield SSE event dicts for ``GET /api/v1/lane/{lane}/pane-stream``.

    First event is base64(``\\x1bc``) (terminal-clear); second (if present)
    is base64 of the last ``TAIL_ON_CONNECT_BYTES`` of the stream log; then
    one base64-encoded event per live chunk delivered through ``q``.

    The caller (route handler) is responsible for ``spawner.subscribe`` to
    obtain ``q`` *before* constructing this generator — that way a
    ``TooManySubscribersError`` surfaces as HTTP 503, not as an SSE event
    inside an already-200 response. The ``finally`` clause unsubscribes so
    a 11th-subscriber bounce releases a slot on disconnect.
    """
    import base64

    try:
        # 1. Terminal-clear sentinel.
        yield {"data": base64.b64encode(b"\x1bc").decode("ascii")}

        # 2. Replay last TAIL_ON_CONNECT_BYTES from stream log.
        try:
            with stream_log.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                start = max(0, size - TAIL_ON_CONNECT_BYTES)
                f.seek(start)
                replay = f.read()
        except FileNotFoundError:
            replay = b""
        if replay:
            yield {"data": base64.b64encode(replay).decode("ascii")}

        # 3. Live tail loop. Cancellation propagates out of ``q.get`` when
        # sse-starlette tears the stream down on client disconnect.
        while True:
            chunk = await q.get()
            yield {"data": base64.b64encode(chunk).decode("ascii")}
    finally:
        await spawner.unsubscribe(lane, q)


# ---------------------------------------------------------------------------
# make_app factory
# ---------------------------------------------------------------------------


def make_app(
    *,
    mission_dir: Path,
    config: AppConfig | None = None,
    port: int = 8080,
) -> FastAPI:
    """Build a Megalodon UI FastAPI app bound to `mission_dir`.

    Args:
        mission_dir: Absolute path to mission directory; must exist.
        config: Optional AppConfig overrides; defaults to AppConfig().
        port: Bind port (default 8080). Used to compute allowed_origins
            unless config.allowed_origins is set.

    Returns:
        FastAPI app with the integration-test endpoint surface registered.

    Raises:
        FileNotFoundError if mission_dir does not exist.
        NotADirectoryError if mission_dir is not a directory.
    """
    mission_dir = Path(mission_dir).resolve()
    if not mission_dir.exists():
        raise FileNotFoundError(f"mission_dir does not exist: {mission_dir}")
    if not mission_dir.is_dir():
        raise NotADirectoryError(f"mission_dir is not a directory: {mission_dir}")

    cfg = config or AppConfig()
    # Δ4: port-derived allowed_origins per FE P2-D-to-C C1.
    if cfg.allowed_origins is not None:
        origins = cfg.allowed_origins
    else:
        origins = (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        )

    mc = load_mission_config(mission_dir)

    ctx = MissionContext(
        mission_dir=mission_dir,
        config=cfg,
        port=port,
        csrf_token=cfg.csrf_token,
        allowed_origins=origins,
        mission_config=mc,
        status_row_re=build_status_row_re(mc),
        task_line_re=build_task_line_re(mc),
        phase_header_re=build_phase_header_re(mc),
        session_store=auth.SessionStore(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001
        """Start the tmux fleet and watchdog; shut down on exit.

        Exit codes:
          10 — socket path too long (fatal; bypass uvicorn signal handling).
          11 — start_all timed out (fatal).
          12 — disk free < 50 MB (fatal, from watchdog task).

        Test overrides via env vars (read inside lifespan so tests can inject
        via monkeypatch.setenv before the context manager runs):
          MEGALODON_LIFESPAN_TIMEOUT_S  — float override for startup timeout.
          MEGALODON_LIFESPAN_SLEEP_S    — if set, sleep this many seconds before
                                          start_all; lets tests trigger the timeout
                                          deterministically.
        """
        # Test mode: skip fleet spawn entirely. Used by the v9.1 integration
        # tests that exercise request handlers without needing a real tmux
        # fleet. The flag also relaxes the socket-path length guard since
        # pytest tmp_path on macOS routinely exceeds 100 bytes.
        test_mode = os.environ.get("MEGALODON_LIFESPAN_TEST_MODE") == "1"
        fake_spawner = os.environ.get("MEGALODON_FAKE_SPAWNER") == "1"

        # 1. Socket path length guard.
        socket = mission_dir / ".fleet" / "tmux.sock"
        if (
            not test_mode
            and not fake_spawner
            and len(str(socket).encode()) > SOCKET_PATH_LIMIT_BYTES
        ):
            print(f"socket path too long: {socket}", file=sys.stderr)
            sys.exit(10)

        if fake_spawner:
            from .spawn_fake import FakeFleetSpawner

            app.state.spawner = FakeFleetSpawner(
                mission_dir,
                ctx.mission_config,
                get_adapter,
                socket,
            )
            app.state.startup_complete = True
            # Start PermissionWatcher in fake-spawner mode so smoke tests can
            # seed permission prompts via stream-log writes and exercise the
            # approval event path end-to-end.
            from .permission_watcher import PermissionWatcher
            from .activity_wall import ActivityWall

            _fake_lane_pairs = [
                (lane.short, lane.name) for lane in ctx.mission_config.lanes
            ]
            _perm_watcher_fake = PermissionWatcher(mission_dir, _fake_lane_pairs)
            await _perm_watcher_fake.start()
            app.state.permission_watcher = _perm_watcher_fake
            _aw_fake = ActivityWall(mission_dir, _perm_watcher_fake)
            await _aw_fake.start()
            app.state.activity_wall = _aw_fake
            from .narrator.hub import NarrativeHub

            app.state.narrative_hub = NarrativeHub()
            app.state.narrative_cache = {}
            try:
                yield
            finally:
                await _aw_fake.stop()
                await _perm_watcher_fake.stop()
            return

        if test_mode:
            app.state.spawner = None
            app.state.startup_complete = True
            applier_task: asyncio.Task | None = None
            if os.environ.get("MEGALODON_INPROCESS_APPLIER") == "1":
                # Drive the queue applier inline so v9.0 e2e specs that POST
                # mutations (challenge / reclaim / inject-task / phase-flip)
                # see them propagate to TASKS.md / STATUS.md without a separate
                # daemon process.
                from .queue.applier import Applier

                applier = Applier(mission_dir, poll_seconds=0.2)

                async def _drain_loop() -> None:
                    while True:
                        try:
                            applier.drain_once()
                        except Exception:
                            pass
                        await asyncio.sleep(0.2)

                applier_task = asyncio.create_task(_drain_loop())
            # Activity wall (test mode — no perm_watcher)
            from .activity_wall import ActivityWall

            _aw_test = ActivityWall(mission_dir, None)
            await _aw_test.start()
            app.state.activity_wall = _aw_test
            from .narrator.hub import NarrativeHub

            app.state.narrative_hub = NarrativeHub()
            app.state.narrative_cache = {}
            try:
                yield
            finally:
                await _aw_test.stop()
                if applier_task is not None:
                    applier_task.cancel()
                    try:
                        await applier_task
                    except (asyncio.CancelledError, Exception):
                        pass
            return

        # 2. Construct FleetSpawner and start_all under a timeout.
        spawner = FleetSpawner(mission_dir, ctx.mission_config, get_adapter, socket)
        app.state.spawner = spawner
        app.state.startup_complete = False

        timeout = float(
            os.environ.get(
                "MEGALODON_LIFESPAN_TIMEOUT_S", LIFESPAN_STARTUP_TIMEOUT_SECONDS
            )
        )
        sleep_s_raw = os.environ.get("MEGALODON_LIFESPAN_SLEEP_S")

        async def _start_with_optional_sleep() -> None:
            if sleep_s_raw is not None:
                await asyncio.sleep(float(sleep_s_raw))
            await spawner.start_all()

        try:
            await asyncio.wait_for(_start_with_optional_sleep(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                await spawner.stop_all()
            except Exception:
                pass
            print(
                f"lifespan startup timeout > {timeout}s",
                file=sys.stderr,
            )
            sys.exit(11)

        app.state.startup_complete = True

        # 3. Start df-check background task (every 60 s; exit 12 if < 50 MB free).
        df_task = asyncio.create_task(_df_watchdog(mission_dir))

        # 4. Start permission_watcher (v9.3): surfaces Claude REPL approval
        #    prompts from each lane's pipe-pane stream to the dashboard.
        from .permission_watcher import PermissionWatcher

        lane_pairs = [(lane.short, lane.name) for lane in ctx.mission_config.lanes]
        perm_watcher = PermissionWatcher(mission_dir, lane_pairs)
        await perm_watcher.start()
        app.state.permission_watcher = perm_watcher

        # 5a. Start activity wall: fan-in from 6 sources into ring buffer.
        from .activity_wall import ActivityWall

        activity_wall = ActivityWall(mission_dir, perm_watcher)
        await activity_wall.start()
        app.state.activity_wall = activity_wall

        # 5b. Narrative hub + cache (passive plumbing for summary board, Task 2.2).
        from .narrator.hub import NarrativeHub

        app.state.narrative_hub = NarrativeHub()
        app.state.narrative_cache = {}

        # 5c. Narrator runtime + scheduler (Task 4.1).
        from .narrator.runtime import NarratorRuntime
        from .narrator.scheduler import clamp_interval_s, run_narrator_scheduler
        from .narrator.board_state import build_lane_rows

        narrator_runtime = NarratorRuntime.from_env()
        await narrator_runtime.start()

        async def _narrator_build_rows():
            tasks_fe = parse_tasks_fe_shape(mission_dir, ctx)
            return await build_lane_rows(
                mission_dir,
                tasks_fe,
                spawner.sessions,
                spawner.adapter_resolver,
                ctx.mission_config.lanes,
            )

        _raw_interval = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
        try:
            _parsed_interval: float | None = (
                float(_raw_interval) if _raw_interval else None
            )
        except (ValueError, TypeError):
            _parsed_interval = None
        narrator_interval_s = clamp_interval_s(_parsed_interval)

        narrator_stop_event = asyncio.Event()
        narrator_scheduler_task = asyncio.create_task(
            run_narrator_scheduler(
                hub=app.state.narrative_hub,
                runtime=narrator_runtime,
                cache=app.state.narrative_cache,
                build_rows=_narrator_build_rows,
                interval_s=narrator_interval_s,
                stop_event=narrator_stop_event,
            )
        )
        app.state.narrator_runtime = narrator_runtime
        app.state.narrator_scheduler_task = narrator_scheduler_task

        # 5. Start in-process queue applier (v9.3): drains pending intents from
        #    agents' POST /api/v1/{task/claim,task/done,status/update,...} so the
        #    requests resolve to applied/rejected quickly. Without this the agents
        #    poll /api/v1/queue/<rid> forever — each retry is a curl that, in a
        #    for-loop, becomes a compound-bash prompt the operator has to approve.
        from .queue.applier import Applier

        _live_applier = Applier(mission_dir, poll_seconds=1.0)
        _have_applier_lock = _live_applier.acquire_singleton()
        if not _have_applier_lock:
            print(
                "[applier] another applier already holds the lock; in-process drain disabled",
                file=sys.stderr,
            )

        async def _live_drain_loop() -> None:
            while True:
                try:
                    _live_applier.drain_once()
                except Exception as e:  # noqa: BLE001
                    print(f"[applier] drain error: {e!r}", file=sys.stderr)
                await asyncio.sleep(1.0)

        applier_task = (
            asyncio.create_task(_live_drain_loop()) if _have_applier_lock else None
        )

        try:
            yield
        finally:
            narrator_stop_event.set()
            narrator_scheduler_task.cancel()
            try:
                await narrator_scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
            await narrator_runtime.stop()
            if applier_task is not None:
                applier_task.cancel()
                try:
                    await applier_task
                except (asyncio.CancelledError, Exception):
                    pass
            if _have_applier_lock:
                try:
                    _live_applier.release_singleton()
                except Exception:
                    pass
            await activity_wall.stop()
            await perm_watcher.stop()
            df_task.cancel()
            try:
                await spawner.stop_all()
            except Exception:
                pass

    app = FastAPI(title="Megalodon UI", version="2.0.0", lifespan=lifespan)
    app.state.megalodon = ctx  # accessible via dependency injection

    @app.middleware("http")
    async def v92_auth_gate(request: Request, call_next):  # noqa: ANN001
        """Gate v9.2-new endpoints; existing v9.1 surface stays open (CR-4 narrow)."""
        path = request.url.path
        method = request.method
        gated = (
            _V92_GATED_PATH_RE.match(path) is not None
            or (method, path) in _V92_GATED_EXACT
        )
        if gated:
            cookie = request.cookies.get(SESSION_COOKIE_NAME)
            if not ctx.session_store.validate(cookie):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "authentication required"},
                )
        return await call_next(request)

    # v9.3.5 — disable browser caching for /static/ and / (index.html).
    # The dogfood loop iterates FE JS rapidly; without no-cache headers the
    # operator's Safari serves stale app.js for hours and the symptom looks
    # like "my fix didn't work" when actually the browser never re-fetched.
    # Tradeoff: extra network round-trips per page load. Acceptable for
    # dev/dogfood; not for prod.
    @app.middleware("http")
    async def no_cache_dev_assets(request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # REPAIR-MUTATIONS-E2E-1-SSE: serve UI assets so index.html's
    # `/static/js/{store,sse,app}.js` and `/static/css/base.css` resolve.
    static_dir = ctx.config.static_dir or (
        Path(__file__).resolve().parent.parent / "ui" / "static"
    )
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir), html=True),
            name="static",
        )

    _register_routes(app, ctx)

    # V9 M2 — contract validation. Opt-in via env var until contract.md is
    # fully cross-checked across all factory callers; flip to default-on once
    # we're confident no surprise drift exists.
    if os.environ.get("M9_VALIDATE_CONTRACT") == "1":
        contract_path = (
            Path(__file__).resolve().parents[1] / "docs" / "v9" / "api-contract.md"
        )
        _validate_contract(app, contract_path)

    return app


# ---------------------------------------------------------------------------
# Request body schemas
# ---------------------------------------------------------------------------


class InjectBody(BaseModel):
    """Body schema for POST /api/v1/lane/{short}/inject."""

    text: str
    enter: bool = True


class ApprovalRuleBody(BaseModel):
    """Body schema for POST /api/v1/approval-rules."""

    pattern: str
    added_by_session: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI, ctx: MissionContext) -> None:

    @app.post("/api/v1/auth/exchange")
    async def post_auth_exchange(req: Request) -> Response:
        """Validate bearer token against ``.fleet/ui.token``; mint a session cookie.

        Plan §6.3: 200 on success with ``mui_session`` HttpOnly+SameSite=Strict
        cookie; 401 on any other outcome (invalid token, missing token, missing
        file). No body discrimination — same 401 shape for all failure paths
        so an attacker can't probe token-file presence.
        """
        try:
            body = await req.json()
        except Exception:
            body = {}
        supplied = str(body.get("token", "")) if isinstance(body, dict) else ""
        stored = auth.read_token(ctx.mission_dir / ".fleet" / "ui.token")
        if not auth.compare_token(supplied, stored):
            return JSONResponse(status_code=401, content={"detail": "invalid token"})
        sid = ctx.session_store.create()
        resp = JSONResponse(status_code=200, content={"ok": True})
        resp.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=sid,
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
            secure=False,  # localhost is plain HTTP
        )
        return resp

    @app.get("/api/v1/lane/{lane}/pane-stream")
    async def lane_pane_stream(lane: str, request: Request):  # noqa: ANN201
        """SSE stream of base64-encoded bytes from a lane's tmux pane (Task 4.2).

        Per plan §6.4 / P4:
          * First event: ``base64(b"\\x1bc")`` — terminal-clear sentinel so
            xterm.js starts from a known state.
          * Second event: ``base64(<last TAIL_ON_CONNECT_BYTES of stream_log>)``
            so a late connector sees recent context.
          * Subsequent events: each live byte chunk from the tail producer,
            base64-encoded.

        Rejection paths:
          * 401 — handled by ``v92_auth_gate`` middleware (this handler is
            only reached with a valid ``mui_session`` cookie).
          * 404 — unknown lane or spawner not yet initialized.
          * 503 — lane already has ``SSE_MAX_SUBSCRIBERS_PER_LANE`` subscribers;
            ``Retry-After: 5`` header set so the browser modal can back off.

        The event-yielding logic lives in
        ``generate_lane_pane_stream_events`` so unit tests can iterate it
        directly without an in-process HTTP transport (httpx ASGITransport
        and Starlette TestClient both buffer SSE bodies until the generator
        completes — fine for finite responses, deadlock for infinite tails).
        End-to-end SSE behaviour is covered by Playwright in Phase 5 against
        a real uvicorn process.
        """
        from sse_starlette.sse import EventSourceResponse

        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        try:
            q = await spawner.subscribe(lane)
        except TooManySubscribersError:
            return JSONResponse(
                status_code=503,
                content={"detail": "too many subscribers"},
                headers={"Retry-After": "5"},
            )

        session = spawner.get(lane)
        return EventSourceResponse(
            generate_lane_pane_stream_events(spawner, lane, session.stream_log, q)
        )

    @app.get("/api/v1/lane/{lane}/state")
    async def lane_state(lane: str):  # noqa: ANN201
        """Return runtime state for a lane (Task 6.4, CV-8).

        ``{running, exited_rc, started_utc, last_bytes_offset}``.

        The handler queries ``tmux display-message -p -F
        '#{pane_dead}|#{pane_dead_status}'`` on demand, with a 1 s TTL cache
        on ``LaneSession.pane_dead_checked_at``. No background polling —
        the cost is bounded by request rate (CV-8).
        """
        import time
        from megalodon_ui import tmux as _tmux

        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        session = spawner.get(lane)

        # Fake-spawner short-circuit: trust in-memory state, skip real tmux query.
        if hasattr(spawner, "set_pane_dead"):
            pass
        else:
            # 1 s TTL cache for the pane-dead probe.
            now = time.monotonic()
            if now - session.pane_dead_checked_at >= 1.0:
                dead, status = await _tmux.display_message_pane_dead(
                    spawner.socket, session.name
                )
                session.pane_dead_checked_at = now
                if dead:
                    session.exited_rc = status
                    session.running = False

        # Best-effort byte count — stream log may not exist in degenerate tests.
        try:
            last_bytes_offset = session.stream_log.stat().st_size
        except (OSError, FileNotFoundError):
            last_bytes_offset = 0

        return JSONResponse(
            content={
                "running": bool(session.running) and session.exited_rc is None,
                "exited_rc": session.exited_rc,
                "started_utc": getattr(session, "started_utc", None),
                "last_bytes_offset": last_bytes_offset,
            }
        )

    @app.post("/api/v1/lane/{lane}/followup")
    async def lane_followup(lane: str, request: Request):  # noqa: ANN201
        """Respawn a lane's tmux pane under a new follow-up prompt (Task 6.2).

        Per plan §6.4: body `{prompt: str, model?: str}` → resolves the lane's
        adapter, calls `adapter.build_followup_argv(prompt, prior_session_id=...,
        model=..., cwd=...)`, then calls `spawner.respawn(lane, argv, env)`.
        Returns 202 immediately; the new session id is discovered
        asynchronously by `spawner.respawn` (P6.3) and persisted to
        ``<mission>/.fleet/<short>.session.txt`` (CV-5).

        Rejection paths:
          * 401 — middleware (handled before this body runs).
          * 404 — unknown lane or spawner not initialized.
          * 422 — missing or whitespace-only prompt.
        """
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422, content={"detail": "invalid JSON body"}
            )
        prompt_raw = body.get("prompt") if isinstance(body, dict) else None
        if not isinstance(prompt_raw, str) or not prompt_raw.strip():
            return JSONResponse(
                status_code=422,
                content={"detail": "prompt is required and must be non-empty"},
            )
        prompt = prompt_raw

        lane_cfg = None
        for lc in ctx.mission_config.lanes:
            if lc.short == lane:
                lane_cfg = lc
                break
        if lane_cfg is None:
            return JSONResponse(
                status_code=404, content={"detail": f"no lane config for {lane}"}
            )

        model_override = body.get("model") if isinstance(body, dict) else None
        model = (
            model_override
            if isinstance(model_override, str) and model_override.strip()
            else lane_cfg.harness.model
        )

        adapter = spawner.adapter_resolver(lane_cfg.harness.cli)
        session = spawner.get(lane)
        argv, env = adapter.build_followup_argv(
            prompt,
            prior_session_id=session.session_id,
            model=model,
            cwd=spawner.mission_dir,
        )

        await spawner.respawn(lane, argv, env)
        return JSONResponse(
            status_code=202,
            content={"lane": lane, "status": "respawned"},
        )

    # Per-lane rate-limit state for /inject. Keyed by lane short-code; each
    # value is a deque of UTC epoch floats for calls within the last 60 s.
    _inject_rl: dict[str, collections.deque] = {}
    _INJECT_RL_WINDOW = 60.0
    _INJECT_RL_MAX = 10
    _INJECT_TEXT_LIMIT = 16384  # bytes

    @app.post("/api/v1/lane/{short}/inject")
    async def lane_inject(short: str, body: InjectBody, request: Request):  # noqa: ANN201
        """Inject keystrokes into a lane's tmux pane.

        Body: ``{text: str, enter: bool}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate, handled before this handler runs).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — unknown lane or spawner not initialized.
          * 413 — text exceeds 16384 bytes (UTF-8 encoded).
          * 429 — rate limit exceeded (10 calls / 60 s per lane).
          * 500 — tmux send-keys failed.

        On success: calls ``tmux.send_keys`` and appends a JSON audit-log line
        to ``.fleet/inject-log-YYYY-MM-DD.jsonl`` (UTC date, SHA-256 of text).
        Returns 202 Accepted with ``{ok: true}``.
        """
        # CSRF verification (timing-safe compare per QA T1.3 follow-up)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        # Lane / spawner resolution
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or short not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {short}"}
            )

        # Text size guard
        text_bytes = body.text.encode("utf-8")
        if len(text_bytes) > _INJECT_TEXT_LIMIT:
            return JSONResponse(
                status_code=413,
                content={"detail": f"text exceeds {_INJECT_TEXT_LIMIT} bytes"},
            )

        # Per-lane rate limit: 10 calls per 60 s
        now = time.time()
        if short not in _inject_rl:
            _inject_rl[short] = collections.deque()
        dq = _inject_rl[short]
        cutoff = now - _INJECT_RL_WINDOW
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= _INJECT_RL_MAX:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded (10 calls/60 s per lane)"},
            )
        dq.append(now)

        # Tmux keystroke injection.
        # Fake-spawner short-circuit: FakeFleetSpawner has no real tmux socket;
        # skip send_keys and fall through to the audit log.  The fake_emit
        # attribute is the canonical marker that distinguishes FakeFleetSpawner
        # from the real FleetSpawner.
        if not hasattr(spawner, "fake_emit"):
            session_name = spawner.sessions[short].name
            rc = await tmux.send_keys(
                spawner.socket, session_name, body.text, enter=body.enter
            )
            if rc != 0:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"tmux send-keys failed (rc={rc})"},
                )

        # Audit log: append JSON line to .fleet/inject-log-YYYY-MM-DD.jsonl
        fleet_dir = ctx.mission_dir / ".fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        ts_now = datetime.now(timezone.utc)
        date_str = ts_now.strftime("%Y-%m-%d")
        log_path = fleet_dir / f"inject-log-{date_str}.jsonl"
        entry = {
            "ts": ts_now.isoformat(),
            "lane": short,
            "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            "byte_count": len(text_bytes),
            "enter": body.enter,
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        return JSONResponse(status_code=202, content={"ok": True})

    @app.post("/api/v1/lane/{short}/restart-loop")
    async def lane_restart_loop(short: str, request: Request):  # noqa: ANN201
        """Restart a lane's /loop cycle using its initial_prompt.

        Body: empty ``{}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate, handled before this handler runs).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — unknown lane or spawner not initialized.
          * 409 — no initial_prompt recorded for this lane.
          * 500 — tmux send-keys failed.

        On success: calls ``tmux.send_keys`` with the lane's initial_prompt
        and appends a JSON audit-log line to ``.fleet/inject-log-YYYY-MM-DD.jsonl``
        (UTC date, SHA-256 of initial_prompt, source="restart-loop").
        Returns 202 Accepted with ``{ok: true}``.
        """
        # CSRF verification (timing-safe compare per QA T1.3 follow-up)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        # Lane / spawner resolution
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or short not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {short}"}
            )

        # Check initial_prompt exists
        session = spawner.sessions[short]
        if not session.initial_prompt:
            return JSONResponse(
                status_code=409,
                content={"detail": "no initial_prompt recorded"},
            )

        text = session.initial_prompt
        text_bytes = text.encode("utf-8")

        # Tmux keystroke injection.
        # Fake-spawner short-circuit: FakeFleetSpawner has no real tmux socket;
        # skip send_keys and fall through to the audit log.  The fake_emit
        # attribute is the canonical marker that distinguishes FakeFleetSpawner
        # from the real FleetSpawner.
        if not hasattr(spawner, "fake_emit"):
            session_name = session.name
            rc = await tmux.send_keys(spawner.socket, session_name, text, enter=True)
            if rc != 0:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"tmux send-keys failed (rc={rc})"},
                )

        # Audit log: append JSON line to .fleet/inject-log-YYYY-MM-DD.jsonl
        # Reuse the same file as inject; add source="restart-loop" to distinguish
        fleet_dir = ctx.mission_dir / ".fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        ts_now = datetime.now(timezone.utc)
        date_str = ts_now.strftime("%Y-%m-%d")
        log_path = fleet_dir / f"inject-log-{date_str}.jsonl"
        entry = {
            "ts": ts_now.isoformat(),
            "lane": short,
            "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            "byte_count": len(text_bytes),
            "enter": True,
            "source": "restart-loop",
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        return JSONResponse(status_code=202, content={"ok": True})

    @app.delete("/api/v1/fleet")
    async def delete_fleet(request: Request):  # noqa: ANN201
        """Destructive teardown — kill the tmux server + unlink bootstrap files (Task 7.1).

        Cookie-gated via ``_V92_GATED_EXACT``. Best-effort throughout:

        * ``tmux.kill_server(socket)`` — non-zero rc is tolerated (server may
          already be gone if the operator killed it manually).
        * Unlinks ``ui.token``, ``tmux.sock``, ``dashboard.url``,
          ``approval-rules.json`` from ``<mission>/.fleet/``; removes all
          ``inject-log-*.jsonl`` files (daily-rotated); ``missing_ok=True``
          keeps the call idempotent.

        After the response is sent, the surrounding lifespan sees
        ``app.state.shutdown_requested = True`` and the uvicorn process exits 0.
        """
        fleet_dir = ctx.mission_dir / ".fleet"
        socket = fleet_dir / "tmux.sock"
        try:
            await tmux.kill_server(socket)
        except FileNotFoundError:
            pass
        for name in ("ui.token", "tmux.sock", "dashboard.url", "approval-rules.json"):
            (fleet_dir / name).unlink(missing_ok=True)
        # Clean daily-rotated inject log files (glob pattern)
        for p in fleet_dir.glob("inject-log-*.jsonl"):
            p.unlink(missing_ok=True)
        request.app.state.shutdown_requested = True
        return JSONResponse(status_code=200, content={"status": "shutdown"})

    if os.environ.get("MEGALODON_FAKE_SPAWNER") == "1":

        @app.post("/api/v1/__fake__/emit")
        async def fake_emit_route(request: Request):  # noqa: ANN201
            """Test-only — fan out a byte chunk into a lane's subscriber queues.

            Registered only when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated via
            ``_V92_GATED_PATH_RE``. Body: ``{lane, data_b64}``.
            """
            import base64

            spawner = getattr(app.state, "spawner", None)
            if spawner is None or not hasattr(spawner, "fake_emit"):
                return JSONResponse(
                    status_code=404, content={"detail": "no fake spawner"}
                )
            body = await request.json()
            lane = body.get("lane")
            data_b64 = body.get("data_b64", "")
            try:
                data = base64.b64decode(data_b64)
            except Exception:
                return JSONResponse(status_code=422, content={"detail": "bad base64"})
            if lane not in spawner.sessions:
                return JSONResponse(
                    status_code=404, content={"detail": f"unknown lane {lane}"}
                )
            await spawner.fake_emit(lane, data)
            return JSONResponse(status_code=200, content={"emitted": len(data)})

        @app.post("/api/v1/__fake__/set_state")
        async def fake_set_state_route(request: Request):  # noqa: ANN201
            """Test-only — flip a lane to running=False+exited_rc=<rc>, or alive."""
            spawner = getattr(app.state, "spawner", None)
            if spawner is None or not hasattr(spawner, "set_pane_dead"):
                return JSONResponse(
                    status_code=404, content={"detail": "no fake spawner"}
                )
            body = await request.json()
            lane = body.get("lane")
            if lane not in spawner.sessions:
                return JSONResponse(
                    status_code=404, content={"detail": f"unknown lane {lane}"}
                )
            if body.get("running") is False:
                spawner.set_pane_dead(lane, int(body.get("rc", 0)))
            else:
                spawner.set_pane_alive(lane)
            return JSONResponse(status_code=200, content={"lane": lane})

        @app.post("/api/v1/__fake__/narrative")
        async def fake_narrative_inject_route(request: Request):  # noqa: ANN201
            """Test-only — seed narrative_cache and publish to narrative_hub.

            Registered only when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated via
            ``_V92_GATED_PATH_RE``. Body: ``{"lanes": {<short>: <row_payload>, ...}}``.

            Merges the supplied lanes into ``app.state.narrative_cache`` (i.e.
            ``cache[short] = row_payload`` for each), then publishes the full
            updated cache as ``{"lanes": dict(app.state.narrative_cache)}`` to
            ``app.state.narrative_hub`` — the same frame shape the real scheduler
            emits, so the board's stream→render path is exercised unchanged.

            Returns ``{"ok": true, "lanes": [<short>, ...]}``.
            """
            body = await request.json()
            incoming = body.get("lanes", {})
            if not isinstance(incoming, dict):
                return JSONResponse(
                    status_code=422, content={"detail": "lanes must be an object"}
                )
            cache = app.state.narrative_cache
            for short, row_payload in incoming.items():
                cache[short] = row_payload
            app.state.narrative_hub.publish({"lanes": dict(cache)})
            return JSONResponse(
                status_code=200,
                content={"ok": True, "lanes": list(incoming.keys())},
            )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        """Liveness + readiness probe.

        Returns 200 {"status": "ok"} once the lifespan startup completes
        (i.e., FleetSpawner.start_all() returned without error).
        Returns 503 {"status": "starting"} during startup (before the lifespan
        has set app.state.startup_complete = True).
        """
        if getattr(app.state, "startup_complete", False):
            return JSONResponse(content={"status": "ok"}, status_code=200)
        return JSONResponse(content={"status": "starting"}, status_code=503)

    @app.get("/api/status")
    async def get_status() -> JSONResponse:
        rows = parse_status(ctx.mission_dir, ctx)
        return JSONResponse(content=rows)

    @app.get("/api/findings")
    async def get_findings(severity: str | None = None, scratch: str | None = None):
        include_scratch = str(scratch).lower() in ("true", "1", "yes")
        findings = parse_findings(ctx.mission_dir, include_scratch=include_scratch)
        if severity:
            # Support CSV list of severities (e.g., "MAJOR,BLOCKING").
            wanted = {s.strip().upper() for s in severity.split(",")}
            findings = [
                f
                for f in findings
                if (str(f.get("severity", "")).strip().upper() in wanted)
            ]
        return JSONResponse(content=findings)

    @app.get(API_CONFIG)
    async def get_config():
        # FE C5: documented response shape.
        # P5.2: `v92_dashboard` is a server-runtime flag (env var
        # `MEGALODON_V92_DASHBOARD`), not a MissionConfig declaration —
        # this lets v9.0 fixtures stay v9.0 without YAML edits.
        v92_raw = os.environ.get("MEGALODON_V92_DASHBOARD", "").strip().lower()
        v92_dashboard = v92_raw in ("1", "true", "yes", "on")
        return {
            "csrf_token": ctx.csrf_token,
            "heartbeat_interval_seconds": ctx.config.heartbeat_interval_seconds,
            "poll_interval_seconds": ctx.config.poll_interval_seconds,
            "stale_threshold_seconds": ctx.config.stale_threshold_seconds,
            "allowed_origins": list(ctx.allowed_origins),
            "lanes": [lane.model_dump() for lane in ctx.mission_config.lanes],
            "phases": ctx.mission_config.phases,
            "task_id_patterns": ctx.mission_config.task_id_patterns.patterns,
            "harnesses": list({lane.harness.cli for lane in ctx.mission_config.lanes}),
            "task_sections": ctx.mission_config.task_sections,
            "v92_dashboard": v92_dashboard,
        }

    @app.post("/api/tasks")
    async def post_task(req: Request):
        body = await req.json()
        kind = body.get("kind", "").upper()
        target = body.get("target_finding", "")
        if not kind:
            raise HTTPException(status_code=422, detail="kind required")

        # Construct task entry. CHALLENGE form: `[ ] [CHALLENGE-<short>] ...`
        short_target = Path(target).stem if target else "manual"
        task_line = f"\n- [ ] [CHALLENGE-{short_target}] CHALLENGE on {target}\n"

        tasks_path = ctx.mission_dir / "TASKS.md"
        if not tasks_path.exists():
            tasks_path.write_text("# Tasks\n")
        # Append to CHALLENGE section if present, else end of file.
        text = tasks_path.read_text()
        if "## CHALLENGE TASKS" in text:
            text = text.replace(
                "## CHALLENGE TASKS",
                f"## CHALLENGE TASKS{task_line}",
                1,
            )
        else:
            text = text.rstrip("\n") + "\n" + task_line
        tasks_path.write_text(text)
        return JSONResponse(
            content={"ok": True, "task_line": task_line.strip()}, status_code=201
        )

    @app.post("/api/lanes/{lane}/reclaim")
    async def post_reclaim(lane: str):
        # Find target lane's working task from STATUS, attempt reclaim.
        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} not found")
        state = target.get("state", "")
        # Parse "working: <task_id>" if present.
        m = re.match(r"working:\s*(\S+)", state)
        if not m:
            # Nothing to reclaim — already idle.
            return Response(status_code=204)
        task_id = m.group(1)
        primitives.reclaim_or_recover(ctx.mission_dir, task_id, "orchestrator")
        return JSONResponse(content={"ok": True, "task_id": task_id})

    @app.post("/api/lanes/{lane}/signal")
    async def post_signal(lane: str, req: Request):
        body = await req.json()
        try:
            primitives.validate_signal(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        text = str(body.get("text", "")).strip()
        cite = str(body.get("cite") or body.get("evidence") or "").strip()

        # Append to STATUS.md row's Notes column (CAS-naive minimal impl).
        status_path = ctx.mission_dir / "STATUS.md"
        if not status_path.exists():
            raise HTTPException(status_code=500, detail="STATUS.md missing")
        status_text = status_path.read_text()

        # Find the target lane's row line; append a SIG token to its Notes cell.
        sig_token = f' [SIG from=orchestrator to={lane} text="{text}" cite={cite}]'
        # Simplest: append the signal text + cite to the Notes column (last cell).
        lines = status_text.splitlines(keepends=True)
        new_lines = []
        appended = False
        lane_upper = lane.upper()
        for line in lines:
            if (
                not appended
                and line.lstrip().startswith("|")
                and lane_upper in line.upper()
            ):
                # Skip header/separator rows (they don't contain agent IDs).
                if "Agent" in line or "---" in line:
                    new_lines.append(line)
                    continue
                # Insert before trailing pipe (and any whitespace/newline).
                stripped = line.rstrip("\n")
                trailing = line[len(stripped) :]
                # Find last "|" in the row to insert before it
                if stripped.endswith("|"):
                    new_line = stripped[:-1] + sig_token + " |" + trailing
                else:
                    new_line = stripped + sig_token + trailing
                new_lines.append(new_line)
                appended = True
            else:
                new_lines.append(line)
        if not appended:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} row not found")
        status_path.write_text("".join(new_lines))
        return JSONResponse(content={"ok": True}, status_code=201)

    @app.post("/api/mission/flip")
    async def post_flip(req: Request):
        body = await req.json()
        from_phase = str(body.get("from", "")).strip()
        to_phase = str(body.get("to", "")).strip()
        if not from_phase or not to_phase:
            raise HTTPException(status_code=422, detail="from and to required")
        won = primitives.try_phase_flip(
            ctx.mission_dir, from_phase, to_phase, "orchestrator"
        )
        if not won:
            raise HTTPException(
                status_code=409, detail="phase-flip lock held by another worker"
            )
        return {"ok": True, "from": from_phase, "to": to_phase}

    # Helper to call other handlers from /api/v1/* aliases.
    class _FakeReq:
        def __init__(self, body):
            self._body = body

        async def json(self):
            return self._body

    def _make_req_with_body(_original, body):
        return _FakeReq(body)

    # ----- canonical /api/v1/* surface per ui/api-contract.md -----
    # TEST P3-E is aligning the integration tests to use these per the
    # canonical contract. Bodies use the contract's field names.

    @app.get("/api/v1/status")
    async def get_v1_status():
        return {"lanes": parse_status(ctx.mission_dir, ctx)}

    @app.get("/api/v1/tasks")
    async def get_v1_tasks():
        # REPAIR-MUTATIONS-E2E-5-STATUS-VIEW (b): TASKS.md parsed into
        # phase/task tree consumed by FE `tasks.js:417,452`.
        return {"phases": parse_tasks(ctx.mission_dir, ctx)}

    @app.get(API_STATE)
    async def get_v1_state():
        # REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT: aggregate bootstrap
        # consumed by FE `sse.js:67 hydrateInitialState()` →
        # `store.js:193-217 hydrate()`. Top-level keys: status, tasks,
        # findings, signals, mission, config.
        #
        # v9.3 dogfood payload completion: state.tasks now uses
        # parse_tasks_fe_shape() which honors `task_sections` from
        # MissionConfig (so headers like ``## PHASE 1 — PLAN`` parse, not
        # only canonical ``## PHASE-PLAN``) and emits the cross-lane bucket
        # the FE expects in tasks.cross. state.signals reads signals/ dir,
        # and state.mission includes id+status (MISSION.md) + last-50
        # events (.mission-events) so mission.js can render the run header.
        mission_phase = "INIT"
        events_path = ctx.mission_dir / ".mission-events"
        if events_path.exists():
            try:
                last_line = events_path.read_text().strip().splitlines()[-1]
                # Format: "<utc> <FROM-PHASE>-><TO-PHASE> by <agent> -- ..."
                if "->" in last_line:
                    after_arrow = last_line.split("->", 1)[1]
                    mission_phase = after_arrow.split(" ", 1)[0].strip()
            except (IndexError, ValueError):
                pass

        tasks_payload = parse_tasks_fe_shape(ctx.mission_dir, ctx)
        mission_md = _read_mission_md_fields(ctx.mission_dir)
        mission_payload: dict[str, Any] = {
            "phase": mission_phase,
            "stuckFlipLock": _detect_stuck_flip_lock(ctx.mission_dir),
            "history": _parse_history_entries(ctx.mission_dir),
            "events": _read_mission_events_tail(ctx.mission_dir, limit=50),
            # lanes_online: omitted here — would require a tmux subprocess
            # against ctx's tmux socket; the FE renders the lane card grid
            # from status.lanes (parse_status) which is already accurate.
        }
        if "id" in mission_md:
            mission_payload["id"] = mission_md["id"]
        if "status" in mission_md:
            mission_payload["status"] = mission_md["status"]

        return {
            "status": {"lanes": parse_status(ctx.mission_dir, ctx)},
            "tasks": tasks_payload,
            # Include scratch findings — the FE's `filter-scratch` chip
            # toggles visibility client-side, but it can only reveal what
            # the store already has. (REPAIR-MUTATIONS-E2E-5-STATUS-VIEW
            # contract.)
            "findings": {"list": parse_findings(ctx.mission_dir, include_scratch=True)},
            "signals": {"list": parse_signals(ctx.mission_dir)},
            "claims": {"list": _list_claim_dirs(ctx.mission_dir)},
            "mission": mission_payload,
            "config": {
                "csrf_token": ctx.csrf_token,
                "poll_interval_seconds": ctx.config.poll_interval_seconds,
            },
        }

    @app.get(API_FINDINGS)
    async def get_v1_findings(
        lane: str | None = None,
        severity: str | None = None,
        task: str | None = None,
        scratch: str | None = None,
    ):
        include_scratch = str(scratch).lower() in ("true", "1", "yes")
        findings = parse_findings(ctx.mission_dir, include_scratch=include_scratch)
        if severity:
            wanted = {s.strip().upper() for s in severity.split(",")}
            findings = [
                f
                for f in findings
                if str(f.get("severity", "")).strip().upper() in wanted
            ]
        if lane:
            findings = [
                f
                for f in findings
                if str(f.get("lane", "")).strip().upper() == lane.upper()
            ]
        if task:
            findings = [
                f
                for f in findings
                if task in str(f.get("task", "")) or task in str(f.get("task-id", ""))
            ]
        return {"findings": findings}

    @app.get(API_FINDINGS + "/{filename}")
    async def get_v1_finding_detail(filename: str):
        """V9 M2 — fetch single finding body + frontmatter by filename.

        FE consumer: ui/static/pages/findings.js:528. Lazily loads body for
        the findings drawer; cached client-side under
        `findings.byFilename.<filename>`.
        """
        # Sanitize: reject path traversal.
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="invalid filename")
        path = ctx.mission_dir / "findings" / filename
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="finding not found")
        text = path.read_text()
        frontmatter = _parse_yaml_frontmatter(text)
        # Body is everything after the closing `---` line; fall back to whole
        # text if there's no frontmatter.
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end >= 0:
                body = text[end + 4 :].lstrip("\n")
        return {"filename": filename, "body": body, "frontmatter": frontmatter}

    @app.post(API_SIGNAL)
    async def post_v1_signal(req: Request):
        """V9 M1.5: now 202-async via queue.

        Routes the signal into the target lane's STATUS row notes via
        STATUS_UPDATE intent. FE may poll /api/v1/queue/{rid}.
        """
        body = await req.json()
        # api-contract.md: {to_lane, claim, evidence}
        to_lane = str(body.get("to_lane", "")).strip()
        claim = str(body.get("claim", "")).strip()
        evidence = str(body.get("evidence", "")).strip()
        if not to_lane:
            raise HTTPException(status_code=422, detail="to_lane required")
        try:
            primitives.validate_signal({"evidence": evidence, "cite": evidence})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        # Read current row for this lane to preserve agent/state.
        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == to_lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {to_lane!r} not found")

        sig_token = (
            f'[SIG from=orchestrator to={to_lane} text="{claim}" cite={evidence}]'
        )
        new_notes = f"{target['notes']} {sig_token}".strip()
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=target["agent"],
            lane=to_lane.upper(),
            new_state=target["state"],
            new_notes=new_notes,
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "STATUS_UPDATE", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.post(API_RECLAIM)
    async def post_v1_reclaim(req: Request):
        """V9 M1.5: now 202-async via queue when there's a task to reclaim.

        If lane is already idle (no `working: <task>`), returns 204 as
        before — nothing to do.
        """
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        if not lane:
            raise HTTPException(status_code=422, detail="lane required")

        rows = parse_status(ctx.mission_dir, ctx)
        target = next((r for r in rows if r["lane"].upper() == lane.upper()), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"lane {lane!r} not found")
        m = re.match(r"working:\s*(\S+)", target.get("state", ""))
        if not m:
            return Response(status_code=204)
        # RULE 6 dispatch (per primitives.reclaim_or_recover docstring):
        #   - matching finding exists → retroactive recovery (state=idle)
        #   - no finding                → STALE-RECLAIMED + rm -rf claim dir
        # E2E spec T-A-RC-e2e asserts the row text contains "STALE-RECLAIMED"
        # after a no-finding reclaim, matching the canonical fix-large fixture.
        task_id = m.group(1)
        has_finding = (
            primitives._finding_exists_for_task(ctx.mission_dir, task_id) is not None
        )
        if has_finding:
            new_state = "idle"
            new_notes = f"retroactive recovery for {task_id} by orchestrator"
        else:
            new_state = "STALE-RECLAIMED"
            new_notes = f"reclaimed by orchestrator (no finding for {task_id})"
        # Apply the primitive side-effects synchronously (rm -rf claim dir or
        # touch done marker) so the on-disk state matches the new STATUS row.
        primitives.reclaim_or_recover(ctx.mission_dir, task_id, "orchestrator")
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=target["agent"],
            lane=lane.upper(),
            new_state=new_state,
            new_notes=new_notes,
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "STATUS_UPDATE", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.post(API_CHALLENGE)
    async def post_v1_challenge(req: Request):
        """V9 M1.5: now 202-async via queue (TASKS_INJECT)."""
        body = await req.json()
        finding = str(body.get("finding_filename", "")).strip()
        description = str(body.get("description", "")).strip()
        if not finding:
            raise HTTPException(status_code=422, detail="finding_filename required")
        short_target = Path(finding).stem
        task_id = f"CHALLENGE-{short_target}"
        rid = _qc.tasks_inject(
            ctx.mission_dir,
            agent="orchestrator",
            submitting_lane=ctx.mission_config.orchestrator_pseudo_lane,
            task_id=task_id,
            lane="A",
            description=description or f"CHALLENGE on {finding}",
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "TASKS_INJECT", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    @app.post(API_PHASE_FLIP)
    async def post_v1_phase_flip(req: Request):
        body = await req.json()
        return await post_flip(_make_req_with_body(req, body))

    @app.post(API_MISSION_STATUS)
    async def post_v1_mission_status(req: Request):
        body = await req.json()
        status = str(body.get("status", "")).strip().upper()
        if status not in ("IDLE", "ACTIVE", "DRAINING", "COMPLETE"):
            raise HTTPException(status_code=422, detail="invalid status")
        # Best-effort: update README Mission status section.
        readme = ctx.mission_dir / "README.md"
        if readme.exists():
            text = readme.read_text()
            new_text = re.sub(
                r"\*\*Current:\s*[^*]+\*\*",
                f"**Current: {status}**",
                text,
                count=1,
            )
            readme.write_text(new_text)
        return {"ok": True, "status": status}

    @app.post(API_INJECT_TASK)
    async def post_v1_inject_task(req: Request):
        """V9 M1.5: now 202-async via queue (TASKS_INJECT).

        Body: {task_text, section?}. We parse a canonical
        ``- [bracket] [LANE-X] `task-id` — description`` line; if it
        parses, route through queue. Free-form text is rejected (FE
        should use the canonical shape).
        """
        body = await req.json()
        task_text = str(body.get("task_text", "")).strip()
        if not task_text:
            raise HTTPException(status_code=422, detail="task_text required")
        m = re.match(
            r"^-?\s*(\[[^\]]+\])\s*\[LANE-([A-Z])\]\s*`([^`]+)`\s*(?:[—-]\s*(.*))?$",
            task_text,
        )
        if not m:
            raise HTTPException(
                status_code=422,
                detail="task_text must match `- [bracket] [LANE-X] `id` — desc`",
            )
        bracket, lane, task_id, desc = (
            m.group(1),
            m.group(2),
            m.group(3),
            (m.group(4) or ""),
        )
        rid = _qc.tasks_inject(
            ctx.mission_dir,
            agent="orchestrator",
            submitting_lane=ctx.mission_config.orchestrator_pseudo_lane,
            task_id=task_id,
            lane=lane,
            description=desc,
            bracket=bracket,
        )
        return JSONResponse(
            status_code=202,
            content={"request_id": rid, "intent": "TASKS_INJECT", "status": "pending"},
            headers={"Location": f"/api/v1/queue/{rid}"},
        )

    # V9 M1.5 — queue request introspection endpoint.
    @app.get("/api/v1/queue/{request_id}")
    async def get_v1_queue_status(request_id: str):
        """Return current state of a queue request submitted via M1.5
        202-async endpoints.

        Response shape: `{request_id, status, rejection_reason}` where
        status ∈ {pending, applied, rejected}.
        """
        mission = ctx.mission_dir
        if (mission / "queue" / "applied" / f"{request_id}.json").exists():
            return {
                "request_id": request_id,
                "status": "applied",
                "rejection_reason": None,
            }
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        if rejected.exists():
            reason_file = mission / "queue" / "rejected" / f"{request_id}-reason.txt"
            reason = reason_file.read_text() if reason_file.exists() else None
            return {
                "request_id": request_id,
                "status": "rejected",
                "rejection_reason": reason,
            }
        if (mission / "queue" / "pending" / f"{request_id}.json").exists():
            return {
                "request_id": request_id,
                "status": "pending",
                "rejection_reason": None,
            }
        raise HTTPException(404, "request_id not found")

    # ----- V9 M2: introspection endpoint for contract scan -----

    # ----- v9.3 permission prompts (dashboard-mediated approval) -----------

    @app.get("/api/v1/permission_prompts")
    async def list_permission_prompts():  # noqa: ANN201
        """Snapshot every lane's pending Claude REPL approval prompt.

        Cookie-gated via ``_V92_GATED_PATH_RE``. Returns ``{prompts: [...]}``
        where each entry is the JSON form of a ``PromptInfo`` (lane, command
        preview, detected_at, fingerprint). Empty list when no lane is
        currently blocked on a prompt.
        """
        watcher = getattr(app.state, "permission_watcher", None)
        if watcher is None:
            return JSONResponse(content={"prompts": []})
        return JSONResponse(
            content={"prompts": [p.to_json() for p in watcher.pending()]}
        )

    @app.post("/api/v1/permission_prompts/{lane}/respond")
    async def respond_permission_prompt(lane: str, request: Request):  # noqa: ANN201
        """Send the operator's approve/deny response to lane via tmux send-keys.

        Body: ``{"action": "approve"|"deny"}``.

        Approve → send ``1`` + Enter (selects Claude's "Yes" menu option).
        Deny    → send ``3`` + Enter (selects "No"). The watcher's pending
        state for the lane is cleared optimistically; the next poll will
        re-populate if the prompt re-appears (e.g. if the agent retries).

        Returns 202 on success, 404 if lane unknown or no prompt active,
        422 if body malformed.
        """
        watcher = getattr(app.state, "permission_watcher", None)
        spawner = getattr(app.state, "spawner", None)
        if watcher is None or spawner is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "permission watcher / spawner not initialized"},
            )
        if lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422, content={"detail": "invalid JSON body"}
            )
        action = body.get("action") if isinstance(body, dict) else None
        if action not in ("approve", "approve_remember", "deny"):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "action must be 'approve', 'approve_remember', or 'deny'"
                },
            )

        # Fake-spawner short-circuit: FakeFleetSpawner has no real tmux socket;
        # skip send_keys and fall through to clear_lane + audit.  The fake_emit
        # attribute is the canonical marker that distinguishes FakeFleetSpawner
        # from the real FleetSpawner (same pattern as inject and restart-loop).
        if not hasattr(spawner, "fake_emit"):
            from . import tmux

            # Claude REPL menu: 1=Yes, 2=Yes-and-remember-pattern, 3=No
            keys = {"approve": "1", "approve_remember": "2", "deny": "3"}[action]
            session_name = spawner.sessions[lane].name
            rc = await tmux.send_keys(spawner.socket, session_name, keys)
            if rc != 0:
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"tmux send-keys failed (rc={rc})"},
                )
        watcher.clear_lane(lane, action=action)
        return JSONResponse(status_code=202, content={"action": action, "lane": lane})

    # ----- v9.4 stale-lane detection ----------------------------------------

    @app.get("/api/v1/lanes/stale")
    async def get_stale_lanes():  # noqa: ANN201
        """Return lanes that have been silent for ≥ 900 s and are NOT pending approval.

        Cookie-gated via ``_V92_GATED_PATH_RE``. Cached for 5 s (serves
        concurrent operator polls without recomputing).

        Response shape::

            {
              "stale_lanes": [
                {"lane": "A", "silent_seconds": 1234.5,
                 "pending_approval": false, "last_activity_source": "stream-log"},
                ...
              ],
              "checked_at_utc": "2026-05-20T15:00:00+00:00"
            }

        ``last_activity_source`` is one of ``"status-md"``, ``"stream-log"``,
        ``"applier-log"``, or ``"none"`` (when all data sources are missing for
        the lane).

        ``silent_seconds`` is null when no source provided any timestamp (the
        lane is treated as infinitely stale).
        """
        now_mono = time.monotonic()
        app_key = id(app)
        cached = _stale_cache.get(app_key)
        # Use cache if it exists, is fresh, and there are no pending test overrides.
        # Pending overrides trigger fresh computation.
        override_count_before = len(_TEST_STALE_OVERRIDES)
        if (
            cached is not None
            and (now_mono - cached["computed_mono"]) < _STALE_CACHE_TTL_SECONDS
            and override_count_before == 0
        ):
            return JSONResponse(content=cached["response"])

        watcher = getattr(app.state, "permission_watcher", None)
        lane_rows = parse_status(ctx.mission_dir, ctx)
        response = _compute_stale_response(
            ctx.mission_dir,
            lane_rows,
            ctx.mission_config,
            watcher,
        )
        override_count_after = len(_TEST_STALE_OVERRIDES)
        # Cache only if no overrides were consumed in this computation.
        # If any were consumed (override_count_after < override_count_before),
        # don't cache so the next call recomputes with the override gone.
        if override_count_after >= override_count_before:
            _stale_cache[app_key] = {"response": response, "computed_mono": now_mono}
        else:
            # Clear cache so next call recomputes.
            _stale_cache.pop(app_key, None)
        return JSONResponse(content=response)

    if os.environ.get("MEGALODON_FAKE_SPAWNER") == "1":

        @app.post("/api/v1/_test/stale_override")
        async def post_stale_override(request: Request):  # noqa: ANN201
            """Test-only — populate _TEST_STALE_OVERRIDES for the next stale check.

            Registered ONLY when ``MEGALODON_FAKE_SPAWNER=1``. Cookie-gated via
            ``_V92_GATED_PATH_RE``. Query params: ``lane`` (str), ``seconds``
            (float). Body: empty or `{}`. CSRF-protected via ``X-CSRF-Token``.

            On success: sets ``_TEST_STALE_OVERRIDES[lane] = seconds`` and
            returns 200 with ``{ok: true, lane, seconds}``. The next call to
            ``GET /api/v1/lanes/stale`` will pop this override and use it as
            the ``silent_seconds`` for the lane (one-shot).

            Rejection paths:
              * 401 — middleware (cookie gate, handled before this handler runs).
              * 403 — missing or mismatched X-CSRF-Token header.
              * 422 — missing lane, missing seconds, or seconds not a valid float.
            """
            # CSRF verification (timing-safe compare per T1.3)
            csrf = request.headers.get("X-CSRF-Token", "")
            if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF token missing or invalid"},
                )

            # Parse query params
            lane = request.query_params.get("lane", "").strip()
            seconds_str = request.query_params.get("seconds", "").strip()

            if not lane:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "lane query param is required"},
                )
            if not seconds_str:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "seconds query param is required"},
                )

            try:
                seconds = float(seconds_str)
            except ValueError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": f"seconds must be a valid float, got {seconds_str!r}"
                    },
                )

            # Set the override (one-shot, consumed by next GET /api/v1/lanes/stale).
            # Clear the stale-lanes cache so the next GET request recomputes
            # with the new override applied.
            _TEST_STALE_OVERRIDES[lane] = seconds
            _stale_cache.pop(id(app), None)

            return JSONResponse(
                status_code=200,
                content={"ok": True, "lane": lane, "seconds": seconds},
            )

    # ----- v9.3 agent-side queue endpoints (all shared-doc mutations) ------
    #
    # The v9 protocol previously had agents direct-edit TASKS.md / STATUS.md /
    # HISTORY.md via the Edit tool and direct mkdir/rm. That created a race
    # surface against operator-side queued mutations. v9.3 closes the loop:
    # every shared-doc mutation — agent OR operator — routes through these
    # endpoints, which call queue_client and hit the same in-process applier.
    # No more direct file edits to group docs.
    #
    # Default mode is asynchronous (202 + Location header to /api/v1/queue/<rid>).
    # Pass ``?wait=true`` to block until the applier resolves the intent (or
    # ~5s elapses) — this collapses the agent's "POST then poll-in-a-for-loop"
    # pattern into a single curl that returns the final {status, ...}. Without
    # wait=true the agent has to write compound-bash for the poll, which trips
    # the static allowlist matcher and prompts the operator. wait=true is the
    # only reason any agent calling these endpoints from a /loop tick should
    # ever need more than one curl per intent.

    async def _wait_for_resolution(
        request_id: str,
        *,
        timeout_s: float = 5.0,
        poll_s: float = 0.15,
    ) -> dict:
        """Block up to ``timeout_s`` for ``request_id`` to land in applied/
        rejected. Returns the same shape as GET /api/v1/queue/{rid}.
        Falls back to ``{status: pending}`` if the applier hasn't resolved
        in time — the agent can then issue a single follow-up GET if it cares.
        """
        deadline = asyncio.get_event_loop().time() + max(0.0, timeout_s)
        mission = ctx.mission_dir
        applied = mission / "queue" / "applied" / f"{request_id}.json"
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        reason_file = mission / "queue" / "rejected" / f"{request_id}-reason.txt"
        while True:
            if applied.exists():
                return {
                    "request_id": request_id,
                    "status": "applied",
                    "rejection_reason": None,
                }
            if rejected.exists():
                reason = reason_file.read_text() if reason_file.exists() else None
                return {
                    "request_id": request_id,
                    "status": "rejected",
                    "rejection_reason": reason,
                }
            if asyncio.get_event_loop().time() >= deadline:
                return {
                    "request_id": request_id,
                    "status": "pending",
                    "rejection_reason": None,
                }
            await asyncio.sleep(poll_s)

    def _wait_param(req: Request) -> bool:
        return str(req.query_params.get("wait", "")).lower() in ("1", "true", "yes")

    def _queue_response(rid: str, intent: str, wait: bool):
        """Build the response for a queue endpoint. If ``wait`` is True, block
        for resolution and return 200 with the final status; otherwise 202.

        The async (202) shape is preserved bit-for-bit for existing callers
        (FE poll loop, integration tests, the legacy v9 protocol). Only when
        the caller opts in with ``?wait=true`` does it switch to synchronous.
        """

        async def _build():
            if not wait:
                return JSONResponse(
                    status_code=202,
                    content={"request_id": rid, "intent": intent, "status": "pending"},
                    headers={"Location": f"/api/v1/queue/{rid}"},
                )
            result = await _wait_for_resolution(rid)
            status = result.get("status", "pending")
            http_code = (
                200 if status == "applied" else (409 if status == "rejected" else 202)
            )
            return JSONResponse(
                status_code=http_code,
                content={
                    "request_id": rid,
                    "intent": intent,
                    "status": status,
                    "rejection_reason": result.get("rejection_reason"),
                },
                headers={"Location": f"/api/v1/queue/{rid}"},
            )

        return _build()

    @app.post("/api/v1/task/claim")
    async def post_v1_task_claim(req: Request):  # noqa: ANN201
        """Claim a task atomically via the queue.

        Body: ``{"lane": "A", "task_id": "P1-A", "agent": "agent-xxxx"}``.
        Queues a TASKS_BRACKET intent that rewrites the task's bracket to
        ``[claimed: <agent> @ <UTC>]``. The applier checks for prior claim
        and rejects if another agent already claimed.

        Default: 202 + Location: /api/v1/queue/<rid> (async, poll for outcome).
        ``?wait=true``: block up to ~5s for resolution and return the final
        status directly (200 applied / 409 rejected / 202 still-pending).
        """
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and task_id and agent):
            raise HTTPException(status_code=422, detail="lane, task_id, agent required")
        rid = _qc.task_claim(ctx.mission_dir, agent=agent, lane=lane, task_id=task_id)
        return await _queue_response(rid, "TASKS_BRACKET", _wait_param(req))

    @app.post("/api/v1/task/done")
    async def post_v1_task_done(req: Request):  # noqa: ANN201
        """Mark a task done via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and task_id and agent):
            raise HTTPException(status_code=422, detail="lane, task_id, agent required")
        rid = _qc.task_done(ctx.mission_dir, agent=agent, lane=lane, task_id=task_id)
        return await _queue_response(rid, "TASKS_BRACKET", _wait_param(req))

    @app.post("/api/v1/status/update")
    async def post_v1_status_update(req: Request):  # noqa: ANN201
        """Update a lane row in STATUS.md via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        if not (lane and agent):
            raise HTTPException(status_code=422, detail="lane and agent required")
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            new_state=body.get("new_state"),
            new_utc=body.get("new_utc"),
            new_notes=body.get("new_notes"),
        )
        return await _queue_response(rid, "STATUS_UPDATE", _wait_param(req))

    @app.post("/api/v1/history/append")
    async def post_v1_history_append(req: Request):  # noqa: ANN201
        """Append a completion entry to HISTORY.md via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        task_id = str(body.get("task_id", "")).strip()
        finding_path = str(body.get("finding_path", "")).strip()
        severity = str(body.get("severity", "INFO")).strip().upper()
        if not (lane and agent and task_id and finding_path):
            raise HTTPException(
                status_code=422,
                detail="lane, agent, task_id, finding_path required",
            )
        rid = _qc.history_append(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            task_id=task_id,
            finding_path=finding_path,
            severity=severity,
        )
        return await _queue_response(rid, "HISTORY_APPEND", _wait_param(req))

    @app.post("/api/v1/mission-event")
    async def post_v1_mission_event(req: Request):  # noqa: ANN201
        """Append an event to .mission-events via the queue. ``?wait=true`` for sync."""
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        agent = str(body.get("agent", "")).strip()
        event_text = str(body.get("event_text", "")).strip()
        if not (lane and agent and event_text):
            raise HTTPException(
                status_code=422,
                detail="lane, agent, event_text required",
            )
        rid = _qc.mission_event(
            ctx.mission_dir,
            agent=agent,
            lane=lane,
            line=event_text,
        )
        return await _queue_response(rid, "MISSION_EVENT_APPEND", _wait_param(req))

    # ----- v9.3 operator feedback queue (file-backed) ----------------------

    @app.post("/api/v1/lane/{lane}/feedback")
    async def post_lane_feedback(lane: str, request: Request):  # noqa: ANN201
        """Append an operator message to ``feedback/<LANE_NAME>.md``.

        Cookie-gated. Body: ``{"message": str}``. The launch-file template
        instructs each agent to read ``feedback/<LANE>.md`` at the start of
        every /loop iteration and act on any unprocessed entries. Format is
        Markdown with a timestamped H2 per message so the agent can diff
        what's new since the last iteration.

        Returns 202 on append, 404 for unknown lane, 422 for empty message.
        """
        spawner = getattr(app.state, "spawner", None)
        if spawner is None or lane not in spawner.sessions:
            return JSONResponse(
                status_code=404, content={"detail": f"unknown lane {lane}"}
            )
        lane_cfg = None
        for lc in ctx.mission_config.lanes:
            if lc.short == lane:
                lane_cfg = lc
                break
        if lane_cfg is None:
            return JSONResponse(
                status_code=404, content={"detail": f"no config for {lane}"}
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422, content={"detail": "invalid JSON body"}
            )
        msg = body.get("message") if isinstance(body, dict) else None
        if not isinstance(msg, str) or not msg.strip():
            return JSONResponse(
                status_code=422,
                content={"detail": "message required (non-empty string)"},
            )
        feedback_dir = ctx.mission_dir / "feedback"
        feedback_dir.mkdir(parents=True, exist_ok=True)
        path = feedback_dir / f"{lane_cfg.name}.md"
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts} — operator\n\n{msg.strip()}\n")
        return JSONResponse(
            status_code=202,
            content={"lane": lane, "lane_name": lane_cfg.name, "appended_at": ts},
        )

    @app.get("/api/v1/__contract_introspect__")
    async def contract_introspect():
        """V9 M2 — list registered routes for contract scan cross-check.

        Returns only /api/v1/* routes. Not part of public contract (declared
        with leading double-underscore by convention; contract_scan.py
        special-cases it).
        """
        seen: set[tuple[str, str]] = set()
        for r in app.routes:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None)
            if not path or not methods:
                continue
            if not path.startswith("/api/v1/"):
                continue
            if path.endswith("__contract_introspect__"):
                continue
            for method in methods:
                if method == "HEAD":
                    continue
                seen.add((method, path))
        return {"registered": sorted([[m, p] for (m, p) in seen])}

    # ----- activity wall endpoints --------------------------------------------

    _AW_LIMIT_MAX = 500
    _AW_LIMIT_MIN = 1
    _AW_LIMIT_DEFAULT = 100

    @app.get("/api/v1/activity-wall/snapshot")
    async def activity_wall_snapshot(request: Request):  # noqa: ANN201
        """Return recent activity-wall events as JSON, newest-first.

        Cookie-gated via ``_V92_GATED_PATH_RE``.

        Query params
        ------------
        limit : int, default 100
            Number of events to return.  Silently clipped to [1, 500];
            callers asking for > 500 receive 500 events (no 400 error).

        Response shape::

            {
              "events": [
                {
                  "type": "finding"|"signal"|"history"|"queue"
                         |"inject"|"restart-loop"|"approval",
                  "lane": "A" | null,
                  "ts": "2026-05-20T15:00:00Z",
                  "summary": "...",
                  "payload": { ... source-specific fields ... }
                },
                ...
              ]
            }

        Events are newest-first. The list may be empty if no events have been
        ingested since server startup.
        """
        try:
            raw = request.query_params.get("limit", str(_AW_LIMIT_DEFAULT))
            limit = int(raw)
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"detail": "limit must be an integer"},
            )
        # Silently clip to valid range.
        limit = max(_AW_LIMIT_MIN, min(_AW_LIMIT_MAX, limit))

        wall = getattr(app.state, "activity_wall", None)
        if wall is None:
            return JSONResponse(content={"events": []})
        return JSONResponse(content={"events": wall.snapshot(limit)})

    @app.get("/api/v1/activity-wall")
    async def activity_wall_sse(request: Request):  # noqa: ANN201
        """SSE stream of NEW activity-wall events as they arrive.

        Cookie-gated via ``_V92_GATED_PATH_RE``. Emits no backlog — the client
        must hydrate history via ``GET /api/v1/activity-wall/snapshot`` first.

        Each SSE data payload is a JSON-encoded event dict::

            data: {"type": ..., "lane": ..., "ts": ..., "summary": ..., "payload": ...}

        On client disconnect the per-connection asyncio.Queue is removed from
        the subscriber list and eligible for GC.
        """
        from sse_starlette.sse import EventSourceResponse

        wall = getattr(app.state, "activity_wall", None)
        if wall is None:
            return JSONResponse(
                status_code=503, content={"detail": "activity wall not initialized"}
            )

        q = wall.subscribe()

        async def _event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {"data": json.dumps(event)}
                    except asyncio.TimeoutError:
                        # Keep-alive: send a comment so the connection stays open.
                        yield {"comment": "ka"}
            finally:
                wall.unsubscribe(q)

        return EventSourceResponse(_event_generator())

    # ----- summary board narrative endpoints (Task 2.4) -----------------------

    @app.get("/api/v1/narrative")
    async def narrative_snapshot(request: Request):  # noqa: ANN201
        """Return the current per-lane narrative cache.

        Cookie-gated via ``_V92_GATED_PATH_RE``. Returns the full cache map
        keyed by lane short-name.  ``now.phrase`` may be null when no narrator
        response has arrived yet; all other deterministic fields (last, now,
        goal, state) are always present for each cached lane.

        Response shape::

            {"lanes": {"A": {"last": ..., "now": {...}, "goal": ..., "state": ...}, ...}}
        """
        return JSONResponse(content={"lanes": dict(app.state.narrative_cache)})

    @app.get("/api/v1/narrative-stream")
    async def narrative_stream(request: Request):  # noqa: ANN201
        """SSE stream of narrative payload updates as they are published.

        Cookie-gated via ``_V92_GATED_PATH_RE``.

        On connect:
        1. Subscribe to the NarrativeHub fan-out queue.
        2. Immediately emit the current cache snapshot as the first SSE frame
           so the client has a baseline without a separate REST round-trip.
        3. Drain the queue, emitting each published payload as an SSE event.
        4. On disconnect (or generator exit): unsubscribe the queue.

        Each SSE data payload is a JSON-encoded dict.  The initial frame has
        the shape ``{"lanes": <cache snapshot>}``; subsequent frames are
        whatever the scheduler publishes (per-lane payload dicts).

        On client disconnect the per-connection asyncio.Queue is removed from
        the hub's subscriber list and eligible for GC.
        """
        from sse_starlette.sse import EventSourceResponse

        hub = getattr(app.state, "narrative_hub", None)
        if hub is None:
            return JSONResponse(
                status_code=503, content={"detail": "narrative hub not initialized"}
            )

        q = hub.subscribe()
        # Snapshot the cache at subscribe time (before any concurrent publish).
        initial = dict(app.state.narrative_cache)

        async def _event_generator():
            try:
                # Initial frame: current cache so client can hydrate immediately.
                yield {"data": json.dumps({"lanes": initial})}
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        payload = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield {"data": json.dumps(payload)}
                    except asyncio.TimeoutError:
                        # Keep-alive: send a comment so the connection stays open.
                        yield {"comment": "ka"}
            finally:
                hub.unsubscribe(q)

        return EventSourceResponse(_event_generator())

    # ----- v9.4 approval-rules (T3.1) ----------------------------------------
    #
    # Persistence: .fleet/approval-rules.json — a flat JSON list.
    # No schema version field; §2 non-goals explicitly exclude schema versioning.
    # Each entry: {pattern: str, added_at_utc: ISO8601, added_by_session: str}
    #
    # Dedup policy: POST with an already-existing pattern returns 200 with the
    # existing entry (not 409) — idempotent for operator retry safety.
    #
    # Corrupt-file policy: GET returns {rules: []} with a WARNING log line rather
    # than 500 — a corrupt file should not hard-block the dashboard.
    #
    # File writes are atomic: write to a .tmp sibling then os.replace() to avoid
    # partial writes on crash.

    _APPROVAL_RULES_FILE = ".fleet/approval-rules.json"

    def _approval_rules_path() -> Path:
        return ctx.mission_dir / _APPROVAL_RULES_FILE

    def _read_approval_rules() -> list[dict]:
        """Read the approval-rules file; return [] if missing or corrupt."""
        import logging as _logging

        path = _approval_rules_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _logging.getLogger(__name__).warning(
                "approval-rules: corrupt file %s (%s) — returning empty list",
                path,
                exc,
            )
            return []

    def _write_approval_rules(rules: list[dict]) -> None:
        """Atomically write rules list to .fleet/approval-rules.json."""
        path = _approval_rules_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @app.post("/api/v1/approval-rules")
    async def post_approval_rule(body: ApprovalRuleBody, request: Request):  # noqa: ANN201
        """Add an approval-rule pattern.

        Body: ``{pattern: str, added_by_session: str}``
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate).
          * 403 — missing or mismatched X-CSRF-Token header.

        Dedup: if an entry with the same ``pattern`` already exists, return
        200 with the existing entry (idempotent — operator retry safe).
        Otherwise append and return 201 with the new entry.
        """
        # CSRF verification (timing-safe compare per T1.3 pattern)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        rules = _read_approval_rules()
        for existing in rules:
            if existing.get("pattern") == body.pattern:
                # Dedup hit — return 200 with existing entry
                return JSONResponse(status_code=200, content=existing)

        entry: dict = {
            "pattern": body.pattern,
            "added_at_utc": datetime.now(timezone.utc).isoformat(),
            "added_by_session": body.added_by_session,
        }
        rules.append(entry)
        _write_approval_rules(rules)
        return JSONResponse(status_code=201, content=entry)

    @app.get("/api/v1/approval-rules")
    async def get_approval_rules():  # noqa: ANN201
        """Return all approval-rule patterns.

        Cookie-gated via ``_V92_GATED_PATH_RE``.

        Response shape: ``{rules: [{pattern, added_at_utc, added_by_session}, ...]}``

        Returns ``{rules: []}`` if the file is missing or corrupt (see
        corrupt-file policy comment above).
        """
        return JSONResponse(content={"rules": _read_approval_rules()})

    @app.delete("/api/v1/approval-rules")
    async def delete_approval_rule(request: Request):  # noqa: ANN201
        """Remove an approval-rule pattern.

        Query param: ``pattern`` (exact match, required).
        Required header: ``X-CSRF-Token`` — must match ``ctx.csrf_token``.

        Rejection paths:
          * 401 — middleware (cookie gate).
          * 403 — missing or mismatched X-CSRF-Token header.
          * 404 — pattern not found in the rules list.

        On success: removes the entry, writes the file atomically, returns 204.
        """
        # CSRF verification (timing-safe compare per T1.3 pattern)
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not secrets.compare_digest(csrf, ctx.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or invalid"},
            )

        pattern = request.query_params.get("pattern")
        if not pattern:
            return JSONResponse(
                status_code=400,
                content={"detail": "query param 'pattern' is required"},
            )

        rules = _read_approval_rules()
        new_rules = [r for r in rules if r.get("pattern") != pattern]
        if len(new_rules) == len(rules):
            return JSONResponse(
                status_code=404,
                content={"detail": f"pattern not found: {pattern}"},
            )
        _write_approval_rules(new_rules)
        return Response(status_code=204)

    @app.get("/api/v1/approval-rules/extract")
    async def get_approval_rules_extract(request: Request):  # noqa: ANN201
        """Extract an --allowedTools pattern from a raw command string.

        Query param: ``command`` (URL-encoded shell command string).
        Cookie-gated via ``_V92_GATED_PATH_RE`` (``approval-rules`` prefix).
        No CSRF required — safe GET method.

        Response: ``{pattern: str | null}``
        ``null`` is returned for compound commands, redirects, empty input, etc.
        """
        from .approval_rules import extract_pattern as _extract_pattern

        command = request.query_params.get("command", "")
        pattern = _extract_pattern(command) if command else None
        return JSONResponse(content={"pattern": pattern})

    # ----- SSE stream (MISSION exit-criterion #4 / TEST signal @19:41Z) -----

    @app.get(API_EVENTS)
    async def sse_events(request: Request):
        """Server-Sent Events stream via sse-starlette EventSourceResponse.

        Emits `sync` on connect; polls STATUS.md mtime on a 0.25s clock and
        emits `status-change` events when the file changes. Per api-contract.md
        §SSE, the canonical event types include sync, status-change,
        task-change, phase-flip, etc; this minimal-viable impl ships sync +
        status-change for MISSION exit-criterion #4.

        REPAIR-MUTATIONS-E2E-1-SSE: switched from raw StreamingResponse →
        EventSourceResponse to get per-event flush. Raw StreamingResponse
        buffered yields, breaking sub-second propagation for the file-touch
        live-update e2e test.
        """
        from sse_starlette.sse import EventSourceResponse

        status_path = ctx.mission_dir / "STATUS.md"

        def _now_iso() -> str:
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        async def event_generator():
            sync_payload = json.dumps(
                {
                    "utc": _now_iso(),
                    "mission_dir": str(ctx.mission_dir),
                }
            )
            yield {"event": SSE_SYNC, "data": sync_payload}

            try:
                last_mtime = status_path.stat().st_mtime
            except FileNotFoundError:
                last_mtime = 0.0

            # Bounded loop: 30s max, 0.25s tick. Guarantees termination even
            # if upstream client disconnect-signal is delayed (ASGI test
            # harness quirks observed during integration testing).
            check_interval = 0.25
            max_iterations = int(30.0 / check_interval)
            for _ in range(max_iterations):
                if await request.is_disconnected():
                    return
                await asyncio.sleep(check_interval)
                try:
                    current_mtime = status_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    payload = json.dumps(
                        {
                            "utc": _now_iso(),
                            "lanes": parse_status(ctx.mission_dir, ctx),
                        }
                    )
                    yield {"event": SSE_STATUS_CHANGE, "data": payload}

        return EventSourceResponse(event_generator())

    @app.get("/", response_class=HTMLResponse)
    async def index():
        # FE C2 Approach A: index.html templating.
        static_dir = ctx.config.static_dir or (
            Path(__file__).resolve().parent.parent / "ui" / "static"
        )
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return HTMLResponse(
                content=f"<html><body><h1>Megalodon UI</h1><p>Mission: {ctx.mission_dir}</p></body></html>"
            )
        html = index_path.read_text()
        # Single-token substitution per Δ4.3.
        html = html.replace("__CSRF_TOKEN__", ctx.csrf_token)
        return HTMLResponse(content=html)

    # REPAIR-MUTATIONS-E2E-2-SPA-CATCHALL: serve index.html shell for SPA
    # routes (/tasks, /findings, /mission, /signals) so client-side router
    # can take over. Declared LAST so api/* and static/* (declared earlier)
    # match first. Anchors SPEC-v2 §3-ter (agent-fec0).
    @app.get("/{spa_path:path}", response_class=HTMLResponse)
    async def spa_fallback(spa_path: str):
        if spa_path.startswith("api/") or spa_path.startswith("static/"):
            raise HTTPException(status_code=404)
        return await index()
