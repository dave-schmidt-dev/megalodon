"""Smoke test for scripts/_logging.py."""

import logging
from pathlib import Path

from scripts._logging import LOG_PATH, get_logger


def test_get_logger_returns_logger():
    log = get_logger("test.smoke")
    assert isinstance(log, logging.Logger)


def test_default_level_is_warning():
    log = get_logger("test.level.warn")
    assert log.level == logging.WARNING


def test_debug_flag_lowers_level():
    log = get_logger("test.level.debug", debug=True)
    assert log.level == logging.DEBUG


def test_log_path_is_tmp_megalodon_scripts():
    assert LOG_PATH == "/tmp/megalodon-scripts.log"


def test_writing_a_warning_creates_log_file():
    log = get_logger("test.write")
    log.warning("hello from test")
    assert Path(LOG_PATH).exists()
