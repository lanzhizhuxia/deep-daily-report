"""Hacker News collector — fetches yesterday's high-score stories via Algolia.

Uses the public Algolia HN Search API (https://hn.algolia.com/api) — no auth,
no API key, no rate limit concerns for our single-call-per-day pattern.

Pattern A (daily-window), consistent with rss.py / twitter.py / twitter_nas.py:
  - Fetch strictly the target date's top stories (UTC window)
  - Write cache to ``hackernews/{YYYY-MM-DD}.jsonl`` for resume
  - Return materials in the standard pipeline schema
  - No topic filtering at collector level — that's pipeline step 0's job

Design notes:
  - httpx is an optional dependency shared with news_6551 (already in
    [news6551] extras). We lazy-import so a missing httpx degrades gracefully
    instead of crashing the whole pipeline.
  - ``source_name`` is set to ``Hacker News ({domain})`` so PER_SOURCE_CAP
    groups stories by their linked domain (arxiv.org, github.com, …), letting
    multiple HN stories survive the cap as long as they link to different
    domains. Stories without a url (Ask HN / text posts) fall back to
    ``Hacker News``.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

DEFAULT_MIN_SCORE = 50
DEFAULT_HITS_PER_PAGE = 100
DEFAULT_MAX_STORIES = 30
HTTP_TIMEOUT_S = 15.0
HN_MATERIAL_RELEVANCE = 5


def _cache_path(hackernews_dir: Path, date_str: str) -> Path:
    return hackernews_dir / f"{date_str}.jsonl"


def _load_cache(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        items: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items
    except OSError:
        return None


def _save_cache(path: Path, hits: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for hit in hits:
                f.write(json.dumps(hit, ensure_ascii=False))
                f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _utc_day_window(date_str: str) -> tuple[int, int]:
    day = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc
    )
    next_day = day + datetime.timedelta(days=1)
    return int(day.timestamp()), int(next_day.timestamp())


def _fetch_algolia(
    date_str: str,
    *,
    min_score: int,
    hits_per_page: int,
    timeout: float,
) -> list[dict[str, Any]]:
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError(
            "hackernews collector requires optional dependency httpx; "
            "install deep-daily-report[news6551] (shared extra)"
        ) from e

    t0, t1 = _utc_day_window(date_str)
    params = {
        "tags": "story",
        "numericFilters": f"points>{min_score},created_at_i>{t0},created_at_i<{t1}",
        "hitsPerPage": str(hits_per_page),
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(ALGOLIA_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    hits = data.get("hits") or []
    return [h for h in hits if isinstance(h, dict)]


def _clean_title(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _story_domain(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _to_material(hit: dict[str, Any]) -> dict[str, Any] | None:
    title = _clean_title(hit.get("title") or "")
    if not title:
        return None
    story_id = hit.get("objectID")
    if not story_id:
        return None

    external_url = (hit.get("url") or "").strip()
    hn_url = f"https://news.ycombinator.com/item?id={story_id}"
    link = external_url or hn_url

    domain = _story_domain(external_url)
    source_name = f"Hacker News ({domain})" if domain else "Hacker News"

    created_i = hit.get("created_at_i")
    if isinstance(created_i, (int, float)) and created_i > 0:
        time_str = (
            datetime.datetime.fromtimestamp(int(created_i), tz=datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    else:
        time_str = str(hit.get("created_at") or "")

    try:
        points = int(hit.get("points") or 0)
    except (TypeError, ValueError):
        points = 0
    try:
        num_comments = int(hit.get("num_comments") or 0)
    except (TypeError, ValueError):
        num_comments = 0

    content_parts = [title]
    meta_bits: list[str] = []
    if points:
        meta_bits.append(f"{points} points")
    if num_comments:
        meta_bits.append(f"{num_comments} comments")
    if meta_bits:
        content_parts.append("(" + ", ".join(meta_bits) + f" on Hacker News: {hn_url})")
    content_zh = " ".join(content_parts)
    return {
        "id": f"hn-{story_id}",
        "source": "hackernews",
        "title": title,
        "content_zh": content_zh,
        "link": link,
        "source_name": source_name,
        "time": time_str,
        "tags": [],
        "relevance": HN_MATERIAL_RELEVANCE,
        "hn_meta": {
            "story_id": str(story_id),
            "score": points,
            "num_comments": num_comments,
            "author": hit.get("author") or "",
            "hn_url": hn_url,
            "external_url": external_url,
        },
    }


def collect_hackernews(
    date_str: str,
    *,
    hackernews_dir: Path,
    min_score: int = DEFAULT_MIN_SCORE,
    max_stories: int = DEFAULT_MAX_STORIES,
    verbose: bool = True,
) -> dict[str, Any]:
    """Collect Hacker News top stories for ``date_str`` via Algolia.

    Cache-aware: if ``hackernews/{date_str}.jsonl`` exists it's reused (enables
    ``--resume`` to skip the network call). Otherwise fetches fresh and writes
    cache atomically.

    Returns ``{"materials": [...], "count": int}`` matching the other
    collectors' contract. Failures are caught and reported as zero materials;
    they never propagate and crash step 1.
    """
    cache_path = _cache_path(hackernews_dir, date_str)

    cached = _load_cache(cache_path)
    if cached is not None:
        if verbose:
            print(f"  [hackernews] Cache hit: {len(cached)} hits from {cache_path.name}")
        hits = cached
    else:
        try:
            hits = _fetch_algolia(
                date_str,
                min_score=min_score,
                hits_per_page=DEFAULT_HITS_PER_PAGE,
                timeout=HTTP_TIMEOUT_S,
            )
        except Exception as e:
            if verbose:
                print(f"  [hackernews] Algolia fetch failed ({e}), skipping", file=sys.stderr)
            return {"materials": [], "count": 0}
        try:
            _save_cache(cache_path, hits)
        except Exception as e:
            if verbose:
                print(f"  [hackernews] Cache save failed: {e}", file=sys.stderr)

    hits.sort(key=lambda h: (-(h.get("points") or 0), h.get("objectID") or ""))
    if max_stories and len(hits) > max_stories:
        hits = hits[:max_stories]

    materials: list[dict[str, Any]] = []
    for hit in hits:
        m = _to_material(hit)
        if m is not None:
            materials.append(m)

    if verbose:
        print(
            f"  [hackernews] {len(materials)} stories "
            f"(points>{min_score}, UTC window for {date_str}, cap={max_stories})"
        )
    return {"materials": materials, "count": len(materials)}
