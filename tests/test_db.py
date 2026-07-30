"""Stage 1 proof: the schema is right and every write path is idempotent."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from src.db import (
    TABLES,
    fundamentals,
    get_engine,
    headlines,
    init_db,
    prices,
    table_counts,
    upsert,
    url_hash,
)


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(tmp_path / "test.db")
    init_db(eng)
    return eng


def price_rows(close: float = 100.0):
    return [
        {
            "ticker": "RELIANCE.NS",
            "date": dt.date(2026, 7, 30),
            "open": 99.0,
            "high": 101.0,
            "low": 98.5,
            "close": close,
            "volume": 5_123_456_789,
        },
        {
            "ticker": "TCS.NS",
            "date": dt.date(2026, 7, 30),
            "open": 3900.0,
            "high": 3950.0,
            "low": 3880.0,
            "close": close + 1,
            "volume": 1_000_000,
        },
    ]


def fundamental_rows(pe: float = 25.0):
    return [
        {
            "ticker": "RELIANCE.NS",
            "asof_date": dt.date(2026, 7, 30),
            "market_cap": 1.9e13,
            "pe": pe,
            "ev_ebitda": 12.5,
            "sector": "Energy",
            "currency": "INR",
        }
    ]


def headline_rows(title: str = "First version of the title"):
    url = "https://example.com/markets/story-1?utm=abc"
    return [
        {
            "url_hash": url_hash(url),
            "ticker": "RELIANCE.NS",
            "source": "Example Wire",
            "title": title,
            "published_at": dt.datetime(2026, 7, 30, 9, 15),
            "url": url,
            "fetched_at": dt.datetime(2026, 7, 30, 9, 30),
        }
    ]


def test_initdb_creates_three_tables(engine):
    assert set(TABLES) == {"prices", "fundamentals", "headlines"}
    assert table_counts(engine) == {"prices": 0, "fundamentals": 0, "headlines": 0}


def test_composite_primary_keys():
    assert [c.name for c in prices.primary_key.columns] == ["ticker", "date"]
    assert [c.name for c in fundamentals.primary_key.columns] == ["ticker", "asof_date"]
    assert [c.name for c in headlines.primary_key.columns] == ["url_hash"]


def test_url_hash_is_stable_and_16_chars():
    h = url_hash("https://example.com/a")
    assert len(h) == 16
    assert h == url_hash("  https://example.com/a  ")  # whitespace-insensitive
    assert h != url_hash("https://example.com/b")


def test_rerunning_every_upsert_adds_zero_rows(engine):
    """The headline requirement: run the identical load twice, counts must not move."""
    for _ in range(2):
        upsert(engine, prices, price_rows())
        upsert(engine, fundamentals, fundamental_rows())
        upsert(engine, headlines, headline_rows())
        assert table_counts(engine) == {"prices": 2, "fundamentals": 1, "headlines": 1}


def test_conflict_updates_values_in_place(engine):
    upsert(engine, prices, price_rows(close=100.0))
    upsert(engine, prices, price_rows(close=111.0))

    with engine.connect() as conn:
        rows = conn.execute(
            select(prices.c.ticker, prices.c.close).order_by(prices.c.ticker)
        ).all()

    assert rows == [("RELIANCE.NS", 111.0), ("TCS.NS", 112.0)]
    assert table_counts(engine)["prices"] == 2


def test_headline_conflict_refreshes_title_not_key(engine):
    upsert(engine, headlines, headline_rows("First version of the title"))
    upsert(engine, headlines, headline_rows("Corrected headline"))

    with engine.connect() as conn:
        titles = conn.execute(select(headlines.c.title)).scalars().all()

    assert titles == ["Corrected headline"]


def test_headline_ticker_is_nullable(engine):
    url = "https://example.com/untagged"
    upsert(
        engine,
        headlines,
        [
            {
                "url_hash": url_hash(url),
                "ticker": None,
                "source": "Example Wire",
                "title": "Broad market story with no name in it",
                "published_at": dt.datetime(2026, 7, 30, 10, 0),
                "url": url,
                "fetched_at": dt.datetime(2026, 7, 30, 10, 1),
            }
        ],
    )
    assert table_counts(engine)["headlines"] == 1


def test_upsert_of_empty_batch_is_a_noop(engine):
    assert upsert(engine, prices, []) == 0
    assert table_counts(engine)["prices"] == 0


def test_fundamentals_new_asof_date_appends_a_snapshot(engine):
    upsert(engine, fundamentals, fundamental_rows())
    later = fundamental_rows(pe=26.0)
    later[0]["asof_date"] = dt.date(2026, 7, 31)
    upsert(engine, fundamentals, later)
    # different as-of date => history, not an overwrite
    assert table_counts(engine)["fundamentals"] == 2
