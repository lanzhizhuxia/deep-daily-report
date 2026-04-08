from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

_UTM_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "ref", "referrer", "fbclid", "gclid", "mc_cid", "mc_eid",
})


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        scheme = "https"
        qs = parse_qs(parsed.query, keep_blank_values=False)
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in _UTM_PARAMS}
        new_query = urlencode(sorted(clean_qs.items()), doseq=True)
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((scheme, parsed.netloc.lower(), path, "", new_query, ""))
    except Exception:
        return url


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    def _tokens(s: str) -> list[str]:
        words = s.split()
        cjk_chars = [c for c in s if '\u4e00' <= c <= '\u9fff']
        bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
        return words + bigrams

    set_a = set(_tokens(a))
    set_b = set(_tokens(b))
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union


def build_event_key(url: str, title: str) -> str:
    parts: list[str] = []
    if url:
        try:
            parsed = urlparse(url)
            parts.append(parsed.netloc.lower())
            path_parts = [seg for seg in parsed.path.split("/") if seg and not seg.isdigit()]
            parts.append("/".join(path_parts))
        except Exception:
            parts.append(url)
    if title:
        norm = normalize_title(title)
        tokens = sorted(norm.split())
        parts.append(" ".join(tokens))
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]
