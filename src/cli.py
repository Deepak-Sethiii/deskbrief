"""DeskBrief command line entry point.

    python -m src.cli initdb     create the SQLite schema
    python -m src.cli dbstats    print row counts per table
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description=__doc__)
    parser.add_argument("--db", default=None, help="override SQLite path")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("initdb", help="create the SQLite schema").set_defaults(func=cmd_initdb)
    sub.add_parser("dbstats", help="print row counts per table").set_defaults(func=cmd_dbstats)
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
