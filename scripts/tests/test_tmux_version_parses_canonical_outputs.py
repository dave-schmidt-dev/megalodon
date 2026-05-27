"""Tests for tmux version parsing and probing."""

from unittest.mock import patch

import pytest

from megalodon_ui._tmux_version import parse_tmux_version, probe_or_exit_6


class TestParseTmuxVersion:
    """Test parse_tmux_version with canonical and edge cases."""

    @pytest.mark.parametrize(
        "output,expected",
        [
            ("tmux 3.5a", (3, 5)),
            ("tmux 3.0a", (3, 0)),
            ("tmux 2.5", (2, 5)),
            ("tmux 2.6", (2, 6)),
            ("tmux next-3.6", (3, 6)),
        ],
    )
    def test_parse_canonical_versions(self, output, expected):
        """Parse canonical tmux version strings."""
        assert parse_tmux_version(output) == expected

    @pytest.mark.parametrize(
        "output",
        [
            "",
            "banana",
            "tmux abc",
        ],
    )
    def test_parse_garbled_raises_valueerror(self, output):
        """Garbled input raises ValueError."""
        with pytest.raises(ValueError):
            parse_tmux_version(output)


class TestProbeOrExit6:
    """Test probe_or_exit_6 exit behavior."""

    def test_probe_success_with_good_version(self):
        """probe_or_exit_6 returns None on good version."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "tmux 3.5a\n"
            assert probe_or_exit_6() is None

    def test_probe_exit_6_on_not_found(self):
        """probe_or_exit_6 exits 6 when tmux not on PATH."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit) as exc_info:
                probe_or_exit_6()
            assert exc_info.value.code == 6

    def test_probe_exit_6_on_nonzero_rc(self):
        """probe_or_exit_6 exits 6 on non-zero return code."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            with pytest.raises(SystemExit) as exc_info:
                probe_or_exit_6()
            assert exc_info.value.code == 6

    def test_probe_exit_6_on_parse_error(self):
        """probe_or_exit_6 exits 6 on parse error."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "garbled output"
            with pytest.raises(SystemExit) as exc_info:
                probe_or_exit_6()
            assert exc_info.value.code == 6

    def test_probe_exit_6_on_too_old_version(self):
        """probe_or_exit_6 exits 6 when version is too old."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "tmux 2.5\n"
            with pytest.raises(SystemExit) as exc_info:
                probe_or_exit_6()
            assert exc_info.value.code == 6
