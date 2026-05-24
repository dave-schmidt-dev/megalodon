"""Tests for megalodon_ui.narrator.client — narrate() + healthy().

Uses httpx.MockTransport to inject responses without any real network calls.
All transports are properly closed to avoid ResourceWarning under -W error.
"""

from __future__ import annotations

import json

import httpx
import pytest

from megalodon_ui.narrator.client import healthy, narrate
from megalodon_ui.narrator.prompt import build_messages

BASE_URL = "http://localhost:8080"
LANE = "AUDIT"
DIGEST = "- TOOL: Read(STATUS.md)\n- RESULT: ok"
TIMEOUT = 6.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_response(content: str) -> bytes:
    """Build a minimal OpenAI-style chat/completions JSON response body."""
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }
    return json.dumps(body).encode()


def _make_handler(responses: list[tuple[int, bytes]]):
    """Return a MockTransport handler that serves ``responses`` in order."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = queue.pop(0)
        return httpx.Response(status, content=body)

    return handler


# ---------------------------------------------------------------------------
# narrate() — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrate_success_returns_stripped_content() -> None:
    """200 response → stripped sentence returned."""
    raw = "  Read foo.py and edited bar.  "
    handler = _make_handler([(200, _ok_response(raw))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result == raw.strip()


@pytest.mark.asyncio
async def test_narrate_builds_correct_request_body() -> None:
    """Request body contains model='narrator' and messages from build_messages."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, content=_ok_response("Agent read foo.py."))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)

    assert len(captured) == 1
    body = captured[0]
    assert body["model"] == "narrator"
    assert body["messages"] == build_messages(LANE, DIGEST)
    assert body["stream"] is False
    assert body["temperature"] == pytest.approx(0.2)
    assert body["max_tokens"] == 80


# ---------------------------------------------------------------------------
# narrate() — failure / degrade paths → all return None, never raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrate_returns_none_on_500() -> None:
    """Non-2xx status → None."""
    handler = _make_handler([(500, b"Internal Server Error")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


@pytest.mark.asyncio
async def test_narrate_returns_none_on_empty_content() -> None:
    """Empty string in choices[0].message.content → None."""
    handler = _make_handler([(200, _ok_response(""))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


@pytest.mark.asyncio
async def test_narrate_returns_none_on_whitespace_only_content() -> None:
    """Whitespace-only content strips to empty → None (documents .strip() branch)."""
    handler = _make_handler([(200, _ok_response("   \n  "))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


@pytest.mark.asyncio
async def test_narrate_returns_none_on_missing_choices() -> None:
    """Malformed JSON (no choices key) → None."""
    bad_body = json.dumps({"result": "ok"}).encode()
    handler = _make_handler([(200, bad_body)])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


@pytest.mark.asyncio
async def test_narrate_returns_none_on_connect_error() -> None:
    """httpx.ConnectError in transport → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


@pytest.mark.asyncio
async def test_narrate_returns_none_on_timeout() -> None:
    """httpx.TimeoutException in transport → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await narrate(client, BASE_URL, LANE, DIGEST, timeout_s=TIMEOUT)
    assert result is None


# ---------------------------------------------------------------------------
# healthy()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_returns_true_on_200() -> None:
    """GET /health → 200 → True."""
    handler = _make_handler([(200, b'{"status":"ok"}')])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await healthy(client, BASE_URL)
    assert result is True


@pytest.mark.asyncio
async def test_healthy_returns_false_on_503() -> None:
    """GET /health → 503 → False."""
    handler = _make_handler([(503, b"service unavailable")])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await healthy(client, BASE_URL)
    assert result is False


@pytest.mark.asyncio
async def test_healthy_returns_false_on_transport_error() -> None:
    """Transport error on /health → False."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no server")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await healthy(client, BASE_URL)
    assert result is False
