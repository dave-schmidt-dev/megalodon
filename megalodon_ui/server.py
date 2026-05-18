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
import os
import re
import shutil
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
    API_CHALLENGE, API_CONFIG, API_EVENTS, API_FINDINGS, API_INJECT_TASK,
    API_MISSION_STATUS, API_PHASE_FLIP, API_RECLAIM, API_SIGNAL, API_STATE,
    SSE_CLAIM_CREATE, SSE_CLAIM_DONE, SSE_FINDING_NEW, SSE_HEARTBEAT,
    SSE_HISTORY_APPEND, SSE_LAGGING, SSE_MISSION_STATUS, SSE_PHASE_FLIP,
    SSE_SIGNAL_NEW, SSE_STATUS_CHANGE, SSE_SYNC, SSE_TASK_CHANGE,
    STALE_THRESHOLD_SECONDS,
)
from ._v92_constants import LIFESPAN_STARTUP_TIMEOUT_SECONDS, SOCKET_PATH_LIMIT_BYTES
from .spawn import FleetSpawner
from .harnesses import get_adapter


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


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_status(mission_dir: Path, ctx: "MissionContext | None" = None) -> list[dict[str, Any]]:
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
    status_re = ctx.status_row_re if ctx is not None else build_status_row_re(load_mission_config(mission_dir))
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


def parse_tasks(mission_dir: Path, ctx: "MissionContext | None" = None) -> list[dict[str, Any]]:
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
        warnings.warn(f"api-contract.md not found at {contract_path} — skipping validation")
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

        # 1. Socket path length guard.
        socket = mission_dir / ".fleet" / "tmux.sock"
        if not test_mode and len(str(socket).encode()) > SOCKET_PATH_LIMIT_BYTES:
            print(f"socket path too long: {socket}", file=sys.stderr)
            sys.exit(10)

        if test_mode:
            app.state.spawner = None
            app.state.startup_complete = True
            try:
                yield
            finally:
                pass
            return

        # 2. Construct FleetSpawner and start_all under a timeout.
        spawner = FleetSpawner(mission_dir, ctx.mission_config, get_adapter, socket)
        app.state.spawner = spawner
        app.state.startup_complete = False

        timeout = float(
            os.environ.get("MEGALODON_LIFESPAN_TIMEOUT_S", LIFESPAN_STARTUP_TIMEOUT_SECONDS)
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

        try:
            yield
        finally:
            df_task.cancel()
            try:
                await spawner.stop_all()
            except Exception:
                pass

    app = FastAPI(title="Megalodon UI", version="2.0.0", lifespan=lifespan)
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

    # V9 M2 — contract validation. Opt-in via env var until contract.md is
    # fully cross-checked across all factory callers; flip to default-on once
    # we're confident no surprise drift exists.
    if os.environ.get("M9_VALIDATE_CONTRACT") == "1":
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "v9" / "api-contract.md"
        _validate_contract(app, contract_path)

    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI, ctx: MissionContext) -> None:

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
                f for f in findings
                if (str(f.get("severity", "")).strip().upper() in wanted)
            ]
        return JSONResponse(content=findings)

    @app.get(API_CONFIG)
    async def get_config():
        # FE C5: documented response shape.
        return {
            "csrf_token": ctx.csrf_token,
            "heartbeat_interval_seconds": ctx.config.heartbeat_interval_seconds,
            "poll_interval_seconds": ctx.config.poll_interval_seconds,
            "stale_threshold_seconds": ctx.config.stale_threshold_seconds,
            "allowed_origins": list(ctx.allowed_origins),
            "lanes": [l.model_dump() for l in ctx.mission_config.lanes],
            "phases": ctx.mission_config.phases,
            "task_id_patterns": ctx.mission_config.task_id_patterns.patterns,
            "harnesses": list({l.harness.cli for l in ctx.mission_config.lanes}),
            "task_sections": ctx.mission_config.task_sections,
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
            "status": {"lanes": parse_status(ctx.mission_dir, ctx)},
            "tasks": {"phases": parse_tasks(ctx.mission_dir, ctx)},
            "findings": {"list": parse_findings(ctx.mission_dir)},
            "signals": {"list": []},
            "mission": {"phase": mission_phase},
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
            findings = [f for f in findings if str(f.get("severity", "")).strip().upper() in wanted]
        if lane:
            findings = [f for f in findings if str(f.get("lane", "")).strip().upper() == lane.upper()]
        if task:
            findings = [f for f in findings if task in str(f.get("task", "")) or task in str(f.get("task-id", ""))]
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

        sig_token = f"[SIG from=orchestrator to={to_lane} text=\"{claim}\" cite={evidence}]"
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
        # Submit status_update to flip lane back to idle via queue.
        rid = _qc.status_update(
            ctx.mission_dir,
            agent=target["agent"],
            lane=lane.upper(),
            new_state="idle",
            new_notes=f"reclaimed by orchestrator",
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
        bracket, lane, task_id, desc = m.group(1), m.group(2), m.group(3), (m.group(4) or "")
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
            return {"request_id": request_id, "status": "applied",
                    "rejection_reason": None}
        rejected = mission / "queue" / "rejected" / f"{request_id}.json"
        if rejected.exists():
            reason_file = mission / "queue" / "rejected" / f"{request_id}-reason.txt"
            reason = reason_file.read_text() if reason_file.exists() else None
            return {"request_id": request_id, "status": "rejected",
                    "rejection_reason": reason}
        if (mission / "queue" / "pending" / f"{request_id}.json").exists():
            return {"request_id": request_id, "status": "pending",
                    "rejection_reason": None}
        raise HTTPException(404, "request_id not found")

    # ----- V9 M2: introspection endpoint for contract scan -----

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
            sync_payload = json.dumps({
                "utc": _now_iso(),
                "mission_dir": str(ctx.mission_dir),
            })
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
                    payload = json.dumps({
                        "utc": _now_iso(),
                        "lanes": parse_status(ctx.mission_dir, ctx),
                    })
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
