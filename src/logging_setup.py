"""Shared logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.config import get_settings


def setup_logging(name: str | None = None) -> logging.Logger:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

        fh = logging.FileHandler(log_dir / "healthcare_agent.log")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    return logging.getLogger(name or "healthcare")
