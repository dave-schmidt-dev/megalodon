"""v9.2-specific operator-tunable constants.

Use AppConfig overrides at runtime; edit this file to change defaults.
Leading underscore in the module name is the internal-package convention;
the constants themselves are part of the operator-tunable surface.
"""

from typing import Final

INITIAL_PANE_COLS: Final[int] = 200  # Wide enough for typical JSON/log lines without wrap; xterm.js FitAddon scales the view.
INITIAL_PANE_ROWS: Final[int] = 50  # Tall enough for typical bursts; full dynamic resize deferred to v9.3.
SSE_PER_SUBSCRIBER_QUEUE_MAXSIZE: Final[int] = 32  # At 8 KiB chunks, 32 entries ≈ 256 KB ≈ 256 ms of buffering under sustained 1 MB/s bursts.
SSE_MAX_SUBSCRIBERS_PER_LANE: Final[int] = 10  # Prevent unbounded fan-out; one operator with a few tabs is fine.
STREAM_LOG_WARN_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MB; watchdog warns at this size.
TAIL_ON_CONNECT_BYTES: Final[int] = 64 * 1024  # 64 KB ≈ ~800 lines on first SSE connect.
COOKIE_MAX_AGE_SECONDS: Final[int] = 86400  # One workday; operator re-runs `python -m megalodon_ui ...` to rotate.
BEARER_TOKEN_BYTES: Final[int] = 32  # `secrets.token_urlsafe(32)` ≈ 43 base64 chars; standard entropy ceiling.
LIFESPAN_STARTUP_TIMEOUT_SECONDS: Final[int] = 30  # If spawn doesn't complete in 30 s the server exits non-zero.
SOCKET_PATH_LIMIT_BYTES: Final[int] = 100  # Conservative margin under macOS 104 / Linux 108.
