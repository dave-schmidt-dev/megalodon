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
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from . import primitives


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


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_STATUS_ROW_RE = re.compile(
    r"^\|\s*(?P<lane>[A-Z][A-Z\- ]*?)\s*\|\s*"
    r"(?P<agent>[^|]+?)\s*\|\s*"
    r"(?P<state>[^|]+?)\s*\|\s*"
    r"(?P<last_utc>[^|]+?)\s*\|\s*"
    r"(?P<notes>.*?)\s*\|\s*$",
    re.MULTILINE,
)


def parse_status(mission_dir: Path) -> list[dict[str, Any]]:
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
    rows: list[dict[str, Any]] = []
    for m in _STATUS_ROW_RE.finditer(text):
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
            is_stale = staleness_seconds > 900.0  # RULE-1: 15 min
        except (ValueError, AttributeError):
            pass
        rows.append({
            "lane": lane,
            "agent": agent,
            "state": m.group("state").strip(),
            "last_utc": last_utc,
            "notes": m.group("notes").strip(),
            "staleness_seconds": staleness_seconds,
            "is_stale": is_stale,
        })
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


_TASK_LINE_RE = re.compile(
    r"^\s*-\s*\[(?P<state_block>[^\]]*)\]\s*\[LANE-(?P<lane>[A-Z])\]\s*"
    r"`(?P<task_id>[^`]+)`\s*(?:[—-]\s*(?P<description>.*))?$",
    re.MULTILINE,
)
_PHASE_HEADER_RE = re.compile(r"^##\s+(?P<phase>PHASE[^\n]*)$", re.MULTILINE)


def parse_tasks(mission_dir: Path) -> list[dict[str, Any]]:
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
    phase_headers = list(_PHASE_HEADER_RE.finditer(text))
    phases: list[dict[str, Any]] = []
    for i, hdr in enumerate(phase_headers):
        start = hdr.end()
        end = phase_headers[i + 1].start() if i + 1 < len(phase_headers) else len(text)
        section = text[start:end]
        tasks: list[dict[str, Any]] = []
        for m in _TASK_LINE_RE.finditer(section):
            state_block = m.group("state_block").strip()
            if state_block == "" or state_block == " ":
                state = "open"
                agent = None
                utc = None
            elif state_block.startswith("done:"):
                state = "done"
                rest = state_block[len("done:"):].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip()
                utc = utc.strip()
            elif state_block.startswith("claimed:"):
                state = "claimed"
                rest = state_block[len("claimed:"):].strip()
                agent, _, utc = rest.partition("@")
                agent = agent.strip()
                utc = utc.strip()
            else:
                state = "open"
                agent = None
                utc = None
            tasks.append({
                "id": m.group("task_id").strip(),
                "lane": f"LANE-{m.group('lane')}",
                "state": state,
                "agent": agent,
                "utc": utc,
                "description": (m.group("description") or "").strip(),
            })
        phases.append({"name": hdr.group("phase").strip(), "tasks": tasks})
    return phases


def parse_findings(mission_dir: Path, *, include_scratch: bool = False) -> list[dict[str, Any]]:
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

    ctx = MissionContext(
        mission_dir=mission_dir,
        config=cfg,
        port=port,
        csrf_token=cfg.csrf_token,
        allowed_origins=origins,
    )

    app = FastAPI(title="Megalodon UI", version="2.0.0")
    app.state.megalodon = ctx  # accessible via dependency injection

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
    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI, ctx: MissionContext) -> None:

    @app.get("/api/status")
    async def get_status() -> JSONResponse:
        rows = parse_status(ctx.mission_dir)
        return JSONResponse(content=rows)

    @app.get("/api/findings")
    async def get_findings(severity: str | None = None, scratch: str | None = None):
        include_scratch = str(scratch).lower() in ("true", "1", "yes")
        findings = parse_findings(ctx.mission_dir, include_scratch=include_scratch)
        if severity:
            # Support CSV list of severities (e.g., "MAJOR,BLOCKING").
            wanted = {s.strip().upper() for s in severity.split(",")}
            findings = [
                f for f in findings
                if (str(f.get("severity", "")).strip().upper() in wanted)
            ]
        return JSONResponse(content=findings)

    @app.get("/api/v1/config")
    async def get_config():
        # FE C5: documented response shape.
        return {
            "csrf_token": ctx.csrf_token,
            "heartbeat_interval_seconds": ctx.config.heartbeat_interval_seconds,
            "poll_interval_seconds": ctx.config.poll_interval_seconds,
            "stale_threshold_seconds": ctx.config.stale_threshold_seconds,
            "allowed_origins": list(ctx.allowed_origins),
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
        return JSONResponse(content={"ok": True, "task_line": task_line.strip()}, status_code=201)

    @app.post("/api/lanes/{lane}/reclaim")
    async def post_reclaim(lane: str):
        # Find target lane's working task from STATUS, attempt reclaim.
        rows = parse_status(ctx.mission_dir)
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
        sig_token = f" [SIG from=orchestrator to={lane} text=\"{text}\" cite={cite}]"
        # Simplest: append the signal text + cite to the Notes column (last cell).
        lines = status_text.splitlines(keepends=True)
        new_lines = []
        appended = False
        lane_upper = lane.upper()
        for line in lines:
            if not appended and line.lstrip().startswith("|") and lane_upper in line.upper():
                # Skip header/separator rows (they don't contain agent IDs).
                if "Agent" in line or "---" in line:
                    new_lines.append(line)
                    continue
                # Insert before trailing pipe (and any whitespace/newline).
                stripped = line.rstrip("\n")
                trailing = line[len(stripped):]
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
            raise HTTPException(status_code=409, detail="phase-flip lock held by another worker")
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
        return {"lanes": parse_status(ctx.mission_dir)}

    @app.get("/api/v1/tasks")
    async def get_v1_tasks():
        # REPAIR-MUTATIONS-E2E-5-STATUS-VIEW (b): TASKS.md parsed into
        # phase/task tree consumed by FE `tasks.js:417,452`.
        return {"phases": parse_tasks(ctx.mission_dir)}

    @app.get("/api/v1/state")
    async def get_v1_state():
        # REPAIR-MUTATIONS-E2E-11-STATE-ENDPOINT: aggregate bootstrap
        # consumed by FE `sse.js:67 hydrateInitialState()` →
        # `store.js:193-217 hydrate()`. Top-level keys: status, tasks,
        # findings, signals, mission, config. Mirror legacy
        # `ui/server.py:916` shape (subset; signals empty until extractor
        # ported, mission.phase from .mission-events tail).
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
        return {
            "status": {"lanes": parse_status(ctx.mission_dir)},
            "tasks": {"phases": parse_tasks(ctx.mission_dir)},
            "findings": {"list": parse_findings(ctx.mission_dir)},
            "signals": {"list": []},
            "mission": {"phase": mission_phase},
            "config": {
                "csrf_token": ctx.csrf_token,
                "poll_interval_seconds": ctx.config.poll_interval_seconds,
            },
        }

    @app.get("/api/v1/findings")
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
            findings = [f for f in findings if str(f.get("severity", "")).strip().upper() in wanted]
        if lane:
            findings = [f for f in findings if str(f.get("lane", "")).strip().upper() == lane.upper()]
        if task:
            findings = [f for f in findings if task in str(f.get("task", "")) or task in str(f.get("task-id", ""))]
        return {"findings": findings}

    @app.post("/api/v1/signal")
    async def post_v1_signal(req: Request):
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
        # Delegate to /api/lanes/{lane}/signal logic
        return await post_signal(to_lane, _make_req_with_body(req, {"text": claim, "cite": evidence}))

    @app.post("/api/v1/reclaim")
    async def post_v1_reclaim(req: Request):
        body = await req.json()
        lane = str(body.get("lane", "")).strip()
        if not lane:
            raise HTTPException(status_code=422, detail="lane required")
        return await post_reclaim(lane)

    @app.post("/api/v1/challenge")
    async def post_v1_challenge(req: Request):
        body = await req.json()
        finding = str(body.get("finding_filename", "")).strip()
        description = str(body.get("description", "")).strip()
        if not finding:
            raise HTTPException(status_code=422, detail="finding_filename required")
        return await post_task(
            _make_req_with_body(req, {"kind": "CHALLENGE", "target_finding": finding, "description": description})
        )

    @app.post("/api/v1/phase-flip")
    async def post_v1_phase_flip(req: Request):
        body = await req.json()
        return await post_flip(_make_req_with_body(req, body))

    @app.post("/api/v1/mission-status")
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

    @app.post("/api/v1/inject-task")
    async def post_v1_inject_task(req: Request):
        body = await req.json()
        task_text = str(body.get("task_text", "")).strip()
        section = str(body.get("section", "CHALLENGE TASKS")).strip()
        if not task_text:
            raise HTTPException(status_code=422, detail="task_text required")
        # Validate canonical task-id syntax if line starts with "- [ ] [LANE-...]"
        tasks_path = ctx.mission_dir / "TASKS.md"
        text = tasks_path.read_text() if tasks_path.exists() else "# Tasks\n"
        injected = f"\n- {task_text}\n" if not task_text.startswith("-") else f"\n{task_text}\n"
        section_header = f"## {section}" if not section.startswith("##") else section
        if section_header in text:
            text = text.replace(section_header, section_header + injected, 1)
        else:
            text = text.rstrip("\n") + "\n" + injected
        tasks_path.write_text(text)
        return {"ok": True, "task_text": task_text}

    # ----- SSE stream (MISSION exit-criterion #4 / TEST signal @19:41Z) -----

    @app.get("/api/v1/events")
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
            sync_payload = json.dumps({
                "utc": _now_iso(),
                "mission_dir": str(ctx.mission_dir),
            })
            yield {"event": "sync", "data": sync_payload}

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
                    payload = json.dumps({
                        "utc": _now_iso(),
                        "lanes": parse_status(ctx.mission_dir),
                    })
                    yield {"event": "status-change", "data": payload}

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
