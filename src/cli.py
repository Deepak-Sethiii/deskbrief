"""DeskBrief command line entry point.

    python -m src.cli initdb           create the SQLite schema
    python -m src.cli dbstats          print row counts per table
    python -m src.cli ingest-prices    yfinance OHLCV + fundamentals
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.db import get_engine, init_db, table_counts
from src.logging_setup import setup_logging

log = logging.getLogger("deskbrief.cli")


def cmd_initdb(args: argparse.Namespace) -> int:
    engine = get_engine(args.db)
    tables = init_db(engine)
    log.info("schema ready at %s", engine.url.database)
    for name in tables:
        log.info("  table: %s", name)
    return 0


def cmd_dbstats(args: argparse.Namespace) -> int:
    engine = get_engine(args.db)
    init_db(engine)
    for name, count in table_counts(engine).items():
        log.info("%-14s %8d rows", name, count)
    return 0


def cmd_ingest_prices(args: argparse.Namespace) -> int:
    from src.config import load_watchlist
    from src.ingest.prices import ingest_prices

    engine = get_engine(args.db)
    init_db(engine)
    before = table_counts(engine)
    result = ingest_prices(engine, load_watchlist(args.watchlist))
    after = table_counts(engine)
    for name in ("prices", "fundamentals"):
        log.info("%-14s %d -> %d rows (delta %+d)", name, before[name], after[name],
                 after[name] - before[name])
    # a run that got nothing at all is a failure worth a non-zero exit code
    return 0 if result["ok"] else 1


def cmd_ingest_news(args: argparse.Namespace) -> int:
    from src.config import load_watchlist
    from src.ingest.news import ingest_news

    engine = get_engine(args.db)
    init_db(engine)
    before = table_counts(engine)
    result = ingest_news(engine, load_watchlist(args.watchlist))
    after = table_counts(engine)
    log.info("%-14s %d -> %d rows (delta %+d)", "headlines", before["headlines"],
             after["headlines"], after["headlines"] - before["headlines"])
    return 0 if result["ok"] else 1


def cmd_ingest(args: argparse.Namespace) -> int:
    rc_prices = cmd_ingest_prices(args)
    rc_news = cmd_ingest_news(args)
    return rc_prices or rc_news


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    parser.add_argument("--db", default=None, help="override SQLite path")
    parser.add_argument("--watchlist", default=None, help="override config/watchlist.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("initdb", help="create the SQLite schema").set_defaults(func=cmd_initdb)
    sub.add_parser("dbstats", help="print row counts per table").set_defaults(func=cmd_dbstats)
    sub.add_parser("ingest-prices", help="yfinance OHLCV + fundamentals").set_defaults(
        func=cmd_ingest_prices
    )
    sub.add_parser("ingest-news", help="RSS headlines + alias tagging").set_defaults(
        func=cmd_ingest_news
    )
    sub.add_parser("ingest", help="prices then news").set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return args.func(args)
    except Exception:
        log.exception("command %r failed", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(main())
