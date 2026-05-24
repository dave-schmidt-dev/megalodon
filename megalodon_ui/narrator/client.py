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

from .prompt import build_last_messages, build_messages


async def narrate(
    client: httpx.AsyncClient,
    base_url: str,
    lane: str,
    digest_text: str,
    *,
    timeout_s: float,
    messages: list[dict] | None = None,
) -> str | None:
    """Ask the local llama-server to phrase a lane's activity as one sentence.

    By default this builds the "Now" (doing-now) prompt via
    :func:`build_messages`. A caller may pass pre-built ``messages`` (e.g. the
    "Last"/completed prompt) to narrate a different single-phrase prompt while
    reusing the same request body, error absorption, and client. Use
    :func:`narrate_last` for the Last column rather than building messages here.

    Args:
        client: Shared ``httpx.AsyncClient`` owned by the caller.
        base_url: Base URL of the llama-server (e.g. ``http://localhost:8080``).
        lane: Human lane label (e.g. ``AUDIT``), forwarded to the prompt builder.
        digest_text: Rendered activity digest from ``render_for_prompt()``.
        timeout_s: Per-request timeout in seconds; the default lives in the
            NarratorRuntime (a later task), not here.
        messages: Optional pre-built chat messages. When None (default), the
            "Now" prompt is built from ``lane`` + ``digest_text``.

    Returns:
        The stripped one-sentence narrative, or ``None`` on any failure
        (timeout, connection error, non-2xx status, malformed JSON, or
        empty/missing content).
    """
    if messages is None:
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


async def narrate_last(
    client: httpx.AsyncClient,
    base_url: str,
    lane: str,
    last_task_desc: str,
    digest_text: str,
    *,
    timeout_s: float,
) -> str | None:
    """Narrate the "Last" column: one sentence about the just-completed task.

    A SEPARATE single-phrase call from :func:`narrate` — it builds the
    "Last"/completed prompt via :func:`build_last_messages` and delegates to
    :func:`narrate` with those messages, so the request body, error absorption,
    and shared client are identical. This keeps each narrate call within the
    model's validated single-phrase competency (we never make one call emit two
    phrases).

    Args:
        client: Shared ``httpx.AsyncClient`` owned by the caller.
        base_url: Base URL of the llama-server.
        lane: Human lane label, forwarded to the prompt builder.
        last_task_desc: Description of the just-completed (closed) task.
        digest_text: Rendered activity digest from ``render_for_prompt()``.
        timeout_s: Per-request timeout in seconds.

    Returns:
        The stripped one-sentence completion summary, or ``None`` on any failure.
    """
    messages = build_last_messages(lane, last_task_desc, digest_text)
    return await narrate(
        client, base_url, lane, digest_text, timeout_s=timeout_s, messages=messages
    )


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
