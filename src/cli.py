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


def build_comps(args: argparse.Namespace):
    """Shared by the `comps` command and the report pipeline."""
    import pandas as pd

    from src.repository import load_fundamentals, load_prices
    from src.transform.metrics import build_comps_table

    engine = get_engine(args.db)
    init_db(engine)
    prices = load_prices(engine)
    fundamentals = load_fundamentals(engine)
    log.info("loaded %d price rows, %d fundamentals rows", len(prices), len(fundamentals))
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    return engine, build_comps_table(prices, fundamentals)


def cmd_comps(args: argparse.Namespace) -> int:
    _, table = build_comps(args)
    if table.empty:
        log.error("comps table is empty -- run `ingest` first")
        return 1
    log.info("comps table (%d names):\n%s", len(table), table.round(4).to_string())
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Build the Excel workbook (and the deck) from whatever is already in the DB."""
    from src.report.excel import TemplateMissingError, write_workbook
    from src.repository import load_headlines

    engine, table = build_comps(args)
    if table.empty:
        log.error("no data to report on -- run `ingest` first")
        return 1

    headlines = load_headlines(engine, limit=60)
    commentary = None
    if not getattr(args, "no_commentary", False):
        from src.report.commentary import generate_commentary

        commentary = generate_commentary(table, headlines)

    chart_path = None
    if not getattr(args, "no_chart", False):
        from src.report.charts import normalised_price_chart
        from src.repository import load_prices

        chart_path = normalised_price_chart(load_prices(engine))

    try:
        out_path = write_workbook(table, headlines, commentary, chart_path,
                                  template=args.template)
    except TemplateMissingError as exc:
        log.error("%s", exc)
        return 1

    if not getattr(args, "no_deck", False):
        from src.report.deck import build_deck

        deck_path = build_deck(table, commentary, chart_path)
        log.info("deck written    : %s", deck_path)

    log.info("workbook ready  : %s", out_path)
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    """Full pipeline: ingest, transform, report. This is what the macro runs."""
    rc_ingest = cmd_ingest(args)
    if rc_ingest != 0:
        log.warning("ingest reported problems (rc=%d) -- reporting on what we have",
                    rc_ingest)
    return cmd_report(args)


def cmd_template_help(_args: argparse.Namespace) -> int:
    from src.report.template_help import render

    print(render())
    return 0


def cmd_verify_template(args: argparse.Namespace) -> int:
    from src.report.excel import TemplateMissingError, verify_template

    try:
        result = verify_template(args.template)
    except TemplateMissingError as exc:
        log.error("%s", exc)
        return 1

    log.info("template : %s", result["path"])
    log.info("sheets   : %s", ", ".join(result["sheets"]))
    log.info("vbaProject.bin present: %s", result["has_vba"])
    if result["missing_sheets"]:
        log.error("MISSING SHEETS: %s -- the writer cannot run. Redo template-help.",
                  result["missing_sheets"])
        return 1
    if not result["has_vba"]:
        log.warning("no VBA in this template. The workbook will still build; you just")
        log.warning("won't get the Refresh button. Do steps 6-7 of `template-help`")
        log.warning("(Alt+F11 -> File -> Import File -> vba\\Refresh.bas, then assign")
        log.warning("the shape to RefreshDeskBrief) and re-run this check.")
        log.info("template USABLE (macro not yet imported)")
        return 0
    log.info("template OK (sheets + VBA present)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    parser.add_argument("--db", default=None, help="override SQLite path")
    parser.add_argument("--watchlist", default=None, help="override config/watchlist.yaml")
    parser.add_argument("--template", default=None, help="override the .xlsm template path")
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
    sub.add_parser("comps", help="print the computed comps table").set_defaults(func=cmd_comps)
    sub.add_parser("template-help", help="print the one-time .xlsm build steps").set_defaults(
        func=cmd_template_help
    )
    sub.add_parser("verify-template", help="check the .xlsm template is usable").set_defaults(
        func=cmd_verify_template
    )

    for name, help_text, func in (
        ("report", "build the workbook + deck from the DB", cmd_report),
        ("refresh", "ingest then report -- the full pipeline", cmd_refresh),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--no-commentary", action="store_true", help="skip the LLM call")
        p.add_argument("--no-chart", action="store_true", help="skip the matplotlib chart")
        p.add_argument("--no-deck", action="store_true", help="skip the PowerPoint deck")
        p.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    # Load .env if present. override=False so a real environment variable always
    # beats the file -- that is what CI and scheduled runs will set.
    try:
        from dotenv import load_dotenv

        from src.db import PROJECT_ROOT

        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover - dotenv is pinned, but never fatal
        pass

    try:
        return args.func(args)
    except Exception:
        log.exception("command %r failed", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(main())
