from __future__ import annotations

import sqlite3
from pathlib import Path

from deep_daily.kb.ingest import collect_stats
from deep_daily.kb.schema import bootstrap_db


def test_collect_stats_totals_and_provenance_partition(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO ingest_runs (started_ts, finished_ts, mode, files_scanned, files_skipped, files_ok, files_failed, rows_inserted, rows_updated, rows_refs_added, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-05-05T00:00:00Z", "2026-05-05T00:01:00Z", "incremental", 5, 2, 3, 0, 4, 1, 2, 1),
        )
        _insert_item(conn, "tweet:curated", "tweet", has_curated=1, has_bulk=0)
        _insert_item(conn, "tweet:bulk", "tweet", has_curated=0, has_bulk=1)
        _insert_item(conn, "tweet:merged", "tweet", has_curated=1, has_bulk=1)
        _insert_item(conn, "article:1", "article", has_curated=0, has_bulk=0)

    stats = collect_stats(db_path)
    assert stats["items_total"] == 4
    per_source = stats["per_source"]
    assert isinstance(per_source, dict)
    assert sum(per_source.values()) == 4
    provenance = stats["provenance_stats"]
    assert isinstance(provenance, dict)
    assert provenance == {
        "tweets_curated_only": 1,
        "tweets_bulk_only": 1,
        "tweets_merged": 1,
    }
    assert sum(provenance.values()) == 3


def _insert_item(conn: sqlite3.Connection, item_id: str, source: str, *, has_curated: int, has_bulk: int) -> None:
    event_ts = "2026-05-05T00:00:00Z"
    conn.execute(
        "INSERT INTO items (id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh, url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            source,
            item_id.split(":", 1)[1],
            event_ts,
            event_ts,
            None,
            None,
            item_id,
            None,
            None,
            None,
            None,
            has_curated,
            has_bulk,
            "single",
            event_ts,
            event_ts,
        ),
    )
