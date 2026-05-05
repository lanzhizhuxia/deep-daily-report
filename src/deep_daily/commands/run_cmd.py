"""``deep-daily run`` \u2014 generate a daily digest for the active HOME.

Per PLAN v2.1 \u00a77 Step 17, \u00a75.5 (dry-run semantics).

This command is a thin orchestrator that owns three concerns:
  1. Resolving the LLM backend from config.yaml + env vars (Oracle v2 \u00a711.3:
     env validation belongs to ``doctor``, but binding concrete backend objects
     happens at command entry).
  2. Resolving the Publisher from config.yaml + CLI override.
  3. Owning ``--dry-run`` semantics per \u00a75.5: force FilePublisher (no external
     push), forward dry_run flag to pipeline so cache + output paths route to
     the isolated ``dailies-dryrun/`` tree and reported_events.json is left
     read-only.

All pipeline internals stay in ``deep_daily.pipeline``. This command contains
no business logic \u2014 if it starts to grow any, that logic belongs in pipeline.
"""

from __future__ import annotations

import argparse
import os
import sys

from deep_daily.home import HomeConfig


def _resolve_llm_backend(home: HomeConfig, cli_override: str | None):
    llm_cfg = home.raw_config.get("llm") or {}
    backend_name = cli_override or str(llm_cfg.get("backend") or "openai").strip()

    if backend_name in ("multikey", "david"):
        if backend_name == "david":
            print(
                "Warning: llm.backend=david is deprecated; use 'multikey'.",
                file=sys.stderr,
            )
        from deep_daily.backends.litellm_multikey import LiteLLMMultiKeyBackend

        return LiteLLMMultiKeyBackend(api_base=os.environ.get("LITELLM_API_BASE"))

    if backend_name == "openai":
        from deep_daily.backends.openai_compat import OpenAICompatibleBackend

        return OpenAICompatibleBackend(
            api_base=os.environ.get("LLM_API_BASE")
            or os.environ.get("LITELLM_API_BASE"),
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get("LITELLM_API_KEY"),
        )

    raise RuntimeError(
        f"Unknown llm.backend={backend_name!r} (expected openai|multikey). "
        f"Edit {home.path}/config.yaml and re-run."
    )


def _resolve_publisher(home: HomeConfig, cli_override: str | None, *, dry_run: bool):
    from deep_daily.publishers.file_publisher import FilePublisher

    if dry_run:
        return FilePublisher()

    pub_cfg = home.raw_config.get("publisher") or {}
    name = cli_override or str(pub_cfg.get("default") or "file").strip()

    if name == "file":
        return FilePublisher()

    if name == "feishu":
        try:
            from tools.rss.feishu_publisher import FeishuPublisher

            return FeishuPublisher()
        except ImportError:
            print(
                "Warning: FeishuPublisher unavailable (tools.rss.feishu_publisher "
                "not on PYTHONPATH), falling back to FilePublisher.",
                file=sys.stderr,
            )
            return FilePublisher()

    raise RuntimeError(
        f"Unknown publisher={name!r} (expected file|feishu). "
        f"Edit {home.path}/config.yaml or pass --publisher."
    )


def cmd_run(args: argparse.Namespace, home: HomeConfig) -> int:
    from deep_daily.config import (
        resolve_effective_models,
        set_effective_models,
    )
    from deep_daily.pipeline import cmd_generate, configure
    from deep_daily.profile_gen import maybe_refresh_profile

    dry_run = bool(getattr(args, "dry_run", False))

    if dry_run:
        print(
            f"[dry-run] outputs → {home.data_dir / 'dailies-dryrun'}  "
            f"(no publish, reported_events.json read-only)",
            file=sys.stderr,
        )

    effective_models = resolve_effective_models(
        raw_config=home.raw_config,
        env=os.environ,
        cli_write_model=getattr(args, "model", None),
    )
    set_effective_models(effective_models)

    llm = _resolve_llm_backend(home, getattr(args, "llm_backend", None))
    publisher = _resolve_publisher(
        home, getattr(args, "publisher", None), dry_run=dry_run
    )
    configure(llm=llm, publisher=publisher)

    if not dry_run:
        maybe_refresh_profile()

    cmd_generate(args)
    return 0
