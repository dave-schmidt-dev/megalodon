"""Integration test configuration and shared helpers."""

import asyncio


async def wait_for_queue_applied(client, request_id: str, timeout: float = 5.0,
                                  poll_interval: float = 0.05,
                                  mission_dir=None) -> dict:
    """Drive the queue applier then poll /api/v1/queue/{request_id} until status != 'pending'.

    In integration tests the applier daemon is not running, so we instantiate
    Applier directly and call drain_once() to process pending queue items
    before each status check.

    Returns the final response body. Raises AssertionError on timeout or if the
    request resolves as 'rejected' (caller can catch if rejection is the asserted
    outcome).
    """
    from megalodon_ui.queue.applier import Applier

    deadline = asyncio.get_event_loop().time() + timeout

    # Derive mission_dir from the queue endpoint if not provided.
    # We call GET queue first to get the mission context implicitly — but we
    # can't get mission_dir from the HTTP client. Callers must pass it.
    # If not provided, fall back to pure-HTTP polling (applier assumed external).
    applier = None
    if mission_dir is not None:
        applier = Applier(mission_dir=mission_dir, poll_seconds=0)

    while True:
        if applier is not None:
            try:
                applier.drain_once()
            except Exception:
                pass  # Best-effort; the status check below will show what happened.

        r = await client.get(f"/api/v1/queue/{request_id}")
        assert r.status_code == 200, f"queue status returned {r.status_code}: {r.text}"
        body = r.json()
        if body["status"] != "pending":
            if body["status"] == "rejected":
                raise AssertionError(
                    f"queue request {request_id} rejected: {body.get('rejection_reason')}"
                )
            return body
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"queue request {request_id} did not resolve within {timeout}s "
                f"(last status: {body['status']})"
            )
        await asyncio.sleep(poll_interval)
