from __future__ import annotations

import logging
import sys

from app.core.config import settings


def setup_logging(level: str | None = None) -> logging.Logger:
    log_level = level or ("DEBUG" if settings.debug else "INFO")
    logger = logging.getLogger("claw_trader")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = setup_logging()
