"""Regression test for v9.3.6 — applier rolling log emits actual UTC.

Bug: `%(asctime)s` defaulted to time.localtime, but we appended a literal `Z`
suffix claiming the timestamp was UTC. The check_megalodon_workers.sh
stale-lane detector parsed `Z`-suffixed strings as UTC and computed silent-
duration vs Unix epoch (`date +%s`), which produced ~4-hour skews in EDT and
falsely flagged active lanes as 200+ minutes stale.

Fix: set the Formatter's `converter = time.gmtime` so the `Z` suffix matches.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from megalodon_ui.queue.applier import _setup_applier_logger


_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")


def _drop_existing_handlers():
    """Remove cached handlers from prior tests so re-setup re-attaches."""
    logger = logging.getLogger("megalodon.queue.applier")
    for h in list(logger.handlers):
        logger.removeHandler(h)


def test_applier_log_timestamp_is_actual_utc(tmp_path):
    _drop_existing_handlers()
    _setup_applier_logger(tmp_path)
    logger = logging.getLogger("megalodon.queue.applier")
    log_path = tmp_path / ".fleet" / "queue-applier.log"

    # Capture wall-clock UTC before emission.
    before_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("test-message-utc-check")
    # No wait needed: RotatingFileHandler flushes on emit, so the record is on
    # disk by the time logger.info() returns.
    after_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    assert log_path.exists(), "applier log file not created"
    contents = log_path.read_text()
    assert "test-message-utc-check" in contents, contents

    match = _TS_RE.search(contents)
    assert match is not None, f"no Z-suffixed timestamp in log: {contents!r}"
    parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")

    # The parsed timestamp (claimed UTC by the trailing `Z`) must fall within
    # the window we captured before/after. Tolerance: 2 seconds either side
    # for filesystem flush + clock granularity.
    assert (
        before_utc.timestamp() - 2 <= parsed.timestamp() <= after_utc.timestamp() + 2
    ), (
        f"timestamp drift: parsed={parsed.isoformat()} "
        f"before={before_utc.isoformat()} after={after_utc.isoformat()}. "
        "If parsed is ~4 hours off, the Formatter is still using localtime."
    )


def test_applier_log_not_localtime_in_non_utc_zone(tmp_path, monkeypatch):
    """Hardening: explicitly assert UTC even when TZ env is set to non-UTC.

    Without this guard, a developer in EDT would see the bug pass on their box
    by accident-of-locale.
    """
    _drop_existing_handlers()
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()  # apply the TZ change

    try:
        _setup_applier_logger(tmp_path)
        logger = logging.getLogger("megalodon.queue.applier")
        log_path = tmp_path / ".fleet" / "queue-applier.log"

        before_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        logger.info("tz-aware-utc-check")
        # No wait needed: RotatingFileHandler flushes on emit.

        match = _TS_RE.search(log_path.read_text())
        assert match is not None
        parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
        skew_s = abs(parsed.timestamp() - before_utc.timestamp())
        assert skew_s < 5, (
            f"applier timestamp skewed {skew_s}s from wall UTC under TZ=America/New_York "
            f"— Formatter is still using localtime. parsed={parsed.isoformat()} "
            f"before={before_utc.isoformat()}"
        )
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()
