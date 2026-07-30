"""Pure metric calculations.

PURITY CONTRACT: nothing in this module performs I/O. No database handles, no
network, no filesystem, no clock, no logging side effects. Every function takes
DataFrames in and returns DataFrames or Series out. That is what makes this the
one layer worth testing hard -- and the tests never need a fixture database.

Input shape is the long format the DB stores:
    prices        columns ticker, date, close (open/high/low/volume ignored here)
    fundamentals  columns ticker, asof_date, market_cap, pe, ev_ebitda,
                  sector, currency

Convention: returns and vol are computed over *available bars*, not calendar
days. A market holiday is simply an absent row, so "21d" means 21 trading
observations back. That matches how a desk quotes it and avoids inventing
prices for days the exchange was shut.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

_PRICE_COLUMNS = ("ticker", "date", "close")


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Validate, narrow, coerce and sort. Rows with an unusable close are dropped."""
    missing = [c for c in _PRICE_COLUMNS if c not in prices.columns]
    if missing:
        raise ValueError(f"prices is missing required column(s): {missing}")

    frame = prices.loc[:, list(_PRICE_COLUMNS)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["date", "close"])
    return frame.sort_values(["ticker", "date"]).reset_index(drop=True)


def _safe_ratio(numerator: float, denominator: float) -> float:
    """numerator/denominator - 1, or NaN if the denominator cannot support it."""
    if denominator is None or not np.isfinite(denominator) or denominator == 0:
        return float("nan")
    if numerator is None or not np.isfinite(numerator):
        return float("nan")
    return float(numerator) / float(denominator) - 1.0


def clean_multiple(value: object) -> float:
    """Sanitise a valuation multiple for display.

    NaN/inf pass through as NaN, and so does anything <= 0: a negative or zero
    trailing P/E is arithmetically real (loss-making or zero earnings) but is
    not a comparable, and silently ranking on it misleads. Blank beats wrong.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(number) or number <= 0:
        return float("nan")
    return number


def returns(prices: pd.DataFrame, windows: tuple[int, ...] = (1, 5, 21)) -> pd.DataFrame:
    """Trailing simple returns per ticker, one column per window (``ret_1d`` ...).

    A window longer than the available history yields NaN rather than a partial
    figure computed off the first bar, which would silently mean something else.
    """
    columns = [f"ret_{w}d" for w in windows]
    frame = _prepare_prices(prices)
    if frame.empty:
        return pd.DataFrame(columns=columns, index=pd.Index([], name="ticker"), dtype=float)

    records: dict[str, dict[str, float]] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        closes = group["close"].to_numpy(dtype=float)
        last = closes[-1]
        records[str(ticker)] = {
            f"ret_{w}d": _safe_ratio(last, closes[-1 - w]) if len(closes) > w else float("nan")
            for w in windows
        }

    out = pd.DataFrame.from_dict(records, orient="index").reindex(columns=columns)
    out.index.name = "ticker"
    return out


def realised_vol(
    prices: pd.DataFrame, window: int = 30, annualise: bool = True
) -> pd.Series:
    """Sample standard deviation of daily log returns over the last ``window`` bars.

    Log returns (not simple) so the series is additive over time; ddof=1 because
    this is a sample, not the population. Annualised by sqrt(252) when asked.
    """
    name = f"realised_vol_{window}d"
    frame = _prepare_prices(prices)
    if frame.empty:
        return pd.Series(dtype=float, name=name, index=pd.Index([], name="ticker"))

    values: dict[str, float] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        closes = group["close"].to_numpy(dtype=float)
        closes = closes[closes > 0]  # log of a non-positive print is undefined
        if closes.size < 3:
            values[str(ticker)] = float("nan")  # need >=2 returns for ddof=1
            continue
        log_returns = np.diff(np.log(closes))[-window:]
        if log_returns.size < 2:
            values[str(ticker)] = float("nan")
            continue
        vol = float(np.std(log_returns, ddof=1))
        values[str(ticker)] = vol * math.sqrt(TRADING_DAYS_PER_YEAR) if annualise else vol

    series = pd.Series(values, name=name, dtype=float)
    series.index.name = "ticker"
    return series


def pct_from_52w_high(prices: pd.DataFrame, lookback_days: int = 365) -> pd.DataFrame:
    """Last close versus the highest close of the trailing 52 weeks.

    Measured off closes, not intraday highs, because the DB's high column is a
    raw print and a single bad tick would permanently depress every later
    reading. Result is <= 0: 0.0 means the name is sitting at its own high.
    """
    columns = ["last_close", "high_52w", "pct_from_52w_high"]
    frame = _prepare_prices(prices)
    if frame.empty:
        return pd.DataFrame(columns=columns, index=pd.Index([], name="ticker"), dtype=float)

    records: dict[str, dict[str, float]] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        cutoff = group["date"].max() - pd.Timedelta(days=lookback_days)
        window = group.loc[group["date"] >= cutoff, "close"]
        last = float(group["close"].to_numpy(dtype=float)[-1])
        high = float(window.max()) if not window.empty else float("nan")
        records[str(ticker)] = {
            "last_close": last,
            "high_52w": high,
            "pct_from_52w_high": _safe_ratio(last, high),
        }

    out = pd.DataFrame.from_dict(records, orient="index").reindex(columns=columns)
    out.index.name = "ticker"
    return out


def latest_fundamentals(fundamentals: pd.DataFrame) -> pd.DataFrame:
    """Collapse the fundamentals history to the most recent snapshot per ticker."""
    columns = ["market_cap", "pe", "ev_ebitda", "sector", "currency"]
    empty = pd.DataFrame(columns=columns, index=pd.Index([], name="ticker"))
    if fundamentals is None or fundamentals.empty or "ticker" not in fundamentals.columns:
        return empty

    frame = fundamentals.copy()
    if "asof_date" in frame.columns:
        frame["asof_date"] = pd.to_datetime(frame["asof_date"], errors="coerce")
        frame = frame.sort_values(["ticker", "asof_date"])
    frame = frame.groupby("ticker", sort=True).tail(1).set_index("ticker")

    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame.loc[:, columns]


def build_comps_table(
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame,
    *,
    return_windows: tuple[int, ...] = (1, 5, 21),
    vol_window: int = 30,
) -> pd.DataFrame:
    """One row per ticker present in ``prices``: performance plus multiples.

    Prices drive the row set, so a name we have bars for but no fundamentals for
    still appears -- with blank multiples. Dropping it would silently shrink the
    universe and make the sheet disagree with the chart.
    """
    performance = pct_from_52w_high(prices)
    trailing = returns(prices, windows=return_windows)
    vol = realised_vol(prices, window=vol_window)
    facts = latest_fundamentals(fundamentals)

    table = performance.join(trailing, how="left").join(vol, how="left")
    table = table.join(facts, how="left")

    for column in ("pe", "ev_ebitda"):
        table[column] = table[column].map(clean_multiple)
    table["market_cap"] = pd.to_numeric(table.get("market_cap"), errors="coerce")
    table["market_cap"] = table["market_cap"].replace([np.inf, -np.inf], np.nan)
    # Billions of the listing currency: raw market cap runs to 13 digits for
    # these names and is unreadable in a cell.
    table["market_cap_bn"] = table["market_cap"] / 1e9

    ordered = [
        "sector", "currency", "last_close",
        *[f"ret_{w}d" for w in return_windows],
        f"realised_vol_{vol_window}d",
        "high_52w", "pct_from_52w_high",
        "market_cap_bn", "pe", "ev_ebitda",
    ]
    table = table.reindex(columns=ordered)
    table.index.name = "ticker"
    return table.sort_index()
