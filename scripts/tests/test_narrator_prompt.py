"""Tests for megalodon_ui.narrator.prompt — the Now and Last prompt builders.

The "Now" prompt (``build_messages``) is the benchmarked, validated prompt and
must NOT change shape. The "Last" prompt (``build_last_messages``, OQ1) is a
SEPARATE single-phrase prompt asking for one past-tense sentence about the
just-completed task. Both share the same faithfulness/format discipline and the
same system + few-shot + real-turn structure so they are consistent and
benchmarkable.
"""

from __future__ import annotations

from megalodon_ui.narrator.prompt import (
    LAST_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_last_messages,
    build_messages,
)

LANE = "AUDIT"
LAST_TASK = "Ship the authentication banner"
DIGEST = "- ASKED: do the thing\n- TOOL: Edit(banner.js)\n- RESULT: ok"


# ---------------------------------------------------------------------------
# build_messages (Now) — structure regression guard (must stay unchanged)
# ---------------------------------------------------------------------------


def test_build_messages_structure() -> None:
    """Now prompt: system + few-shot pairs + the real user turn."""
    msgs = build_messages(LANE, DIGEST)
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    # Few-shot demos come in user/assistant pairs; the final message is the
    # real user turn carrying the lane + digest.
    assert msgs[-1]["role"] == "user"
    assert LANE in msgs[-1]["content"]
    assert DIGEST in msgs[-1]["content"]
    # Alternating roles after the system message.
    roles = [m["role"] for m in msgs[1:]]
    assert roles[0::2] == ["user"] * (len(roles) // 2 + len(roles) % 2)
    assert roles[1::2] == ["assistant"] * (len(roles) // 2)


# ---------------------------------------------------------------------------
# build_last_messages (Last / completed) — OQ1
# ---------------------------------------------------------------------------


def test_build_last_messages_returns_system_fewshot_and_real_turn() -> None:
    """Last prompt: a distinct system prompt + few-shot demos + the real turn."""
    msgs = build_last_messages(LANE, LAST_TASK, DIGEST)
    assert msgs[0] == {"role": "system", "content": LAST_SYSTEM_PROMPT}
    # Has at least one few-shot pair before the real turn.
    assert len(msgs) >= 4
    # Alternating user/assistant after the system message.
    roles = [m["role"] for m in msgs[1:]]
    assert roles[0::2] == ["user"] * (len(roles) // 2 + len(roles) % 2)
    assert roles[1::2] == ["assistant"] * (len(roles) // 2)


def test_build_last_messages_real_turn_carries_lane_task_and_digest() -> None:
    """The real (final) user turn includes the lane, the closed task, and the digest."""
    msgs = build_last_messages(LANE, LAST_TASK, DIGEST)
    real = msgs[-1]
    assert real["role"] == "user"
    assert LANE in real["content"]
    assert LAST_TASK in real["content"]
    assert DIGEST in real["content"]


def test_build_last_messages_framing_is_completed() -> None:
    """The Last system prompt frames the output as COMPLETED / past-tense work."""
    sys_lower = LAST_SYSTEM_PROMPT.lower()
    assert "just completed" in sys_lower
    assert "past-tense" in sys_lower or "past tense" in sys_lower
    # The real turn explicitly asks for "just completed".
    real = build_last_messages(LANE, LAST_TASK, DIGEST)[-1]["content"]
    assert "just-completed" in real.lower() or "just completed" in real.lower()


def test_last_prompt_is_distinct_from_now_prompt() -> None:
    """OQ1 design: Last is a SEPARATE prompt, not the Now prompt reused."""
    assert LAST_SYSTEM_PROMPT != SYSTEM_PROMPT
    now = build_messages(LANE, DIGEST)
    last = build_last_messages(LANE, LAST_TASK, DIGEST)
    assert now[0] != last[0]  # different system prompts


def test_build_last_messages_single_phrase_discipline_in_system() -> None:
    """Same faithfulness/format discipline as Now: one sentence, no markdown/lists,
    only provided events, never invent."""
    sys_lower = LAST_SYSTEM_PROMPT.lower()
    assert "one" in sys_lower  # one sentence
    assert "no markdown" in sys_lower
    assert "never invent" in sys_lower
