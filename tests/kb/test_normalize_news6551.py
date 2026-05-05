from __future__ import annotations

from pathlib import Path

import pytest

from deep_daily.kb.normalize import normalize_news6551


def test_normalize_news6551_timestamp_and_category() -> None:
    item = normalize_news6551(
        Path("/tmp/news-6551/2026-02-21_rwa"),
        {
            "id": "6551-abc",
            "time": "2026-02-18T12:50:00.747106Z",
            "title": "RWA title",
            "link": "https://example.com/rwa",
            "source_name": "Reuters",
            "relevance": 5,
        },
    )
    assert item.id == "news6551:6551-abc"
    assert item.source == "news6551"
    assert item.event_ts == "2026-02-18T12:50:00Z"
    assert item.fetched_ts == "2026-02-18T12:50:00Z"
    assert item.author == "Reuters"
    assert item.title == "RWA title"
    assert item.body == "RWA title"
    assert item.category == "rwa"
    assert item.relevance == 5


def test_normalize_news6551_missing_optional_fields() -> None:
    item = normalize_news6551(
        Path("/tmp/news-6551/2026-02-21_ai-and-automation"),
        {"id": "6551-xyz", "time": "2026-02-18T12:50:00Z", "title": "AI title"},
    )
    assert item.url is None
    assert item.author is None
    assert item.category == "ai-and-automation"
    assert item.relevance is None


def test_normalize_news6551_missing_timestamp_raises() -> None:
    with pytest.raises(KeyError):
        normalize_news6551(Path("/tmp/news-6551/2026-02-21_rwa"), {"id": "6551-abc", "title": "oops"})
