"""Read side: DB -> DataFrames.

Deliberately separate from src/transform/metrics.py. The metrics module is pure
by contract, so every database touch lives here instead.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from src.db import fundamentals as fundamentals_table
from src.db import headlines as headlines_table
from src.db import prices as prices_table


def _frame(engine: Engine, stmt) -> pd.DataFrame:
    with engine.connect() as conn:
        result = conn.execute(stmt)
        return pd.DataFrame(result.fetchall(), columns=list(result.keys()))


def load_prices(engine: Engine, tickers: list[str] | None = None) -> pd.DataFrame:
    stmt = select(prices_table).order_by(prices_table.c.ticker, prices_table.c.date)
    if tickers:
        stmt = stmt.where(prices_table.c.ticker.in_(tickers))
    return _frame(engine, stmt)


def load_fundamentals(engine: Engine, tickers: list[str] | None = None) -> pd.DataFrame:
    stmt = select(fundamentals_table).order_by(
        fundamentals_table.c.ticker, fundamentals_table.c.asof_date
    )
    if tickers:
        stmt = stmt.where(fundamentals_table.c.ticker.in_(tickers))
    return _frame(engine, stmt)


def load_headlines(engine: Engine, limit: int = 40, since_days: int = 3) -> pd.DataFrame:
    """Most recent headlines. Feeds carry undated items, so NULLs are kept last."""
    cutoff = dt.datetime.now() - dt.timedelta(days=since_days)
    stmt = (
        select(headlines_table)
        .where(
            (headlines_table.c.published_at.is_(None))
            | (headlines_table.c.published_at >= cutoff)
        )
        .order_by(headlines_table.c.published_at.desc().nullslast())
        .limit(limit)
    )
    frame = _frame(engine, stmt)
    if frame.empty:  # nothing recent: fall back to the newest we have at all
        frame = _frame(
            engine,
            select(headlines_table)
            .order_by(headlines_table.c.fetched_at.desc().nullslast())
            .limit(limit),
        )
    return frame
