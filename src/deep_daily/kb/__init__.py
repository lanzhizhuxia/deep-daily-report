"""Knowledge base package for PRD 002 Milestone 1."""

from .ingest import IngestResult, ingest
from .normalize import (
    NormalizedItem,
    normalize_article,
    normalize_hn,
    normalize_news6551,
    normalize_tweet_bulk,
    normalize_tweet_curated,
)
from .query import search_text
from .schema import bootstrap_db

__all__ = [
    "IngestResult",
    "NormalizedItem",
    "bootstrap_db",
    "ingest",
    "normalize_article",
    "normalize_hn",
    "normalize_news6551",
    "normalize_tweet_bulk",
    "normalize_tweet_curated",
    "search_text",
]
