from __future__ import annotations

from pathlib import Path

import pytest

from deep_daily.kb.normalize import normalize_hn


def test_normalize_hn_timestamp_and_category_encoding() -> None:
    item = normalize_hn(
        Path("/tmp/hackernews/2026-05-05.jsonl"),
        {
            "objectID": "48019219",
            "created_at": "2026-05-05T07:34:55Z",
            "author": "john-doe",
            "title": "MCP on HN",
            "url": "https://example.com/mcp",
            "points": 307,
            "num_comments": 312,
        },
    )
    assert item.id == "hn:48019219"
    assert item.source == "hn"
    assert item.event_ts == "2026-05-05T07:34:55Z"
    assert item.body == "MCP on HN"
    assert item.category == "hn:points=307,num_comments=312"


def test_normalize_hn_missing_optional_fields() -> None:
    item = normalize_hn(
        Path("/tmp/hackernews/2026-05-05.jsonl"),
        {"objectID": "48019219", "created_at": "2026-05-05T07:34:55Z", "title": "MCP on HN"},
    )
    assert item.author is None
    assert item.url is None
    assert item.category is None


def test_normalize_hn_missing_timestamp_raises() -> None:
    with pytest.raises(KeyError):
        normalize_hn(Path("/tmp/hackernews/2026-05-05.jsonl"), {"objectID": "48019219", "title": "oops"})
