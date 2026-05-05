"""Knowledge base package for PRD 002 Milestone 1."""

from .ingest import IngestResult, ingest
from .normalize import NormalizedItem, normalize_article, normalize_tweet_bulk, normalize_tweet_curated
from .schema import bootstrap_db

__all__ = [
    "IngestResult",
    "NormalizedItem",
    "bootstrap_db",
    "ingest",
    "normalize_article",
    "normalize_tweet_bulk",
    "normalize_tweet_curated",
]
