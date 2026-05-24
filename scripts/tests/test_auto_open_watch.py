"""Unit tests for the observed dashboard auto-open decision core (Task D4).

All tests inject a fake subscriber-count getter, a fake ``open_fn`` (records
calls), and a fake no-op ``sleep`` so they are instant and fully deterministic
— no real browser, no real waiting. The live-branch lifespan wiring (which
needs a real tmux fleet) is covered end-to-end by D6's restart-reconnect spec.
"""

from __future__ import annotations

import pytest

from megalodon_ui.dashboard_open import (
    DEFAULT_OPEN_GRACE_S,
    auto_open_watch,
    parse_open_grace_env,
)

_URL = "http://127.0.0.1:8080/#t=SECRET"


class _OpenRecorder:
    """Records calls to open_fn; optionally raises to test best-effort."""

    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[str] = []
        self._raises = raises

    def __call__(self, url: str) -> None:
        self.calls.append(url)
        if self._raises:
            raise RuntimeError("no display (synthetic)")


class _CountingSleep:
    """Fake sleep: never actually waits; records how many times it was awaited."""

    def __init__(self) -> None:
        self.awaits: int = 0

    async def __call__(self, _delay: float) -> None:
        self.awaits += 1


# ---------------------------------------------------------------------------
# enabled=False → never open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_never_opens() -> None:
    opener = _OpenRecorder()
    sleep = _CountingSleep()
    count_calls = {"n": 0}

    def _count() -> int:
        count_calls["n"] += 1
        return 0

    result = await auto_open_watch(
        enabled=False,
        force_open=False,
        url=_URL,
        get_subscriber_count=_count,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=sleep,
    )

    assert result is False
    assert opener.calls == []
    assert count_calls["n"] == 0, "disabled path must not consult the count"
    assert sleep.awaits == 0


# ---------------------------------------------------------------------------
# force_open=True → open immediately, skip the window + count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_open_opens_immediately() -> None:
    opener = _OpenRecorder()
    sleep = _CountingSleep()
    count_calls = {"n": 0}

    def _count() -> int:
        count_calls["n"] += 1
        return 0

    result = await auto_open_watch(
        enabled=True,
        force_open=True,
        url=_URL,
        get_subscriber_count=_count,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=sleep,
    )

    assert result is True
    assert opener.calls == [_URL]
    assert count_calls["n"] == 0, "force-open must not consult the subscriber count"
    assert sleep.awaits == 0, "force-open must not wait the window"


# ---------------------------------------------------------------------------
# zero subscribers through the whole window → open once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_subscriber_opens_after_window() -> None:
    opener = _OpenRecorder()
    sleep = _CountingSleep()

    result = await auto_open_watch(
        enabled=True,
        force_open=False,
        url=_URL,
        get_subscriber_count=lambda: 0,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=sleep,
    )

    assert result is True
    assert opener.calls == [_URL]
    # Bounded poll count drove the window without real waiting.
    assert sleep.awaits > 0


# ---------------------------------------------------------------------------
# subscriber appears (0 then >0) → never open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_appears_skips_open() -> None:
    opener = _OpenRecorder()
    sleep = _CountingSleep()
    seq = iter([0, 2])  # first poll: none; second poll: a live tab reconnected

    def _count() -> int:
        return next(seq)

    result = await auto_open_watch(
        enabled=True,
        force_open=False,
        url=_URL,
        get_subscriber_count=_count,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=sleep,
    )

    assert result is False
    assert opener.calls == []


# ---------------------------------------------------------------------------
# open_fn raises → swallowed (best-effort), no exception propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_fn_failure_is_swallowed() -> None:
    opener = _OpenRecorder(raises=True)
    sleep = _CountingSleep()

    # Must not raise even though open_fn raises.
    result = await auto_open_watch(
        enabled=True,
        force_open=False,
        url=_URL,
        get_subscriber_count=lambda: 0,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=sleep,
    )

    # It still attempted the open and returns its decision (True).
    assert opener.calls == [_URL]
    assert result is True


@pytest.mark.asyncio
async def test_force_open_fn_failure_is_swallowed() -> None:
    opener = _OpenRecorder(raises=True)

    result = await auto_open_watch(
        enabled=True,
        force_open=True,
        url=_URL,
        get_subscriber_count=lambda: 0,
        open_fn=opener,
        grace_s=8.0,
        poll_s=0.5,
        sleep=_CountingSleep(),
    )

    assert opener.calls == [_URL]
    assert result is True


# ---------------------------------------------------------------------------
# grace env parse + clamp
# ---------------------------------------------------------------------------


def test_grace_env_unset_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEGALODON_DASHBOARD_OPEN_GRACE_S", raising=False)
    assert parse_open_grace_env() == DEFAULT_OPEN_GRACE_S


def test_grace_env_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALODON_DASHBOARD_OPEN_GRACE_S", "12")
    assert parse_open_grace_env() == 12.0


def test_grace_env_clamped_low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALODON_DASHBOARD_OPEN_GRACE_S", "0")
    assert parse_open_grace_env() == 1.0


def test_grace_env_clamped_high(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALODON_DASHBOARD_OPEN_GRACE_S", "9999")
    assert parse_open_grace_env() == 60.0


def test_grace_env_unparseable_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALODON_DASHBOARD_OPEN_GRACE_S", "not-a-number")
    assert parse_open_grace_env() == DEFAULT_OPEN_GRACE_S
