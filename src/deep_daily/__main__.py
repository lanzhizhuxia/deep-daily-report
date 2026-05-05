"""Deep-daily CLI dispatcher.

Implements PLAN v2.1 §11.1 exactly:

  - Parse args first.
  - HOME-free commands (``init``, ``templates``, ``version``) bypass
    ``HomeConfig.resolve`` and ``init_runtime`` entirely.
  - HOME-required commands resolve HOME, call ``init_runtime(home)`` once,
    then dispatch.
  - ``allow_walkup`` is True only for ``doctor`` and for ``run --date <YYYY-MM-DD>``
    per §3.5 (interactive inspection).

No I/O at module import time — every heavy import lives inside the dispatch
branches.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep-daily",
        description="Deep daily digest tool (v0.3.0 generic HOME architecture)",
    )
    parser.add_argument(
        "--home",
        type=str,
        default=None,
        help="Path to the deep-daily HOME (overrides $DEEP_DAILY_HOME). "
        "Required unless walk-up discovery applies (see docs/architecture.md).",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    # ------------------------------------------------------------------ init
    p_init = sub.add_parser("init", help="Initialize a new deep-daily HOME at <path>")
    p_init.add_argument("path", type=str, help="Target directory for the new HOME")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files if the target already contains content",
    )
    p_init.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive mode: accept defaults without prompting",
    )
    p_init.add_argument(
        "--reader-name",
        type=str,
        default=None,
        help="Reader name for config.yaml (default: derived from target dir name)",
    )

    # ------------------------------------------------------------- templates
    p_tpl = sub.add_parser("templates", help="Manage bundled template packs")
    p_tpl.add_argument("action", choices=["list"], help="Template action")

    # --------------------------------------------------------------- version
    sub.add_parser("version", help="Print deep-daily version and exit")

    # ------------------------------------------------------------------ run
    p_run = sub.add_parser("run", help="Run the daily pipeline")
    p_run.add_argument(
        "--date", type=str, default=None, help="Target date YYYY-MM-DD (default: today)"
    )
    p_run.add_argument(
        "--force", action="store_true", help="Ignore cache; regenerate from scratch"
    )
    p_run.add_argument(
        "--resume", action="store_true", help="Resume from cached pipeline steps"
    )
    p_run.add_argument(
        "--model", type=str, default=None, help="Override the Step 3 write model"
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not publish; do not update reported_events; "
        "write to dailies-dryrun/ only",
    )
    p_run.add_argument(
        "--llm-backend",
        choices=["openai", "multikey", "david"],
        default=None,
        help="Override config.yaml llm.backend "
        "('david' is a deprecated alias for 'multikey')",
    )
    p_run.add_argument(
        "--publisher",
        choices=["file", "feishu"],
        default=None,
        help="Override config.yaml publisher.default",
    )

    # ---------------------------------------------------------------- fetch
    p_fetch = sub.add_parser("fetch", help="Run collectors only (no LLM, no publish)")
    p_fetch.add_argument(
        "--collectors",
        type=str,
        default=None,
        help="Comma-separated collector names "
        "(reserved — currently respects config.yaml toggles only)",
    )

    # ---------------------------------------------------------------- doctor
    p_doctor = sub.add_parser("doctor", help="Health check of this HOME")
    p_doctor.add_argument(
        "--deep", action="store_true", help="Also probe LLM reachability"
    )
    p_doctor.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )

    # --------------------------------------------------------------------- kb
    p_kb = sub.add_parser("kb", help="Knowledge base commands")
    kb_sub = p_kb.add_subparsers(dest="kb_cmd", required=True)
    p_kb_ingest = kb_sub.add_parser("ingest", help="Ingest raw files into kb.db")
    p_kb_ingest.add_argument(
        "--rebuild", action="store_true", help="Drop and recreate kb.db before ingest"
    )
    p_kb_ingest.add_argument(
        "--since", type=str, default=None, help="Only process files since YYYY-MM-DD"
    )
    p_kb_ingest.add_argument(
        "--sources", type=str, default=None, help="Comma-separated sources: articles,tweets"
    )
    p_kb_stats = kb_sub.add_parser("stats", help="Print kb stats")
    p_kb_stats.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_kb_query = kb_sub.add_parser("query", help="Run FTS query against kb.db")
    p_kb_query.add_argument("query", type=str, help="FTS5 MATCH query string")
    p_kb_query.add_argument("--source", type=str, default=None, help="Filter source")
    p_kb_query.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD inclusive")
    p_kb_query.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD inclusive")
    p_kb_query.add_argument("--author", type=str, default=None, help="Case-insensitive author substring")
    p_kb_query.add_argument("--limit", type=int, default=20, help="Max rows (clamped to 1..100)")
    p_kb_query.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_kb_mcp = kb_sub.add_parser("mcp", help="Run KB MCP stdio server or install Claude Desktop config")
    p_kb_mcp.add_argument(
        "--install-claude-desktop",
        action="store_true",
        help="Install/merge Claude Desktop MCP server config and exit",
    )

    # ---------------------------------------------------------------- backup
    p_backup = sub.add_parser("backup", help="Archive and back up HOME data to NAS")
    p_backup.add_argument("--dry-run", action="store_true", help="Print plan; write nothing")
    p_backup.add_argument("--retention", type=int, default=None, help="Override retention count")
    p_backup.add_argument(
        "--skip-checksum",
        action="store_true",
        help="Skip sha256 verification before remote rename",
    )
    p_backup.add_argument(
        "--force-unlock",
        action="store_true",
        help="Override stale lock checks and take the backup lock",
    )

    # ------------------------------------------------------- migrate-legacy
    p_mig = sub.add_parser(
        "migrate-legacy", help="Copy runtime data from a legacy ~/.local/deep-daily layout"
    )
    p_mig.add_argument(
        "--from",
        dest="from_path",
        type=str,
        default=None,
        help="Legacy source path (default: ~/.local/deep-daily/legacy-data)",
    )
    p_mig.add_argument(
        "--force",
        action="store_true",
        help="Re-migrate into a target whose manifest is status=completed",
    )
    p_mig.add_argument(
        "--dry-run", action="store_true", help="Print the plan; write nothing"
    )
    p_mig.add_argument(
        "--confirm-near-schedule",
        action="store_true",
        help="Proceed even if we are inside the 06:45-07:15 launchd window",
    )

    return parser


def _run_home_free(cmd: str, args: argparse.Namespace) -> int:
    if cmd == "init":
        from deep_daily.commands.init_cmd import cmd_init

        # cmd_init currently raises SystemExit on error via its own wrapper.
        cmd_init(args)
        return 0

    if cmd == "templates":
        from deep_daily.commands.templates_cmd import cmd_templates

        return cmd_templates(args)

    if cmd == "version":
        from deep_daily import __version__

        print(__version__)
        return 0

    raise AssertionError(f"Unhandled home-free command: {cmd!r}")


def _run_home_required(cmd: str, args: argparse.Namespace) -> int:
    from deep_daily.home import HomeConfig, HomeInvalidError, HomeNotFoundError

    allow_walkup = cmd == "doctor" or (
        cmd == "run" and getattr(args, "date", None) is not None
    )
    try:
        home = HomeConfig.resolve(cli_home=args.home, allow_walkup=allow_walkup)
    except (HomeNotFoundError, HomeInvalidError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    # migrate-legacy must see an empty data/ dir (Oracle v3 guard), so we
    # skip init_runtime scaffolding for that command. The command creates
    # target subdirs itself as it walks the legacy tree.
    if cmd != "migrate-legacy":
        from deep_daily.config import init_runtime

        init_runtime(home)

    if cmd == "run":
        from deep_daily.commands.run_cmd import cmd_run

        return cmd_run(args, home)
    if cmd == "fetch":
        from deep_daily.commands.fetch_cmd import cmd_fetch

        return cmd_fetch(args, home)
    if cmd == "doctor":
        from deep_daily.commands.doctor_cmd import cmd_doctor

        return cmd_doctor(args, home)
    if cmd == "kb":
        from deep_daily.commands.kb_cmd import cmd_kb

        return cmd_kb(args, home)
    if cmd == "backup":
        from deep_daily.commands.backup_cmd import cmd_backup

        return cmd_backup(args, home)
    if cmd == "migrate-legacy":
        from deep_daily.commands.migrate_legacy_cmd import cmd_migrate_legacy

        return cmd_migrate_legacy(args, home)

    raise AssertionError(f"Unhandled home-required command: {cmd!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.cmd
    home_free = {"init", "templates", "version"}
    if cmd in home_free:
        return _run_home_free(cmd, args)
    return _run_home_required(cmd, args)


if __name__ == "__main__":
    sys.exit(main())
