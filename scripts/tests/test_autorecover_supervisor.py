"""Tests for the auto-recovery supervisor (Task F / contract §4).

SAFETY contract:
  * DEFAULT OFF — armed only by MEGALODON_AUTORECOVER=1 (fail-closed).
  * Restarts only a dead / deny-looping lane that has been continuously
    unhealthy past the grace window.
  * Bounded: per-lane exponential backoff + a hard max-attempts cap.
  * Idempotent: never acts on a healthy lane; a recovered lane resets state.
  * Every action logged to .fleet/autorecover.log.

The restart-loop call is injected (``restart_fn``) so these tests never spawn
tmux; a controllable ``clock`` makes grace/backoff deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from megalodon_ui.server import AutoRecoverSupervisor, autorecover_enabled


class _Clock:
    """Manually-advanced monotonic clock for deterministic grace/backoff tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _supervisor(
    tmp_path: Path,
    *,
    liveness: dict[str, str],
    denies: dict[str, int] | None = None,
    restarts: list[str],
    clock: _Clock,
    grace: float = 60.0,
    max_attempts: int = 5,
    backoff_base: float = 30.0,
    deny_threshold: int = 5,
) -> AutoRecoverSupervisor:
    denies = denies or {}

    async def _restart(lane: str) -> None:
        restarts.append(lane)

    return AutoRecoverSupervisor(
        tmp_path,
        get_liveness=lambda: dict(liveness),
        get_consecutive_denies=lambda: dict(denies),
        restart_fn=_restart,
        grace_seconds=grace,
        deny_threshold=deny_threshold,
        max_attempts=max_attempts,
        backoff_base_seconds=backoff_base,
        backoff_cap_seconds=600.0,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Gate: default OFF
# ---------------------------------------------------------------------------


def test_autorecover_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEGALODON_AUTORECOVER", raising=False)
    assert autorecover_enabled() is False


def test_autorecover_requires_exact_one(monkeypatch):
    monkeypatch.setenv("MEGALODON_AUTORECOVER", "true")
    assert autorecover_enabled() is False
    monkeypatch.setenv("MEGALODON_AUTORECOVER", "0")
    assert autorecover_enabled() is False
    monkeypatch.setenv("MEGALODON_AUTORECOVER", "1")
    assert autorecover_enabled() is True


# ---------------------------------------------------------------------------
# Dead lane: debounce + grace, then exactly one restart within backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_lane_restarted_once_after_grace(tmp_path):
    clock = _Clock()
    restarts: list[str] = []
    sup = _supervisor(
        tmp_path, liveness={"A": "dead"}, restarts=restarts, clock=clock, grace=60.0
    )

    # First sighting: debounce — no restart, just records unhealthy_since.
    await sup.tick()
    assert restarts == []

    # Still within grace → no restart.
    clock.advance(30.0)
    await sup.tick()
    assert restarts == []

    # Past grace → exactly one restart.
    clock.advance(40.0)  # total 70s > 60s grace
    await sup.tick()
    assert restarts == ["A"]

    # Immediately tick again: backoff not elapsed → no second restart.
    await sup.tick()
    assert restarts == ["A"]

    # The action is logged.
    log = (tmp_path / ".fleet" / "autorecover.log").read_text()
    assert "lane=A restart #1" in log
    assert "reason=dead" in log


@pytest.mark.asyncio
async def test_healthy_lane_never_restarted(tmp_path):
    clock = _Clock()
    restarts: list[str] = []
    sup = _supervisor(
        tmp_path, liveness={"A": "running"}, restarts=restarts, clock=clock
    )
    for _ in range(5):
        clock.advance(120.0)
        await sup.tick()
    assert restarts == []


@pytest.mark.asyncio
async def test_recovery_resets_state(tmp_path):
    """A lane that recovers mid-grace must not be restarted on a later relapse
    until it has been continuously unhealthy past grace AGAIN."""
    clock = _Clock()
    restarts: list[str] = []
    live = {"A": "dead"}
    sup = AutoRecoverSupervisor(
        tmp_path,
        get_liveness=lambda: dict(live),
        get_consecutive_denies=lambda: {},
        restart_fn=lambda lane: restarts.append(lane),
        grace_seconds=60.0,
        clock=clock,
    )
    await sup.tick()  # unhealthy_since set
    clock.advance(40.0)
    live["A"] = "running"  # recovered before grace elapsed
    await sup.tick()  # resets state
    assert restarts == []

    live["A"] = "dead"  # relapse
    await sup.tick()  # new unhealthy_since (debounce again)
    clock.advance(40.0)
    await sup.tick()  # only 40s into the NEW window → still no restart
    assert restarts == []
    clock.advance(30.0)
    await sup.tick()  # now > grace → restart
    assert restarts == ["A"]


# ---------------------------------------------------------------------------
# Bounded: backoff increases, attempt cap enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_and_max_attempts_cap(tmp_path):
    clock = _Clock()
    restarts: list[str] = []
    sup = _supervisor(
        tmp_path,
        liveness={"A": "dead"},
        restarts=restarts,
        clock=clock,
        grace=10.0,
        max_attempts=3,
        backoff_base=30.0,
    )

    # Drive past grace for the first restart.
    await sup.tick()  # debounce
    clock.advance(11.0)
    await sup.tick()  # restart #1
    assert restarts == ["A"]

    # Backoff after #1 is 30s; advancing 29s is not enough.
    clock.advance(29.0)
    await sup.tick()
    assert restarts == ["A"]
    clock.advance(2.0)  # now 31s since #1 > 30s backoff
    await sup.tick()  # restart #2
    assert restarts == ["A", "A"]

    # Backoff after #2 is 60s.
    clock.advance(61.0)
    await sup.tick()  # restart #3 (hits the cap)
    assert restarts == ["A", "A", "A"]

    # Cap reached — no further restarts no matter how long we wait.
    clock.advance(100000.0)
    await sup.tick()
    assert restarts == ["A", "A", "A"]


@pytest.mark.asyncio
async def test_deny_loop_lane_restarted(tmp_path):
    """A lane at/over the consecutive-deny threshold is unhealthy → restarted."""
    clock = _Clock()
    restarts: list[str] = []
    sup = _supervisor(
        tmp_path,
        liveness={"A": "running"},  # alive, but deny-looping
        denies={"A": 6},
        restarts=restarts,
        clock=clock,
        grace=60.0,
        deny_threshold=5,
    )
    await sup.tick()  # debounce
    clock.advance(70.0)
    await sup.tick()
    assert restarts == ["A"]
    log = (tmp_path / ".fleet" / "autorecover.log").read_text()
    assert "reason=deny-loop(6)" in log


@pytest.mark.asyncio
async def test_below_deny_threshold_not_restarted(tmp_path):
    clock = _Clock()
    restarts: list[str] = []
    sup = _supervisor(
        tmp_path,
        liveness={"A": "running"},
        denies={"A": 4},  # below threshold of 5
        restarts=restarts,
        clock=clock,
        grace=10.0,
        deny_threshold=5,
    )
    await sup.tick()
    clock.advance(120.0)
    await sup.tick()
    assert restarts == []
