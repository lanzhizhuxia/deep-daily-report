"""``deep-daily fetch`` \u2014 run collectors only, no LLM, no publish.

Per PLAN v2.1 \u00a77 Step 17, \u00a73.6.

Invokes ``pipeline.collect_shared`` to populate the data cache (articles,
tweets, news_6551) without running the LLM pipeline or publishing. Primary
use cases:
  - Warm caches before a scheduled run so the 07:00 window completes faster.
  - Debug collector output in isolation.

Like ``run --dry-run``, fetch does NOT write to ``reported_events.json`` \u2014
that mutation is owned by the digest generation path (step 5 + its lock).
Fetch's side effects are limited to the collector output directories
(``articles/``, ``tweets/``, ``news-6551/``) and the step1 pipeline cache
under ``dailies/.pipeline/``.

``--collectors`` filter is declared in the PLAN but deferred: collectors
already self-gate on ``collectors.<name>.enabled`` in config.yaml, so
instance-level enable/disable is the primary knob. A filter is a future
extension when per-invocation overrides become necessary.
"""

from __future__ import annotations

import argparse
import datetime
import sys

from deep_daily.home import HomeConfig


def cmd_fetch(args: argparse.Namespace, home: HomeConfig) -> int:
    from deep_daily.pipeline import collect_shared

    date_str = getattr(args, "date", None) or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        print(
            f"Invalid date format: {date_str} (expected YYYY-MM-DD)",
            file=sys.stderr,
        )
        return 2

    force = bool(getattr(args, "force", False))
    resume = bool(getattr(args, "resume", False))

    print(f"Fetching collectors for {date_str} (home={home.path})")
    try:
        shared = collect_shared(date_str, force=force, resume=resume)
    except Exception as err:
        print(f"Fetch failed: {err}", file=sys.stderr)
        return 1

    materials = shared.get("materials") or []
    by_source: dict[str, int] = {}
    for m in materials:
        src = str(m.get("source") or "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    print(f"Fetched {len(materials)} materials total")
    for src, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {src}: {count}")
    return 0
