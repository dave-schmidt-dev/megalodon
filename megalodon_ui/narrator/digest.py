"""Parse a Claude Code session JSONL transcript into a compact, faithful digest.

The transcript (``~/.claude/projects/<encoded-cwd>/<session>.jsonl``) is a
line-per-event log. Each line is a JSON envelope with ``type``, ``timestamp``,
and a ``message`` whose ``content`` is either a string or a list of typed
blocks (``thinking`` / ``text`` / ``tool_use`` / ``tool_result``).

We normalize that into an ordered list of :class:`DigestEvent` — one short,
human-readable line per meaningful action — plus rolled-up token usage. The
output is deliberately lossy *toward faithfulness*: a ``tool_use`` becomes
``Read(launch-AUDIT.md)`` so a downstream summarizer can phrase it but cannot
invent a tool that was never called.

No network, no model — pure parsing. Malformed lines are skipped, never fatal.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Kinds of normalized event, in rough order of usefulness to a narrator.
KIND_PROMPT = "prompt"  # what the agent was asked to do this turn
KIND_SAY = "say"  # assistant natural-language text
KIND_TOOL = "tool"  # a tool the assistant invoked
KIND_RESULT = "result"  # a tool's result (truncated)
KIND_THINKING = "thinking"  # assistant private reasoning (marker only)

# Strip the /loop (and similar) command wrappers so the prompt text is readable.
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_CMD_TAG_RE = re.compile(r"</?command-[a-z]+>")


@dataclass
class DigestEvent:
    """One normalized transcript event."""

    ts: str | None
    kind: str
    text: str
    tool_id: str | None = None  # for KIND_TOOL: the tool_use id, to match results


@dataclass
class SessionDigest:
    """Compact, model-free summary of a session transcript."""

    session_id: str
    events: list[DigestEvent] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    model: str | None = None
    last_ts: str | None = None

    @property
    def latest_tool(self) -> str | None:
        """Most recent tool invocation text, or None."""
        for ev in reversed(self.events):
            if ev.kind == KIND_TOOL:
                return ev.text
        return None

    @property
    def latest_say(self) -> str | None:
        """Most recent assistant natural-language line, or None."""
        for ev in reversed(self.events):
            if ev.kind == KIND_SAY:
                return ev.text
        return None

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and clip to ``limit`` chars with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _summarize_tool(name: str, tool_input: dict) -> str:
    """Render a ``tool_use`` block as ``Name(key detail)`` — concise but specific.

    Specific enough that a downstream summarizer cannot substitute a different
    tool or target; short enough to fit many events in a small context.
    """
    inp = tool_input if isinstance(tool_input, dict) else {}

    def base(p: object) -> str:
        return Path(str(p)).name if p else ""

    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return f"{name}({base(inp.get('file_path'))})"
    if name == "Bash":
        cmd = _clip(str(inp.get("command", "")), 60)
        desc = str(inp.get("description", "")).strip()
        return f"Bash({cmd})" + (f" — {desc}" if desc else "")
    if name in ("Grep", "Glob"):
        return f"{name}({_clip(str(inp.get('pattern', '')), 40)})"
    if name in ("Task", "Agent"):
        return f"{name}({_clip(str(inp.get('description', '')), 50)})"
    if name == "TodoWrite":
        return "TodoWrite(update task list)"
    # Generic: tool name + first scalar input value, if any.
    for v in inp.values():
        if isinstance(v, (str, int, float)):
            return f"{name}({_clip(str(v), 40)})"
    return f"{name}()"


def _normalize_prompt(text: str) -> str:
    """Pull the human-readable instruction out of a (possibly wrapped) user msg."""
    m = _CMD_ARGS_RE.search(text)
    if m:
        return _clip(m.group(1), 160)
    # Strip any stray command-* tags, then clip.
    return _clip(_CMD_TAG_RE.sub("", text), 160)


def parse_session(path: str | Path) -> SessionDigest:
    """Parse a session JSONL file into a :class:`SessionDigest`.

    Robust to truncated/streaming files: unparseable lines are skipped. A
    missing file yields an empty digest (session may not have started yet).
    """
    path = Path(path)
    digest = SessionDigest(session_id=path.stem)
    if not path.is_file():
        return digest

    answered_ids: set[str] = set()  # tool_use ids that received a tool_result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue

        ts = ev.get("timestamp")
        if ts:
            digest.last_ts = ts
        etype = ev.get("type")
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue

        if etype == "assistant":
            if not digest.model:
                digest.model = msg.get("model")
            _accumulate_usage(digest, msg.get("usage"))
            _ingest_assistant_content(digest, ts, msg.get("content"))
        elif etype == "user":
            _ingest_user_content(digest, ts, msg.get("content"), answered_ids)

    # Mark tool calls that never got a result (e.g. session ended mid-tool, or
    # the call is still in flight). Without this, a trailing un-answered tool
    # like `Bash(ls scripts/)` reads as completed and a summarizer invents an
    # outcome ("no scripts found"). The marker tells it the result is unknown.
    for ev in digest.events:
        if ev.kind == KIND_TOOL and ev.tool_id and ev.tool_id not in answered_ids:
            ev.text += " [no result yet]"

    return digest


def _accumulate_usage(digest: SessionDigest, usage: object) -> None:
    if not isinstance(usage, dict):
        return
    digest.input_tokens += int(usage.get("input_tokens", 0) or 0)
    digest.output_tokens += int(usage.get("output_tokens", 0) or 0)
    digest.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0) or 0)
    digest.cache_creation_tokens += int(
        usage.get("cache_creation_input_tokens", 0) or 0
    )


def _ingest_assistant_content(
    digest: SessionDigest, ts: str | None, content: object
) -> None:
    if not isinstance(content, list):
        if isinstance(content, str) and content.strip():
            digest.events.append(DigestEvent(ts, KIND_SAY, _clip(content, 200)))
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = str(block.get("text", "")).strip()
            if text:
                digest.events.append(DigestEvent(ts, KIND_SAY, _clip(text, 200)))
        elif btype == "thinking":
            digest.events.append(DigestEvent(ts, KIND_THINKING, "(thinking)"))
        elif btype == "tool_use":
            digest.events.append(
                DigestEvent(
                    ts,
                    KIND_TOOL,
                    _summarize_tool(str(block.get("name", "?")), block.get("input")),
                    tool_id=block.get("id"),
                )
            )


def _ingest_user_content(
    digest: SessionDigest, ts: str | None, content: object, answered: set[str]
) -> None:
    if isinstance(content, str):
        if content.strip():
            digest.events.append(
                DigestEvent(ts, KIND_PROMPT, _normalize_prompt(content))
            )
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            tuid = block.get("tool_use_id")
            if tuid:
                answered.add(tuid)
            raw = block.get("content")
            text = raw if isinstance(raw, str) else json.dumps(raw)
            digest.events.append(DigestEvent(ts, KIND_RESULT, _clip(text, 120)))


def render_for_prompt(
    digest: SessionDigest, *, last_n: int = 14, max_chars: int = 1800
) -> str:
    """Render the most recent events as a compact text block for the narrator.

    Thinking markers are dropped (no signal for a one-line narrative). The block
    is the *only* thing the model sees about this lane — never raw JSONL/ANSI.
    """
    useful = [ev for ev in digest.events if ev.kind != KIND_THINKING]
    window = useful[-last_n:]
    lines: list[str] = []
    label = {
        KIND_PROMPT: "ASKED",
        KIND_SAY: "SAID",
        KIND_TOOL: "TOOL",
        KIND_RESULT: "RESULT",
    }
    for ev in window:
        lines.append(f"- {label.get(ev.kind, ev.kind.upper())}: {ev.text}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        # Trim from the top (oldest) until within budget.
        while lines and len("\n".join(lines)) > max_chars:
            lines.pop(0)
        block = "\n".join(lines)
    return block or "- (no activity yet)"
