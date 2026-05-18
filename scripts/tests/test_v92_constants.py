"""Tests for v9.2 operator-tunable constants."""

import pytest

# Test importability of all 10 constants
from megalodon_ui._v92_constants import (
    BEARER_TOKEN_BYTES,
    COOKIE_MAX_AGE_SECONDS,
    INITIAL_PANE_COLS,
    INITIAL_PANE_ROWS,
    LIFESPAN_STARTUP_TIMEOUT_SECONDS,
    SOCKET_PATH_LIMIT_BYTES,
    SSE_MAX_SUBSCRIBERS_PER_LANE,
    SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE,
    STREAM_LOG_WARN_BYTES,
    TAIL_ON_CONNECT_BYTES,
)


@pytest.mark.parametrize(
    "constant,expected_value",
    [
        (INITIAL_PANE_COLS, 200),
        (INITIAL_PANE_ROWS, 50),
        (SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE, 32),
        (SSE_MAX_SUBSCRIBERS_PER_LANE, 10),
        (STREAM_LOG_WARN_BYTES, 500 * 1024 * 1024),
        (TAIL_ON_CONNECT_BYTES, 64 * 1024),
        (COOKIE_MAX_AGE_SECONDS, 86400),
        (BEARER_TOKEN_BYTES, 32),
        (LIFESPAN_STARTUP_TIMEOUT_SECONDS, 30),
        (SOCKET_PATH_LIMIT_BYTES, 100),
    ],
)
def test_constant_values(constant, expected_value):
    """Verify each constant matches the plan value."""
    assert constant == expected_value


def test_no_symbol_overlap():
    """Verify no symbol overlap between v92 constants and base constants."""
    import megalodon_ui.constants
    import megalodon_ui._v92_constants

    v92_public = {x for x in dir(megalodon_ui._v92_constants) if not x.startswith("_") and x.isupper()}
    base_public = {x for x in dir(megalodon_ui.constants) if not x.startswith("_") and x.isupper()}

    overlap = v92_public & base_public
    assert not overlap, f"Symbol overlap found: {overlap}"
