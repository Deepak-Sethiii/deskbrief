"""RSS headline ingest with alias-based ticker tagging.

HOW TAGGING WORKS -- AND WHAT IT IS NOT
---------------------------------------
This is **case-insensitive substring matching against a hand-maintained alias
list**, not named-entity recognition. There is no model, no gazetteer beyond
config/watchlist.yaml, and no entity disambiguation. Concretely it will:

* miss any name written in a way the alias list does not anticipate; and
* mis-tag a story that merely mentions a company in passing, because a mention
  is not the same thing as a story being *about* that company.

Two cheap guards keep the obvious false positives out: aliases are matched on
word boundaries (so "ITC" does not fire inside "SWITCH"), and the longest alias
wins (so "HDFC Bank" beats bare "HDFC"). That is the whole of the cleverness.
Treat the ticker column as a weak hint, not ground truth.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
from typing import Any, Iterable

import feedparser
import requests
from sqlalchemy.engine import Engine

from src.config import FeedSpec, Watchlist, load_watchlist
from src.db import headlines as headlines_table
from src.db import upsert, url_hash
from src.net import DEFAULT_TIMEOUT, with_retries

log = logging.getLogger(__name__)

# Some Indian publishers 403 a bare python-requests UA.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskBrief/0.1 (+research tool)"


def build_alias_patterns(watchlist: Watchlist) -> list[tuple[re.Pattern[str], str]]:
    """(compiled pattern, ticker) pairs, longest alias first so specifics win."""
    pairs: list[tuple[str, str]] = []
    for spec in watchlist.tickers:
        for alias in spec.aliases or (spec.name,):
            pairs.append((alias, spec.ticker))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)

    compiled: list[tuple[re.Pattern[str], str]] = []
    for alias, ticker in pairs:
        # (?<!\w)...(?!\w) rather than \b: \b misbehaves around '&' in "L&T".
        pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
        compiled.append((pattern, ticker))
    return compiled


def tag_ticker(text: str, patterns: Iterable[tuple[re.Pattern[str], str]]) -> str | None:
    """First (i.e. longest) alias that appears in the text wins. None if no hit."""
    if not text:
        return None
    for pattern, ticker in patterns:
        if pattern.search(text):
            return ticker
    return None


def fetch_feed(feed: FeedSpec) -> feedparser.FeedParserDict:
    """Fetch and parse one RSS feed.

    We pull the bytes with requests rather than handing the URL to feedparser,
    purely so we can enforce a timeout -- feedparser.parse(url) has none.
    """

    def _call() -> feedparser.FeedParserDict:
        response = requests.get(feed.url, headers={"User-Agent": _UA}, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if not parsed.entries:
            raise ValueError(f"no entries parsed from {feed.url}")
        return parsed

    return with_retries(_call, what=f"feed({feed.name})")


def clean_title(raw: str) -> str:
    """Undo RSS entity encoding and collapse whitespace.

    Indian feeds are inconsistent: some double-escape, so "&amp;#39;" arrives as
    "#39;". Unescape twice, then strip any leftover numeric entity fragments.
    """
    text = html.unescape(html.unescape(raw or ""))
    text = re.sub(r"&?#\d{2,4};", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def _published_at(entry: Any) -> dt.datetime | None:
    """feedparser normalises publication time to a UTC struct_time when it can."""
    for key in ("published_parsed", "updated_parsed"):
        stamp = entry.get(key)
        if stamp:
            try:
                return dt.datetime(*stamp[:6])  # naive UTC; see README limitations
            except (TypeError, ValueError):
                continue
    return None


def entries_to_rows(
    feed: FeedSpec,
    parsed: feedparser.FeedParserDict,
    patterns: list[tuple[re.Pattern[str], str]],
    fetched_at: dt.datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        title = clean_title(entry.get("title") or "")
        if not url or not title:
            continue
        # Match on title + summary: the summary often names the company the
        # headline only alludes to. Still substring matching, still not NER.
        haystack = f"{title} {clean_title(entry.get('summary', ''))}"
        rows.append(
            {
                "url_hash": url_hash(url),
                "ticker": tag_ticker(haystack, patterns),
                "source": feed.name,
                "title": title,
                "published_at": _published_at(entry),
                "url": url,
                "fetched_at": fetched_at,
            }
        )
    return rows


def ingest_news(engine: Engine, watchlist: Watchlist | None = None) -> dict[str, Any]:
    """Load every configured feed. A dead feed is logged and skipped, not fatal."""
    watchlist = watchlist or load_watchlist()
    patterns = build_alias_patterns(watchlist)
    fetched_at = dt.datetime.now()

    all_rows: list[dict[str, Any]] = []
    ok: list[str] = []
    failed: list[str] = []

    for feed in watchlist.feeds:
        try:
            parsed = fetch_feed(feed)
            rows = entries_to_rows(feed, parsed, patterns, fetched_at)
            tagged = sum(1 for r in rows if r["ticker"])
            all_rows.extend(rows)
            ok.append(feed.name)
            log.info("%-28s %3d headlines, %2d tagged to a watchlist name",
                     feed.name, len(rows), tagged)
        except Exception as exc:  # noqa: BLE001 - one dead feed must not kill the run
            failed.append(feed.name)
            log.error("%-28s SKIPPED: %s", feed.name, exc)

    # Two feeds can syndicate the same URL; collapse before the upsert so the
    # statement never sees the same primary key twice in one batch.
    deduped: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        deduped.setdefault(row["url_hash"], row)

    written = upsert(engine, headlines_table, list(deduped.values()))
    log.info("news ingest done: %d/%d feeds ok, %d unique headlines",
             len(ok), len(watchlist.feeds), written)
    if failed:
        log.warning("failed feeds: %s", ", ".join(failed))

    return {"ok": ok, "failed": failed, "headlines": written,
            "duplicates_collapsed": len(all_rows) - len(deduped)}
