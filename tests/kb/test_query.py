from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from deep_daily.kb.query import search_text
from deep_daily.kb.schema import bootstrap_db


def test_search_text_filters_and_sorting(caplog, tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_item(conn, "tweet:1", "tweet", "2026-04-15T01:00:00Z", "balajis", "RWA alpha", "RWA alpha body")
        _insert_item(conn, "tweet:2", "tweet", "2026-04-15T01:00:00Z", "Balajis", "RWA beta", "RWA beta body")
        _insert_item(conn, "article:1", "article", "2026-04-10T01:00:00Z", "feed", "BlackRock stablecoin", "BlackRock stablecoin body")
        _insert_item(conn, "hn:1", "hn", "2026-04-20T01:00:00Z", "hn-user", "mcp story", "mcp story body")

        rows = search_text(conn, query="RWA", source="tweet", start_date="2026-04-01", end_date="2026-04-30", author="BAL", limit=200)
        assert [row["id"] for row in rows] == ["tweet:1", "tweet:2"]
        assert rows[0]["event_ts"] == "2026-04-15T01:00:00Z"
        assert "RWA" in rows[0]["snippet"]

        article_rows = search_text(conn, query='"BlackRock stablecoin"', limit=0)
        assert [row["id"] for row in article_rows] == ["article:1"]

        hn_rows = search_text(conn, query="mcp", source="hn", limit=101)
        assert [row["id"] for row in hn_rows] == ["hn:1"]

        with caplog.at_level(logging.WARNING, logger="deep_daily.kb.query"):
            invalid_rows = search_text(conn, query="AND")
        assert invalid_rows == []
        assert "FTS query failed" in caplog.text


def test_search_text_empty_result_and_trigger_update_path(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_item(conn, "tweet:1", "tweet", "2026-04-15T01:00:00Z", "alice", None, "old token")
        assert search_text(conn, query="missing") == []
        assert [row["id"] for row in search_text(conn, query='"old token"')] == ["tweet:1"]
        conn.execute("UPDATE items SET body = ? WHERE id = ?", ("new token", "tweet:1"))
        assert search_text(conn, query='"old token"') == []
        assert [row["id"] for row in search_text(conn, query='"new token"')] == ["tweet:1"]


def _insert_item(
    conn: sqlite3.Connection,
    item_id: str,
    source: str,
    event_ts: str,
    author: str | None,
    title: str | None,
    body: str | None,
) -> None:
    conn.execute(
        "INSERT INTO items (id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh, url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            source,
            item_id.split(":", 1)[1],
            event_ts,
            event_ts,
            author,
            title,
            body,
            None,
            f"https://example.com/{item_id}",
            None,
            None,
            0,
            0,
            "single",
            event_ts,
            event_ts,
        ),
    )
