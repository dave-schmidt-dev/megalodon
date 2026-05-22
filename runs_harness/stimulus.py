"""Deterministic dashboard-visibility stimulus harness (v9.4 T4.3 gate).

For each prior failure mode, force a known condition via the server's test
endpoints, then assert the dashboard surface reflects it within a deadline.
Each check returns a StimulusResult; the harness exit code is the gate.

This Python harness keeps exactly TWO checks that genuinely fail if the server
contract breaks:
  - stale-lane:      _test/stale_override (~server.py:2490) → /api/v1/lanes/stale
  - signal-fidelity: write signals/<unique>.md → /api/v1/state signals.list

The activity-wall and empty-state fidelity assertions live at the DOM level in
ui/tests/e2e/visibility.spec.ts (Playwright), because:
  - __fake__/emit feeds lane subscriber byte-queues, NOT the activity wall, so a
    Python-level emit→wall assertion would be hollow; the wall is fed by
    filesystem watchers on findings/signals/history/queue (activity_wall.py).
  - __fake__/set_state flips spawner.sessions[lane].running, which does NOT
    touch STATUS.md, so /api/v1/state cannot reflect it; empty-state is a
    rendered-DOM concern.

Endpoint shapes grounded in megalodon_ui/server.py:
  - _test/stale_override (~line 2490): QUERY PARAMS lane+seconds, X-CSRF-Token header
  - /api/v1/lanes/stale              : {stale_lanes: [{lane, silent_seconds, ...}]}
  - /api/v1/state                    : includes {signals: {list: [...]}}
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass
class StimulusResult:
    name: str
    passed: bool
    detail: str
    latency_ms: float


# ---------------------------------------------------------------------------
# Internal polling helper
# ---------------------------------------------------------------------------


async def _wait_until(predicate, deadline_s: float, poll_s: float = 0.2):
    """Poll async predicate() until truthy or deadline; return (ok, elapsed_ms)."""
    start = time.monotonic()
    while time.monotonic() - start < deadline_s:
        try:
            if await predicate():
                return True, (time.monotonic() - start) * 1000
        except Exception:
            pass
        await asyncio.sleep(poll_s)
    return False, deadline_s * 1000


async def _get_csrf(client: httpx.AsyncClient) -> str:
    """Return the CSRF token from the server's config endpoint."""
    r = await client.get("/api/v1/config")
    r.raise_for_status()
    return r.json().get("csrf_token", "")


async def _authenticate(client: httpx.AsyncClient, token: str | None) -> None:
    """Exchange a UI token for an mui_session cookie (server.py:1371).

    Cookie-gated endpoints (lanes/stale, _test/*) require a valid session.
    No-op when ``token`` is falsy (open endpoints like /api/v1/state and
    /api/v1/config don't need it). The AsyncClient persists the Set-Cookie.
    """
    if not token:
        return
    r = await client.post("/api/v1/auth/exchange", json={"token": token})
    r.raise_for_status()


# ---------------------------------------------------------------------------
# Check 1 — stale lane
# ---------------------------------------------------------------------------


async def run_stale_lane_check(
    base_url: str, lane_short: str, deadline_s: float, token: str | None = None
) -> StimulusResult:
    """Force a stale lane via _test/stale_override; assert it shows in /api/v1/lanes/stale.

    Server contract (server.py ~line 2490-2550):
      POST /api/v1/_test/stale_override?lane=<short>&seconds=<float>
        Header: X-CSRF-Token: <token>
        Body: empty or {}
      Response: {ok: true, lane, seconds}

    ``token`` authenticates the session (these endpoints are cookie-gated via
    _V92_GATED_PATH_RE). The override is consumed one-shot by the NEXT GET
    /api/v1/lanes/stale. To make the check robust against the one-shot semantics
    + the 5 s stale cache, we use a seconds value well above the 900 s threshold
    and re-arm the override before each poll attempt so a cache-served or
    already-consumed read can't flake.
    """
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as c:
        await _authenticate(c, token)
        csrf = await _get_csrf(c)
        override_seconds = 100_000.0  # vastly above the 900 s stale threshold

        async def shows_stale():
            # Re-arm the override on every poll so the one-shot consumption +
            # 5 s cache TTL can't race us into a false negative.
            r = await c.post(
                f"/api/v1/_test/stale_override?lane={lane_short}&seconds={override_seconds}",
                headers={"X-CSRF-Token": csrf},
                json={},
            )
            if r.status_code != 200:
                return False
            resp = await c.get("/api/v1/lanes/stale")
            resp.raise_for_status()
            data = resp.json()
            stale_lanes = {entry["lane"] for entry in data.get("stale_lanes", [])}
            return lane_short in stale_lanes

        # First POST also validates the endpoint contract up-front for a clear
        # failure detail if the override path is broken.
        first = await c.post(
            f"/api/v1/_test/stale_override?lane={lane_short}&seconds={override_seconds}",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        if first.status_code != 200:
            return StimulusResult(
                "stale-lane",
                False,
                f"stale_override POST returned {first.status_code}: {first.text}",
                0.0,
            )

        ok, ms = await _wait_until(shows_stale, deadline_s)
        return StimulusResult(
            "stale-lane",
            ok,
            "stale lane surfaced in /api/v1/lanes/stale"
            if ok
            else "stale lane not surfaced before deadline",
            ms,
        )


# ---------------------------------------------------------------------------
# Check 2 — signal fidelity
# ---------------------------------------------------------------------------


async def run_signal_fidelity_check(
    base_url: str, mission_dir: str, deadline_s: float, token: str | None = None
) -> StimulusResult:
    """Write a UNIQUE signal file to disk; assert /api/v1/state reflects it.

    Server contract (server.py ~line 781-828):
      Signals are parsed from <mission_dir>/signals/LANE-X-to-LANE-Y-<UTC>.md
      Filename regex: ^LANE-[A-Z0-9]+-to-LANE-[A-Z0-9]+-<utc>.md$

    GET /api/v1/state response (server.py ~line 2061-2076):
      {signals: {list: [{filename, from_lane, to_lane, to, utc, kind, body}, ...]}}

    The filename embeds a uuid4 so a second invocation can't pass trivially on a
    pre-existing file — the assertion only succeeds if THIS call's file surfaces.
    """
    signals_dir = Path(mission_dir) / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    # Unique per call: uuid4 suffix in the UTC segment keeps the filename grammar
    # valid (the regex treats everything after the second LANE- as the utc group).
    unique = uuid.uuid4().hex[:12]
    sig_filename = f"LANE-A-to-LANE-B-2026-05-22T00-00-00Z-{unique}.md"
    sig_path = signals_dir / sig_filename
    sig_path.write_text(
        f"# Stimulus harness signal fidelity check {unique}\n\nTest signal.\n"
    )

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as c:
        await _authenticate(c, token)

        async def signal_reflected():
            resp = await c.get("/api/v1/state")
            resp.raise_for_status()
            data = resp.json()
            sig_list = data.get("signals", {}).get("list", [])
            return any(s.get("filename") == sig_filename for s in sig_list)

        ok, ms = await _wait_until(signal_reflected, deadline_s)
        return StimulusResult(
            "signal-fidelity",
            ok,
            f"signal {sig_filename} reflected in /api/v1/state"
            if ok
            else f"signal {sig_filename} not reflected before deadline",
            ms,
        )


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


async def _run_all_checks(
    base_url: str, mission_dir: str, token: str | None = None
) -> list[StimulusResult]:
    """Run all stimulus checks sequentially and return results.

    Exactly two checks: stale-lane and signal-fidelity. (activity-wall and
    empty-state fidelity are covered at the DOM level in visibility.spec.ts.)
    """
    results: list[StimulusResult] = []
    results.append(
        await run_stale_lane_check(base_url, "A", deadline_s=10.0, token=token)
    )
    results.append(
        await run_signal_fidelity_check(
            base_url, mission_dir, deadline_s=10.0, token=token
        )
    )
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage:
        uv run python3 -m runs_harness.stimulus --base-url http://localhost:8765 \\
            --json-out /tmp/harness.json --mission-dir /path/to/run
    """
    import argparse

    parser = argparse.ArgumentParser(description="Stimulus harness CLI runner")
    parser.add_argument("--base-url", required=True, help="Server base URL")
    parser.add_argument("--json-out", required=False, help="Path to write JSON summary")
    parser.add_argument(
        "--mission-dir",
        required=False,
        default="/tmp/harness-mission",
        help=(
            "Mission directory the server watches; ONLY the signal-fidelity "
            "check writes here (it drops a unique signals/*.md file)."
        ),
    )
    parser.add_argument(
        "--token",
        required=False,
        default=None,
        help=(
            "UI token for the auth/exchange handshake (cookie-gated endpoints "
            "like lanes/stale need it). If omitted, read from "
            "<mission-dir>/.fleet/ui.token when present."
        ),
    )
    args = parser.parse_args(argv)

    token = args.token
    if token is None:
        token_path = Path(args.mission_dir) / ".fleet" / "ui.token"
        if token_path.exists():
            token = token_path.read_text().strip()

    results = asyncio.run(_run_all_checks(args.base_url, args.mission_dir, token))

    any_failed = False
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            any_failed = True
        print(f"CHECK {r.name} {status} {r.latency_ms:.0f}ms — {r.detail}")

    if args.json_out:
        summary = {
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "latency_ms": r.latency_ms,
                }
                for r in results
            ],
            "passed": not any_failed,
        }
        Path(args.json_out).write_text(json.dumps(summary, indent=2))
        print(f"\nJSON summary written to {args.json_out}")

    if any_failed:
        print("\nHARNESS: FAIL")
        return 1
    print("\nHARNESS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
