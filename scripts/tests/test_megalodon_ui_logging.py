"""Tests for megalodon_ui._logging module."""

import logging

from megalodon_ui._logging import get_logger


def test_get_logger_returns_logger():
    """Test that get_logger returns a logging.Logger instance."""
    logger = get_logger("test_returns_logger")
    assert isinstance(logger, logging.Logger)


def test_rotating_file_handler_configured():
    """Test that logger has exactly one RotatingFileHandler to megalodon-ui.log."""
    logger = get_logger("test_handler_config")
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert handler.__class__.__name__ == "RotatingFileHandler"
    assert handler.baseFilename.endswith("megalodon-ui.log")


def test_idempotency():
    """Test that calling get_logger twice returns same instance with one handler."""
    first = get_logger("test_idempotent")
    second = get_logger("test_idempotent")
    assert first is second
    assert len(first.handlers) == 1


def test_debug_via_env(monkeypatch):
    """Test that MEGALODON_DEBUG=1 sets DEBUG level."""
    monkeypatch.setenv("MEGALODON_DEBUG", "1")
    logger = get_logger("test_debug_env")
    assert logger.level == logging.DEBUG


def test_debug_via_param():
    """Test that debug=True parameter sets DEBUG level."""
    logger = get_logger("test_debug_param", debug=True)
    assert logger.level == logging.DEBUG


def test_default_level_info(monkeypatch):
    """Test that default level is INFO (no debug)."""
    monkeypatch.delenv("MEGALODON_DEBUG", raising=False)
    logger = get_logger("test_default_level")
    assert logger.level == logging.INFO
