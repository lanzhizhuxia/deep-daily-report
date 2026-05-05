from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from deep_daily.commands.kb_cmd import cmd_kb
from deep_daily.home import HomeConfig
from deep_daily.kb.schema import bootstrap_db


def test_kb_query_json_output(capsys, tmp_home: Path) -> None:
    home = HomeConfig.load(tmp_home)
    db_path = home.path / "data" / "kb" / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_item(conn, "tweet:1", "tweet", "RWA token", "alice")
    rc = cmd_kb(
        argparse.Namespace(kb_cmd="query", query="RWA", source="tweet", start=None, end=None, author=None, limit=20, json=True),
        home,
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload[0]["id"] == "tweet:1"


def test_kb_query_invalid_source(capsys, tmp_home: Path) -> None:
    home = HomeConfig.load(tmp_home)
    rc = cmd_kb(
        argparse.Namespace(kb_cmd="query", query="RWA", source="bad", start=None, end=None, author=None, limit=20, json=False),
        home,
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "Unsupported source" in err


def _insert_item(conn: sqlite3.Connection, item_id: str, source: str, body: str, author: str) -> None:
    event_ts = "2026-04-15T00:00:00Z"
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
            None,
            body,
            None,
            None,
            None,
            None,
            0,
            0,
            "single",
            event_ts,
            event_ts,
        ),
    )
