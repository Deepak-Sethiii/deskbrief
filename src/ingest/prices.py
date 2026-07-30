"""Price and fundamentals ingest via yfinance.

yfinance is an unofficial scraper of Yahoo Finance. Two consequences shape this
module:

1. ``Ticker.info`` issues a separate, heavily rate-limited request and regularly
   returns a partial dict or raises. ``Ticker.fast_info`` is cheap and stable, so
   we take everything we can from it and only reach for ``.info`` for the three
   fields it cannot give us (P/E, EV/EBITDA, sector) -- inside try/except.
2. Any single name can fail at any time. A failure is logged and skipped; it
   never aborts the run. We write None rather than inventing a value.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

import pandas as pd
import yfinance as yf
from sqlalchemy.engine import Engine

from src.config import Watchlist, load_watchlist
from src.db import fundamentals as fundamentals_table
from src.db import prices as prices_table
from src.db import upsert
from src.net import DEFAULT_TIMEOUT, with_retries

log = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    """NaN/inf/pandas-NA -> None, numpy scalars -> Python scalars."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):  # numpy scalar
        try:
            value = value.item()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    return value


def fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Daily OHLCV frame for one symbol. Retried with backoff."""

    def _call() -> pd.DataFrame:
        frame = yf.Ticker(symbol).history(
            period=period,
            interval=interval,
            # yfinance already split-adjusts OHLC either way (verified against
            # HDFCBANK.NS 2:1 on 2025-08-26: the series is continuous through
            # it). auto_adjust=True additionally back-adjusts for dividends, so
            # what we store is a total-return series -- which is what returns
            # and 52w-high math want. Cost: `close` is not the print a broker
            # shows, and we inherit Yahoo's adjustments unverified.
            auto_adjust=True,
            timeout=DEFAULT_TIMEOUT,
        )
        if frame is None or frame.empty:
            raise ValueError(f"empty history frame for {symbol}")
        return frame

    return with_retries(_call, what=f"history({symbol})")


def history_to_rows(symbol: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        stamp = index.date() if hasattr(index, "date") else pd.Timestamp(index).date()
        close = _clean(row.get("Close"))
        if close is None:
            continue  # a bar with no close is not a bar
        volume = _clean(row.get("Volume"))
        rows.append(
            {
                "ticker": symbol,
                "date": stamp,
                "open": _clean(row.get("Open")),
                "high": _clean(row.get("High")),
                "low": _clean(row.get("Low")),
                "close": close,
                "volume": int(volume) if volume is not None else None,
            }
        )
    return rows


def fetch_fundamentals(symbol: str, asof: dt.date) -> dict[str, Any]:
    """Best-effort fundamentals row. Always returns a row; fields may be None."""
    record: dict[str, Any] = {
        "ticker": symbol,
        "asof_date": asof,
        "market_cap": None,
        "pe": None,
        "ev_ebitda": None,
        "sector": None,
        "currency": None,
    }

    # --- preferred path: fast_info (one cheap request, rarely rate-limited) ---
    try:
        fast = with_retries(lambda: yf.Ticker(symbol).fast_info, what=f"fast_info({symbol})")
        record["market_cap"] = _clean(fast.get("marketCap") or fast.get("market_cap"))
        record["currency"] = _clean(fast.get("currency"))
    except Exception as exc:  # noqa: BLE001
        log.warning("fast_info unavailable for %s: %s -- leaving those fields None", symbol, exc)

    # --- fallback path: .info, only for what fast_info cannot supply ---
    try:
        info = with_retries(lambda: yf.Ticker(symbol).get_info(), what=f"info({symbol})")
        info = info or {}
        record["pe"] = _clean(info.get("trailingPE"))
        record["ev_ebitda"] = _clean(info.get("enterpriseToEbitda"))
        record["sector"] = _clean(info.get("sector"))
        if record["market_cap"] is None:
            record["market_cap"] = _clean(info.get("marketCap"))
        if record["currency"] is None:
            record["currency"] = _clean(info.get("currency"))
    except Exception as exc:  # noqa: BLE001
        log.warning(".info unavailable for %s: %s -- pe/ev_ebitda/sector stay None", symbol, exc)

    return record


def ingest_prices(engine: Engine, watchlist: Watchlist | None = None) -> dict[str, Any]:
    """Load prices + fundamentals for the whole watchlist. Never raises per-ticker."""
    watchlist = watchlist or load_watchlist()
    asof = dt.date.today()

    price_rows: list[dict[str, Any]] = []
    fundamental_rows: list[dict[str, Any]] = []
    ok: list[str] = []
    failed: list[str] = []

    for spec in watchlist.tickers:
        symbol = spec.ticker
        try:
            frame = fetch_history(symbol, watchlist.history_period, watchlist.history_interval)
            rows = history_to_rows(symbol, frame)
            if not rows:
                raise ValueError("history returned no usable bars")
            price_rows.extend(rows)
            fundamental_rows.append(fetch_fundamentals(symbol, asof))
            ok.append(symbol)
            log.info("%-14s %4d bars, last close %.2f", symbol, len(rows), rows[-1]["close"])
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not kill the run
            failed.append(symbol)
            log.error("%-14s SKIPPED: %s", symbol, exc)

    n_prices = upsert(engine, prices_table, price_rows)
    n_funds = upsert(engine, fundamentals_table, fundamental_rows)
    log.info(
        "prices ingest done: %d/%d tickers ok, %d price rows, %d fundamentals rows",
        len(ok), len(watchlist.tickers), n_prices, n_funds,
    )
    if failed:
        log.warning("failed tickers: %s", ", ".join(failed))

    return {"ok": ok, "failed": failed, "price_rows": n_prices, "fundamental_rows": n_funds}
