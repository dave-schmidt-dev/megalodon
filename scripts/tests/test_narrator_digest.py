"""Unit tests for megalodon_ui.narrator.digest — transcript → compact digest."""

from __future__ import annotations

import json
from pathlib import Path

from megalodon_ui.narrator.digest import (
    KIND_RESULT,
    KIND_SAY,
    KIND_TOOL,
    parse_session,
    render_for_prompt,
)


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _assistant(
    content: list[dict], usage: dict | None = None, model: str = "opus"
) -> dict:
    msg: dict = {"role": "assistant", "content": content, "model": model}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "timestamp": "2026-05-23T20:00:00Z", "message": msg}


def _user(content) -> dict:
    return {
        "type": "user",
        "timestamp": "2026-05-23T20:00:01Z",
        "message": {"role": "user", "content": content},
    }


def test_parses_tools_text_and_results_in_order(tmp_path: Path) -> None:
    f = tmp_path / "s1.jsonl"
    _write_jsonl(
        f,
        [
            _user(
                "<command-name>/loop</command-name>"
                "<command-args>Read launch-AUDIT.md and execute one iteration.</command-args>"
            ),
            _assistant(
                [{"type": "thinking", "thinking": "hmm"}],
                usage={"input_tokens": 10, "output_tokens": 5},
            ),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/x/launch-AUDIT.md"},
                    }
                ]
            ),
            _user(
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "1\t# launch-AUDIT.md\n2\t...",
                    }
                ]
            ),
            _assistant(
                [
                    {"type": "text", "text": "Claiming the AUDIT lane now."},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {
                            "command": "scripts/claim.sh P1-A agent-x",
                            "description": "Claim P1-A",
                        },
                    },
                ],
                usage={"input_tokens": 20, "output_tokens": 8},
            ),
        ],
    )

    d = parse_session(f)

    kinds = [(e.kind, e.text) for e in d.events]
    # Prompt wrapper stripped to the bare instruction.
    assert kinds[0][0] == "prompt"
    assert "Read launch-AUDIT.md" in kinds[0][1]
    assert "<command" not in kinds[0][1]
    # tool_use rendered as Name(target).
    assert ("tool", "Read(launch-AUDIT.md)") in kinds
    assert any(
        k == KIND_TOOL and t.startswith("Bash(") and "claim.sh" in t for k, t in kinds
    )
    assert ("say", "Claiming the AUDIT lane now.") in kinds
    assert any(k == KIND_RESULT for k, _ in kinds)

    assert d.latest_tool.startswith("Bash(")
    assert d.latest_say == "Claiming the AUDIT lane now."
    assert d.input_tokens == 30
    assert d.output_tokens == 13
    assert d.total_tokens == 43
    assert d.model == "opus"


def test_render_drops_thinking_and_labels(tmp_path: Path) -> None:
    f = tmp_path / "s2.jsonl"
    _write_jsonl(
        f,
        [
            _assistant([{"type": "thinking", "thinking": "secret"}]),
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/a/b.md"},
                    }
                ]
            ),
        ],
    )
    out = render_for_prompt(parse_session(f))
    assert "secret" not in out
    assert "(thinking)" not in out
    assert "TOOL: Read(b.md)" in out


def test_missing_file_is_empty_not_error(tmp_path: Path) -> None:
    d = parse_session(tmp_path / "nope.jsonl")
    assert d.events == []
    assert d.total_tokens == 0
    assert render_for_prompt(d) == "- (no activity yet)"


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    f = tmp_path / "s3.jsonl"
    f.write_text(
        "not json\n"
        + json.dumps(_assistant([{"type": "text", "text": "hello"}]))
        + "\n{ broken\n",
        encoding="utf-8",
    )
    d = parse_session(f)
    assert [e.text for e in d.events if e.kind == KIND_SAY] == ["hello"]


def test_unanswered_tool_marked_no_result(tmp_path: Path) -> None:
    """A tool_use with no matching tool_result gets a [no result yet] marker."""
    f = tmp_path / "s5.jsonl"
    _write_jsonl(
        f,
        [
            # answered tool: has a matching tool_result -> no marker
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Read",
                        "input": {"file_path": "/a.md"},
                    }
                ]
            ),
            _user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]),
            # trailing unanswered tool: session ends here -> marker
            _assistant(
                [
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "Bash",
                        "input": {"command": "ls scripts/"},
                    }
                ]
            ),
        ],
    )
    d = parse_session(f)
    tools = [e for e in d.events if e.kind == KIND_TOOL]
    answered = next(e for e in tools if e.tool_id == "t1")
    pending = next(e for e in tools if e.tool_id == "t2")
    assert "[no result yet]" not in answered.text
    assert pending.text.endswith("[no result yet]")


def test_window_respects_last_n(tmp_path: Path) -> None:
    f = tmp_path / "s4.jsonl"
    events = [
        _assistant(
            [{"type": "tool_use", "name": "Read", "input": {"file_path": f"/f{i}.md"}}]
        )
        for i in range(20)
    ]
    _write_jsonl(f, events)
    out = render_for_prompt(parse_session(f), last_n=5)
    assert out.count("TOOL:") == 5
    # Keeps the most-recent five (f15..f19), drops the oldest.
    assert "Read(f19.md)" in out
    assert "Read(f0.md)" not in out
