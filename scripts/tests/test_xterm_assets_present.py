"""P5.1 — assert vendored xterm.js assets exist and VERSION.txt is consistent.

The v9.2 dashboard imports xterm.js + addon-fit straight from the static
mount (no bundler). If a future contributor "updates" the vendored files
without re-running the SHA pin, the file bytes will silently diverge from
the recorded version + hash and we will have no idea what's actually
shipping. This test makes that divergence loud.

Asserts:
  - ui/static/xterm/{xterm.js, xterm.css, addon-fit.js} exist + non-empty
  - VERSION.txt exists and is non-empty
  - VERSION.txt declares versions for @xterm/xterm and @xterm/addon-fit
  - VERSION.txt declares SHA256 for each vendored file
  - Computed SHA256 of each file equals the declared SHA256
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest


XTERM_DIR = Path(__file__).resolve().parent.parent.parent / "ui" / "static" / "xterm"

VENDORED_FILES = ("xterm.js", "xterm.css", "addon-fit.js")


def _read_version_txt() -> str:
    path = XTERM_DIR / "VERSION.txt"
    assert path.is_file(), f"VERSION.txt missing at {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), "VERSION.txt is empty"
    return text


def test_xterm_directory_exists() -> None:
    assert XTERM_DIR.is_dir(), f"vendored xterm directory missing at {XTERM_DIR}"


@pytest.mark.parametrize("filename", VENDORED_FILES)
def test_vendored_file_present_and_nonempty(filename: str) -> None:
    path = XTERM_DIR / filename
    assert path.is_file(), f"missing vendored file: {path}"
    assert path.stat().st_size > 0, f"vendored file is empty: {path}"


def test_version_txt_declares_xterm_versions() -> None:
    text = _read_version_txt()
    assert "@xterm/xterm" in text, "VERSION.txt missing @xterm/xterm declaration"
    assert "@xterm/addon-fit" in text, "VERSION.txt missing @xterm/addon-fit declaration"
    assert re.search(r"@xterm/xterm@\d+\.\d+\.\d+", text), (
        "VERSION.txt must pin @xterm/xterm to a semver (e.g. @xterm/xterm@5.5.0)"
    )
    assert re.search(r"@xterm/addon-fit@\d+\.\d+\.\d+", text), (
        "VERSION.txt must pin @xterm/addon-fit to a semver"
    )


def test_version_txt_declares_license() -> None:
    text = _read_version_txt()
    assert "MIT" in text or "License" in text, (
        "VERSION.txt must record the upstream license (xterm.js is MIT)"
    )


@pytest.mark.parametrize("filename", VENDORED_FILES)
def test_version_txt_sha256_matches_file_bytes(filename: str) -> None:
    text = _read_version_txt()
    file_path = XTERM_DIR / filename
    actual = hashlib.sha256(file_path.read_bytes()).hexdigest()
    pattern = re.compile(
        rf"{re.escape(filename)}\s*[:=]?\s*(?:sha256\s*[:=]?\s*)?([0-9a-fA-F]{{64}})",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    assert match, (
        f"VERSION.txt missing SHA256 line for {filename}. "
        f"Computed hash for current bytes: {actual}"
    )
    declared = match.group(1).lower()
    assert declared == actual, (
        f"SHA256 mismatch for {filename}: VERSION.txt declares {declared} "
        f"but file bytes hash to {actual}"
    )
