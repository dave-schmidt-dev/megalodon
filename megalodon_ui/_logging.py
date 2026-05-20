"""RotatingFileHandler factory for megalodon_ui web UI.

Writes to /tmp/megalodon-ui.log, 1 MB / 2 backups. DEBUG when
os.environ.get("MEGALODON_DEBUG") == "1" or debug=True passed.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_PATH = "/tmp/megalodon-ui.log"
MAX_BYTES = 1_048_576
BACKUP_COUNT = 2


def get_logger(name: str, debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured (e.g., re-import in same process)
        should_debug = debug or os.environ.get("MEGALODON_DEBUG") == "1"
        logger.setLevel(logging.DEBUG if should_debug else logging.INFO)
        return logger
    should_debug = debug or os.environ.get("MEGALODON_DEBUG") == "1"
    logger.setLevel(logging.DEBUG if should_debug else logging.INFO)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
