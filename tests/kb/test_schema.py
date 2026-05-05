from __future__ import annotations

import sqlite3
from pathlib import Path

from deep_daily.kb.schema import bootstrap_db


def test_bootstrap_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')"
            )
        }
        assert "items" in tables
        assert "item_raw_refs" in tables
        assert "ingest_runs" in tables
        assert "ingest_files" in tables
        assert "meta" in tables
        assert "items_fts" in tables
        assert "items_ai" in tables


def test_fts_triggers_mirror_items(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO items (id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh, url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tweet:1",
                "tweet",
                "1",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "alice",
                None,
                "hello token",
                None,
                "https://x.com/1",
                None,
                None,
                0,
                0,
                "single",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        assert conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0] == 1
        conn.execute("UPDATE items SET body = ? WHERE id = ?", ("goodbye token", "tweet:1"))
        row = conn.execute("SELECT body FROM items_fts").fetchone()
        assert row[0] == "goodbye token"
        conn.execute("DELETE FROM items WHERE id = ?", ("tweet:1",))
        assert conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0] == 0
