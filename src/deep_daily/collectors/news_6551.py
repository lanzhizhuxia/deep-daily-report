from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from deep_daily import config
from deep_daily.dedup import normalize_title as _normalize_title
from deep_daily.dedup import normalize_url as _normalize_url
from deep_daily.dedup import title_similarity as _title_similarity

DAILY_API_CALL_BUDGET = 8
API_SINGLE_TIMEOUT_S = 10.0
API_TOTAL_TIMEOUT_S = 30.0
DEFAULT_RESULT_LIMIT = 15
TITLE_DEDUP_THRESHOLD = 0.85
NEWS_MATERIAL_RELEVANCE = 5


def _config_path() -> Path:
    env = os.environ.get("OPENNEWS_CONFIG_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return config.get_app_config().configs_dir / "6551-config.json"


def _cache_dir() -> Path:
    return config.get_app_config().data_root / "news-6551"


def _load_6551_config() -> dict[str, str]:
    config_path = _config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"6551 config not found: {config_path}")
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        "api_base": cfg.get("api_base_url", "https://ai.6551.io").rstrip("/"),
        "api_token": os.environ.get("OPENNEWS_TOKEN") or cfg.get("api_token", ""),
    }


def _load_news_sources_config() -> dict[str, Any]:
    path = config.get_app_config().news_sources_yaml_path
    if not path.exists():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _get_whitelisted_source_codes(sources_cfg: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for src in sources_cfg.get("sources", []):
        code = src.get("code")
        if code and src.get("enabled", True):
            codes.append(code)
    return codes


def _cache_path(date_str: str) -> Path:
    return _cache_dir() / f"{date_str}.json"


def _load_cache(date_str: str) -> dict[str, Any]:
    path = _cache_path(date_str)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(date_str: str, cache: dict[str, Any]) -> None:
    import tempfile

    path = _cache_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cache_key(date_str: str, topic_slug: str) -> str:
    return f"{date_str}_{topic_slug}"


def _dedup_materials(news_items: list[dict[str, Any]], existing_materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_urls: set[str] = set()
    existing_titles: list[str] = []
    for m in existing_materials:
        url = _normalize_url(m.get("link", ""))
        if url:
            existing_urls.add(url)
        title = _normalize_title(m.get("title", ""))
        if title:
            existing_titles.append(title)

    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    for item in news_items:
        url = _normalize_url(item.get("link", ""))
        title_norm = _normalize_title(item.get("title", ""))
        if url and url in existing_urls:
            continue
        if url and url in seen_urls:
            continue
        dup_found = False
        for et in existing_titles:
            if _title_similarity(title_norm, et) > TITLE_DEDUP_THRESHOLD:
                dup_found = True
                break
        if dup_found:
            continue
        for st in seen_titles:
            if _title_similarity(title_norm, st) > TITLE_DEDUP_THRESHOLD:
                dup_found = True
                break
        if dup_found:
            continue
        result.append(item)
        if url:
            existing_urls.add(url)
            seen_urls.add(url)
        if title_norm:
            existing_titles.append(title_norm)
            seen_titles.append(title_norm)
    return result


def _make_material_id(url: str, title: str) -> str:
    raw = (url or title or str(time.time())).encode("utf-8")
    return "6551-" + hashlib.sha256(raw).hexdigest()[:8]


def _format_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title_raw = item.get("text") or item.get("title") or ""
    if not title_raw:
        return None
    title = re.sub(r'<[^>]+>', ' ', title_raw)
    title = title.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    title = re.sub(r'\s+', ' ', title).strip()
    link = item.get("link", "")
    source_name = item.get("newsType") or item.get("source") or "6551 News"
    ai_rating = item.get("aiRating") or {}
    content_zh = ai_rating.get("summary") or item.get("summary") or item.get("content") or item.get("description") or title
    ts = item.get("ts") or item.get("createdAt") or ""
    if ts and str(ts).isdigit() and len(str(ts)) >= 10:
        try:
            from datetime import datetime, timezone
            ts_s = int(str(ts)[:10])
            ts = datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            ts = str(ts)
    return {
        "id": _make_material_id(link, title),
        "source": "news-6551",
        "title": title,
        "content_zh": content_zh.strip() if content_zh else title,
        "link": link,
        "source_name": source_name,
        "time": ts,
        "tags": [],
        "relevance": NEWS_MATERIAL_RELEVANCE,
    }


def _call_news_search(api_base: str, api_token: str, query: str, source_codes: list[str] | None = None, limit: int = DEFAULT_RESULT_LIMIT, timeout: float = API_SINGLE_TIMEOUT_S) -> list[dict[str, Any]]:
    try:
        import httpx
    except ImportError as e:
        raise RuntimeError("news_6551 requires optional dependency httpx; install deep-daily-report[news6551]") from e
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    body: dict[str, Any] = {"limit": limit, "q": query}
    if source_codes:
        body["engineTypes"] = {"news": source_codes}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        resp = client.post(f"{api_base}/open/news_search", json=body)
        resp.raise_for_status()
        data = resp.json()
    return data.get("data", [])


def fetch_daily_news(date_str: str, topics: dict[str, Any], *, existing_materials: list[dict[str, Any]] | None = None, verbose: bool = True) -> list[dict[str, Any]]:
    if existing_materials is None:
        existing_materials = []
    try:
        cfg = _load_6551_config()
    except Exception as e:
        if verbose:
            print(f"  [6551 news] Config load failed ({e}), skipping", file=sys.stderr)
        return []
    api_base = cfg["api_base"]
    api_token = cfg["api_token"]
    if not api_token:
        if verbose:
            print("  [6551 news] No api_token in config, skipping", file=sys.stderr)
        return []
    sources_cfg = _load_news_sources_config()
    whitelisted_codes = _get_whitelisted_source_codes(sources_cfg)
    day_cache = _load_cache(date_str)
    all_news_materials: list[dict[str, Any]] = []
    all_topics = list(topics.get("pinned", [])) + list(topics.get("dynamic", []))
    call_count = 0
    total_start = time.monotonic()
    for topic in all_topics:
        if call_count >= DAILY_API_CALL_BUDGET:
            if verbose:
                print(f"  [6551 news] Daily budget exhausted ({DAILY_API_CALL_BUDGET} calls), skipping remaining topics", file=sys.stderr)
            break
        elapsed = time.monotonic() - total_start
        if elapsed >= API_TOTAL_TIMEOUT_S:
            if verbose:
                print(f"  [6551 news] Total timeout {API_TOTAL_TIMEOUT_S}s reached after {call_count} calls, stopping", file=sys.stderr)
            break
        slug = topic.get("slug", "")
        label = topic.get("label", slug)
        search_terms = topic.get("news_search_terms", [])
        if not search_terms:
            if verbose:
                print(f"  [6551 news] Topic '{slug}' has no news_search_terms, skipping", file=sys.stderr)
            continue
        query = search_terms[0]
        ck = _cache_key(date_str, slug)
        if ck in day_cache:
            cached_items = day_cache[ck]
            if verbose:
                print(f"  [6551 news] Topic '{label}': cache hit ({len(cached_items)} items)")
            all_news_materials.extend(cached_items)
            continue
        remaining = API_TOTAL_TIMEOUT_S - (time.monotonic() - total_start)
        single_timeout = min(API_SINGLE_TIMEOUT_S, remaining)
        if single_timeout <= 0:
            break
        if verbose:
            print(f"  [6551 news] Fetching topic '{label}' (call {call_count + 1}/{DAILY_API_CALL_BUDGET})...")
        try:
            raw_items = _call_news_search(api_base, api_token, query, source_codes=whitelisted_codes if whitelisted_codes else None, timeout=single_timeout)
            call_count += 1
        except Exception as e:
            if verbose:
                print(f"  [6551 news] Error fetching '{label}': {e}", file=sys.stderr)
            call_count += 1
            continue
        formatted: list[dict[str, Any]] = []
        for raw in raw_items:
            m = _format_news_item(raw)
            if m:
                formatted.append(m)
        if verbose:
            print(f"  [6551 news] Topic '{label}': {len(raw_items)} raw → {len(formatted)} formatted")
        day_cache[ck] = formatted
        try:
            _save_cache(date_str, day_cache)
        except Exception as e:
            if verbose:
                print(f"  [6551 news] Cache save failed: {e}", file=sys.stderr)
        all_news_materials.extend(formatted)
    if not all_news_materials:
        if verbose:
            print("  [6551 news] No news materials fetched", file=sys.stderr)
        return []
    before_dedup = len(all_news_materials)
    deduped = _dedup_materials(all_news_materials, existing_materials)
    removed = before_dedup - len(deduped)
    if verbose:
        print(f"  [6551 news] {before_dedup} raw → {len(deduped)} after dedup (removed {removed} duplicates, {call_count} API calls used)")
    return deduped
