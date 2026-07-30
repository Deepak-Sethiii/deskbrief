"""Matplotlib chart generation.

Normalised to 100 at the start of the window rather than plotted on raw prices:
these names span 286 to 3931 rupees, so a raw-price chart would be one flat line
at the bottom and one at the top and would show nothing about relative
performance.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import matplotlib

# Agg: no GUI backend. This runs headless from a .bat launched by Excel.
matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"

# Colour-blind-safe qualitative palette (Okabe-Ito), extended to 8 series.
PALETTE = (
    "#0072B2", "#D55E00", "#009E73", "#CC79A7",
    "#E69F00", "#56B4E9", "#8C564B", "#7F7F7F",
)
GRID_COLOUR = "#D9D9D9"
TEXT_COLOUR = "#333333"


def normalised_price_chart(
    prices: pd.DataFrame,
    *,
    lookback_days: int = 180,
    output_path: str | Path | None = None,
    dpi: int = 140,
    compact: bool = False,
) -> Path | None:
    """Rebased line chart of the watchlist. Returns the PNG path, or None.

    compact=True renders a short, wide variant for the PowerPoint slide. A PNG
    sized for a full Excel sheet becomes unreadable when scaled into a 7.7-inch
    slot, so the deck gets its own aspect ratio and larger relative type rather
    than a shrunken copy of the same file.
    """
    if prices is None or prices.empty:
        log.warning("chart skipped: no price data")
        return None

    frame = prices.loc[:, ["ticker", "date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"])
    if frame.empty:
        log.warning("chart skipped: no usable price rows")
        return None

    cutoff = frame["date"].max() - pd.Timedelta(days=lookback_days)
    frame = frame[frame["date"] >= cutoff]

    wide = frame.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
    wide = wide.sort_index()
    # Forward-fill only: a name that has not traded yet must stay blank rather
    # than be back-filled with a price that did not exist.
    wide = wide.ffill()

    first_valid = wide.apply(lambda column: column.dropna().iloc[0] if column.notna().any() else pd.NA)
    rebased = wide.divide(first_valid, axis=1) * 100.0
    rebased = rebased.dropna(axis=1, how="all")
    if rebased.empty:
        log.warning("chart skipped: nothing to plot after rebasing")
        return None

    default_name = "normalised_prices_deck.png" if compact else "normalised_prices.png"
    out_path = Path(output_path) if output_path else ASSETS_DIR / default_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    figsize = (10.0, 3.0) if compact else (11.0, 5.4)
    size_title, size_tick, size_legend = (16, 11, 10) if compact else (13, 8, 8)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    # Sort by final value so the legend order matches the visual order of the
    # line ends -- much easier to read with 8 overlapping series.
    ordered = rebased.iloc[-1].sort_values(ascending=False).index
    for index, ticker in enumerate(ordered):
        ax.plot(
            rebased.index,
            rebased[ticker],
            label=f"{ticker}  {rebased[ticker].iloc[-1]:.0f}",
            color=PALETTE[index % len(PALETTE)],
            linewidth=1.6,
        )

    ax.axhline(100.0, color=TEXT_COLOUR, linewidth=0.9, linestyle="--", alpha=0.6)

    ax.set_title(
        f"Watchlist performance, rebased to 100 ({lookback_days}d)",
        fontsize=size_title, fontweight="bold", color=TEXT_COLOUR, loc="left", pad=12,
    )
    ax.set_ylabel("Index (start = 100)", fontsize=size_tick, color=TEXT_COLOUR)
    ax.tick_params(labelsize=size_tick, colors=TEXT_COLOUR)
    ax.grid(True, color=GRID_COLOUR, linewidth=0.7, alpha=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_COLOUR)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.autofmt_xdate(rotation=0, ha="center")

    ax.legend(
        loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False,
        fontsize=size_legend, labelcolor=TEXT_COLOUR, borderaxespad=0,
        ncol=2 if compact else 1,  # two short columns beat one tall one at 3in high
        columnspacing=1.0, handlelength=1.4,
    )
    if not compact:  # the slide carries its own source line in the footer
        fig.text(
            0.01, 0.01,
            f"Source: Yahoo Finance via yfinance. Generated {dt.datetime.now():%Y-%m-%d %H:%M}.",
            fontsize=7, color="#888888",
        )

    fig.tight_layout(rect=(0, 0.0 if compact else 0.03, 1, 1))
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)  # explicit: Agg figures leak if the pipeline loops

    log.info("chart written: %s (%d series, %d sessions)",
             out_path, rebased.shape[1], rebased.shape[0])
    return out_path
