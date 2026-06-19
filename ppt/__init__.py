"""ppt — 永久投资组合辅助工具."""

import logging
import os
from pathlib import Path


def _setup_logging() -> None:
    """Configure logging to ~/.pp/pp.log (§2.2)."""
    log_dir = Path.home() / ".pp"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "pp.log"

    level = logging.DEBUG if os.environ.get("PP_DEBUG") == "1" else logging.INFO
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.FileHandler(str(log_path), encoding="utf-8"),
        ],
    )


_setup_logging()
