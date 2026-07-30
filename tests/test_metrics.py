"""Stage 3 coverage. This is the only layer with business logic, so it gets the
real tests: missing days, single-row series, zero/NaN denominators in multiples,
and a ticker present in prices but absent from fundamentals.

Every test builds its DataFrames in memory -- the purity contract means no
fixture database is needed anywhere in this file.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from src.transform.metrics import (
    build_comps_table,
    clean_multiple,
    latest_fundamentals,
    pct_from_52w_high,
    realised_vol,
    returns,
)


def make_prices(ticker: str, closes, start=dt.date(2026, 1, 1), skip=()):
    """Long-format price frame. `skip` drops calendar days to simulate holidays."""
    rows, day = [], start
    for close in closes:
        while day.weekday() in skip:
            day += dt.timedelta(days=1)
        rows.append({"ticker": ticker, "date": day, "close": close})
        day += dt.timedelta(days=1)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# returns
# --------------------------------------------------------------------------

def test_returns_simple_arithmetic():
    prices = make_prices("A", [100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
    out = returns(prices, windows=(1, 5))
    assert out.loc["A", "ret_1d"] == pytest.approx(110 / 104 - 1)
    assert out.loc["A", "ret_5d"] == pytest.approx(110 / 100 - 1)


def test_returns_window_longer_than_history_is_nan_not_partial():
    prices = make_prices("A", [100.0, 105.0])
    out = returns(prices, windows=(1, 5, 21))
    assert out.loc["A", "ret_1d"] == pytest.approx(0.05)
    assert math.isnan(out.loc["A", "ret_5d"])
    assert math.isnan(out.loc["A", "ret_21d"])


def test_returns_single_row_series_yields_all_nan():
    out = returns(make_prices("A", [100.0]))
    assert out.index.tolist() == ["A"]
    assert out.loc["A"].isna().all()


def test_returns_counts_bars_not_calendar_days():
    """Gaps (weekends/holidays) must not shift the lookback."""
    dense = make_prices("A", [10.0, 11.0, 12.0, 13.0])
    sparse = pd.DataFrame(
        {
            "ticker": ["A"] * 4,
            # same four bars, but spread across five weeks
            "date": [dt.date(2026, 1, 5), dt.date(2026, 1, 12),
                     dt.date(2026, 1, 26), dt.date(2026, 2, 9)],
            "close": [10.0, 11.0, 12.0, 13.0],
        }
    )
    assert returns(dense, windows=(3,)).loc["A", "ret_3d"] == pytest.approx(
        returns(sparse, windows=(3,)).loc["A", "ret_3d"]
    )


def test_returns_zero_previous_close_is_nan_not_infinity():
    prices = make_prices("A", [0.0, 50.0])
    out = returns(prices, windows=(1,))
    assert math.isnan(out.loc["A", "ret_1d"])


def test_returns_unsorted_input_is_sorted_first():
    prices = make_prices("A", [100.0, 110.0]).iloc[::-1].reset_index(drop=True)
    assert returns(prices, windows=(1,)).loc["A", "ret_1d"] == pytest.approx(0.10)


def test_returns_handles_multiple_tickers_independently():
    prices = pd.concat([make_prices("A", [10.0, 11.0]), make_prices("B", [20.0, 19.0])])
    out = returns(prices, windows=(1,))
    assert out.loc["A", "ret_1d"] == pytest.approx(0.10)
    assert out.loc["B", "ret_1d"] == pytest.approx(-0.05)


def test_returns_on_empty_frame_returns_empty_shaped_frame():
    empty = pd.DataFrame(columns=["ticker", "date", "close"])
    out = returns(empty, windows=(1, 5))
    assert out.empty and list(out.columns) == ["ret_1d", "ret_5d"]


def test_returns_rejects_frame_without_required_columns():
    with pytest.raises(ValueError, match="missing required column"):
        returns(pd.DataFrame({"ticker": ["A"], "price": [1.0]}))


def test_rows_with_nan_close_are_dropped_not_propagated():
    prices = make_prices("A", [100.0, float("nan"), 110.0])
    assert returns(prices, windows=(1,)).loc["A", "ret_1d"] == pytest.approx(0.10)


# --------------------------------------------------------------------------
# realised_vol
# --------------------------------------------------------------------------

def test_realised_vol_matches_manual_calculation():
    closes = [100.0, 102.0, 101.0, 105.0, 103.0]
    out = realised_vol(make_prices("A", closes), window=30, annualise=False)
    expected = np.std(np.diff(np.log(closes)), ddof=1)
    assert out["A"] == pytest.approx(expected)


def test_realised_vol_annualises_by_sqrt_252():
    closes = [100.0, 102.0, 101.0, 105.0, 103.0]
    prices = make_prices("A", closes)
    raw = realised_vol(prices, annualise=False)["A"]
    ann = realised_vol(prices, annualise=True)["A"]
    assert ann == pytest.approx(raw * math.sqrt(252))


def test_realised_vol_single_row_series_is_nan():
    assert math.isnan(realised_vol(make_prices("A", [100.0]))["A"])


def test_realised_vol_two_rows_is_nan_because_one_return_has_no_sample_sd():
    assert math.isnan(realised_vol(make_prices("A", [100.0, 101.0]))["A"])


def test_realised_vol_flat_series_is_zero():
    assert realised_vol(make_prices("A", [100.0] * 10))["A"] == pytest.approx(0.0)


def test_realised_vol_ignores_non_positive_prints():
    """A 0.0 close would make log() undefined; it is dropped, not propagated."""
    out = realised_vol(make_prices("A", [100.0, 0.0, 102.0, 101.0, 103.0]))
    assert np.isfinite(out["A"])


def test_realised_vol_uses_only_the_last_window_bars():
    calm = [100.0] * 40
    wild = [100.0, 140.0, 70.0, 130.0]
    tail_only = realised_vol(make_prices("A", calm + wild), window=3)["A"]
    assert tail_only > 0.5  # dominated by the recent wild bars, not the calm ones


# --------------------------------------------------------------------------
# pct_from_52w_high
# --------------------------------------------------------------------------

def test_pct_from_52w_high_basic():
    out = pct_from_52w_high(make_prices("A", [100.0, 200.0, 150.0]))
    assert out.loc["A", "high_52w"] == pytest.approx(200.0)
    assert out.loc["A", "last_close"] == pytest.approx(150.0)
    assert out.loc["A", "pct_from_52w_high"] == pytest.approx(-0.25)


def test_pct_from_52w_high_at_the_high_is_zero():
    out = pct_from_52w_high(make_prices("A", [100.0, 120.0]))
    assert out.loc["A", "pct_from_52w_high"] == pytest.approx(0.0)


def test_pct_from_52w_high_single_row_is_zero_against_itself():
    out = pct_from_52w_high(make_prices("A", [100.0]))
    assert out.loc["A", "pct_from_52w_high"] == pytest.approx(0.0)


def test_pct_from_52w_high_excludes_bars_older_than_the_lookback():
    old_peak = pd.DataFrame(
        {
            "ticker": ["A", "A"],
            "date": [dt.date(2024, 1, 1), dt.date(2026, 1, 1)],
            "close": [500.0, 100.0],
        }
    )
    out = pct_from_52w_high(old_peak, lookback_days=365)
    assert out.loc["A", "high_52w"] == pytest.approx(100.0)  # the 500 is out of window
    assert out.loc["A", "pct_from_52w_high"] == pytest.approx(0.0)


def test_pct_from_52w_high_zero_high_is_nan_not_division_error():
    out = pct_from_52w_high(make_prices("A", [0.0, 0.0]))
    assert math.isnan(out.loc["A", "pct_from_52w_high"])


# --------------------------------------------------------------------------
# multiples sanitisation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value", [None, float("nan"), float("inf"), float("-inf"), 0, 0.0, -12.5, "n/a", "", []]
)
def test_clean_multiple_rejects_unusable_values(value):
    assert math.isnan(clean_multiple(value))


@pytest.mark.parametrize("value, expected", [(24.6, 24.6), ("18.2", 18.2), (1, 1.0)])
def test_clean_multiple_keeps_usable_values(value, expected):
    assert clean_multiple(value) == pytest.approx(expected)


# --------------------------------------------------------------------------
# build_comps_table
# --------------------------------------------------------------------------

def two_ticker_prices():
    return pd.concat(
        [
            make_prices("A", [100.0, 102.0, 101.0, 105.0, 103.0]),
            make_prices("B", [50.0, 51.0, 52.0, 53.0, 54.0]),
        ],
        ignore_index=True,
    )


def fundamentals_frame(**overrides):
    row = {
        "ticker": "A",
        "asof_date": dt.date(2026, 7, 30),
        "market_cap": 2.0e12,
        "pe": 24.6,
        "ev_ebitda": 12.1,
        "sector": "Energy",
        "currency": "INR",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_comps_table_keeps_ticker_missing_from_fundamentals():
    """The headline edge case: B has bars but no fundamentals row."""
    table = build_comps_table(two_ticker_prices(), fundamentals_frame())
    assert table.index.tolist() == ["A", "B"]
    assert table.loc["A", "sector"] == "Energy"
    assert pd.isna(table.loc["B", "sector"])
    assert pd.isna(table.loc["B", "pe"])
    assert pd.isna(table.loc["B", "market_cap_bn"])
    # ...but B's price-derived columns are fully populated
    assert np.isfinite(table.loc["B", "last_close"])
    assert np.isfinite(table.loc["B", "ret_1d"])


def test_comps_table_zero_and_negative_multiples_become_blank():
    table = build_comps_table(two_ticker_prices(), fundamentals_frame(pe=0, ev_ebitda=-3.0))
    assert math.isnan(table.loc["A", "pe"])
    assert math.isnan(table.loc["A", "ev_ebitda"])


def test_comps_table_nan_multiples_survive_as_nan():
    table = build_comps_table(
        two_ticker_prices(), fundamentals_frame(pe=float("nan"), ev_ebitda=float("inf"))
    )
    assert math.isnan(table.loc["A", "pe"])
    assert math.isnan(table.loc["A", "ev_ebitda"])


def test_comps_table_market_cap_is_scaled_to_billions():
    table = build_comps_table(two_ticker_prices(), fundamentals_frame(market_cap=2.0e12))
    assert table.loc["A", "market_cap_bn"] == pytest.approx(2000.0)


def test_comps_table_with_no_fundamentals_at_all():
    table = build_comps_table(two_ticker_prices(), pd.DataFrame())
    assert table.index.tolist() == ["A", "B"]
    assert table[["pe", "ev_ebitda", "market_cap_bn"]].isna().all().all()


def test_comps_table_uses_latest_fundamentals_snapshot():
    history = pd.concat(
        [
            fundamentals_frame(asof_date=dt.date(2026, 7, 29), pe=10.0),
            fundamentals_frame(asof_date=dt.date(2026, 7, 30), pe=20.0),
        ],
        ignore_index=True,
    )
    table = build_comps_table(two_ticker_prices(), history)
    assert table.loc["A", "pe"] == pytest.approx(20.0)


def test_comps_table_column_set_is_stable():
    table = build_comps_table(two_ticker_prices(), fundamentals_frame())
    assert list(table.columns) == [
        "sector", "currency", "last_close",
        "ret_1d", "ret_5d", "ret_21d",
        "realised_vol_30d", "high_52w", "pct_from_52w_high",
        "market_cap_bn", "pe", "ev_ebitda",
    ]


def test_comps_table_on_empty_prices_is_empty_not_an_error():
    table = build_comps_table(
        pd.DataFrame(columns=["ticker", "date", "close"]), fundamentals_frame()
    )
    assert table.empty


def test_latest_fundamentals_tolerates_missing_columns():
    sparse = pd.DataFrame([{"ticker": "A", "asof_date": dt.date(2026, 7, 30), "pe": 15.0}])
    out = latest_fundamentals(sparse)
    assert out.loc["A", "pe"] == pytest.approx(15.0)
    assert pd.isna(out.loc["A", "ev_ebitda"])


def test_metrics_module_performs_no_io():
    """Purity guard: the module must not import anything that touches the world."""
    import src.transform.metrics as metrics

    source = open(metrics.__file__, encoding="utf-8").read()
    for forbidden in ("import requests", "import sqlite3", "from src.db",
                      "import yfinance", "open(", "requests.get"):
        assert forbidden not in source, f"metrics.py must stay pure, found {forbidden!r}"
