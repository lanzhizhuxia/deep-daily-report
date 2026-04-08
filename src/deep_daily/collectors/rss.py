from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collect_articles(date_str: str, *, articles_dir: Path) -> dict[str, Any]:
    materials: list[dict[str, Any]] = []
    article_count = 0
    if articles_dir.exists():
        for fpath in articles_dir.glob("*.json"):
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            fetched_date = data.get("fetched_at", "")[:10]
            if fetched_date != date_str:
                continue
            relevance = data.get("relevance", 0)
            if not isinstance(relevance, int) or relevance < 3:
                continue
            article_count += 1
            materials.append(
                {
                    "id": data.get("id", fpath.stem),
                    "source": data.get("source", "rss"),
                    "title": data.get("title_zh", data.get("title_original", "")),
                    "content_zh": data.get("summary_zh", ""),
                    "link": data.get("link", ""),
                    "source_name": data.get("feed_title", ""),
                    "time": data.get("fetched_at", ""),
                    "tags": data.get("tags", []),
                    "relevance": relevance,
                }
            )
    return {"materials": materials, "count": article_count}
