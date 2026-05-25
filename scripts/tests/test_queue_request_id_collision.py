"""Regression tests for the same-second request_id collision data-loss bug.

Root cause (adversarial audit): `_request_id` used second-granularity UTC plus
only `token_hex(2)` (16 bits). Two same-second, same-agent, same-target,
same-intent submits could collide on the request_id; `_atomic_write` then
`os.replace`-clobbered the earlier pending file, *silently destroying* that
request. The applier's `applied/{rid}.json` existed for the survivor, so a
"did it apply?" wait succeeded while the other write was gone (~7%/100, ~25%
observed).

Fix (both):
  1. Widen entropy: per-process monotonic counter + `token_hex(8)` so
     same-second same-agent submits cannot collide in practice.
  2. Fail loud: `_atomic_write` refuses to clobber an existing pending file
     with *distinct* content (raises PendingCollisionError); identical content
     (idempotent retry) stays a safe no-op.
"""

import json
import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from megalodon_ui.queue import queue_client
from megalodon_ui.queue.applier import Applier


def _drain_until(applier, predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        applier.drain_once()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_n_same_second_appends_no_overwrite_all_survive(queue_mission, monkeypatch):
    """Submit N same-second HISTORY appends by one agent; assert
    (a) exactly N pending files exist (no overwrites),
    (b) every record survives end-to-end (all N lines land after drain),
    (c) no two distinct requests share a request_id.
    """
    n = 200
    # Freeze the clock to a single second so every submit shares submitted_utc.
    # This is the exact adversarial condition; with the old id scheme it caused
    # collisions, overwrites, and silent loss.
    monkeypatch.setattr(queue_client, "utc_now", lambda: "2026-05-25T12:00:00Z")

    rids = []
    for i in range(n):
        rid = queue_client.history_append(
            queue_mission,
            "agent-aaaa",
            "AUDIT",
            f"Q-COLL-{i:04d}",
            f"findings/coll-{i:04d}.md",
            "MINOR",
        )
        rids.append(rid)

    # (c) no two distinct requests share a request_id.
    assert len(set(rids)) == n, "duplicate request_ids generated"

    # (a) exactly N pending files exist (no overwrites destroyed any).
    pending = list((queue_mission / "queue" / "pending").glob("*.json"))
    assert len(pending) == n, f"expected {n} pending files, found {len(pending)}"

    # Sanity: each pending file is the distinct request we submitted.
    seen_lines = set()
    for p in pending:
        req = json.loads(p.read_text(encoding="utf-8"))
        seen_lines.add(req["payload"]["line"])
    assert len(seen_lines) == n

    # (b) every record survives end-to-end after the applier drains.
    applier = Applier(queue_mission)
    assert _drain_until(
        applier,
        lambda: all(
            (queue_mission / "queue" / "applied" / f"{rid}.json").exists()
            for rid in rids
        ),
        timeout=30.0,
    ), "not all requests reached applied/"

    history = (queue_mission / "HISTORY.md").read_text(encoding="utf-8")
    missing = [i for i in range(n) if f"findings/coll-{i:04d}.md" not in history]
    assert not missing, f"silently lost findings: {missing}"


def test_distinct_request_collision_fails_loud(queue_mission, monkeypatch):
    """If two *distinct* requests are forced onto the same request_id, the
    second submit must FAIL LOUD (PendingCollisionError) rather than silently
    overwriting the first."""
    monkeypatch.setattr(queue_client, "utc_now", lambda: "2026-05-25T12:00:00Z")
    # Force a fixed request_id so the two submits collide on the pending path.
    monkeypatch.setattr(
        queue_client,
        "_request_id",
        lambda agent, target, intent, utc: "FORCED-COLLISION-RID",
    )

    queue_client.history_append(
        queue_mission, "agent-aaaa", "AUDIT", "Q-1", "findings/1.md", "MINOR"
    )
    with pytest.raises(queue_client.PendingCollisionError):
        # Different payload (distinct content) at the same forced rid.
        queue_client.history_append(
            queue_mission, "agent-aaaa", "AUDIT", "Q-2", "findings/2.md", "MAJOR"
        )

    # First request must be intact (NOT clobbered).
    pending = list((queue_mission / "queue" / "pending").glob("*.json"))
    assert len(pending) == 1
    req = json.loads(pending[0].read_text(encoding="utf-8"))
    assert "findings/1.md" in req["payload"]["line"]


def test_idempotent_retry_same_content_is_noop(queue_mission, monkeypatch):
    """A legitimate idempotent retry (same request_id AND identical content)
    must remain a safe no-op overwrite, NOT raise."""
    monkeypatch.setattr(queue_client, "utc_now", lambda: "2026-05-25T12:00:00Z")
    monkeypatch.setattr(
        queue_client,
        "_request_id",
        lambda agent, target, intent, utc: "STABLE-RID",
    )

    rid1 = queue_client.history_append(
        queue_mission, "agent-aaaa", "AUDIT", "Q-1", "findings/1.md", "MINOR"
    )
    # Identical args -> identical envelope (utc frozen, rid forced) -> no-op.
    rid2 = queue_client.history_append(
        queue_mission, "agent-aaaa", "AUDIT", "Q-1", "findings/1.md", "MINOR"
    )
    assert rid1 == rid2 == "STABLE-RID"

    pending = list((queue_mission / "queue" / "pending").glob("*.json"))
    assert len(pending) == 1


def test_request_id_still_sortable_and_readable(queue_mission, monkeypatch):
    """The id keeps its readable/sortable structure: leading UTC + agent +
    target + intent, so applier sort-by-(submitted_utc, name) is unaffected."""
    monkeypatch.setattr(queue_client, "utc_now", lambda: "2026-05-25T12:00:00Z")
    rid = queue_client.history_append(
        queue_mission, "agent-aaaa", "AUDIT", "Q-1", "findings/1.md", "MINOR"
    )
    assert rid.startswith("2026-05-25T12-00-00Z-agent-aaaa-")
    assert "HISTORY_APPEND" in rid
