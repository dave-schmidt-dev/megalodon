"""Test that the @pytest.mark.isolated marker is registered."""

import subprocess
import shutil

import pytest


def test_isolated_marker_registered():
    """Verify the isolated marker is registered (not just used)."""
    if not shutil.which("uv"):
        pytest.skip("uv not on PATH")

    result = subprocess.run(
        ["uv", "run", "--with", "pytest", "pytest", "--markers"],
        cwd="/Users/dave/Documents/Projects/megalodon",
        capture_output=True,
        text=True,
    )

    # Check that the marker is listed in output
    assert "isolated:" in result.stdout, (
        f"isolated marker not found in pytest --markers output.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
