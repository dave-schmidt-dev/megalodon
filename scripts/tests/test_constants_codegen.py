"""V9 M4 codegen tests."""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import gen_js_constants  # noqa: E402


def test_generates_valid_js_with_banner():
    js = gen_js_constants.generate_js()
    assert "AUTO-GENERATED" in js
    assert "DO NOT EDIT" in js
    assert "megalodon_ui/constants.py" in js


def test_string_constant_emitted():
    js = gen_js_constants.generate_js()
    assert 'export const CONTROL_MODE_KEY = "controlMode";' in js


def test_int_constant_emitted():
    js = gen_js_constants.generate_js()
    assert "export const STALE_THRESHOLD_SECONDS = 900;" in js


def test_tuple_constant_emitted_as_js_array():
    js = gen_js_constants.generate_js()
    # Tuple emitted as JS array literal with all event names present.
    assert "export const SSE_EVENT_TYPES = [" in js
    assert '"status-change"' in js
    assert '"task-change"' in js
    assert "];" in js


def test_skips_private_attributes(monkeypatch):
    import megalodon_ui.constants as c
    monkeypatch.setattr(c, "_PRIVATE", "x", raising=False)
    js = gen_js_constants.generate_js()
    assert "_PRIVATE" not in js


def test_skips_non_upper_attributes(monkeypatch):
    import megalodon_ui.constants as c
    monkeypatch.setattr(c, "camelCase", "x", raising=False)
    monkeypatch.setattr(c, "snake_case", "x", raising=False)
    js = gen_js_constants.generate_js()
    assert "camelCase" not in js
    assert "snake_case" not in js


def test_unsupported_type_raises():
    # Simulate by adding a dict at runtime; codegen should refuse.
    import megalodon_ui.constants as c
    original = getattr(c, "BAD_DICT", None)
    c.BAD_DICT = {"a": 1}
    try:
        with pytest.raises(SystemExit) as exc:
            gen_js_constants.generate_js()
        assert "BAD_DICT" in str(exc.value)
    finally:
        if original is None:
            delattr(c, "BAD_DICT")
        else:
            c.BAD_DICT = original


def test_check_mode_passes_when_synced(tmp_path, monkeypatch):
    # Write current generated output to a temp file, point gen at it, --check should exit 0.
    js = gen_js_constants.generate_js()
    tmp_js = tmp_path / "constants.js"
    tmp_js.write_text(js, encoding="utf-8")
    monkeypatch.setattr(gen_js_constants, "JS_OUTPUT_PATH", tmp_js)
    rc = gen_js_constants.main(["--check"])
    assert rc == 0


def test_check_mode_fails_when_drifted(tmp_path, monkeypatch, capsys):
    tmp_js = tmp_path / "constants.js"
    tmp_js.write_text("// stale\n", encoding="utf-8")
    monkeypatch.setattr(gen_js_constants, "JS_OUTPUT_PATH", tmp_js)
    rc = gen_js_constants.main(["--check"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "OUT OF SYNC" in captured.err


def test_committed_js_matches_python():
    """Regression net: if someone edits constants.py and forgets to regenerate JS,
    pytest catches it."""
    if not gen_js_constants.JS_OUTPUT_PATH.exists():
        pytest.skip("constants.js not yet generated (first run)")
    expected = gen_js_constants.generate_js()
    actual = gen_js_constants.JS_OUTPUT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "constants.js drifted from constants.py — "
        "run python3 scripts/gen_js_constants.py"
    )
