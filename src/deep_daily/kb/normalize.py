from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NormalizedItem:
    id: str
    source: str
    native_id: str
    event_ts: str
    fetched_ts: str
    author: str | None
    title: str | None
    body: str | None
    body_zh: str | None
    url: str | None
    category: str | None
    relevance: int | None
    has_curated: int
    has_bulk: int
    preferred_src: str
    raw_hash: str


def normalize_article(path: Path, raw_json: dict[str, Any]) -> NormalizedItem:
    native_id = _clean_text(raw_json.get("id")) or path.stem
    summary = _clean_text(raw_json.get("summary_zh"))
    insight = _clean_text(raw_json.get("insight_zh"))
    body = _join_optional(summary, insight)
    title = _clean_text(raw_json.get("title"))
    if title is None:
        title = _clean_text(raw_json.get("title_zh"))
    if title is None:
        title = _clean_text(raw_json.get("title_original"))
    category = _clean_text(raw_json.get("topic"))
    if category is None:
        category = _clean_text(raw_json.get("category"))

    return NormalizedItem(
        id=f"article:{native_id}",
        source="article",
        native_id=native_id,
        event_ts=_normalize_timestamp(raw_json["fetched_at"]),
        fetched_ts=_normalize_timestamp(raw_json["fetched_at"]),
        author=_clean_text(raw_json.get("feed_title")),
        title=title,
        body=body,
        body_zh=None,
        url=_clean_text(raw_json.get("link")),
        category=category,
        relevance=_coerce_int(raw_json.get("relevance")),
        has_curated=0,
        has_bulk=0,
        preferred_src="single",
        raw_hash=_raw_hash(raw_json),
    )


def normalize_tweet_curated(path: Path, raw_json: dict[str, Any]) -> NormalizedItem:
    native_id = _tweet_native_id(path, raw_json)
    body = _clean_text(raw_json.get("content"))
    reference_content = _clean_text(raw_json.get("reference_content"))
    if body is not None and reference_content is not None:
        body = f"{body}\n\n---\n\n{reference_content}"
    elif body is None:
        body = reference_content

    fetched_src = raw_json.get("fetched_at") or raw_json.get("event_time")
    return NormalizedItem(
        id=f"tweet:{native_id}",
        source="tweet",
        native_id=native_id,
        event_ts=_normalize_timestamp(raw_json["event_time"]),
        fetched_ts=_normalize_timestamp(fetched_src),
        author=_clean_text(raw_json.get("handle")),
        title=None,
        body=body,
        body_zh=_clean_text(raw_json.get("content_zh")),
        url=_clean_text(raw_json.get("tweet_url")),
        category=None,
        relevance=_coerce_int(raw_json.get("relevance")),
        has_curated=0,
        has_bulk=0,
        preferred_src="single",
        raw_hash=_raw_hash(raw_json),
    )


def normalize_tweet_bulk(path: Path, record: dict[str, Any]) -> NormalizedItem:
    native_id = _tweet_native_id(path, record)
    body = _clean_text(record.get("content"))
    reference_content = _clean_text(record.get("reference_content"))
    if body is not None and reference_content is not None:
        body = f"{body}\n\n---\n\n{reference_content}"
    elif body is None:
        body = reference_content

    return NormalizedItem(
        id=f"tweet:{native_id}",
        source="tweet",
        native_id=native_id,
        event_ts=_normalize_bulk_timestamp(record.get("collected_at") or record.get("event_time")),
        fetched_ts=_normalize_bulk_timestamp(record.get("collected_at") or record.get("event_time")),
        author=_clean_text(record.get("handle")),
        title=None,
        body=body,
        body_zh=_clean_text(record.get("content_zh")),
        url=_clean_text(record.get("tweet_url")),
        category=None,
        relevance=_coerce_int(record.get("relevance")),
        has_curated=0,
        has_bulk=0,
        preferred_src="single",
        raw_hash=_raw_hash(record),
    )


def _tweet_native_id(path: Path, raw_json: dict[str, Any]) -> str:
    raw_id = _clean_text(raw_json.get("id"))
    if raw_id:
        return raw_id[3:] if raw_id.startswith("tw_") else raw_id
    stem = path.stem
    return stem[3:] if stem.startswith("tw_") else stem


def _raw_hash(raw_json: dict[str, Any]) -> str:
    payload = json.dumps(raw_json, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _normalize_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KeyError("timestamp field missing")
    normalized = value.strip()
    if normalized.endswith("Z"):
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _normalize_bulk_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KeyError("timestamp field missing")
    normalized = value.strip()
    dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    dt = dt.astimezone(UTC).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _join_optional(first: str | None, second: str | None) -> str | None:
    if first and second:
        return f"{first}\n\n{second}"
    return first or second
