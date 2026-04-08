"""Deep Daily Report — Full-featured standalone daily digest pipeline.

This is the complete self-use edition (ISSUE-201), extracted from
<legacy-bot>/tools/rss/daily_report.py with all features preserved:
NAS tweets, news_6551, cross-day dedup, multi-reader support.

Extension points:
    - LLMBackend: Pluggable LLM provider (default: OpenAI-compatible HTTP)
    - Publisher: Pluggable publish channel (default: file-only)
"""

from __future__ import annotations

__version__ = "0.2.0"
