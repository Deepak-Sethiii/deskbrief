"""One place to configure logging for every entry point."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Log to stdout only.

    run_refresh.bat appends stdout to logs/deskbrief.log, so adding a FileHandler
    here would write every line twice. One sink, one source of truth.
    """
    root = logging.getLogger()
    if root.handlers:  # idempotent: repeated CLI calls in one process
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)
    root.setLevel(level)

    # yfinance/urllib3 are chatty at INFO and drown out our own progress lines.
    for noisy in ("urllib3", "yfinance", "peewee", "matplotlib", "PIL", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
