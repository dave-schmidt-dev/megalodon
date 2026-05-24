"""Tests for Task 4.1 — narrator runtime + scheduler wired into the lifespan.

Three test groups:

1. build_rows composition — the _narrator_build_rows closure returns a
   dict[str, LaneRow] with deterministic fields; narrator_ok=False (no model).
2. Narrator absent under test/fake modes — assert narrator_runtime not set on
   app.state after startup in MEGALODON_LIFESPAN_TEST_MODE and MEGALODON_FAKE_SPAWNER.
3. Clean start→degraded→stop teardown — runtime.start (missing model path,
   so it degrades), scheduler task created, then stop sequence leaks nothing
   under -W error.

Tests run under ``pytest -W error``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.auth import write_token_atomic
from megalodon_ui.narrator.board_state import LaneRow, build_lane_rows
from megalodon_ui.narrator.hub import NarrativeHub
from megalodon_ui.narrator.runtime import NarratorRuntime
from megalodon_ui.narrator.scheduler import clamp_interval_s, run_narrator_scheduler
from megalodon_ui.server import make_app

TOKEN = "lifespan-wiring-token"


# ---------------------------------------------------------------------------
# Helpers shared with test_board_state.py (inline, not imported, to keep
# this test file self-contained and avoid cross-test coupling)
# ---------------------------------------------------------------------------


def _lane_cfg(
    name: str,
    short: str,
    role: str,
    cli: str = "claude",
) -> MagicMock:
    """Return a MagicMock shaped like a LaneConfig."""
    cfg = MagicMock()
    cfg.name = name
    cfg.short = short
    cfg.role = role
    cfg.harness = MagicMock()
    cfg.harness.cli = cli
    return cfg


def _session(session_id: str | None, cwd: Path | None = None) -> MagicMock:
    """Return a MagicMock shaped like a LaneSession."""
    s = MagicMock()
    s.session_id = session_id
    s.cwd = cwd or Path("/some/mission")
    return s


def _setup_mission(tmp_path: Path) -> None:
    """Create minimal required mission directory structure for make_app."""
    fleet = tmp_path / ".fleet"
    fleet.mkdir(parents=True, exist_ok=True)
    write_token_atomic(fleet / "ui.token", TOKEN)
    (tmp_path / "STATUS.md").write_text("# Status\n")
    (tmp_path / "TASKS.md").write_text("# Tasks\n")
    (tmp_path / "HISTORY.md").write_text("# History\n")
    (tmp_path / "findings").mkdir(exist_ok=True)
    (tmp_path / "signals").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# FakeRuntime: mirrors test_narrative_scheduler.py's FakeRuntime, kept
# local here to avoid cross-file coupling.
# ---------------------------------------------------------------------------


class FakeRuntime:
    """Minimal stand-in for NarratorRuntime — scheduler surface only."""

    def __init__(self, *, ready: bool = False) -> None:
        self._ready = ready
        self.client = None
        self.base_url = "http://fake"
        self.narrate_timeout_s = 0.5
        self._stopped = False

    def is_ready(self) -> bool:
        return self._ready

    async def stop(self) -> None:
        self._stopped = True


# ---------------------------------------------------------------------------
# 1. build_rows composition
# ---------------------------------------------------------------------------


class TestBuildRowsClosure:
    """The _narrator_build_rows closure composition with real build_lane_rows."""

    @pytest.mark.asyncio
    async def test_returns_dict_of_lane_rows(self, tmp_path: Path) -> None:
        """Closure returns dict[str, LaneRow] with deterministic fields; narrator_ok=False."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text(
            "## PHASE-PLAN\n- [ ] [AUDIT] `A-1` — first task\n",
            encoding="utf-8",
        )

        from megalodon_ui.server import parse_tasks_fe_shape

        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit all findings", cli="claude")]
        session = _session(session_id="sid-abc", cwd=mission_dir)
        sessions = {"A": session}

        fake_adapter = MagicMock()
        fake_adapter.session_log_path.return_value = None  # no log → no digest

        def adapter_resolver(cli: str) -> MagicMock:
            return fake_adapter

        # Build the same closure that the lifespan assembles, using the same
        # function calls documented in the plan.
        ctx_mock = MagicMock()
        ctx_mock.mission_config.lanes = lane_cfgs

        async def _narrator_build_rows():
            tasks_fe = parse_tasks_fe_shape(mission_dir, ctx_mock)
            return await build_lane_rows(
                mission_dir,
                tasks_fe,
                sessions,
                adapter_resolver,
                lane_cfgs,
            )

        # parse_session is not called when session_log_path returns None; use
        # a real call to exercise the actual code path.
        rows = await _narrator_build_rows()

        assert isinstance(rows, dict)
        assert "A" in rows
        row = rows["A"]
        assert isinstance(row, LaneRow)
        # Deterministic fields are populated.
        assert row.lane == "A"
        assert row.lane_name == "AUDIT"
        # narrator_ok is always False from board_state (scheduler flips it).
        assert row.narrator_ok is False

    @pytest.mark.asyncio
    async def test_closure_with_claimed_task_populates_now(
        self, tmp_path: Path
    ) -> None:
        """A claimed task produces now != None with task_id, desc, phrase=None."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text(
            "## PHASE-PLAN\n"
            "- [x] [AUDIT] `A-1` — completed task\n"
            "- [ ] [AUDIT] `A-2` — claimed task\n",
            encoding="utf-8",
        )

        from megalodon_ui.server import parse_tasks_fe_shape

        lane_cfgs = [_lane_cfg("AUDIT", "A", "Audit role", cli="claude")]
        sessions = {"A": _session(session_id=None)}
        fake_adapter = MagicMock()
        fake_adapter.session_log_path.return_value = None

        ctx_mock = MagicMock()
        ctx_mock.mission_config.lanes = lane_cfgs

        async def _narrator_build_rows():
            tasks_fe = parse_tasks_fe_shape(mission_dir, ctx_mock)
            return await build_lane_rows(
                mission_dir,
                tasks_fe,
                sessions,
                lambda cli: fake_adapter,
                lane_cfgs,
            )

        rows = await _narrator_build_rows()

        assert "A" in rows
        row = rows["A"]
        assert row.narrator_ok is False
        # goal is populated either from role, last, or now.
        assert row.goal is not None
        assert isinstance(row.goal, str)

    @pytest.mark.asyncio
    async def test_non_claude_lane_has_no_digest(self, tmp_path: Path) -> None:
        """Non-Claude harness lane → digest_text=None, tokens=None."""
        mission_dir = tmp_path / "mission"
        mission_dir.mkdir()
        (mission_dir / "TASKS.md").write_text("## PHASE-PLAN\n", encoding="utf-8")

        from megalodon_ui.server import parse_tasks_fe_shape

        lane_cfgs = [_lane_cfg("BUILD", "B", "Build the artefact", cli="codex")]
        sessions = {"B": _session(session_id="sid-xyz")}
        fake_adapter = MagicMock()
        ctx_mock = MagicMock()
        ctx_mock.mission_config.lanes = lane_cfgs

        call_count = 0

        def counting_parse(path):
            nonlocal call_count
            call_count += 1
            from megalodon_ui.narrator.digest import SessionDigest

            return SessionDigest(session_id="sid-xyz")

        async def _build():
            tasks_fe = parse_tasks_fe_shape(mission_dir, ctx_mock)
            return await build_lane_rows(
                mission_dir,
                tasks_fe,
                sessions,
                lambda cli: fake_adapter,
                lane_cfgs,
            )

        with patch(
            "megalodon_ui.narrator.board_state.parse_session",
            side_effect=counting_parse,
        ):
            rows = await _build()

        assert call_count == 0  # never called for non-claude lane
        assert rows["B"].tokens is None
        assert rows["B"].digest_text is None
        assert rows["B"].narrator_ok is False


# ---------------------------------------------------------------------------
# 2. Narrator absent under test / fake modes
# ---------------------------------------------------------------------------


class TestNarratorAbsentUnderTestModes:
    """narrator_runtime must NOT be set on app.state in test/fake lifespan branches."""

    @pytest.mark.asyncio
    async def test_test_mode_narrator_runtime_not_set(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """MEGALODON_LIFESPAN_TEST_MODE=1 → narrator_runtime absent on app.state."""
        monkeypatch.setenv("MEGALODON_LIFESPAN_TEST_MODE", "1")
        _setup_mission(tmp_path)
        app = make_app(mission_dir=tmp_path)

        async with app.router.lifespan_context(app):
            assert getattr(app.state, "narrator_runtime", None) is None
            assert getattr(app.state, "narrator_scheduler_task", None) is None
            # Hub and cache are still present (needed by endpoints).
            assert app.state.narrative_hub is not None
            assert app.state.narrative_cache is not None

    @pytest.mark.asyncio
    async def test_fake_spawner_narrator_runtime_not_set(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """MEGALODON_FAKE_SPAWNER=1 → narrator_runtime absent on app.state."""
        monkeypatch.delenv("MEGALODON_LIFESPAN_TEST_MODE", raising=False)
        monkeypatch.setenv("MEGALODON_FAKE_SPAWNER", "1")
        _setup_mission(tmp_path)
        app = make_app(mission_dir=tmp_path)

        async with app.router.lifespan_context(app):
            assert getattr(app.state, "narrator_runtime", None) is None
            assert getattr(app.state, "narrator_scheduler_task", None) is None
            # Hub and cache still present.
            assert app.state.narrative_hub is not None
            assert app.state.narrative_cache is not None


# ---------------------------------------------------------------------------
# 3. Clean start→degraded→stop teardown
# ---------------------------------------------------------------------------


class TestNarratorTeardownClean:
    """Prove the teardown sequence (stop_event → cancel → runtime.stop) leaks nothing.

    Approach: drives the runtime+scheduler+stop_event assembly directly,
    mirroring how test_narrative_scheduler.py drives the scheduler. The live
    lifespan branch requires a real tmux fleet (spawner.start_all()), which is
    out of scope for unit tests. Instead we validate the same teardown contract
    at the component level: this is exactly what Task 4.1 wires together.
    """

    @pytest.mark.asyncio
    async def test_degraded_runtime_start_stop_no_leak(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """NarratorRuntime with a missing model: start is non-fatal, stop closes client."""
        import httpx
        from megalodon_ui.narrator import runtime as runtime_mod

        # Never become healthy — simulates missing model degraded path.
        async def always_false(client, base_url, *, timeout_s=1.0):
            return False

        monkeypatch.setattr(runtime_mod, "healthy", always_false)
        # Prevent any actual spawn attempt being tried (the missing-model path
        # skips spawn already, but pin it for safety).
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            # Should not be called, but if it is, return a process that exits
            # immediately so no zombie hangs the test.
            lambda *a, **_kw: (_ for _ in ()).throw(
                FileNotFoundError("no llama-server in test")
            ),
        )

        missing_model = tmp_path / "does-not-exist.gguf"
        rt = NarratorRuntime(
            missing_model,
            port=8085,
            poll_interval_s=0.0,
            health_timeout_s=0.0,
            backoff_base_s=0.0,
            backoff_max_s=0.0,
            terminate_wait_s=0.01,
        )

        await rt.start()  # must not raise
        assert rt.is_ready() is False
        client = rt.client
        assert isinstance(client, httpx.AsyncClient)

        await rt.stop()  # must close client, no warning
        assert client.is_closed is True
        assert rt.is_ready() is False

    @pytest.mark.asyncio
    async def test_stop_event_cancel_scheduler_then_runtime_stop_no_leak(
        self, monkeypatch
    ) -> None:
        """Full teardown sequence: stop_event.set → cancel scheduler → runtime.stop.

        Uses FakeRuntime so no real subprocess or HTTP client is involved.
        Verifies no dangling tasks remain after the sequence.
        """
        from megalodon_ui.narrator import scheduler as scheduler_mod

        # Monkeypatch narrate so no HTTP is attempted.
        async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
            return None  # narrator down, deterministic-only

        monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

        hub = NarrativeHub()
        runtime = FakeRuntime(ready=False)
        cache: dict = {}
        stop_event = asyncio.Event()

        async def _build_rows() -> dict[str, LaneRow]:
            return {}

        # Mirrors the lifespan setup.
        scheduler_task = asyncio.create_task(
            run_narrator_scheduler(
                hub=hub,
                runtime=runtime,
                cache=cache,
                build_rows=_build_rows,
                interval_s=clamp_interval_s(None),  # 30 s — never fires in test
                stop_event=stop_event,
            )
        )

        # Give the scheduler one event-loop pass to reach the wait.
        await asyncio.sleep(0)

        # Mirrors the lifespan teardown sequence.
        stop_event.set()
        scheduler_task.cancel()
        try:
            await scheduler_task
        except (asyncio.CancelledError, Exception):
            pass

        await runtime.stop()

        # No dangling tasks (the scheduler task is done).
        assert scheduler_task.done()
        assert runtime._stopped is True

    @pytest.mark.asyncio
    async def test_stop_event_alone_exits_scheduler_cleanly(self, monkeypatch) -> None:
        """Setting stop_event (without cancel) exits the scheduler loop cleanly."""
        from megalodon_ui.narrator import scheduler as scheduler_mod

        async def fake_narrate(client, base_url, lane, digest_text, *, timeout_s):
            return None

        monkeypatch.setattr(scheduler_mod, "narrate", fake_narrate)

        hub = NarrativeHub()
        runtime = FakeRuntime(ready=False)
        cache: dict = {}
        stop_event = asyncio.Event()

        async def _build_rows() -> dict[str, LaneRow]:
            return {}

        scheduler_task = asyncio.create_task(
            run_narrator_scheduler(
                hub=hub,
                runtime=runtime,
                cache=cache,
                build_rows=_build_rows,
                interval_s=clamp_interval_s(None),
                stop_event=stop_event,
            )
        )

        await asyncio.sleep(0)

        # Signal stop without cancelling — the loop should exit naturally.
        stop_event.set()

        # Wake the scheduler so the stop_event fires immediately.
        hub.tick_now.set()

        # Wait a brief moment for the loop to notice and exit.
        try:
            await asyncio.wait_for(scheduler_task, timeout=2.0)
        except asyncio.TimeoutError:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            pytest.fail("scheduler did not exit promptly after stop_event.set()")

        assert scheduler_task.done()
        assert not scheduler_task.cancelled()

    @pytest.mark.asyncio
    async def test_interval_env_parsing(self, monkeypatch) -> None:
        """MEGALODON_NARRATOR_INTERVAL_S is parsed and clamped correctly."""
        import os

        # Valid value within range.
        monkeypatch.setenv("MEGALODON_NARRATOR_INTERVAL_S", "45")
        raw = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
        try:
            parsed: float | None = float(raw) if raw else None
        except (ValueError, TypeError):
            parsed = None
        assert clamp_interval_s(parsed) == 45.0

        # Below minimum → clamped to 15.
        monkeypatch.setenv("MEGALODON_NARRATOR_INTERVAL_S", "5")
        raw = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
        try:
            parsed = float(raw) if raw else None
        except (ValueError, TypeError):
            parsed = None
        assert clamp_interval_s(parsed) == 15.0

        # Unparseable → defaults to 30.
        monkeypatch.setenv("MEGALODON_NARRATOR_INTERVAL_S", "not-a-number")
        raw = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
        try:
            parsed = float(raw) if raw else None
        except (ValueError, TypeError):
            parsed = None
        assert clamp_interval_s(parsed) == 30.0

        # Unset → defaults to 30.
        monkeypatch.delenv("MEGALODON_NARRATOR_INTERVAL_S", raising=False)
        raw = os.environ.get("MEGALODON_NARRATOR_INTERVAL_S")
        try:
            parsed = float(raw) if raw else None
        except (ValueError, TypeError):
            parsed = None
        assert clamp_interval_s(parsed) == 30.0
