"""RotatingFileHandler factory for v9 helper scripts.

Per global CLAUDE.md: file logging from day one, RotatingFileHandler to
/tmp/<project>.log, 1 MB / 2 backups, WARNING+ default, DEBUG with --debug.
"""

import logging
from logging.handlers import RotatingFileHandler

LOG_PATH = "/tmp/megalodon-scripts.log"
MAX_BYTES = 1_048_576
BACKUP_COUNT = 2


def get_logger(name: str, debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured (e.g., re-import in same process)
        logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        return logger
    logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)sZ | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
