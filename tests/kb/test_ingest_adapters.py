from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from deep_daily import config as config_module
from deep_daily.config import init_runtime
from deep_daily.home import HomeConfig
from deep_daily.kb.ingest import ingest


def test_ingest_news6551_and_hn_pipeline(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    (home.data_dir / "news-6551" / "2026-02-21.json").write_text(
        json.dumps(
            {
                "2026-02-21_rwa": [
                    {
                        "id": "6551-abc",
                        "title": "RWA headline",
                        "link": "https://example.com/rwa",
                        "source_name": "Reuters",
                        "time": "2026-02-18T12:50:00.747106Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (home.data_dir / "hackernews" / "2026-05-05.jsonl").write_text(
        json.dumps(
            {
                "objectID": "48019219",
                "created_at": "2026-05-05T07:34:55Z",
                "author": "john-doe",
                "title": "MCP story",
                "url": "https://example.com/mcp",
                "points": 307,
                "num_comments": 312,
            }
        ),
        encoding="utf-8",
    )
    init_runtime(home)
    db_path = home.path / "data" / "kb" / "kb.db"
    result = ingest(db_path, rebuild=True, sources=["news-6551", "hackernews"])
    assert result.ok == 1
    with sqlite3.connect(db_path) as conn:
        news_row = conn.execute("SELECT source, category FROM items WHERE id = 'news6551:6551-abc'").fetchone()
        hn_row = conn.execute("SELECT source, category FROM items WHERE id = 'hn:48019219'").fetchone()
        refs = conn.execute("SELECT item_id, provenance, raw_path, raw_locator FROM item_raw_refs ORDER BY item_id").fetchall()
    assert news_row == ("news6551", "rwa")
    assert hn_row == ("hn", "hn:points=307,num_comments=312")
    assert refs == [
        ("hn:48019219", "single", str(home.data_dir / "hackernews" / "2026-05-05.jsonl"), "1"),
        ("news6551:6551-abc", "single", str(home.data_dir / "news-6551" / "2026-02-21.json"), "2026-02-21_rwa"),
    ]
