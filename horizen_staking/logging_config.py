"""Application logging — rotating file (logs/app.log) + stderr.

Configured once in the app factory. Modules log via `logging.getLogger(__name__)`,
which propagates to the "horizen_staking" logger's handlers. Full exception detail
is logged here (server-side) while the API returns only generic messages to clients.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Config

_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("horizen_staking")
    logger.setLevel(logging.DEBUG if cfg.debug else logging.INFO)
    if logger.handlers:  # already configured (e.g. create_app called again)
        return logger

    fmt = logging.Formatter(_FMT, _DATEFMT)

    log_dir = cfg.project_root / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        pass  # no writable logs/ dir (e.g. read-only deploy) -> stderr only

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger
