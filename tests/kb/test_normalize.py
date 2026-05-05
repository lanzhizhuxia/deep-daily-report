from __future__ import annotations

import json
from pathlib import Path

from deep_daily.kb.normalize import normalize_article, normalize_tweet_curated


def test_normalize_article_fixture() -> None:
    path = Path("/tmp/article.json")
    raw = json.loads((Path(__file__).parent / "fixtures" / "article.json").read_text())
    item = normalize_article(path, raw)
    assert item.id == "article:abc123"
    assert item.source == "article"
    assert item.native_id == "abc123"
    assert item.event_ts == "2026-04-29T00:01:18Z"
    assert item.fetched_ts == "2026-04-29T00:01:18Z"
    assert item.author == "example-feed"
    assert item.title == "中文标题"
    assert item.body == "摘要\n\n洞察"
    assert item.body_zh is None
    assert item.url == "https://example.com/article"
    assert item.category == "rwa"
    assert item.relevance == 3
    assert item.preferred_src == "single"


def test_normalize_tweet_fixture() -> None:
    path = Path("/tmp/tw_2049029095842005458.json")
    raw = json.loads((Path(__file__).parent / "fixtures" / "tweet.json").read_text())
    item = normalize_tweet_curated(path, raw)
    assert item.id == "tweet:2049029095842005458"
    assert item.source == "tweet"
    assert item.native_id == "2049029095842005458"
    assert item.event_ts == "2026-04-28T07:33:00Z"
    assert item.fetched_ts == "2026-04-28T08:00:00Z"
    assert item.author == "tester"
    assert item.title is None
    assert item.body == "hello world\n\n---\n\nquoted reply"
    assert item.body_zh == "你好世界"
    assert item.url == "https://x.com/test/status/2049029095842005458"
    assert item.category is None
    assert item.relevance == 4


def test_normalize_missing_optional_fields() -> None:
    item = normalize_tweet_curated(
        Path("/tmp/tw_1.json"),
        {"id": "tw_1", "event_time": "2026-01-01T00:00:00Z", "tweet_url": "u", "content": "x"},
    )
    assert item.author is None
    assert item.body_zh is None
    assert item.relevance is None
