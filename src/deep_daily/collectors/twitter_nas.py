from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any


def _sanitize_tweet_content(text: str) -> str:
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(char, "")
    text = re.sub(r"^[^·]+·\s*@\w+\s*·\s*", "", text).strip()
    return text[:2000]


def collect_nas_tweets(
    date_str: str,
    *,
    tweets_nas_dir: Path,
    kols_path: Path,
    dynamic_kols_path: Path,
    existing_materials: list[dict[str, Any]],
) -> dict[str, Any]:
    NAS_TWEET_CAP = 300
    NAS_PER_HANDLE_CAP = 3
    NAS_MIN_CONTENT_LEN = 50
    _MEME_RE = re.compile(
        r"^[\s\W\U0001F000-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
        r"\U0001F900-\U0001F9FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F]+$"
    )

    _kol_handles: set[str] = set()
    if kols_path.exists():
        try:
            with open(kols_path, encoding="utf-8") as _kf:
                _kol_data = json.load(_kf)
            for _entry in _kol_data.get("kols", []):
                _h = (_entry.get("handle") or "").lower().lstrip("@")
                if _h:
                    _kol_handles.add(_h)
        except (json.JSONDecodeError, OSError):
            pass

    if dynamic_kols_path.exists():
        try:
            with open(dynamic_kols_path, encoding="utf-8") as _dkf:
                _dyn_kol_data = json.load(_dkf)
            for _entry in _dyn_kol_data.get("kols", []):
                _h = (_entry.get("handle") or "").lower().lstrip("@")
                if _h:
                    _kol_handles.add(_h)
        except (json.JSONDecodeError, OSError):
            pass

    seen_tweet_ids = {m["id"] for m in existing_materials if m.get("source") == "twitter"}
    seen_status_ids: set[str] = set()
    for _existing_tid in seen_tweet_ids:
        _num_match = re.search(r"(\d+)$", _existing_tid)
        if _num_match:
            seen_status_ids.add(_num_match.group(1))
    nas_tweet_count = 0
    nas_kol_count = 0
    nas_filtered_short = 0
    nas_filtered_meme = 0
    nas_filtered_cap = 0
    nas_handle_counts: dict[str, int] = {}
    datetime.datetime.strptime(date_str, "%Y-%m-%d")
    nas_dates = [date_str]

    materials: list[dict[str, Any]] = []

    for nas_date in nas_dates:
        nas_jsonl_files = [
            tweets_nas_dir / f"tweets-{nas_date}.jsonl",
            tweets_nas_dir / f"tweets-brave-{nas_date}.jsonl",
        ]
        for nas_jsonl in nas_jsonl_files:
            if not nas_jsonl.exists():
                continue
            try:
                with open(nas_jsonl, "r", encoding="utf-8") as jf:
                    for line in jf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        tid = data.get("id", "")
                        _tid_num = re.search(r"(\d+)$", tid)
                        _numeric_id = _tid_num.group(1) if _tid_num else tid
                        if _numeric_id in seen_status_ids:
                            continue
                        seen_status_ids.add(_numeric_id)

                        event_type = data.get("event_type", "")
                        if event_type not in ("newTweet", "quote"):
                            continue

                        event_time = data.get("event_time", data.get("collected_at", ""))
                        event_date = event_time[:10] if event_time else ""
                        if event_date not in nas_dates:
                            continue

                        handle = data.get("handle", "unknown")
                        handle_lower = handle.lower()
                        raw_content = data.get("content", "")

                        if len(raw_content) < NAS_MIN_CONTENT_LEN:
                            nas_filtered_short += 1
                            continue

                        if _MEME_RE.match(raw_content):
                            nas_filtered_meme += 1
                            continue

                        is_kol = handle_lower in _kol_handles
                        if not is_kol and nas_tweet_count >= NAS_TWEET_CAP:
                            continue
                        if not is_kol:
                            nas_handle_counts.setdefault(handle_lower, 0)
                            nas_handle_counts[handle_lower] += 1
                            if nas_handle_counts[handle_lower] > NAS_PER_HANDLE_CAP:
                                nas_filtered_cap += 1
                                continue

                        nas_tweet_count += 1
                        if is_kol:
                            nas_kol_count += 1

                        content_zh = data.get("content_zh", "") or raw_content
                        content_zh = _sanitize_tweet_content(content_zh)
                        preview = raw_content[:30].replace("\n", " ")
                        title = f"@{handle}: {preview}..."
                        relevance = 7 if is_kol else 5

                        materials.append(
                            {
                                "id": tid,
                                "source": "twitter",
                                "title": title,
                                "content_zh": content_zh,
                                "link": data.get("tweet_url", ""),
                                "source_name": f"Twitter @{handle}",
                                "time": event_time,
                                "tags": ["kol"] if is_kol else [],
                                "relevance": relevance,
                                "content_len": len(raw_content),
                                "tweet_meta": {
                                    "handle": handle,
                                    "event_type": event_type,
                                    "is_kol": is_kol,
                                    "reference_handle": data.get("reference_handle", ""),
                                    "reference_content_zh": "",
                                },
                            }
                        )
            except OSError:
                print(f"  WARNING: Failed to read NAS JSONL: {nas_jsonl}", file=sys.stderr)

    return {
        "materials": materials,
        "count": nas_tweet_count,
        "kol_count": nas_kol_count,
        "filtered_short": nas_filtered_short,
        "filtered_meme": nas_filtered_meme,
        "filtered_cap": nas_filtered_cap,
        "file_count": len(nas_dates),
    }
