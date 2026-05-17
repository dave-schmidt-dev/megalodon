"""V9 A9 — tests for operator-side Claude Code session JSONL parser."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.parse_session_tokens import parse


def test_parses_empty_jsonl_returns_zero_tokens(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    result = parse(p)
    assert result["tokens"]["input"] == 0
    assert result["tokens"]["output"] == 0


def test_sums_input_output_tokens(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(
        json.dumps({"message": {"usage": {"input_tokens": 100, "output_tokens": 50}, "model": "claude-opus-4-7"}}) + "\n"
        + json.dumps({"message": {"usage": {"input_tokens": 200, "output_tokens": 75}, "model": "claude-opus-4-7"}}) + "\n"
    )
    result = parse(p)
    assert result["tokens"]["input"] == 300
    assert result["tokens"]["output"] == 125


def test_handles_cache_tokens(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"message": {"usage": {
        "input_tokens": 10, "output_tokens": 5,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 200,
    }, "model": "claude-opus-4-7"}}) + "\n")
    result = parse(p)
    assert result["tokens"]["cache_creation"] == 100
    assert result["tokens"]["cache_read"] == 200


def test_estimates_cost_for_known_model(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"message": {"usage": {
        "input_tokens": 1_000_000, "output_tokens": 1_000_000
    }, "model": "claude-opus-4-7"}}) + "\n")
    result = parse(p)
    # opus pricing: 15/M in + 75/M out = 90 USD
    assert result["estimated_cost_usd"] == 90.0


def test_handles_malformed_lines_gracefully(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("not-json garbage\n" + json.dumps({"message": {"usage": {"input_tokens": 5, "output_tokens": 3}}}) + "\n")
    result = parse(p)
    assert result["tokens"]["input"] == 5
