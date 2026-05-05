from __future__ import annotations

import random

from deep_daily.kb.dedup import BULK_FILLS_WHEN_NULL, CURATED_OVERRIDES, MUST_MATCH_OR_WARN, merge_tweet
from deep_daily.kb.normalize import NormalizedItem


def _item(**overrides: object) -> NormalizedItem:
    base = {
        "id": "tweet:1",
        "source": "tweet",
        "native_id": "1",
        "event_ts": "2026-05-01T00:00:00Z",
        "fetched_ts": "2026-05-01T00:00:00Z",
        "author": None,
        "title": None,
        "body": None,
        "body_zh": None,
        "url": "https://x.com/a/status/1",
        "category": None,
        "relevance": None,
        "has_curated": 0,
        "has_bulk": 0,
        "preferred_src": "single",
        "raw_hash": "hash",
    }
    base.update(overrides)
    return NormalizedItem(**base)


def test_merge_tweet_curated_preserves_body_zh_over_bulk() -> None:
    curated = _item(body="curated", body_zh="中文", author="alice")
    bulk = _item(body="bulk", body_zh=None, author=None, fetched_ts="2026-05-01T01:00:00Z", raw_hash="bulk")
    merged = merge_tweet(merge_tweet(None, curated, "curated"), bulk, "bulk")
    assert merged.body == "curated"
    assert merged.body_zh == "中文"
    assert merged.has_curated == 1
    assert merged.has_bulk == 1
    assert merged.preferred_src == "curated"


def test_merge_tweet_warns_on_event_ts_drift(caplog) -> None:
    existing = _item(event_ts="2026-05-01T00:00:00Z")
    incoming = _item(event_ts="2026-05-01T00:00:01Z", raw_hash="bulk")
    with caplog.at_level("WARNING", logger="deep_daily.kb.dedup"):
        merged = merge_tweet(existing, incoming, "bulk")
    assert merged.event_ts == "2026-05-01T00:00:00Z"
    assert "field drift on tweet:1: event_ts" in caplog.text


def test_merge_tweet_decision_matrix() -> None:
    bulk_first = _item(
        body=None,
        body_zh=None,
        title=None,
        author=None,
        url=None,
        relevance=None,
        category=None,
        fetched_ts="2026-05-01T00:00:00Z",
    )
    curated = _item(
        body="curated-body",
        body_zh="curated-zh",
        title="curated-title",
        author="curated-author",
        url="https://x.com/curated/status/1",
        relevance=5,
        category="rwa",
        fetched_ts="2026-05-01T02:00:00Z",
        raw_hash="curated-hash",
    )
    merged = merge_tweet(merge_tweet(None, bulk_first, "bulk"), curated, "curated")
    for field in CURATED_OVERRIDES:
        assert getattr(merged, field) == getattr(curated, field)
    assert merged.url is None
    assert merged.preferred_src == "curated"
    assert merged.has_curated == 1
    assert merged.has_bulk == 1

    bulk_fill = _item(
        body="bulk-body",
        body_zh="bulk-zh",
        title="bulk-title",
        author="bulk-author",
        url="https://x.com/bulk/status/1",
        relevance=3,
        category="stablecoin",
        fetched_ts="2026-05-01T03:00:00Z",
        raw_hash="bulk-fill-hash",
    )
    merged_bulk = merge_tweet(_item(url=None), bulk_fill, "bulk")
    for field in BULK_FILLS_WHEN_NULL:
        assert getattr(merged_bulk, field) == getattr(bulk_fill, field)
    for field in MUST_MATCH_OR_WARN - {"url"}:
        assert getattr(merged_bulk, field) == getattr(bulk_fill, field)


def test_merge_tweet_order_independence_fuzz() -> None:
    rng = random.Random(0)
    text_fields = ["body", "body_zh", "title", "author", "url", "category"]
    for idx in range(60):
        curated_kwargs = {field: (f"c-{idx}-{field}" if rng.choice([True, False]) else None) for field in text_fields if field != "url"}
        curated_kwargs["url"] = None
        bulk_kwargs = {field: (f"b-{idx}-{field}" if rng.choice([True, False]) else None) for field in text_fields}
        curated = _item(
            fetched_ts=f"2026-05-01T00:{idx % 60:02d}:00Z",
            relevance=rng.choice([None, 1, 2, 3, 4, 5]),
            raw_hash=f"curated-{idx}",
            **curated_kwargs,
        )
        bulk = _item(
            fetched_ts=f"2026-05-01T01:{idx % 60:02d}:00Z",
            relevance=rng.choice([None, 1, 2, 3, 4, 5]),
            raw_hash=f"bulk-{idx}",
            **bulk_kwargs,
        )
        merged_curated_first = merge_tweet(merge_tweet(None, curated, "curated"), bulk, "bulk")
        merged_bulk_first = merge_tweet(merge_tweet(None, bulk, "bulk"), curated, "curated")
        merged_curated_first = _item(**{**merged_curated_first.__dict__, "raw_hash": "same"})
        merged_bulk_first = _item(**{**merged_bulk_first.__dict__, "raw_hash": "same"})
        assert merged_curated_first == merged_bulk_first
