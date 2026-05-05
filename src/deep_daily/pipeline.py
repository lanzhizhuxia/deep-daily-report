from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import importlib as _importlib
import markdown as markdown_lib

from deep_daily import config
from deep_daily.collectors.rss import collect_articles
from deep_daily.collectors.twitter import collect_tweets
from deep_daily.collectors.twitter_nas import collect_nas_tweets
from deep_daily.config import (
    ReaderConfig,
    _load_active_systems,
    _load_reader_profile,
    _load_topic_config,
)
from deep_daily.dedup import normalize_title, normalize_url, title_similarity
from deep_daily.protocols import LLMBackend, Publisher
from deep_daily.urls import generate_daily_url


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically via tmp-rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.rename(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Cross-day dedup: reported events index (ISSUE-190)
# ---------------------------------------------------------------------------


def _load_reported_events(path: Path | None = None) -> Dict[str, Any]:
    """Load reported_events.json. Returns empty store on missing/corrupt/schema mismatch."""
    p = path or config.REPORTED_EVENTS_PATH
    if not p.exists():
        return {
            "schema_version": 1,
            "ttl_days": config.REPORTED_EVENTS_TTL_DAYS,
            "events": [],
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            print(
                "  WARNING: reported_events.json schema mismatch, starting fresh",
                file=sys.stderr,
            )
            return {
                "schema_version": 1,
                "ttl_days": config.REPORTED_EVENTS_TTL_DAYS,
                "events": [],
            }
        if not isinstance(raw.get("events"), list):
            raw["events"] = []
        return raw
    except (json.JSONDecodeError, OSError) as err:
        print(
            f"  WARNING: reported_events.json load failed ({err}), starting fresh",
            file=sys.stderr,
        )
        return {
            "schema_version": 1,
            "ttl_days": config.REPORTED_EVENTS_TTL_DAYS,
            "events": [],
        }


def _load_reader_delivered(path: Path) -> set[str]:
    """Load per-reader delivered event keys from JSON file."""
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("delivered"), list):
            return set(raw["delivered"])
        return set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_reader_delivered(path: Path, keys: set[str]) -> None:
    """Save per-reader delivered event keys atomically (no lock needed — single writer)."""
    _atomic_write_json(path, {"delivered": sorted(keys)})


def _prune_expired_events(store: Dict[str, Any], date_str: str) -> Dict[str, Any]:
    """Remove events whose last_reported is older than TTL."""
    try:
        current = datetime.date.fromisoformat(date_str)
    except ValueError:
        return store
    ttl = store.get("ttl_days", config.REPORTED_EVENTS_TTL_DAYS)
    cutoff = (current - datetime.timedelta(days=ttl)).isoformat()
    before = len(store["events"])
    store["events"] = [
        e for e in store["events"] if e.get("last_reported", "") >= cutoff
    ]
    pruned = before - len(store["events"])
    if pruned:
        print(
            f"  Cross-day dedup: pruned {pruned} expired events (TTL={ttl}d)",
            file=sys.stderr,
        )
    return store


def _build_event_key(url: str, title: str) -> str:
    """Build a stable event key: sha256(domain|path_skeleton|sorted_title_tokens)[:12]."""
    parts: List[str] = []
    if url:
        try:
            parsed = urlparse(url)
            parts.append(parsed.netloc.lower())
            # Path skeleton: strip numeric segments for article ID invariance
            path_parts = [
                seg for seg in parsed.path.split("/") if seg and not seg.isdigit()
            ]
            parts.append("/".join(path_parts))
        except Exception:
            parts.append(url)
    if title:
        norm = normalize_title(title)
        tokens = sorted(norm.split())
        parts.append(" ".join(tokens))
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _extract_entities(text: str) -> List[str]:
    """Extract likely entity names from text using regex patterns (no NLP deps)."""
    _STOP_WORDS = frozenset(
        {
            "The",
            "This",
            "That",
            "These",
            "Those",
            "With",
            "From",
            "What",
            "When",
            "Where",
            "How",
            "Why",
            "Which",
            "About",
            "After",
            "Before",
            "Between",
            "Into",
            "Through",
            "During",
            "New",
            "May",
            "Will",
            "Can",
            "Could",
            "Would",
            "Should",
            "Also",
            "Just",
            "More",
            "Most",
            "Some",
            "All",
            "Any",
            "Now",
            "But",
            "Not",
            "Yet",
            "Its",
            "Has",
            "Had",
            "Are",
            "Was",
            "Were",
            "Been",
            "Being",
            "Have",
            "Does",
            "Did",
            "For",
            "And",
            "Our",
            "Their",
            "Your",
            "His",
            "Her",
            "Over",
            "Under",
            "Each",
            "Every",
            "Both",
            "Here",
            "There",
            "While",
            "Since",
            "Until",
            "Still",
            "Very",
            "Much",
            "Many",
            "First",
            "Last",
            "Next",
            "Other",
            "Such",
            "Only",
            "Even",
            "Back",
            "Well",
            "Long",
            "High",
            "Low",
            "Big",
            "Top",
            "Key",
            "Set",
            "Per",
            "Via",
            "Fee",
            "Tax",
            "Vote",
            "Move",
            "Rise",
            "Drop",
            "Push",
            "Pull",
            "Data",
            "Plan",
            "Risk",
            "Fund",
            "Deal",
            "Rule",
            "Swap",
            "Stake",
            "Price",
            "Trade",
            "Launch",
            "Update",
            "Report",
            "Market",
            "Token",
        }
    )
    entities: List[str] = []
    # English proper nouns: capitalized words 3+ chars, not at sentence start
    for m in re.finditer(r"(?<!\. )(?<!\n)\b([A-Z][a-zA-Z]{2,20})\b", text):
        word = m.group(1)
        if word not in _STOP_WORDS:
            entities.append(word)
    # CJK entities: common suffixes for orgs/products/protocols
    for m in re.finditer(
        r"([\u4e00-\u9fff]{2,6}(?:协议|平台|交易所|基金|公司|项目|网络|链|币))", text
    ):
        entities.append(m.group(1))
    return list(dict.fromkeys(entities))  # dedupe preserving order


def _detect_new_signals(
    material: Dict[str, Any], matched_event: Dict[str, Any]
) -> bool:
    """Return True if material brings new information vs the matched event."""
    # New URL not in canonical set
    mat_url = normalize_url(material.get("link", ""))
    if mat_url and mat_url not in set(matched_event.get("canonical_urls", [])):
        return True
    # New entities
    text = f"{material.get('title', '')} {material.get('content_zh', '')}"
    new_entities = _extract_entities(text)
    known = set(matched_event.get("key_entities", []))
    novel = [e for e in new_entities if e not in known]
    if len(novel) >= 2:
        return True
    return False


def _llm_boundary_check(material: Dict[str, Any], matched_event: Dict[str, Any]) -> str:
    """LLM review for gray-zone similarity (0.4-0.7). Returns 'new' or 'ongoing'."""
    mat_title = material.get("title", "")
    mat_snippet = (material.get("content_zh") or "")[:200]
    event_title = matched_event.get("title_fingerprint", "")
    event_entities = ", ".join(matched_event.get("key_entities", [])[:5])

    prompt = f"""判断以下新素材是否与已报道事件是同一事件的后续报道。

已报道事件：
- 标题指纹：{event_title}
- 关键实体：{event_entities}
- 报道次数：{matched_event.get("report_count", 1)}

新素材：
- 标题：{mat_title}
- 摘要：{mat_snippet}

只回答一个词：same（同一事件的后续）或 different（不同事件）"""

    try:
        model = config.get_effective_models().filter
        raw = _call_llm(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.0,
            max_tokens=64,
        )
        answer = raw.strip().lower()
        if "same" in answer:
            return "ongoing"
        return "new"
    except RuntimeError as err:
        if "EffectiveModels has not been resolved" in str(err):
            print(
                "  Cross-day dedup: EffectiveModels unresolved, "
                "skipping gray-zone LLM review and defaulting to new",
                file=sys.stderr,
            )
            return "new"
        print(
            f"  Cross-day dedup: LLM boundary check failed ({err}), defaulting to new",
            file=sys.stderr,
        )
        return "new"
    except Exception as err:
        print(
            f"  Cross-day dedup: LLM boundary check failed ({err}), defaulting to new",
            file=sys.stderr,
        )
        return "new"  # fail-safe: don't suppress


def _tag_material_freshness(
    materials: List[Dict[str, Any]],
    store: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Tag each material as new/ongoing/stale. Returns list with stale items removed."""
    events = store.get("events", [])
    # Build lookup indexes
    url_to_event: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        for u in ev.get("canonical_urls", []):
            url_to_event[normalize_url(u)] = ev

    result: List[Dict[str, Any]] = []
    stats = {"new": 0, "ongoing": 0, "stale": 0}

    for m in materials:
        mat_url = normalize_url(m.get("link", ""))
        mat_title_norm = normalize_title(m.get("title", ""))
        matched_event = None

        # Phase 1: exact URL match
        if mat_url and mat_url in url_to_event:
            matched_event = url_to_event[mat_url]
        else:
            # Phase 2: title similarity match
            best_sim = 0.0
            best_event = None
            for ev in events:
                sim = title_similarity(mat_title_norm, ev.get("title_fingerprint", ""))
                if sim > best_sim:
                    best_sim = sim
                    best_event = ev

            if best_sim >= config.TITLE_SIM_MATCH_THRESHOLD:
                matched_event = best_event
            elif best_sim >= config.TITLE_SIM_GRAY_LOW and best_event is not None:
                # Gray zone: LLM review
                verdict = _llm_boundary_check(m, best_event)
                if verdict == "ongoing":
                    matched_event = best_event

        if matched_event is None:
            m["_freshness"] = "new"
            stats["new"] += 1
            result.append(m)
        elif _detect_new_signals(m, matched_event):
            m["_freshness"] = "ongoing"
            m["_matched_event_key"] = matched_event.get("event_key", "")
            stats["ongoing"] += 1
            result.append(m)
        else:
            m["_freshness"] = "stale"
            stats["stale"] += 1
            # stale: do NOT append to result

    print(
        f"  Cross-day dedup: {stats['new']} new, {stats['ongoing']} ongoing, "
        f"{stats['stale']} stale (dropped)",
        file=sys.stderr,
    )
    return result


def _enforce_ongoing_cap(
    bucket_mats: List[Dict[str, Any]],
    cap: int = config.ONGOING_CAP_PER_TOPIC,
) -> List[Dict[str, Any]]:
    """Keep at most `cap` ongoing materials per topic bucket, prefer higher relevance."""
    ongoing = [m for m in bucket_mats if m.get("_freshness") == "ongoing"]
    others = [m for m in bucket_mats if m.get("_freshness") != "ongoing"]
    if len(ongoing) <= cap:
        return bucket_mats
    # Sort by relevance descending, keep top cap
    ongoing.sort(key=lambda m: m.get("relevance", 0), reverse=True)
    kept = ongoing[:cap]
    dropped = len(ongoing) - cap
    print(f"    ongoing cap: kept {cap}, dropped {dropped}", file=sys.stderr)
    return others + kept


def _update_reported_events(
    store: Dict[str, Any],
    materials: List[Dict[str, Any]],
    date_str: str,
) -> Dict[str, Any]:
    """Update the reported events index with materials that made it into the report."""
    events = store.get("events", [])
    key_map: Dict[str, int] = {e["event_key"]: i for i, e in enumerate(events)}

    for m in materials:
        freshness = m.get("_freshness", "new")
        mat_url = normalize_url(m.get("link", ""))
        mat_title = m.get("title", "")
        text = f"{mat_title} {m.get('content_zh', '')}"
        entities = _extract_entities(text)

        if freshness == "ongoing":
            event_key = m.get("_matched_event_key", "")
            if event_key and event_key in key_map:
                ev = events[key_map[event_key]]
                ev["last_reported"] = date_str
                ev["report_count"] = ev.get("report_count", 1) + 1
                if mat_url and mat_url not in ev.get("canonical_urls", []):
                    ev["canonical_urls"].append(mat_url)
                # Merge new entities
                existing = set(ev.get("key_entities", []))
                for ent in entities:
                    if ent not in existing:
                        ev["key_entities"].append(ent)
                        existing.add(ent)
                continue

        # New event
        event_key = _build_event_key(mat_url, mat_title)
        if event_key in key_map:
            # Key collision with existing event — update it
            ev = events[key_map[event_key]]
            ev["last_reported"] = date_str
            ev["report_count"] = ev.get("report_count", 1) + 1
            if mat_url and mat_url not in ev.get("canonical_urls", []):
                ev["canonical_urls"].append(mat_url)
        else:
            new_ev = {
                "event_key": event_key,
                "first_reported": date_str,
                "last_reported": date_str,
                "canonical_urls": [mat_url] if mat_url else [],
                "title_fingerprint": normalize_title(mat_title),
                "key_entities": entities[:10],
                "report_count": 1,
            }
            events.append(new_ev)
            key_map[event_key] = len(events) - 1

    store["events"] = events
    return store


# ---------------------------------------------------------------------------
# Multi-key LiteLLM routing (ISSUE-190)
# ---------------------------------------------------------------------------

_news_6551: Any = None
_hackernews: Any = None
_llm_backend: LLMBackend | None = None
_publisher: Publisher | None = None


def configure(
    *,
    llm: LLMBackend | None = None,
    publisher: Publisher | None = None,
) -> None:
    global _llm_backend, _publisher
    if llm is not None:
        _llm_backend = llm
    if publisher is not None:
        _publisher = publisher


def _get_llm() -> LLMBackend:
    if _llm_backend is None:
        from deep_daily.backends.openai_compat import OpenAICompatibleBackend

        return OpenAICompatibleBackend(
            api_base=os.environ.get("LLM_API_BASE")
            or os.environ.get("LITELLM_API_BASE", ""),
            api_key=os.environ.get("LLM_API_KEY")
            or os.environ.get("LITELLM_API_KEY", ""),
        )
    return _llm_backend


def _get_publisher() -> Publisher:
    if _publisher is None:
        from deep_daily.publishers.file_publisher import FilePublisher

        return FilePublisher()
    return _publisher


def _call_llm(
    messages: list,
    *,
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
) -> str:
    return _get_llm().chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _call_llm_with_retry(
    messages: list,
    *,
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    retry_temperature: float = 0.5,
) -> str:
    try:
        return _call_llm(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as first_err:
        print(
            f"  LLM call failed ({first_err}), retrying with temp={retry_temperature}...",
            file=sys.stderr,
        )
        return _call_llm(
            messages,
            model=model,
            temperature=retry_temperature,
            max_tokens=max_tokens,
        )


def _repair_json_unescaped_quotes(text: str) -> str:
    """Fix unescaped double-quotes inside JSON string values.

    LLMs often produce JSON like: "topic_angle": "到"AI"背后的真相"
    where the inner " chars break JSON parsing.  We use a line-by-line
    regex approach: for lines matching `"key": "value",` we replace
    unescaped inner quotes with 「」 (CJK corner brackets).
    """
    lines = text.split("\n")
    repaired_lines: List[str] = []
    kv_pattern = re.compile(r'^(\s*"[^"]+"\s*:\s*")(.*)(",?\s*)$')
    for line in lines:
        m = kv_pattern.match(line)
        if m:
            prefix, value, suffix = m.group(1), m.group(2), m.group(3)
            inner_fixed = ""
            quote_count = 0
            i = 0
            while i < len(value):
                if value[i] == '"' and (i == 0 or value[i - 1] != "\\"):
                    inner_fixed += "「" if quote_count % 2 == 0 else "」"
                    quote_count += 1
                else:
                    inner_fixed += value[i]
                i += 1
            repaired_lines.append(prefix + inner_fixed + suffix)
        else:
            repaired_lines.append(line)
    return "\n".join(repaired_lines)


def _parse_json_response(text: str) -> Any:
    """Extract JSON from LLM response, handling ```json``` fences and repairs."""
    cleaned = text.strip()

    def _try_parse(s: str) -> Any:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        repaired = _repair_json_unescaped_quotes(s)
        return json.loads(repaired)

    # Try outer code-fence extraction first (greedy: outermost fences only)
    outer_fence = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", cleaned, re.DOTALL)
    if outer_fence:
        try:
            return _try_parse(outer_fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try direct parse
    try:
        return _try_parse(cleaned)
    except json.JSONDecodeError:
        pass
    # Legacy split-based extraction for edge cases
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts[1:]:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                return _try_parse(candidate)
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("No valid JSON found", cleaned, 0)


def _extract_content_md_regex(text: str) -> Optional[str]:
    """Regex fallback: extract content_md from malformed JSON with unescaped chars."""
    m = re.search(
        r'"content_md"\s*:\s*"(.*?)"\s*,\s*\n\s*"key_quote"',
        text,
        re.DOTALL,
    )
    if m:
        val = m.group(1)
        val = val.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        if len(val) > 50:
            return val
    return None


def step0_classify_by_topic(
    materials: List[Dict[str, Any]],
    topic_config: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Classify materials into topic buckets using keyword matching (no LLM).

    Returns dict keyed by topic slug (plus '_unclassified'), values are lists of materials.
    A material CAN appear in multiple topic buckets, unless a topic has
    exclude_if_in configured (materials already in those topics are excluded).
    """
    # Build keyword lookup: slug -> list of lowercase keywords
    all_topics = topic_config.get("pinned", []) + topic_config.get("dynamic", [])
    topic_keywords: Dict[str, List[str]] = {}
    for t in all_topics:
        topic_keywords[t["slug"]] = [kw.lower() for kw in t.get("keywords", [])]

    buckets: Dict[str, List[Dict[str, Any]]] = {t["slug"]: [] for t in all_topics}
    buckets["_unclassified"] = []
    matched_ids: set = set()

    for m in materials:
        search_text = (m.get("title", "") + " " + m.get("content_zh", "")).lower()
        material_matched = False
        for slug, keywords in topic_keywords.items():
            for kw in keywords:
                if len(kw) <= 5:
                    if re.search(rf"\b{re.escape(kw)}\b", search_text, re.IGNORECASE):
                        buckets[slug].append(m)
                        matched_ids.add(m["id"])
                        material_matched = True
                        break
                elif kw in search_text:
                    buckets[slug].append(m)
                    matched_ids.add(m["id"])
                    material_matched = True
                    break  # one keyword match is enough for this topic
        if not material_matched:
            buckets["_unclassified"].append(m)

    # Phase 2: priority-based exclusion (e.g., crypto excludes materials already in rwa/defi)
    exclude_rules: Dict[str, List[str]] = {}
    for t in all_topics:
        eii = t.get("exclude_if_in", [])
        if eii:
            exclude_rules[t["slug"]] = eii

    if exclude_rules:
        for slug, exclude_slugs in exclude_rules.items():
            # Collect IDs already claimed by higher-priority topics
            claimed_ids: set = set()
            for es in exclude_slugs:
                claimed_ids.update(m["id"] for m in buckets.get(es, []))
            if claimed_ids:
                before = len(buckets[slug])
                buckets[slug] = [m for m in buckets[slug] if m["id"] not in claimed_ids]
                removed = before - len(buckets[slug])
                if removed:
                    print(
                        f"  Step 0: '{slug}' excluded {removed} materials (already in {exclude_slugs})",
                        file=sys.stderr,
                    )

    # Log counts
    for slug, items in buckets.items():
        if slug != "_unclassified" or items:
            print(f"  Step 0: topic '{slug}' → {len(items)} materials", file=sys.stderr)
    if buckets["_unclassified"]:
        print(
            f"  Step 0: _unclassified → {len(buckets['_unclassified'])} materials",
            file=sys.stderr,
        )

    return buckets


def _sanitize_tweet_content(text: str) -> str:
    """Strip zero-width chars, Brave prefix noise, and truncate to 2000 chars."""
    # Remove zero-width characters
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(char, "")
    # Strip "Name · @handle · " prefix (Brave Search artifact)
    text = re.sub(r"^[^·]+·\s*@\w+\s*·\s*", "", text).strip()
    return text[:2000]


def _pipeline_dir(date_str: str, cache_dir: Path | None = None) -> Path:
    """Return the pipeline cache directory for a given date."""
    return (cache_dir or config.PIPELINE_DIR) / date_str


def _load_step_cache(
    date_str: str,
    step: "int | str",
    lane: str = "",
    cache_dir: Path | None = None,
) -> Optional[Any]:
    """Load cached step result if it exists."""
    suffix = f"-{lane}" if lane else ""
    path = _pipeline_dir(date_str, cache_dir) / f"step-{step}{suffix}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_step_cache(
    date_str: str,
    step: "int | str",
    data: Any,
    lane: str = "",
    cache_dir: Path | None = None,
) -> None:
    """Save step result to pipeline cache."""
    suffix = f"-{lane}" if lane else ""
    _atomic_write_json(
        _pipeline_dir(date_str, cache_dir) / f"step-{step}{suffix}.json", data
    )


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
  :root{{--bg:#1a1a1a;--panel:#1e1e1e;--card:#202224;--text:#d7d7d7;--muted:#9aa0a6;--accent:#e6c15a;--link:#7ddc9a;--border:#343434;--border-heavy:#444}}
  *,*::before,*::after{{box-sizing:border-box}}
  body{{margin:0;font-family:"Helvetica Neue","Arial Narrow",Arial,"PingFang SC","Hiragino Sans GB",sans-serif;color:var(--text);background:var(--bg);line-height:1.7}}
  body::after{{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;background:linear-gradient(rgba(255,255,255,.02) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px);background-size:40px 40px}}
  header{{background:#222;border-bottom:2px solid var(--border-heavy);color:var(--text);padding:2rem 1rem;text-align:center;position:relative;z-index:1}}
  header h1{{margin:0 0 .3rem;font-size:1.6rem;font-weight:700;font-family:"JetBrains Mono",monospace;color:var(--accent);text-transform:uppercase;letter-spacing:2px}}
  header .date{{opacity:.7;font-size:.95rem;color:#888}}

  /* --- Layout: sidebar + content --- */
  .layout-wrap{{display:flex;position:relative;z-index:1;min-height:calc(100vh - 80px)}}
  .sidebar{{display:none}}
  .content-area{{flex:1;min-width:0;max-width:960px;margin:0 auto;padding:0 2rem 3rem}}
  .content-rail{{width:100%}}

  /* --- Sidebar + Content (PC >= 1024px) --- */
  @media(min-width:1024px){{
    .layout-wrap{{display:grid;grid-template-columns:clamp(240px,14vw,300px) minmax(0,1fr)}}
    .sidebar{{display:block;position:sticky;top:0;align-self:flex-start;height:100vh;overflow-y:auto;padding:1.5rem 0 2rem;border-right:1px solid var(--border);background:var(--panel);z-index:10;scrollbar-width:thin;scrollbar-color:#444 transparent}}
    .sidebar::-webkit-scrollbar{{width:4px}}
    .sidebar::-webkit-scrollbar-thumb{{background:#444;border-radius:2px}}
    .sidebar .side-label{{display:block;padding:.5rem 1.2rem;font-size:.7rem;font-weight:600;color:#666;text-transform:uppercase;letter-spacing:1px;font-family:"JetBrains Mono",monospace}}
    .sidebar .side-item{{display:block;padding:.55rem 1.2rem .55rem 1.6rem;font-size:.9rem;color:var(--muted);text-decoration:none;cursor:pointer;border-left:3px solid transparent;transition:all .12s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .sidebar .side-item:hover{{color:var(--text);background:rgba(255,255,255,.04)}}
    .sidebar .side-item.active{{color:var(--accent);border-left-color:var(--accent);background:rgba(230,193,90,.08)}}
    .sidebar .side-item.disabled{{color:#555;cursor:default}}
    .sidebar .side-item.disabled:hover{{background:transparent;color:#555}}
    .sidebar .side-sub{{display:block;padding:.3rem 1.2rem .3rem 2.4rem;font-size:.8rem;color:#666;text-decoration:none;cursor:pointer;transition:all .12s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .sidebar .side-sub:hover{{color:#bbb}}
    .sidebar .side-sub.active{{color:var(--link);border-left:2px solid var(--link);background:rgba(125,220,154,.06)}}
    .content-area{{flex:1;min-width:0;max-width:none;margin:0;padding:1rem clamp(24px,3vw,56px) 3rem;overflow-x:hidden}}
    .content-rail{{width:min(1100px,100%);margin:0 auto}}
    /* On PC, show all topic sections (no tab hide/show) */
    .tab-panel{{display:block !important;margin-bottom:3rem;scroll-margin-top:1rem}}
    .tab-panel .topic-section{{scroll-margin-top:1rem}}
    /* Hide mobile tab bar on PC */
    .topic-tabs{{display:none !important}}
  }}

  /* --- Tab bar (mobile/tablet < 1024px) --- */
  .topic-tabs{{display:flex;gap:0;background:#2a2a2a;border:2px solid var(--border-heavy);border-radius:0;margin-bottom:0;overflow-x:auto;-webkit-overflow-scrolling:touch}}
  .topic-tabs button{{flex:1;min-width:0;padding:.8rem .5rem;border:none;background:transparent;font-size:.95rem;font-weight:600;color:#888;cursor:pointer;border-bottom:3px solid transparent;white-space:nowrap;transition:all .15s;font-family:"JetBrains Mono",monospace}}
  .topic-tabs button:hover{{color:var(--text);background:#333}}
  .topic-tabs button.active{{color:var(--accent);border-bottom-color:var(--accent)}}
  .topic-tabs button.disabled{{color:#555;cursor:default}}
  .topic-tabs button.disabled:hover{{background:transparent;color:#555}}
  .tab-panel{{display:none}}
  .tab-panel.active{{display:block}}

  /* --- Content blocks --- */
  .panorama{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1.5rem;margin-bottom:1.5rem}}
  .panorama h2{{margin-top:0;font-size:1.2rem;color:var(--accent);font-family:"JetBrains Mono",monospace}}
  .panorama h3{{font-size:1rem;color:#ccc;margin:1.2rem 0 .5rem;font-weight:600}}
  .panorama p{{margin:.8rem 0;line-height:1.75}}
  .panorama ul,.panorama ol{{padding-left:1.5rem;margin:.5rem 0}}
  .panorama li{{margin:.3rem 0}}
  .panorama strong{{color:var(--accent)}}
  .panorama a{{color:var(--link)}}
  .topic-section{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1.5rem 1.8rem;margin-bottom:1rem}}
  .topic-section > h2{{color:var(--text);font-size:1.15rem;margin-top:0;border-bottom:1px solid var(--border);padding-bottom:.5rem;display:flex;align-items:center;justify-content:space-between;font-family:"JetBrains Mono",monospace}}
  .topic-content h1,.topic-content h2,.topic-content h3{{font-size:1rem;color:#ccc;margin:1.2rem 0 .5rem;font-weight:600}}
  .topic-content p{{margin:.8rem 0;line-height:1.75}}
  .topic-content ul,.topic-content ol{{padding-left:1.5rem;margin:.5rem 0}}
  .topic-content blockquote{{border-left:3px solid var(--accent);margin:1rem 0;padding:.5rem 1rem;color:#ccc;background:#2a2a2a;border-radius:4px}}
  .topic-content pre{{overflow-x:auto;max-width:100%;white-space:pre-wrap;word-break:break-word;background:#2a2a2a;padding:1rem;border-radius:6px;font-size:.85em;border:1px solid var(--border)}}
  .topic-content code{{background:#333;padding:2px 5px;border-radius:3px;font-size:.9em}}
  .topic-content pre code{{background:none;padding:0}}
  .topic-content img{{max-width:100%;height:auto}}
  .topic-content a{{color:var(--link);text-decoration-thickness:1px;text-underline-offset:2px}}
  .topic-content strong{{color:var(--accent)}}
  .copy-btn{{display:inline-flex;align-items:center;gap:.3rem;padding:.25rem .5rem;font-size:.75rem;color:#666;background:transparent;border:1px solid transparent;border-radius:4px;cursor:pointer;transition:all .15s;flex-shrink:0}}
  .copy-btn:hover{{color:var(--text);background:#333;border-color:var(--border)}}
  .copy-btn.copied{{color:var(--link);background:#1a2e1a;border-color:var(--link)}}
  .copy-btn svg{{width:15px;height:15px}}
  .copy-btn .copy-label{{display:none}}
  .copy-btn.copied .copy-label{{display:inline}}
  .citations{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:1.5rem;margin-top:2rem}}
  .citations h2{{margin-top:0;font-size:1.1rem;color:var(--accent);font-family:"JetBrains Mono",monospace}}
  .citations ul{{padding-left:1.2rem}}
  .citations li{{margin:.3rem 0;font-size:.9rem}}
  .citations a{{color:var(--link);text-decoration:none}}
  .citations a:hover{{text-decoration:underline}}
  .empty-panel{{text-align:center;color:#666;padding:3rem 1rem;font-size:1.1rem}}
  a{{color:var(--link)}}

  /* --- Topic divider (PC only, between tab-panels shown simultaneously) --- */
  .tab-panel-title{{display:none}}
  @media(min-width:1024px){{
    .tab-panel + .tab-panel{{border-top:2px solid var(--border);padding-top:2rem}}
    .tab-panel-title{{display:flex;align-items:center;gap:.6rem;margin:0 0 1.2rem;font-size:1.3rem;font-weight:700;color:var(--accent);font-family:"JetBrains Mono",monospace}}
    .tab-panel-title .tp-badge{{font-size:.75rem;padding:.15rem .5rem;border:1px solid #555;color:#aaa;font-weight:400}}
  }}

  @media(max-width:480px){{
    header{{padding:1.2rem .8rem}}
    header h1{{font-size:1.25rem}}
    .content-area{{padding:0 .8rem 2rem}}
    .topic-section,.panorama,.citations{{padding:1rem}}
    .topic-tabs button{{font-size:.82rem;padding:.6rem .3rem}}
  }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="date">{date}</div>
</header>
<div class="layout-wrap">
  {sidebar_html}
  <main class="content-area">
    <div class="content-rail">
    {tab_nav_html}
    {tab_panels_html}
    </div>
  </main>
</div>
<script>
(function(){{
  var IS_PC = window.matchMedia('(min-width:1024px)').matches;

  /* --- Mobile tab switching (unchanged logic) --- */
  if (!IS_PC) {{
    document.querySelectorAll('.topic-tabs button:not(.disabled)').forEach(function(btn){{
      btn.addEventListener('click',function(){{
        document.querySelectorAll('.topic-tabs button').forEach(function(b){{b.classList.remove('active');}});
        document.querySelectorAll('.tab-panel').forEach(function(p){{p.classList.remove('active');}});
        this.classList.add('active');
        var panel=document.getElementById(this.dataset.target);
        if(panel) panel.classList.add('active');
      }});
    }});
  }}

  /* --- PC sidebar scroll spy + click --- */
  if (IS_PC) {{
    var sideItems = document.querySelectorAll('.sidebar .side-item:not(.disabled)');
    var sideSubItems = document.querySelectorAll('.sidebar .side-sub');
    var allPanels = document.querySelectorAll('.tab-panel[id]');
    var allSections = document.querySelectorAll('.tab-panel .topic-section[id]');

    /* Click handler for sidebar items */
    sideItems.forEach(function(item){{
      item.addEventListener('click', function(e){{
        e.preventDefault();
        var target = document.getElementById(this.dataset.target);
        if (target) target.scrollIntoView({{behavior:'smooth', block:'start'}});
      }});
    }});
    sideSubItems.forEach(function(item){{
      item.addEventListener('click', function(e){{
        e.preventDefault();
        var target = document.getElementById(this.dataset.target);
        if (target) target.scrollIntoView({{behavior:'smooth', block:'start'}});
      }});
    }});

    /* Scroll spy: highlight current topic/section in sidebar */
    var ticking = false;
    function updateSpy(){{
      var scrollY = window.scrollY || document.documentElement.scrollTop;
      var viewMid = scrollY + window.innerHeight * 0.3;
      var activePanel = null;
      allPanels.forEach(function(p){{
        if (p.offsetTop <= viewMid) activePanel = p;
      }});
      sideItems.forEach(function(s){{ s.classList.remove('active'); }});
      if (activePanel) {{
        var match = document.querySelector('.sidebar .side-item[data-target="' + activePanel.id + '"]');
        if (match) match.classList.add('active');
      }}
      /* Sub-section spy */
      var activeSec = null;
      allSections.forEach(function(sec){{
        if (sec.offsetTop <= viewMid) activeSec = sec;
      }});
      sideSubItems.forEach(function(s){{ s.classList.remove('active'); }});
      if (activeSec) {{
        var m2 = document.querySelector('.sidebar .side-sub[data-target="' + activeSec.id + '"]');
        if (m2) m2.classList.add('active');
      }}
      ticking = false;
    }}
    window.addEventListener('scroll', function(){{
      if (!ticking) {{ ticking = true; requestAnimationFrame(updateSpy); }}
    }});
    updateSpy();
  }}

  /* --- Copy button --- */
  document.querySelectorAll('.copy-btn').forEach(function(btn){{
    btn.addEventListener('click',function(e){{
      e.preventDefault();
      var sec=this.closest('.topic-section');
      var title=sec.querySelector('h2 > span').textContent;
      var body=sec.querySelector('.topic-content').innerText;
      var text=title+'\\n\\n'+body;
      navigator.clipboard.writeText(text).then(function(){{
        btn.classList.add('copied');
        setTimeout(function(){{btn.classList.remove('copied');}},1500);
      }});
    }});
  }});

  /* --- Respond to window resize (PC <-> mobile switch) --- */
  window.matchMedia('(min-width:1024px)').addEventListener('change', function(){{
    location.reload();
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Step 1: Material collection
# ---------------------------------------------------------------------------


def step1_collect_materials(date_str: str) -> Dict[str, Any]:
    """Collect articles and tweets for the target date.

    Returns dict with keys: materials (list), stats (dict).
    """
    materials: List[Dict[str, Any]] = []

    rss_result = collect_articles(date_str, articles_dir=config.ARTICLES_DIR)
    article_count = rss_result["count"]
    materials.extend(rss_result["materials"])

    tweet_result = collect_tweets(date_str, tweets_dir=config.TWEETS_DIR)
    tweet_count = tweet_result["count"]
    materials.extend(tweet_result["materials"])

    nas_result = collect_nas_tweets(
        date_str,
        tweets_nas_dir=config.TWEETS_NAS_DIR,
        kols_path=config.KOLS_PATH,
        dynamic_kols_path=config.DYNAMIC_KOLS_PATH,
        existing_materials=materials,
    )
    nas_tweet_count = nas_result["count"]
    materials.extend(nas_result["materials"])

    if nas_tweet_count or nas_result["filtered_short"]:
        print(
            f"  NAS JSONL: {nas_tweet_count} tweets ({nas_result['kol_count']} KOL) from {nas_result['file_count']} files"
            f" | filtered: {nas_result['filtered_short']} short, {nas_result['filtered_meme']} meme, {nas_result['filtered_cap']} cap"
        )
    materials.sort(key=lambda m: (-m["relevance"], m.get("time", "")), reverse=False)

    source_counts: Dict[str, int] = {}
    capped_materials: List[Dict[str, Any]] = []
    dropped_by_source: Dict[str, int] = {}
    for m in materials:
        src = m["source_name"]
        source_counts.setdefault(src, 0)
        source_counts[src] += 1
        if source_counts[src] <= config.PER_SOURCE_CAP:
            capped_materials.append(m)
        else:
            dropped_by_source.setdefault(src, 0)
            dropped_by_source[src] += 1

    if dropped_by_source:
        drop_info = ", ".join(f"{s}: -{c}" for s, c in dropped_by_source.items())
        print(f"  Step 1: per-source cap dropped: {drop_info}", file=sys.stderr)
    materials = capped_materials

    news_6551_count = 0
    global _news_6551
    if _news_6551 is None:
        try:
            _news_6551 = _importlib.import_module("deep_daily.collectors.news_6551")
        except ImportError as _ie:
            print(f"  [6551 news] Import failed ({_ie}), skipping", file=sys.stderr)
            _news_6551 = False
    if _news_6551 and _news_6551 is not False:
        try:
            _topic_config = _load_topic_config()
            news_materials = _news_6551.fetch_daily_news(
                date_str,
                _topic_config,
                existing_materials=materials,
                verbose=True,
            )
            news_6551_count = len(news_materials)
            materials.extend(news_materials)
        except Exception as _ne:
            print(
                f"  [6551 news] fetch_daily_news failed ({_ne}), skipping",
                file=sys.stderr,
            )

    hackernews_count = 0
    global _hackernews
    if _hackernews is None:
        try:
            _hackernews = _importlib.import_module("deep_daily.collectors.hackernews")
        except ImportError as _hie:
            print(f"  [hackernews] Import failed ({_hie}), skipping", file=sys.stderr)
            _hackernews = False
    if _hackernews and _hackernews is not False:
        try:
            hn_result = _hackernews.collect_hackernews(
                date_str,
                hackernews_dir=config.HACKERNEWS_DIR,
                verbose=True,
            )
            hackernews_count = hn_result["count"]
            materials.extend(hn_result["materials"])
        except Exception as _hne:
            print(
                f"  [hackernews] collect_hackernews failed ({_hne}), skipping",
                file=sys.stderr,
            )

    stats = {
        "date": date_str,
        "article_count": article_count,
        "tweet_count": tweet_count,
        "news_6551_count": news_6551_count,
        "hackernews_count": hackernews_count,
        "total": len(materials),
    }
    print(
        f"  Step 1: collected {article_count} articles + {tweet_count} tweets"
        f" + {nas_tweet_count} NAS tweets + {news_6551_count} 6551 news"
        f" + {hackernews_count} HN stories = {len(materials)} materials"
    )
    return {"materials": materials, "stats": stats}


def _split_lanes(materials: List[Dict]) -> Dict[str, List[Dict]]:
    """Split materials into 3 lanes: rss, tweet-long, tweet-normal."""
    rss = [m for m in materials if m["source"] != "twitter"]
    tweet_long = [
        m
        for m in materials
        if m["source"] == "twitter"
        and m.get("content_len", 0) > config.TWEET_LONG_THRESHOLD
    ]
    tweet_normal = [
        m
        for m in materials
        if m["source"] == "twitter"
        and m.get("content_len", 0) <= config.TWEET_LONG_THRESHOLD
    ]
    # Sort tweets by relevance desc, then cap
    tweet_normal.sort(key=lambda m: m.get("relevance", 0), reverse=True)
    tweet_long.sort(key=lambda m: m.get("relevance", 0), reverse=True)
    return {
        "rss": rss,  # all RSS, no cap
        "tweet-normal": tweet_normal[: config.LANE_CAP_TWEET_NORMAL],
        "tweet-long": tweet_long[: config.LANE_CAP_TWEET_LONG],
    }


# ---------------------------------------------------------------------------
# Step 1b: Reader-profile relevance filter
# ---------------------------------------------------------------------------


def step1b_filter_by_relevance(
    materials: List[Dict[str, Any]],
    *,
    reader_snippet: str | None = None,
) -> List[Dict[str, Any]]:
    if len(materials) <= 15:
        return materials

    model = config.get_effective_models().filter
    reader_snippet = reader_snippet or _load_reader_profile()

    material_lines: List[str] = []
    for m in materials:
        snippet = (m.get("content_zh") or "")[:100].replace("\n", " ")
        material_lines.append(
            f"{m['id']}|{m['source_name']}|{m['title'][:60]}|{snippet}"
        )

    prompt = f"""读者画像：{reader_snippet}

以下是今日 {len(materials)} 条素材（格式: ID|来源|标题|摘要）：

{chr(10).join(material_lines)}

请为每条素材打 1-5 的相关度分（5=极度相关，1=完全无关）。

输出严格 JSON 格式：
{{"scores": {{"素材ID": 分数, ...}}}}

评分标准：
- 5: 直接涉及读者当前项目或核心兴趣
- 4: 与读者技术栈/行业高度相关
- 3: 有一定参考价值
- 2: 泛泛的行业新闻
- 1: 与读者完全无关
- JSON 字符串值中不要使用双引号，如需引用请用「」"""

    messages = [
        {"role": "system", "content": "你是一个精准的内容过滤器。只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = _call_llm_with_retry(
            messages, model=model, temperature=0.1, max_tokens=4096
        )
        parsed = _parse_json_response(raw)
        scores = parsed.get("scores", {})

        filtered = []
        dropped = 0
        for m in materials:
            score = scores.get(m["id"], 3)
            if not isinstance(score, (int, float)):
                score = 3
            if score >= 3:
                m["profile_relevance"] = int(score)
                filtered.append(m)
            else:
                dropped += 1

        print(
            f"  Step 1b: relevance filter kept {len(filtered)}/{len(materials)} (dropped {dropped})"
        )
        return filtered
    except Exception as err:
        print(
            f"  Step 1b: relevance filter failed ({err}), keeping all", file=sys.stderr
        )
        return materials


# ---------------------------------------------------------------------------
# Lightweight mode (< 3 materials)
# ---------------------------------------------------------------------------


def _generate_lightweight_daily(
    date_str: str,
    materials: List[Dict[str, Any]],
    *,
    output_dir: Path | None = None,
) -> None:
    """Generate a minimal daily summary when < 3 materials are available."""
    print(
        f"  Lightweight mode: only {len(materials)} material(s), skipping deep pipeline"
    )
    out_dir = output_dir or config.DAILIES_DIR

    if not materials:
        summary_md = "今日暂无符合条件的内容。"
        one_liner = "今日暂无更新"
    else:
        lines = [f"今日共 {len(materials)} 条内容：\n"]
        for m in materials:
            lines.append(f"- **{m['title']}** ({m['source_name']})")
            if m.get("content_zh"):
                lines.append(f"  {m['content_zh'][:200]}")
            lines.append("")
        summary_md = "\n".join(lines)
        one_liner = f"今日 {len(materials)} 条简讯"

    # Build minimal HTML
    title = f"深度日报 — {date_str}"
    panorama_html = _md_to_html(summary_md)
    html = HTML_TEMPLATE.format(
        title=title,
        date=date_str,
        sidebar_html="",
        tab_nav_html="",
        tab_panels_html=f'<div class="panorama">{panorama_html}</div>',
    )

    html_path = out_dir / f"{date_str}.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    meta = {
        "date": date_str,
        "one_liner": one_liner,
        "topics": [],
        "material_count": len(materials),
        "lightweight": True,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_write_json(out_dir / f"{date_str}.json", meta)

    # Print Feishu push text
    url = generate_daily_url(date_str)
    push_lines = [
        f"深度日报 — {date_str}",  # noqa: RUF001
        one_liner,
        "",
    ]
    if url:
        push_lines.append(f"查看全文: {url}")
    print("\n".join(push_lines))


# ---------------------------------------------------------------------------
# Step 2: Topic clustering
# ---------------------------------------------------------------------------


def step2_cluster_topics(
    materials: List[Dict[str, Any]],
    date_str: str,
    *,
    max_topics: int = 4,
    topic_label: str = "",
    exclude_hint: str = "",
    reader_snippet: str | None = None,
) -> List[Dict[str, Any]]:
    """Cluster materials into ≤{max_topics} sub-angles within a topic via LLM."""
    model = config.get_effective_models().cluster

    # Build material summaries for prompt
    material_lines: List[str] = []
    valid_ids = {m["id"] for m in materials}
    for m in materials:
        snippet = (m.get("content_zh") or "")[:200].replace("\n", " ")
        material_lines.append(
            f"- ID: {m['id']} | 标题: {m['title']} | 来源: {m['source_name']} | 摘要: {snippet}"
        )

    reader_snippet = reader_snippet or _load_reader_profile()

    # Build topic-scoped prompt (materials already classified by step0)
    topic_context = ""
    if topic_label:
        topic_context = f"本轮素材均属于「{topic_label}」主题。请在此主题范围内找出 ≤{max_topics} 个有价值的子角度。"
    if exclude_hint:
        topic_context += f"\n{exclude_hint}"

    user_prompt = f"""读者画像：{reader_snippet}

你正在分析【{topic_label or "综合"}】主题下的素材。

{topic_context}

以下是 {date_str} 的 {len(materials)} 条素材：

{chr(10).join(material_lines)}

请将这些素材归类为子角度。

输出严格 JSON 格式：
[
  {{
    "topic_title": "子角度标题",
    "topic_angle": "叙事角度简述（1句话）",
    "material_ids": ["id1", "id2"],
    "priority": 1,
    "action_tag": "🔧可动手 (~15min)|🔧可动手 (~1h)|🔧可动手 (~半天)|👀持续关注|⚠️风险信号|📎存档"
  }}
]

要求：
- 每条素材必须属于且仅属于一个子角度
- priority 按对读者的相关度排序（1=最相关），不是按素材数量
- 硬约束：最终输出 ≤{max_topics} 个子角度。每个子角度必须关联至少一个具体系统、工具或待办。若素材无法映射到具体行动或具体系统，合并入最相关子角度的素材列表，不得独立成子角度。
- action_tag 必须从以下选一个：🔧可动手 (~15min) / 🔧可动手 (~1h) / 🔧可动手 (~半天) / 👀持续关注 / ⚠️风险信号 / 📎存档
- 🔧可动手 必须附带预估时间，帮助读者决定「现在做」还是「加入 backlog」
- 只输出 JSON，不要其他文字
- JSON 字符串值中不要使用双引号，如需引用请用「」"""

    messages = [
        {
            "role": "system",
            "content": "你是一位资深科技编辑，擅长从零散信息中提炼叙事主线。",
        },
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = _call_llm_with_retry(
            messages,
            model=model,
            temperature=0.3,
            retry_temperature=0.5,
            max_tokens=4096,
        )
        topics = _parse_json_response(raw)

        # Validate
        if not isinstance(topics, list) or not (1 <= len(topics) <= max_topics):
            raise ValueError(
                f"Expected 1-{max_topics} topics, got {len(topics) if isinstance(topics, list) else type(topics)}"
            )

        # Validate material_ids reference real materials
        for topic in topics:
            topic["material_ids"] = [
                mid for mid in topic.get("material_ids", []) if mid in valid_ids
            ]

        # Check no empty topics
        topics = [t for t in topics if t["material_ids"]]
        if len(topics) < 1:
            raise ValueError("All topics empty after validation")

        print(f"  Step 2: clustered into {len(topics)} topics")
        return topics

    except Exception as err:
        print(
            f"  Step 2: LLM clustering failed ({err}), using category fallback",
            file=sys.stderr,
        )
        return _fallback_cluster_by_source(materials, max_topics=max_topics)


def _fallback_cluster_by_source(
    materials: List[Dict[str, Any]], *, max_topics: int = 4
) -> List[Dict[str, Any]]:
    """Fallback clustering: group articles by category/feed, tweets by handle."""
    groups: Dict[str, List[str]] = {}
    for m in materials:
        if m.get("tweet_meta"):
            key = f"Twitter @{m['tweet_meta']['handle']}"
        else:
            key = m.get("source_name", "Other")
        groups.setdefault(key, []).append(m["id"])

    topics = []
    for i, (name, ids) in enumerate(groups.items(), 1):
        topics.append(
            {
                "topic_title": name,
                "topic_angle": f"来自 {name} 的内容汇总",
                "material_ids": ids,
                "priority": i,
            }
        )

    # Cap at 7 topics by merging smallest groups
    while len(topics) > max_topics:
        topics.sort(key=lambda t: len(t["material_ids"]))
        smallest = topics.pop(0)
        # Keep the larger group's name instead of always renaming to '综合'
        if len(smallest["material_ids"]) >= len(topics[0]["material_ids"]):
            topics[0]["topic_title"] = smallest["topic_title"]
            topics[0]["topic_angle"] = smallest["topic_angle"]
        topics[0]["material_ids"].extend(smallest["material_ids"])

    print(f"  Step 2 (fallback): grouped into {len(topics)} topics by source")
    return topics


# ---------------------------------------------------------------------------
# Step 3: Per-topic deep writing
# ---------------------------------------------------------------------------


def step3_write_topics(
    topics: List[Dict[str, Any]],
    materials: List[Dict[str, Any]],
    *,
    model: str,
    reader_snippet: str | None = None,
    active_systems: str | None = None,
) -> List[Dict[str, Any]]:
    """Write deep analysis for each topic in parallel."""
    material_map = {m["id"]: m for m in materials}
    reader_snippet = reader_snippet or _load_reader_profile()
    active_systems = active_systems or _load_active_systems()

    system_prompt = f"""你是一位深度科技分析师，擅长将碎片信息整合为深度洞察文章。

读者画像：{reader_snippet}

读者的活跃系统：{active_systems}

写作要求：
- 使用中文，语言简洁务实
- 深度分析，不是简单罗列，但避免哲学化
- 引用具体数据和事实
- 每篇 300-600 字
- 使用 Markdown 格式
- 在适当位置标注引用来源
- 文末必须包含一个 actionability 标签行，格式举例：`**标签: 🔧可动手 (~15min)**` 或 `**标签: 🔧可动手 (~1h)**` 或 `**标签: 👀持续关注**` 或 `**标签: ⚠️风险信号**` 或 `**标签: 📎存档**`，加一句话说明理由。🔧可动手 必须附带预估时间。
- 如果内容与读者的活跃系统**直接、具体**相关（如涉及相同技术栈、相同工具名），在文末标签前加一行 `**💡 与你相关**：` 说明具体哪个系统可以怎么用。但如果关联性只是泛泛的行业相关（如「AI 行业从业者所以相关」），则**不要添加此段**。每期日报全部文章中最多保留 3 个「与你相关」段落，优先留给最有操作性的。
- 如果素材标记为【跟进】，只写新增进展，不复述已知背景。用一句话概括「此前已报道：XXX」即可。"""

    def _write_single_topic(topic: Dict[str, Any]) -> Dict[str, Any]:
        topic_materials = [
            material_map[mid] for mid in topic["material_ids"] if mid in material_map
        ]
        if not topic_materials:
            return {
                "topic_title": topic["topic_title"],
                "content_md": f"*{topic['topic_title']}：暂无详细内容*",
                "key_quote": "",
                "citations": [],
            }

        material_text_parts: List[str] = []
        citations: List[Dict[str, str]] = []
        for m in topic_materials:
            prefix = "【跟进】" if m.get("_freshness") == "ongoing" else ""
            material_text_parts.append(
                f"### {prefix}{m['title']}\n来源: {m['source_name']}\n链接: {m['link']}\n\n{m.get('content_zh', '')}"
            )
            if m.get("link"):
                citations.append(
                    {"title": _citation_label(m.get("title", "")), "url": m["link"]}
                )

        user_prompt = f"""主题：{topic["topic_title"]}
叙事角度：{topic.get("topic_angle", "")}
建议标签：{topic.get("action_tag", "")}

以下是相关素材：

{chr(10).join(material_text_parts)}

请围绕主题写一篇深度分析，并提取一句最有力的引用（key_quote）。

输出严格 JSON 格式：
{{
  "content_md": "Markdown 格式的深度分析文章",
  "key_quote": "最有力的一句引用"
}}

注意：JSON 字符串值中不要使用双引号，如需引用请用「」"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        raw = ""
        try:
            raw = _call_llm_with_retry(
                messages,
                model=model,
                temperature=0.5,
                retry_temperature=0.6,
                max_tokens=16384,
            )
            parsed = _parse_json_response(raw)
            return {
                "topic_title": topic["topic_title"],
                "content_md": parsed.get("content_md", ""),
                "key_quote": parsed.get("key_quote", ""),
                "citations": citations,
            }
        except (json.JSONDecodeError, ValueError):
            # LLM returned content but _parse_json_response failed.
            # Try extracting content_md from code-fenced JSON wrapper
            if raw:
                stripped = raw.strip()
                fence_match = re.search(
                    r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL
                )
                if fence_match:
                    try:
                        inner = json.loads(fence_match.group(1).strip())
                        if isinstance(inner, dict) and "content_md" in inner:
                            return {
                                "topic_title": topic["topic_title"],
                                "content_md": inner["content_md"],
                                "key_quote": inner.get("key_quote", ""),
                                "citations": citations,
                            }
                    except (json.JSONDecodeError, ValueError):
                        pass
                # Strip code fences and try to recover
                clean = re.sub(r"^```(?:json|markdown)?\s*\n?", "", stripped)
                clean = re.sub(r"\n?```\s*$", "", clean)
                # Last-resort: try parsing the clean text as JSON to extract content_md
                if clean.lstrip().startswith("{"):
                    try:
                        obj = json.loads(clean)
                        if isinstance(obj, dict) and "content_md" in obj:
                            return {
                                "topic_title": topic["topic_title"],
                                "content_md": obj["content_md"],
                                "key_quote": obj.get("key_quote", ""),
                                "citations": citations,
                            }
                    except (json.JSONDecodeError, ValueError):
                        pass
                    # json.loads failed (unescaped chars in markdown) — regex extraction
                    extracted = _extract_content_md_regex(clean)
                    if extracted:
                        return {
                            "topic_title": topic["topic_title"],
                            "content_md": extracted,
                            "key_quote": "",
                            "citations": citations,
                        }
                if len(clean) > 50:
                    return {
                        "topic_title": topic["topic_title"],
                        "content_md": clean,
                        "key_quote": "",
                        "citations": citations,
                    }
            raise
        except Exception as err:
            print(
                f"    Topic '{topic['topic_title']}' failed ({err}), degrading to bullet points",
                file=sys.stderr,
            )
            # Degrade to bullet-point summary
            bullet_lines = [f"## {topic['topic_title']}\n"]
            for m in topic_materials:
                bullet_lines.append(f"- **{m['title']}** ({m['source_name']})")
                snippet = (m.get("content_zh") or "")[:150]
                if snippet:
                    bullet_lines.append(f"  {snippet}")
            return {
                "topic_title": topic["topic_title"],
                "content_md": "\n".join(bullet_lines),
                "key_quote": "",
                "citations": citations,
            }

    # Parallel execution
    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(_write_single_topic, t): i for i, t in enumerate(topics)
        }
        result_slots: Dict[int, Dict[str, Any]] = {}
        for future in concurrent.futures.as_completed(future_map):
            idx = future_map[future]
            try:
                result_slots[idx] = future.result()
            except Exception as exc:
                print(f"    Topic #{idx} raised {exc}", file=sys.stderr)
                result_slots[idx] = {
                    "topic_title": topics[idx]["topic_title"],
                    "content_md": f"*生成失败: {exc}*",
                    "key_quote": "",
                    "citations": [],
                }
        # Maintain original order
        for i in range(len(topics)):
            results.append(result_slots[i])

    print(f"  Step 3: wrote {len(results)} topic analyses")
    return results


# ---------------------------------------------------------------------------
# Step 4: Panorama + assembly
# ---------------------------------------------------------------------------


def step4_assemble_tabbed(
    date_str: str,
    topic_data: Dict[str, Dict[str, Any]],
    topic_order: List[Dict[str, Any]],
    *,
    output_dir: Path | None = None,
    reader_snippet: str | None = None,
    active_systems: str | None = None,
) -> Dict[str, Any]:
    """Assemble tab-based HTML with per-topic panels."""
    panorama_model = config.get_effective_models().appendix
    copy_icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
    lane_labels = {
        "rss": "\U0001f4f0 \u6df1\u5ea6\u6587\u7ae0",
        "tweet-long": "\U0001f9f5 \u63a8\u7279\u6df1\u5ea6",
        "tweet-normal": "\U0001f426 \u63a8\u7279\u901f\u89c8",
    }
    out_dir = output_dir or config.DAILIES_DIR

    # --- Per-topic panorama generation ---
    reader_snippet = reader_snippet or _load_reader_profile()
    active_systems = active_systems or _load_active_systems()
    all_topic_one_liners: List[str] = []

    for slug, tdata in topic_data.items():
        if not tdata.get("lane_results"):
            tdata["panorama_md"] = ""
            tdata["one_liner"] = ""
            continue
        # Collect all topic results across lanes for this topic
        topic_results = []
        for ln in ("rss", "tweet-long", "tweet-normal"):
            topic_results.extend(tdata.get("lane_results", {}).get(ln, []))
        if not topic_results:
            tdata["panorama_md"] = ""
            tdata["one_liner"] = ""
            continue
        summary_lines = [f"- {tr['topic_title']}" for tr in topic_results]
        prompt = (
            f"\u8bfb\u8005\u753b\u50cf\uff1a{reader_snippet}\n"
            f"\u8bfb\u8005\u6d3b\u8dc3\u7cfb\u7edf\uff1a{active_systems}\n\n"
            f"\u4ee5\u4e0b\u662f\u300c{tdata['label']}\u300d\u4e3b\u9898\u4e0b\u7684 {len(topic_results)} \u4e2a\u5b50\u89d2\u5ea6\uff1a\n"
            + chr(10).join(summary_lines)
            + "\n\n"
            "\u8bf7\u751f\u6210\uff1a1) panorama_md: 100-200\u5b57\u4e3b\u9898\u6982\u89c8(Markdown) 2) one_liner: \u4e00\u53e5\u8bdd\u6458\u8981(\u226430\u5b57)\n"
            '\u8f93\u51fa\u4e25\u683c JSON: {"panorama_md": "...", "one_liner": "..."}\n'
            "JSON \u5b57\u7b26\u4e32\u503c\u4e2d\u4e0d\u8981\u4f7f\u7528\u53cc\u5f15\u53f7\uff0c\u5982\u9700\u5f15\u7528\u8bf7\u7528\u300c\u300d"
        )
        try:
            raw = _call_llm(
                [{"role": "user", "content": prompt}],
                model=panorama_model,
                temperature=0.3,
                max_tokens=1024,
            )
            parsed = _parse_json_response(raw)
            tdata["panorama_md"] = parsed.get("panorama_md", "")
            tdata["one_liner"] = parsed.get("one_liner", "")[:30]
        except Exception as err:
            print(f"  Step 4: panorama for '{slug}' failed ({err})", file=sys.stderr)
            tdata["panorama_md"] = "\u3001".join(
                tr["topic_title"] for tr in topic_results
            )
            tdata["one_liner"] = (
                f"{tdata['label']}\uff1a{len(topic_results)}\u4e2a\u5b50\u89d2\u5ea6"[
                    :30
                ]
            )
        all_topic_one_liners.append(f"{tdata['label']}: {tdata['one_liner']}")

    # --- Global one_liner ---
    if len(all_topic_one_liners) > 1:
        try:
            combine_prompt = (
                "\u8bf7\u5c06\u4ee5\u4e0b\u5404\u4e3b\u9898\u4e00\u53e5\u8bdd\u6458\u8981\u5408\u5e76\u4e3a\u4e00\u4e2a\u5168\u5c40\u4e00\u53e5\u8bdd\u6458\u8981(\u226440\u5b57):\n"
                + chr(10).join(all_topic_one_liners)
                + "\n"
                "\u8f93\u51fa\u7eaf\u6587\u672c\uff0c\u4e0d\u8981JSON\u3002"
            )
            global_one_liner = _call_llm(
                [{"role": "user", "content": combine_prompt}],
                model=panorama_model,
                temperature=0.2,
                max_tokens=256,
            ).strip()[:40]
        except Exception:
            global_one_liner = (
                all_topic_one_liners[0][:40]
                if all_topic_one_liners
                else "\u4eca\u65e5\u65e5\u62a5"
            )
    elif all_topic_one_liners:
        global_one_liner = all_topic_one_liners[0][:40]
    else:
        global_one_liner = "\u4eca\u65e5\u65e5\u62a5"

    # --- Build tab nav + sidebar ---
    first_active_slug = None
    tab_buttons: List[str] = []
    sidebar_items: List[str] = []
    sidebar_items.append('<span class="side-label">主题导航</span>')
    # Track section IDs for sidebar sub-items (built during panel generation)
    sidebar_sub_items: Dict[str, List[str]] = {}  # slug -> list of sub-item HTML
    for t in topic_order:
        slug = t["slug"]
        label = t["label"]
        tdata = topic_data.get(slug, {})
        has_content = bool(tdata.get("lane_results"))
        if has_content and first_active_slug is None:
            first_active_slug = slug
        cls = (
            "active"
            if slug == first_active_slug
            else ("disabled" if not has_content else "")
        )
        tab_buttons.append(
            f'<button class="{cls}" data-target="tab-{slug}">{_escape_html(label)}</button>'
        )
        sidebar_items.append(
            f'<a class="side-item {cls}" data-target="tab-{slug}">{_escape_html(label)}</a>'
        )
        sidebar_sub_items[slug] = []
    tab_nav_html = '<nav class="topic-tabs">' + "".join(tab_buttons) + "</nav>"

    # --- Build tab panels ---
    tab_panels: List[str] = []
    for t in topic_order:
        slug = t["slug"]
        tdata = topic_data.get(slug, {})
        has_content = bool(tdata.get("lane_results"))
        active_cls = " active" if slug == first_active_slug else ""

        if not has_content:
            tab_panels.append(
                f'<div class="tab-panel{active_cls}" id="tab-{slug}">'
                f'<div class="empty-panel">\u4eca\u65e5\u65e0\u300c{_escape_html(t["label"])}\u300d\u76f8\u5173\u5185\u5bb9</div>'
                f"</div>"
            )
            continue

        parts: List[str] = []
        lane_results = tdata.get("lane_results", {})
        panorama_html = _md_to_html(tdata.get("panorama_md", ""))
        topic_one_liner = tdata.get("one_liner", "")

        # Topic title (visible on PC only as section header)
        sub_count = sum(
            len(lane_results.get(ln, []))
            for ln in ("rss", "tweet-long", "tweet-normal")
        )
        parts.append(
            f'<div class="tab-panel-title">{_escape_html(t["label"])}'
            f'<span class="tp-badge">{sub_count} \u5b50\u89d2\u5ea6</span></div>'
        )

        # Panorama
        parts.append(
            f'<div class="panorama">'
            f"<h2>\u6982\u89c8</h2>"
            f"<div>{panorama_html}</div>"
            f"</div>"
        )

        # Lane sections
        section_counter = 0
        for lane_name in ("rss", "tweet-long", "tweet-normal"):
            lane_topics = lane_results.get(lane_name, [])
            if not lane_topics:
                continue
            label = lane_labels.get(lane_name, lane_name)
            parts.append(
                f'<div class="lane-section"><h2 class="lane-header">{_escape_html(label)}</h2>'
            )
            for tr in lane_topics:
                content_html = _md_to_html(tr["content_md"])
                sec_id = f"sec-{slug}-{section_counter}"
                section_counter += 1
                parts.append(
                    f'<div class="topic-section" id="{sec_id}">'
                    f"<h2><span>{_escape_html(tr['topic_title'])}</span>"
                    f'<button class="copy-btn" title="\u590d\u5236\u672c\u8282\u5185\u5bb9">{copy_icon}<span class="copy-label">\u5df2\u590d\u5236</span></button></h2>'
                    f'<div class="topic-content">{content_html}</div>'
                    f"</div>"
                )
                # Collect sidebar sub-item for this section
                sidebar_sub_items[slug].append(
                    f'<a class="side-sub" data-target="{sec_id}">{_escape_html(tr["topic_title"][:20])}</a>'
                )
            parts.append("</div>")

        # Per-topic appendix
        appendix_mats = tdata.get("appendix_materials", [])
        if appendix_mats:
            APPENDIX_CAP = 50
            appendix_sorted = sorted(
                appendix_mats, key=lambda m: (-m.get("relevance", 0), m.get("time", ""))
            )[:APPENDIX_CAP]
            total_overflow = len(appendix_mats)
            from collections import OrderedDict

            handle_groups: Dict[str, List[Dict]] = OrderedDict()
            for m in appendix_sorted:
                handle = (m.get("tweet_meta", {}) or {}).get("handle", "") or m.get(
                    "source_name", "Other"
                )
                handle_groups.setdefault(handle, []).append(m)
            # LLM summarize per handle
            handle_summaries: Dict[str, str] = {}
            try:
                summary_model = config.get_effective_models().appendix
                handle_lines: List[str] = []
                for handle, tweets in handle_groups.items():
                    tweet_texts = [
                        ((m.get("content_zh") or m.get("title", ""))[:150]).replace(
                            "\n", " "
                        )
                        for m in tweets
                    ]
                    handle_lines.append(
                        f"@{handle} ({len(tweets)}\u6761): " + " | ".join(tweet_texts)
                    )
                summary_prompt = (
                    "\u4ee5\u4e0b\u662f\u591a\u4e2a\u63a8\u7279\u8d26\u53f7\u4eca\u65e5\u53d1\u5e03\u7684\u63a8\u6587\u6458\u8981\u3002\n"
                    "\u8bf7\u4e3a\u6bcf\u4e2a\u8d26\u53f7\u751f\u6210\u4e00\u53e5\u8bdd\u4e2d\u6587\u89c2\u70b9\u603b\u7ed3\uff0815-30\u5b57\uff09\u3002\n\n"
                    + chr(10).join(handle_lines)
                    + "\n\n"
                    '\u8f93\u51fa\u4e25\u683c JSON: {"\u8d26\u53f7handle": "\u4e00\u53e5\u8bdd\u603b\u7ed3", ...}\n'
                    "\u53ea\u8f93\u51fa JSON\u3002"
                )
                raw = _call_llm(
                    [{"role": "user", "content": summary_prompt}],
                    model=summary_model,
                    temperature=0.2,
                    max_tokens=2048,
                )
                handle_summaries = _parse_json_response(raw)
                if not isinstance(handle_summaries, dict):
                    handle_summaries = {}
            except Exception:
                pass
            group_parts: List[str] = []
            for handle, tweets in handle_groups.items():
                items_html = []
                for m in tweets:
                    m_title = _escape_html(m.get("title", "")[:80])
                    m_link = _escape_html(m.get("link", ""))
                    if m_link:
                        items_html.append(
                            f'<li><a href="{m_link}" target="_blank">{m_title}</a></li>'
                        )
                    else:
                        items_html.append(f"<li>{m_title}</li>")
                handle_esc = _escape_html(handle)
                summary_line = handle_summaries.get(handle, "") or handle_summaries.get(
                    f"@{handle}", ""
                )
                summary_html = (
                    f'<br><span style="color:#666;font-size:0.9em">{_escape_html(summary_line)}</span>'
                    if summary_line
                    else ""
                )
                group_parts.append(
                    f'<div class="handle-group" style="margin-bottom:0.8em">'
                    f"<strong>@{handle_esc} ({len(tweets)})</strong>{summary_html}"
                    f'<ul style="margin:0.2em 0">{chr(10).join(items_html)}</ul>'
                    f"</div>"
                )
            summary_text = f"\U0001f4cb \u66f4\u591a\u63a8\u6587\uff08\u5c55\u793a{len(appendix_sorted)}/{total_overflow}\u6761\uff0c{len(handle_groups)}\u4e2a\u8d26\u53f7\uff09"
            parts.append(
                f'<details class="topic-section" style="margin-top:1.5em">'
                f'<summary style="cursor:pointer;font-size:1.1em;font-weight:bold;padding:0.5em 0">{summary_text}</summary>'
                f'<div class="topic-content" style="font-size:0.85em;line-height:1.5">{chr(10).join(group_parts)}</div>'
                f"</details>"
            )

        # Per-topic citations
        citations: List[Dict[str, str]] = []
        seen_urls: set = set()
        for ln in ("rss", "tweet-long", "tweet-normal"):
            for tr in lane_results.get(ln, []):
                for cite in tr.get("citations", []):
                    url = cite.get("url", "")
                    if url and url not in seen_urls:
                        citations.append(cite)
                        seen_urls.add(url)
        if citations:
            cite_items = [
                f'<li><a href="{_escape_html(c["url"])}" target="_blank">{_escape_html(_citation_label(c.get("title", "")))}</a></li>'
                for c in citations
            ]
            parts.append(
                f'<div class="citations"><h2>\u5f15\u7528\u6765\u6e90</h2><ul>{chr(10).join(cite_items)}</ul></div>'
            )

        tab_panels.append(
            f'<div class="tab-panel{active_cls}" id="tab-{slug}">{chr(10).join(parts)}</div>'
        )

    tab_panels_html = chr(10).join(tab_panels)
    title = f"\u6df1\u5ea6\u65e5\u62a5 \u2014 {date_str}"

    # --- Build sidebar HTML (PC only, rendered but hidden via CSS on mobile) ---
    sidebar_parts: List[str] = list(sidebar_items)  # topic nav items
    # Inject sub-items after each topic's side-item
    final_sidebar: List[str] = []
    for si in sidebar_parts:
        final_sidebar.append(si)
        # Check if this is a side-item with data-target and append its sub-items
        for t in topic_order:
            slug = t["slug"]
            marker = f'data-target="tab-{slug}"'
            if marker in si and sidebar_sub_items.get(slug):
                final_sidebar.extend(sidebar_sub_items[slug])
    sidebar_html = '<aside class="sidebar">' + chr(10).join(final_sidebar) + "</aside>"

    html = HTML_TEMPLATE.format(
        title=title,
        date=date_str,
        sidebar_html=sidebar_html,
        tab_nav_html=tab_nav_html,
        tab_panels_html=tab_panels_html,
    )

    # Save HTML
    html_path = out_dir / f"{date_str}.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    # Save metadata JSON
    all_topic_results = []
    for tdata in topic_data.values():
        for ln in ("rss", "tweet-long", "tweet-normal"):
            all_topic_results.extend(tdata.get("lane_results", {}).get(ln, []))
    meta = {
        "date": date_str,
        "one_liner": global_one_liner,
        "topics": [
            {
                "slug": t["slug"],
                "label": t["label"],
                "one_liner": topic_data.get(t["slug"], {}).get("one_liner", ""),
            }
            for t in topic_order
        ],
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_write_json(out_dir / f"{date_str}.json", meta)

    print(f"  Step 4: assembled tabbed HTML ({len(html)} bytes) + metadata")
    return {"one_liner": global_one_liner, "topic_data": topic_data}


def _escape_html(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_CITATION_LABEL_MAX = 120


def _citation_label(raw: str, max_chars: int = _CITATION_LABEL_MAX) -> str:
    """Normalize a citation display label: collapse whitespace, truncate with ellipsis.

    Upstream materials (especially tweets) may carry full body text in the `title` field.
    Rendering that raw into HTML produces 2000+ char citation entries. This helper keeps
    citations scannable (default 120 chars, ellipsis on overflow).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"


def _md_to_html(md_text: str) -> str:
    """Convert markdown to HTML, inserting blank lines before list blocks
    that LLMs typically omit (required by the markdown spec)."""
    import re

    # Ensure blank line before unordered list items (- / *)
    fixed = re.sub(
        r"(\n)(?!\n)([ \t]*[-*][ \t])",
        r"\n\n\2",
        "\n" + md_text,
    )
    # Ensure blank line before ordered list items (1. / 2. / ...)
    fixed = re.sub(
        r"(\n)(?!\n)([ \t]*\d+\.[ \t])",
        r"\n\n\2",
        fixed,
    )
    fixed = fixed.lstrip("\n")

    raw_html = markdown_lib.markdown(fixed, extensions=["extra", "sane_lists"])
    return raw_html.replace("<a ", '<a target="_blank" rel="noopener" ')


def step5_publish(
    date_str: str,
    one_liner: str,
    topic_titles: List[str],
    panorama_md: str = "",
    *,
    reader_config: ReaderConfig | None = None,
) -> None:
    """Verify output, print Feishu push text, and clean up old files."""
    out_dir = reader_config.output_dir if reader_config else config.DAILIES_DIR
    html_path = out_dir / f"{date_str}.html"
    if not html_path.exists() or html_path.stat().st_size == 0:
        raise RuntimeError(f"HTML output missing or empty: {html_path}")

    url = generate_daily_url(date_str)

    topic_lines = "\n".join(f"- {t}" for t in topic_titles)
    push_text = f"深度日报 — {date_str}\n{one_liner}\n\n主题:\n{topic_lines}\n"
    if url:
        push_text += f"\n查看全文: {url}\n"

    print(push_text)

    notify = reader_config.notification if reader_config else {}
    _get_publisher().publish(
        date_str=date_str,
        html_path=str(html_path),
        one_liner=one_liner,
        topic_titles=topic_titles,
        panorama_md=panorama_md,
        full_url=url,
        topic_id=notify.get("topic_id", "work.rss.daily-report"),
        event_id=notify.get("event_id", "rss_daily_report_ready"),
        dedupe_prefix=notify.get("dedupe_prefix", "rss_daily_report"),
    )

    _cleanup_old_pipeline_files()
    print(f"  Step 5: published. HTML at {html_path}")


def _cleanup_old_pipeline_files() -> None:
    """Delete .pipeline/ and dailies/ files older than 14 days."""
    cutoff = time.time() - config.CLEANUP_MAX_AGE_DAYS * 86400

    # Clean pipeline dirs
    if config.PIPELINE_DIR.exists():
        for date_dir in config.PIPELINE_DIR.iterdir():
            if not date_dir.is_dir():
                continue
            try:
                if date_dir.stat().st_mtime < cutoff:
                    for f in date_dir.iterdir():
                        f.unlink(missing_ok=True)
                    date_dir.rmdir()
            except OSError:
                pass

    # Clean old dailies (html + json)
    if config.DAILIES_DIR.exists():
        for fpath in config.DAILIES_DIR.iterdir():
            if fpath.name.startswith("."):
                continue
            if not fpath.is_file():
                continue
            try:
                if fpath.stat().st_mtime < cutoff:
                    fpath.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Concurrent-safe reported_events update (AC-201-14e)
# ---------------------------------------------------------------------------


def _locked_update_reported_events(path: Path, updater: Callable) -> None:
    """Acquire fcntl lock, read, apply updater, atomic write, release lock.

    The *updater* callable receives the current store dict and must return the
    updated store dict.  The lock file is ``{path}.lock``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        store = _load_reported_events(path)
        store = updater(store)
        _atomic_write_json(path, store)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def collect_shared(
    date_str: str,
    *,
    force: bool = False,
    resume: bool = False,
) -> dict:
    """Step 1 + cross-day freshness tagging.  Returns shared materials + event_store.

    Material collection and global freshness tagging — performed identically
    across all instances regardless of reader profile.
    """
    # --- Step 1: Material collection ---
    step1_data = None
    if resume:
        step1_data = _load_step_cache(date_str, 1)
        if step1_data:
            print("  Step 1: loaded from cache")
    if step1_data is None:
        step1_data = step1_collect_materials(date_str)
        _save_step_cache(date_str, 1, step1_data)

    materials = step1_data["materials"]

    # --- ISSUE-190: Cross-day freshness tagging ---
    event_store = _load_reported_events()
    event_store = _prune_expired_events(event_store, date_str)
    pre_dedup_count = len(materials)
    if event_store.get("events"):
        materials = _tag_material_freshness(materials, event_store)
        print(
            f"  Cross-day dedup: {pre_dedup_count} -> {len(materials)} materials",
            file=sys.stderr,
        )
    else:
        for m in materials:
            m["_freshness"] = "new"
        print("  Cross-day dedup: cold start, all new", file=sys.stderr)

    return {"materials": materials, "event_store": event_store}


def generate_for_reader(
    shared: dict,
    reader: ReaderConfig,
    date_str: str,
    *,
    write_model: str,
    force: bool = False,
    resume: bool = False,
    dry_run: bool = False,
) -> dict:
    """Steps 0-5 for a single reader.  Returns {success: bool, ...}."""
    rid = reader.reader_id
    cache_dir = reader.cache_dir
    out_dir = reader.output_dir

    print(f"\n{'=' * 60}")
    print(f"  Reader: {rid}")
    print(f"{'=' * 60}")

    # AC-14c: deepcopy shared materials to prevent cross-reader mutation
    materials = copy.deepcopy(shared["materials"])
    event_store = shared["event_store"]

    html_path = out_dir / f"{date_str}.html"

    # Force mode: clear reader's pipeline cache
    if force:
        pipe_dir = _pipeline_dir(date_str, cache_dir)
        if pipe_dir.exists():
            for f in pipe_dir.iterdir():
                f.unlink(missing_ok=True)
        html_path.unlink(missing_ok=True)
        json_path = out_dir / f"{date_str}.json"
        json_path.unlink(missing_ok=True)

    # --- Step 0: Classify materials into topic buckets (per-reader topic_config) ---
    topic_config = reader.topic_config
    all_topics = topic_config["pinned"] + topic_config["dynamic"]

    step0_data = None
    if resume:
        step0_data = _load_step_cache(date_str, 0, cache_dir=cache_dir)
        if step0_data:
            print("  Step 0: loaded from cache")
    if step0_data is None:
        buckets = step0_classify_by_topic(materials, topic_config)
        step0_data = {
            "buckets": {slug: [m["id"] for m in mats] for slug, mats in buckets.items()}
        }
        _save_step_cache(date_str, 0, step0_data, cache_dir=cache_dir)
    else:
        material_map = {m["id"]: m for m in materials}
        buckets = {}
        for slug, ids in step0_data.get("buckets", {}).items():
            buckets[slug] = [material_map[mid] for mid in ids if mid in material_map]

    # --- Per-topic pipeline ---
    topic_data: Dict[str, Dict[str, Any]] = {}
    total_lane_materials = 0
    published_ids: set = set()

    for t in all_topics:
        slug = t["slug"]
        label = t["label"]
        bucket_mats = buckets.get(slug, [])
        bucket_mats = _enforce_ongoing_cap(bucket_mats)
        eii = t.get("exclude_if_in", [])
        if eii:
            eii_labels = [tt["label"] for tt in all_topics if tt["slug"] in eii]
            exclude_hint = (
                "注意："
                + "、".join(eii_labels)
                + " 相关素材已在独立主题中分析，本主题应避免生成与这些主题重叠的子角度。"
            )
        else:
            exclude_hint = ""
        topic_data[slug] = {
            "label": label,
            "lane_results": {},
            "appendix_materials": [],
        }

        if not bucket_mats:
            print(f"\n  --- Topic: {label} (0 materials) --- skipped")
            continue

        print(f"\n  === Topic: {label} ({len(bucket_mats)} materials) ===")

        lanes = _split_lanes(bucket_mats)
        lane_tweet_ids: set = set()
        for lane_mats in lanes.values():
            for m in lane_mats:
                if m["source"] == "twitter":
                    lane_tweet_ids.add(m["id"])
        topic_appendix = [
            m
            for m in bucket_mats
            if m["source"] == "twitter" and m["id"] not in lane_tweet_ids
        ]
        topic_data[slug]["appendix_materials"] = topic_appendix

        lane_results: Dict[str, List[Dict[str, Any]]] = {}

        for lane_name in ("rss", "tweet-long", "tweet-normal"):
            lane_mats = lanes.get(lane_name, [])
            if not lane_mats:
                continue
            lane_config = config.LANE_CONFIGS[lane_name]
            cache_lane = f"{slug}-{lane_name}"
            print(f"  --- {lane_config['label']} ({len(lane_mats)} materials) ---")

            skip_1b = lane_config["skip_1b"]
            if callable(skip_1b):
                skip_1b = skip_1b(lane_mats)
            if not skip_1b:
                step1b_data = None
                if resume:
                    step1b_data = _load_step_cache(
                        date_str, "1b", lane=cache_lane, cache_dir=cache_dir
                    )
                    if step1b_data:
                        print(f"  Step 1b [{cache_lane}]: loaded from cache")
                if step1b_data is None:
                    lane_mats = step1b_filter_by_relevance(
                        lane_mats, reader_snippet=reader.profile_snippet
                    )
                    _save_step_cache(
                        date_str,
                        "1b",
                        {"materials": [m["id"] for m in lane_mats]},
                        lane=cache_lane,
                        cache_dir=cache_dir,
                    )
                else:
                    cached_ids = set(step1b_data.get("materials", []))
                    lane_mats = (
                        [m for m in lane_mats if m["id"] in cached_ids]
                        if cached_ids
                        else lane_mats
                    )
            else:
                print(f"  Step 1b [{cache_lane}]: skipped")

            total_lane_materials += len(lane_mats)

            step2_data = None
            if resume:
                step2_data = _load_step_cache(
                    date_str, 2, lane=cache_lane, cache_dir=cache_dir
                )
                if step2_data:
                    print(f"  Step 2 [{cache_lane}]: loaded from cache")
            if step2_data is None:
                dynamic_max = min(
                    lane_config["max_topics"], max(1, len(lane_mats) // 5)
                )
                topics = step2_cluster_topics(
                    lane_mats,
                    date_str,
                    max_topics=dynamic_max,
                    topic_label=label,
                    exclude_hint=exclude_hint,
                    reader_snippet=reader.profile_snippet,
                )
                step2_data = {"topics": topics}
                _save_step_cache(
                    date_str, 2, step2_data, lane=cache_lane, cache_dir=cache_dir
                )
            topics = step2_data["topics"]

            for tp in topics:
                for mid in tp.get("material_ids", []):
                    published_ids.add(mid)

            step3_data = None
            if resume:
                step3_data = _load_step_cache(
                    date_str, 3, lane=cache_lane, cache_dir=cache_dir
                )
                if step3_data:
                    print(f"  Step 3 [{cache_lane}]: loaded from cache")
            if step3_data is None:
                topic_results = step3_write_topics(
                    topics,
                    lane_mats,
                    model=write_model,
                    reader_snippet=reader.profile_snippet,
                    active_systems=reader.active_systems,
                )
                step3_data = {"topic_results": topic_results}
                _save_step_cache(
                    date_str, 3, step3_data, lane=cache_lane, cache_dir=cache_dir
                )
            lane_results[lane_name] = step3_data["topic_results"]

        topic_data[slug]["lane_results"] = lane_results

    # Lightweight check
    if total_lane_materials < 3:
        _generate_lightweight_daily(
            date_str,
            materials,
            output_dir=out_dir,
        )
        return {"success": True, "reader_id": rid, "lightweight": True}

    # --- Global deep article cap ---
    GLOBAL_DEEP_ARTICLE_CAP = 12
    total_deep = sum(
        len(results)
        for tdata in topic_data.values()
        for results in tdata.get("lane_results", {}).values()
    )
    if total_deep > GLOBAL_DEEP_ARTICLE_CAP:
        overflow = total_deep - GLOBAL_DEEP_ARTICLE_CAP

        def _topic_total(slug: str) -> int:
            lr = topic_data.get(slug, {}).get("lane_results", {})
            return sum(len(v) for v in lr.values())

        for trim_lane in ("tweet-normal", "tweet-long"):
            if overflow <= 0:
                break
            for t in reversed(all_topics):
                if overflow <= 0:
                    break
                slug = t["slug"]
                lr = topic_data.get(slug, {}).get("lane_results", {})
                lane_list = lr.get(trim_lane, [])
                while lane_list and overflow > 0 and _topic_total(slug) > 1:
                    lane_list.pop()
                    overflow -= 1
        if overflow > 0:
            for t in reversed(all_topics):
                if overflow <= 0:
                    break
                slug = t["slug"]
                lr = topic_data.get(slug, {}).get("lane_results", {})
                lane_list = lr.get("rss", [])
                while len(lane_list) > 1 and overflow > 0 and _topic_total(slug) > 1:
                    lane_list.pop()
                    overflow -= 1
        print(
            f"  Global cap: trimmed to {GLOBAL_DEEP_ARTICLE_CAP} deep articles (was {total_deep})"
        )

    # --- Step 4 ---
    step4_result = None
    if resume:
        step4_result = _load_step_cache(date_str, 4, cache_dir=cache_dir)
        if step4_result and html_path.exists():
            print("  Step 4: loaded from cache")
    if step4_result is None:
        step4_result = step4_assemble_tabbed(
            date_str,
            topic_data,
            all_topics,
            output_dir=out_dir,
            reader_snippet=reader.profile_snippet,
            active_systems=reader.active_systems,
        )
        _save_step_cache(
            date_str, 4, {"one_liner": step4_result["one_liner"]}, cache_dir=cache_dir
        )

    one_liner = step4_result["one_liner"]

    topic_titles: List[str] = []
    for t in all_topics:
        slug = t["slug"]
        tdata = topic_data.get(slug, {})
        tol = tdata.get("one_liner", "")
        if tol:
            topic_titles.append(f"{t['label']}: {tol}")

    # --- Step 5: Publish ---
    # Dry-run skips publish: per PLAN v2.1 §5.5, no Feishu push, no side channel.
    # Output HTML is still written to dailies-dryrun/ by the step5 caller below
    # via reader.output_dir, which build_default_reader_from_home(dry_run=True)
    # already swapped to the isolated tree.
    if not dry_run:
        step5_publish(
            date_str,
            one_liner,
            topic_titles,
            reader_config=reader,
        )

    # --- Update global reported events (with lock) ---
    # Dry-run skips this whole block: reported_events.json is prod state.
    material_map = {m["id"]: m for m in materials}
    published_materials = [
        material_map[mid] for mid in published_ids if mid in material_map
    ]
    if published_materials and not dry_run:

        def _updater(store: Dict[str, Any]) -> Dict[str, Any]:
            return _update_reported_events(store, published_materials, date_str)

        try:
            _locked_update_reported_events(config.REPORTED_EVENTS_PATH, _updater)
            print(
                f"  Cross-day dedup: indexed {len(published_materials)} materials",
                file=sys.stderr,
            )
        except Exception as err:
            print(
                f"  WARNING: reported_events.json update failed ({err})",
                file=sys.stderr,
            )

    # --- Update per-reader delivered keys ---
    # Dry-run skips this too — delivered keys encode what the user has seen;
    # letting dry-run write them would pollute next prod run's dedup state.
    if not dry_run:
        delivered_path = reader.cache_dir.parent / "reported_events.json"
        existing_delivered = _load_reader_delivered(delivered_path)
        new_keys = set()
        for m in published_materials:
            mat_url = normalize_url(m.get("link", ""))
            new_keys.add(_build_event_key(mat_url, m.get("title", "")))
        if new_keys - existing_delivered:
            _save_reader_delivered(delivered_path, existing_delivered | new_keys)

    print(f"\n  Done: reader '{rid}' daily digest for {date_str} generated.")
    return {"success": True, "reader_id": rid}


def cmd_generate(args: argparse.Namespace) -> None:
    """Run the topic-first daily digest pipeline."""
    date_str = args.date or datetime.date.today().isoformat()

    # Validate date format
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        print(f"Invalid date format: {date_str} (expected YYYY-MM-DD)", file=sys.stderr)
        sys.exit(1)

    write_model = config.get_effective_models().write

    # --- Build reader list (v0.3.0: one HOME = one reader) ---
    # dry_run flag is introduced in Step 17 (__main__.py). Until then, args may
    # not carry the attribute — default to False for back-compat.
    dry_run = getattr(args, "dry_run", False)
    readers = [config.build_default_reader_from_home(dry_run=dry_run)]

    # --- Pre-flight: skip if all outputs exist (single reader, no force/resume) ---
    if len(readers) == 1 and not args.force and not args.resume:
        r = readers[0]
        html_path = r.output_dir / f"{date_str}.html"
        if html_path.exists():
            print(f"\u65e5\u62a5\u5df2\u5b58\u5728: {html_path}")
            sys.exit(0)

    print(f"Generating deep daily digest for {date_str}")
    print(f"  Write model: {write_model}")
    print(f"  Readers: {', '.join(r.reader_id for r in readers)}")

    # --- Shared collection (runs once) ---
    try:
        shared = collect_shared(date_str, force=args.force, resume=args.resume)
    except Exception as err:
        print(f"Shared collection failed: {err}", file=sys.stderr)
        sys.exit(1)

    # Commit global first_seen after shared collection (independent of reader success).
    # Dry-run skips this: per PLAN v2.1 §5.5, dry-run must not mutate reported_events.json.
    if not dry_run:
        try:
            event_store = shared["event_store"]
            _atomic_write_json(config.REPORTED_EVENTS_PATH, event_store)
        except Exception as err:
            print(
                f"  WARNING: global first_seen commit failed ({err})", file=sys.stderr
            )

    # --- Per-reader generation (AC-14b: error isolation) ---
    results: list[dict] = []
    for reader in readers:
        try:
            result = generate_for_reader(
                shared,
                reader,
                date_str,
                write_model=write_model,
                force=args.force,
                resume=args.resume,
                dry_run=dry_run,
            )
            results.append(result)
        except Exception as err:
            print(f"\n  FAILED: reader '{reader.reader_id}' — {err}", file=sys.stderr)
            results.append(
                {"success": False, "reader_id": reader.reader_id, "error": str(err)}
            )

    # --- Summary ---
    succeeded = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    print(f"\nDone: {len(succeeded)}/{len(results)} readers succeeded.")
    if failed:
        for f in failed:
            print(
                f"  FAILED: {f['reader_id']} — {f.get('error', 'unknown')}",
                file=sys.stderr,
            )
        if not succeeded:
            sys.exit(1)
