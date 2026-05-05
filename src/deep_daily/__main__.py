from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deep daily digest pipeline (ISSUE-084 Phase 3)",
    )
    parser.add_argument(
        "--home",
        type=str,
        default=None,
        help="Path to the deep-daily HOME (overrides $DEEP_DAILY_HOME)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Generate deep daily digest")
    gen_parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date in YYYY-MM-DD format (default: today)",
    )
    gen_parser.add_argument(
        "--force", action="store_true", help="Ignore cache, regenerate from scratch"
    )
    gen_parser.add_argument(
        "--resume", action="store_true", help="Resume from cached pipeline steps"
    )
    gen_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override Step 3 write model (default: DAILY_WRITE_MODEL env or google/gemini-3-pro-preview)",
    )
    gen_parser.add_argument(
        "--llm-backend",
        choices=["openai", "multikey"],
        default="openai",
        help="LLM backend implementation (multikey = round-robin across multiple keys)",
    )
    gen_parser.add_argument(
        "--publisher",
        choices=["file", "feishu"],
        default="file",
        help="Publish channel (file=local HTML only, feishu=push card via NotificationHub)",
    )

    args = parser.parse_args()

    from deep_daily.home import HomeConfig, HomeNotFoundError, HomeInvalidError

    allow_walkup = (
        args.command == "generate" and getattr(args, "date", None) is not None
    )
    try:
        home = HomeConfig.resolve(cli_home=args.home, allow_walkup=allow_walkup)
    except (HomeNotFoundError, HomeInvalidError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    from deep_daily.config import init_runtime

    init_runtime(home)

    if args.command == "generate":
        _dispatch_generate(args)


def _dispatch_generate(args: argparse.Namespace) -> None:
    from deep_daily.backends.openai_compat import OpenAICompatibleBackend
    from deep_daily.pipeline import cmd_generate, configure
    from deep_daily.profile_gen import maybe_refresh_profile
    from deep_daily.publishers.file_publisher import FilePublisher

    if args.llm_backend == "multikey":
        from deep_daily.backends.david_multikey import DavidMultiKeyBackend

        llm = DavidMultiKeyBackend(api_base=os.environ.get("LITELLM_API_BASE"))
    else:
        llm = OpenAICompatibleBackend(
            api_base=os.environ.get("LLM_API_BASE")
            or os.environ.get("LITELLM_API_BASE"),
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get("LITELLM_API_KEY"),
        )

    pub = FilePublisher()
    if args.publisher == "feishu":
        try:
            from tools.rss.feishu_publisher import FeishuPublisher

            pub = FeishuPublisher()
        except ImportError:
            print(
                "Warning: FeishuPublisher not available (tools.rss.feishu_publisher not on PYTHONPATH), falling back to FilePublisher"
            )

    configure(llm=llm, publisher=pub)
    maybe_refresh_profile()
    cmd_generate(args)


if __name__ == "__main__":
    main()
