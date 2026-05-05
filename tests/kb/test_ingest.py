from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from deep_daily import config as config_module
from deep_daily.config import init_runtime
from deep_daily.home import HomeConfig
from deep_daily.kb.ingest import ingest


def test_ingest_watermark_and_error(isolated_runtime, tmp_path: Path, tmp_home: Path) -> None:
    config_module._runtime = None
    home = tmp_home
    articles = home / "data" / "articles"
    tweets = home / "data" / "tweets"
    articles.joinpath("a1.json").write_text(
        json.dumps(
            {
                "id": "a1",
                "link": "https://example.com/a1",
                "summary_zh": "s",
                "fetched_at": "2026-01-01T00:00:00",
                "feed_title": "feed",
            }
        )
    )
    tweets.joinpath("tw_1.json").write_text(
        json.dumps(
            {
                "id": "tw_1",
                "event_time": "2026-01-02T00:00:00Z",
                "tweet_url": "https://x.com/1",
                "content": "tweet",
            }
        )
    )
    articles.joinpath("bad.json").write_text("{")
    home_cfg = HomeConfig.load(home)
    init_runtime(home_cfg)
    db_path = home / "data" / "kb" / "kb.db"
    result = ingest(db_path, rebuild=True)
    assert result.ok == 1
    assert result.files_failed == 1
    with sqlite3.connect(db_path) as conn:
        err = conn.execute("SELECT status FROM ingest_files WHERE path LIKE '%bad.json'").fetchone()
        assert err[0] == "error"
        run = conn.execute("SELECT ok FROM ingest_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        assert run[0] == 1
    second = ingest(db_path)
    assert second.files_skipped == 2


def test_rebuild_recreates(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home_cfg = HomeConfig.load(tmp_home)
    init_runtime(home_cfg)
    db_path = tmp_home / "data" / "kb" / "kb.db"
    first = ingest(db_path, rebuild=True)
    second = ingest(db_path, rebuild=True)
    assert first.ok == 1
    assert second.ok == 1
