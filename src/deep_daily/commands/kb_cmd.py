from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

from deep_daily.home import HomeConfig

VALID_QUERY_SOURCES = {"article", "tweet", "news6551", "hn"}


def cmd_kb(args: argparse.Namespace, home: HomeConfig) -> int:
    from deep_daily.config import init_runtime
    from deep_daily.kb.cli import format_query_json, format_query_table, format_stats, format_stats_json
    from deep_daily.kb.ingest import collect_stats, ingest
    from deep_daily.kb.mcp import install_claude_desktop, run_stdio_server
    from deep_daily.kb.query import search_text
    from deep_daily.kb.state import KBLockHeldError

    db_path = home.path / "data" / "kb" / "kb.db"
    if args.kb_cmd == "ingest":
        sources = args.sources.split(",") if args.sources else None
        try:
            init_runtime(home)
            ingest(db_path, sources=sources, rebuild=bool(args.rebuild), since=args.since)
        except ValueError as err:
            print(f"Invalid value: {err}", file=sys.stderr)
            return 2
        except KBLockHeldError as err:
            print(f"Lock held: {err}", file=sys.stderr)
            return 1
        except Exception as err:
            print(f"kb ingest failed: {err}", file=sys.stderr)
            return 1
        return 0

    if args.kb_cmd == "stats":
        stats = collect_stats(db_path)
        if args.json:
            print(format_stats_json(stats))
        else:
            print(format_stats(stats))
        return 0

    if args.kb_cmd == "query":
        if args.source and args.source not in VALID_QUERY_SOURCES:
            print(f"Invalid value: Unsupported source: {args.source}", file=sys.stderr)
            return 2
        try:
            with _connect_read_only(db_path) as conn:
                results = search_text(
                    conn,
                    query=args.query,
                    source=args.source,
                    start_date=args.start,
                    end_date=args.end,
                    author=args.author,
                    limit=args.limit,
                )
        except ValueError as err:
            print(f"Invalid value: {err}", file=sys.stderr)
            return 2
        except sqlite3.Error as err:
            print(f"kb query failed: {err}", file=sys.stderr)
            return 1
        if args.json:
            print(format_query_json(results))
        else:
            print(format_query_table(results))
        return 0

    if args.kb_cmd == "mcp":
        if getattr(args, "install_claude_desktop", False):
            config_path, existed, replaced = install_claude_desktop(home)
            if replaced:
                print("Warning: replaced existing deep-daily-kb Claude Desktop entry.", file=sys.stderr)
            state = "existing" if existed else "new"
            print(f"Installed. Restart Claude Desktop to activate. ({state} config: {config_path})")
            return 0
        try:
            asyncio.run(run_stdio_server(db_path=db_path))
        except sqlite3.Error as err:
            print(f"kb mcp failed: {err}", file=sys.stderr)
            return 1
        return 0

    raise AssertionError(f"Unhandled kb subcommand: {args.kb_cmd!r}")


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=1")
    return conn
