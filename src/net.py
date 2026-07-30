"""Retry helper shared by every network call in the ingest layer.

Both upstreams here are free, unofficial and rate-limited (yfinance scrapes
Yahoo; the RSS feeds are public endpoints), so a transient failure is the normal
case rather than the exceptional one. Everything that touches the network goes
through :func:`with_retries`.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_TIMEOUT = 20.0  # seconds, passed down to requests/yfinance
MAX_ATTEMPTS = 3


def with_retries(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = MAX_ATTEMPTS,
    base_delay: float = 1.5,
) -> T:
    """Call ``fn`` up to ``attempts`` times with exponential backoff + jitter.

    Re-raises the last exception if every attempt fails; callers decide whether
    that is fatal (it never is at ticker level -- see prices.py).
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - upstream raises many types
            last = exc
            if attempt == attempts:
                break
            # jitter stops 8 tickers from retrying in lockstep against a rate limiter
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.4)
            log.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                what, attempt, attempts, exc, delay,
            )
            time.sleep(delay)

    assert last is not None
    log.error("%s failed after %d attempts: %s", what, attempts, last)
    raise last
