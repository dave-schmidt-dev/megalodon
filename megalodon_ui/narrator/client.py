"""HTTP client for the narrator llama-server.

This module exposes two thin async functions that talk to a local
``llama-server`` (OpenAI-compatible API). Both functions receive an
``httpx.AsyncClient`` from the caller — they never create or close one.
All errors are absorbed; callers observe ``None`` or ``False`` on any
failure so the advisory narrative degrades gracefully without disturbing
the load-bearing board.
"""

from __future__ import annotations

import httpx

from .prompt import build_messages


async def narrate(
    client: httpx.AsyncClient,
    base_url: str,
    lane: str,
    digest_text: str,
    *,
    timeout_s: float,
) -> str | None:
    """Ask the local llama-server to phrase a lane's activity as one sentence.

    Args:
        client: Shared ``httpx.AsyncClient`` owned by the caller.
        base_url: Base URL of the llama-server (e.g. ``http://localhost:8080``).
        lane: Human lane label (e.g. ``AUDIT``), forwarded to the prompt builder.
        digest_text: Rendered activity digest from ``render_for_prompt()``.
        timeout_s: Per-request timeout in seconds; the default lives in the
            NarratorRuntime (a later task), not here.

    Returns:
        The stripped one-sentence narrative, or ``None`` on any failure
        (timeout, connection error, non-2xx status, malformed JSON, or
        empty/missing content).
    """
    messages = build_messages(lane, digest_text)
    body = {
        "model": "narrator",
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 80,
        "stream": False,
    }
    try:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json=body,
            timeout=timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        content: str = data["choices"][0]["message"]["content"]
        stripped = content.strip()
        return stripped if stripped else None
    except Exception:  # noqa: BLE001
        return None


async def healthy(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    timeout_s: float = 1.0,
) -> bool:
    """Check whether the llama-server is reachable and healthy.

    Args:
        client: Shared ``httpx.AsyncClient`` owned by the caller.
        base_url: Base URL of the llama-server.
        timeout_s: Request timeout in seconds (defaults to 1.0 here because
            health checks are expected to be fast).

    Returns:
        ``True`` if the server responds with a 2xx status, ``False`` on any
        error or non-2xx response.
    """
    try:
        response = await client.get(f"{base_url}/health", timeout=timeout_s)
        return response.is_success
    except Exception:  # noqa: BLE001
        return False
