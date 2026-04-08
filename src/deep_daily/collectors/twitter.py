from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _sanitize_tweet_content(text: str) -> str:
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(char, "")
    text = re.sub(r"^[^·]+·\s*@\w+\s*·\s*", "", text).strip()
    return text[:2000]


def collect_tweets(date_str: str, *, tweets_dir: Path) -> dict[str, Any]:
    materials: list[dict[str, Any]] = []
    tweet_count = 0
    if tweets_dir.exists():
        for fpath in tweets_dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            event_date = data.get("event_time", "")[:10]
            if event_date != date_str:
                continue
            event_type = data.get("event_type", "")
            if event_type not in ("tweet", "quote"):
                continue
            tweet_count += 1

            handle = data.get("handle", "unknown")
            raw_content = data.get("content", "")
            content_zh = data.get("content_zh", "") or raw_content
            content_zh = _sanitize_tweet_content(content_zh)

            preview = raw_content[:30].replace("\n", " ")
            title = f"@{handle}: {preview}..."

            materials.append(
                {
                    "id": data.get("id", fpath.stem),
                    "source": data.get("source", "twitter"),
                    "title": title,
                    "content_zh": content_zh,
                    "link": data.get("tweet_url", ""),
                    "source_name": f"Twitter @{handle}",
                    "time": data.get("event_time", ""),
                    "tags": [],
                    "relevance": 5,
                    "content_len": len(raw_content),
                    "tweet_meta": {
                        "handle": handle,
                        "event_type": event_type,
                        "reference_handle": data.get("reference_handle", ""),
                        "reference_content_zh": data.get("reference_content_zh", ""),
                    },
                }
            )
    return {"materials": materials, "count": tweet_count}
