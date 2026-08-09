"""Load and validate config/watchlist.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.paths import DEFAULT_WATCHLIST


@dataclass(frozen=True)
class TickerSpec:
    ticker: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str


@dataclass(frozen=True)
class Watchlist:
    tickers: tuple[TickerSpec, ...]
    feeds: tuple[FeedSpec, ...]
    history_period: str = "1y"
    history_interval: str = "1d"

    @property
    def symbols(self) -> list[str]:
        return [t.ticker for t in self.tickers]

    def name_for(self, ticker: str) -> str:
        for spec in self.tickers:
            if spec.ticker == ticker:
                return spec.name
        return ticker


def load_watchlist(path: str | Path | None = None) -> Watchlist:
    path = Path(path) if path is not None else DEFAULT_WATCHLIST
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    tickers = tuple(
        TickerSpec(
            ticker=str(t["ticker"]).strip(),
            name=str(t.get("name", t["ticker"])).strip(),
            aliases=tuple(str(a).strip() for a in t.get("aliases", []) if str(a).strip()),
        )
        for t in raw.get("tickers", [])
    )
    if not tickers:
        raise ValueError(f"{path} lists no tickers")

    feeds = tuple(
        FeedSpec(name=str(f["name"]).strip(), url=str(f["url"]).strip())
        for f in raw.get("feeds", [])
    )

    return Watchlist(
        tickers=tickers,
        feeds=feeds,
        history_period=str(raw.get("history_period", "1y")),
        history_interval=str(raw.get("history_interval", "1d")),
    )
