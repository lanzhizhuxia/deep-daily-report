from __future__ import annotations

import logging
from dataclasses import replace
from typing import Literal

from .normalize import NormalizedItem

logger = logging.getLogger("deep_daily.kb.dedup")

CURATED_OVERRIDES = {"body", "body_zh", "title", "author", "relevance", "category"}
BULK_FILLS_WHEN_NULL = {"body", "body_zh", "title", "author", "url", "relevance", "category"}
MUST_MATCH_OR_WARN = {"native_id", "event_ts", "url"}


def merge_tweet(
    existing: NormalizedItem | None,
    incoming: NormalizedItem,
    source: Literal["curated", "bulk"],
) -> NormalizedItem:
    """Merge one normalized tweet into the canonical tweet item."""
    if existing is None:
        return replace(
            incoming,
            has_curated=1 if source == "curated" else 0,
            has_bulk=1 if source == "bulk" else 0,
            preferred_src=source,
        )

    merged = replace(existing)
    if source == "curated":
        merged = replace(merged, has_curated=1)
    else:
        merged = replace(merged, has_bulk=1)

    for field in MUST_MATCH_OR_WARN:
        existing_val = getattr(existing, field)
        incoming_val = getattr(incoming, field)
        if existing_val and incoming_val and existing_val != incoming_val:
            logger.warning(
                "field drift on %s: %s existing=%r incoming=%r (source=%s)",
                existing.id,
                field,
                existing_val,
                incoming_val,
                source,
            )

    if source == "curated":
        updates = {field: getattr(incoming, field) for field in CURATED_OVERRIDES if getattr(incoming, field) is not None}
        updates["preferred_src"] = "curated"
        merged = replace(merged, **updates)
    else:
        updates = {
            field: getattr(incoming, field)
            for field in BULK_FILLS_WHEN_NULL
            if getattr(merged, field) is None and getattr(incoming, field) is not None
        }
        if updates:
            merged = replace(merged, **updates)

    return replace(merged, fetched_ts=max(existing.fetched_ts, incoming.fetched_ts), raw_hash=existing.raw_hash)
