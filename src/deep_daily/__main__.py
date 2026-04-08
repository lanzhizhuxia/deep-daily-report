from __future__ import annotations

import argparse
import os
from pathlib import Path

from deep_daily.backends.david_multikey import DavidMultiKeyBackend
from deep_daily.backends.openai_compat import OpenAICompatibleBackend
from deep_daily.pipeline import cmd_generate, configure
from deep_daily.profile_gen import maybe_refresh_profile
from deep_daily.publishers.file_publisher import FilePublisher


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deep daily digest pipeline (ISSUE-084 Phase 3)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Generate deep daily digest")
    gen_parser.add_argument("--date", type=str, default=None, help="Target date in YYYY-MM-DD format (default: today)")
    gen_parser.add_argument("--force", action="store_true", help="Ignore cache, regenerate from scratch")
    gen_parser.add_argument("--resume", action="store_true", help="Resume from cached pipeline steps")
    gen_parser.add_argument("--model", type=str, default=None, help="Override Step 3 write model (default: DAILY_WRITE_MODEL env or google/gemini-3-pro-preview)")
    gen_parser.add_argument("--readers", type=str, default=None, help="Path to readers.yaml for multi-reader mode")
    gen_parser.add_argument("--reader-id", type=str, default=None, help="Run only this reader (requires --readers)")
    gen_parser.add_argument("--data-root", type=str, default=None, help="Override data root (default: ~/.local/deep-daily/legacy-data)")
    gen_parser.add_argument("--configs-dir", type=str, default=None, help="Override configs dir (default: package configs/)")
    gen_parser.add_argument("--llm-backend", choices=["openai", "david"], default="openai", help="LLM backend implementation")

    args = parser.parse_args()

    if args.command == "generate":
        llm = (
            DavidMultiKeyBackend(api_base=os.environ.get("LITELLM_API_BASE"))
            if args.llm_backend == "david"
            else OpenAICompatibleBackend(
                api_base=os.environ.get("LLM_API_BASE") or os.environ.get("LITELLM_API_BASE"),
                api_key=os.environ.get("LLM_API_KEY") or os.environ.get("LITELLM_API_KEY"),
            )
        )
        configure(
            llm=llm,
            publisher=FilePublisher(),
            data_root=Path(args.data_root).expanduser() if args.data_root else None,
            configs_dir=Path(args.configs_dir).expanduser() if args.configs_dir else None,
        )
        maybe_refresh_profile()
        cmd_generate(args)


if __name__ == "__main__":
    main()
