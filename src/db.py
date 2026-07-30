"""SQLAlchemy Core schema and idempotent upsert helpers for DeskBrief.

Design notes
------------
* SQLAlchemy *Core*, not the ORM: every row here is a flat market-data record
  with a natural composite key, so an identity map buys us nothing.
* Every write goes through :func:`upsert`, which emits
  ``INSERT ... ON CONFLICT (pk) DO UPDATE``. We never DELETE-then-INSERT, so a
  re-run refreshes values in place and adds zero duplicate rows.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "deskbrief.db"

metadata = MetaData()

# Daily OHLCV bars. (ticker, date) is the natural key -- one bar per name per day.
prices = Table(
    "prices",
    metadata,
    Column("ticker", String, primary_key=True, nullable=False),
    Column("date", Date, primary_key=True, nullable=False),
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float),
    # Volume exceeds 2^31 for large caps, so BigInteger rather than Integer.
    Column("volume", BigInteger),
)

# Point-in-time snapshot of valuation multiples. Keyed by as-of date so a later
# re-run on a new day appends a new snapshot instead of overwriting history.
fundamentals = Table(
    "fundamentals",
    metadata,
    Column("ticker", String, primary_key=True, nullable=False),
    Column("asof_date", Date, primary_key=True, nullable=False),
    Column("market_cap", Float),
    Column("pe", Float),
    Column("ev_ebitda", Float),
    Column("sector", String),
    Column("currency", String),
)

# RSS headlines. The URL is the identity of a story, but URLs are long and
# unbounded, so we key on a truncated sha256 of it (see :func:`url_hash`).
headlines = Table(
    "headlines",
    metadata,
    Column("url_hash", String(16), primary_key=True, nullable=False),
    Column("ticker", String, nullable=True),  # nullable: most stories tag to no name
    Column("source", String),
    Column("title", String),
    Column("published_at", DateTime),
    Column("url", String),
    Column("fetched_at", DateTime),
)

Index("ix_prices_ticker_date", prices.c.ticker, prices.c.date)
Index("ix_headlines_published", headlines.c.published_at)

TABLES: dict[str, Table] = {t.name: t for t in metadata.sorted_tables}


def url_hash(url: str) -> str:
    """Stable 16-hex-char identity for a story URL: ``sha256(url)[:16]``."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def get_engine(db_path: str | Path | None = None, *, echo: bool = False) -> Engine:
    """Create an Engine against the SQLite file, creating ``data/`` if needed."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{path}", echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver glue
        cur = dbapi_conn.cursor()
        # WAL keeps a reader (Excel-facing report step) from blocking the writer.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def init_db(engine: Engine) -> list[str]:
    """Create any missing tables. Idempotent -- safe to run on every start."""
    metadata.create_all(engine)
    return [t.name for t in metadata.sorted_tables]


def upsert(
    engine: Engine,
    table: Table,
    rows: Sequence[Mapping[str, Any]],
    *,
    update_columns: Iterable[str] | None = None,
) -> int:
    """``INSERT ... ON CONFLICT (pk) DO UPDATE`` a batch of rows.

    Returns the number of rows submitted (SQLite reports the same rowcount for
    an insert and an update, so this is a submitted-count, not a changed-count).
    """
    if not rows:
        return 0

    pk_columns = [c.name for c in table.primary_key.columns]
    if update_columns is None:
        update_columns = [c.name for c in table.columns if c.name not in pk_columns]

    stmt = sqlite_insert(table)
    stmt = stmt.on_conflict_do_update(
        index_elements=pk_columns,
        set_={name: getattr(stmt.excluded, name) for name in update_columns},
    )

    with engine.begin() as conn:
        conn.execute(stmt, list(rows))
    return len(rows)


def table_counts(engine: Engine) -> dict[str, int]:
    """Row count per table -- used by the CLI to prove re-runs add no rows."""
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for name, table in TABLES.items():
            counts[name] = conn.execute(select(func.count()).select_from(table)).scalar_one()
    return counts
