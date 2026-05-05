from __future__ import annotations

import argparse
import sys

from deep_daily.home import HomeConfig


def cmd_kb(args: argparse.Namespace, home: HomeConfig) -> int:
    from deep_daily.config import init_runtime
    from deep_daily.kb.cli import format_stats, format_stats_json
    from deep_daily.kb.ingest import collect_stats, ingest
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

    raise AssertionError(f"Unhandled kb subcommand: {args.kb_cmd!r}")
