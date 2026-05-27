"""Smoke test for scripts/_logging.py."""

import logging

import pytest

from scripts._logging import LOG_PATH, get_logger


@pytest.fixture(autouse=True)
def _redirect_log_path(tmp_path, monkeypatch):
    """Point the RotatingFileHandler at a per-test tmp_path file.

    ``get_logger`` builds a ``RotatingFileHandler(LOG_PATH, ...)`` which OPENS
    (and thus creates, even with no write) the target file at construction time.
    Redirecting LOG_PATH here keeps the suite from ever creating the shared
    global ``/tmp/megalodon-scripts.log``. The real-path contract is asserted
    separately in ``test_log_path_is_tmp_megalodon_scripts`` (which never builds
    a handler).
    """
    monkeypatch.setattr("scripts._logging.LOG_PATH", str(tmp_path / "scripts.log"))


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
    # Contract: the module's DEFAULT log path. The autouse fixture patches the
    # module attribute, so assert against the imported constant captured at
    # import time (the real committed value), not the patched module attr.
    assert LOG_PATH == "/tmp/megalodon-scripts.log"


def test_writing_a_warning_creates_log_file(tmp_path, monkeypatch):
    # Redirect the RotatingFileHandler path to a per-test tmp_path file so the
    # suite never writes the shared global /tmp/megalodon-scripts.log. get_logger
    # reads module-level LOG_PATH at handler-creation time and caches handlers by
    # logger name, so we patch LOG_PATH and use a unique name to force a fresh
    # handler bound to the redirected path.
    redirected = tmp_path / "megalodon-scripts.log"
    monkeypatch.setattr("scripts._logging.LOG_PATH", str(redirected))
    log = get_logger("test.write.redirected")
    log.warning("hello from test")
    assert redirected.exists()
