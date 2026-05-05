from __future__ import annotations

from pathlib import Path

import pytest

from deep_daily.kb.normalize import normalize_tweet_bulk


def test_normalize_tweet_bulk_uses_collected_at_utc8_to_utc() -> None:
    item = normalize_tweet_bulk(
        Path("/tmp/tweets.jsonl"),
        {
            "id": "tw_123",
            "collected_at": "2026-05-01T00:00:00+08:00",
            "tweet_url": "https://x.com/test/status/123",
            "content": "hello",
            "content_zh": "你好",
            "handle": "tester",
        },
    )
    assert item.native_id == "123"
    assert item.event_ts == "2026-04-30T16:00:00Z"
    assert item.fetched_ts == "2026-04-30T16:00:00Z"
    assert item.body == "hello"
    assert item.body_zh == "你好"


def test_normalize_tweet_bulk_missing_fields() -> None:
    item = normalize_tweet_bulk(
        Path("/tmp/tweets.jsonl"),
        {"id": "tw_1", "event_time": "2026-05-01T00:00:00+08:00", "tweet_url": "u", "reference_content": "reply"},
    )
    assert item.body == "reply"
    assert item.author is None
    assert item.relevance is None


def test_normalize_tweet_bulk_missing_timestamp_raises() -> None:
    with pytest.raises(KeyError):
        normalize_tweet_bulk(Path("/tmp/tweets.jsonl"), {"id": "tw_1", "tweet_url": "u"})
