from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from deep_daily import config as config_module
from deep_daily.config import init_runtime
from deep_daily.home import HomeConfig
from deep_daily.kb.ingest import ingest


def test_ingest_tweets_dedup_and_refs(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    tweets_dir = home.data_dir / "tweets"
    bulk_dir = home.data_dir / "tweets-nas"
    tweets_dir.joinpath("tw_1.json").write_text(
        json.dumps(
            {
                "id": "tw_1",
                "event_time": "2026-05-01T00:00:00Z",
                "fetched_at": "2026-05-01T00:05:00Z",
                "tweet_url": "https://x.com/test/status/1",
                "content": "curated",
                "content_zh": "策展",
                "handle": "tester",
            }
        ),
        encoding="utf-8",
    )
    bulk_dir.joinpath("tweets.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "tw_1",
                        "collected_at": "2026-05-01T08:05:00+08:00",
                        "tweet_url": "https://x.com/test/status/1",
                        "content": "bulk",
                        "handle": "tester",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "tw_2",
                        "collected_at": "2026-05-01T09:00:00+08:00",
                        "tweet_url": "https://x.com/test/status/2",
                        "content": "bulk only",
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    init_runtime(home)
    db_path = home.path / "data" / "kb" / "kb.db"
    result = ingest(db_path, rebuild=True, sources=["tweets", "tweets-nas"])
    assert result.ok == 1
    with sqlite3.connect(db_path) as conn:
        dup_rows = conn.execute(
            "SELECT native_id, COUNT(*) FROM items WHERE source='tweet' GROUP BY native_id HAVING COUNT(*) > 1"
        ).fetchall()
        assert dup_rows == []
        violations = conn.execute(
            "SELECT COUNT(*) FROM items WHERE has_curated=1 AND has_bulk=1 AND (SELECT COUNT(*) FROM item_raw_refs WHERE item_id=items.id) != 2"
        ).fetchone()[0]
        assert violations == 0


def test_ingest_second_run_skips_unchanged_tweet_files(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    (home.data_dir / "tweets").joinpath("tw_1.json").write_text(
        json.dumps(
            {
                "id": "tw_1",
                "event_time": "2026-05-01T00:00:00Z",
                "tweet_url": "https://x.com/test/status/1",
                "content": "curated",
            }
        ),
        encoding="utf-8",
    )
    (home.data_dir / "tweets-nas").joinpath("tweets.jsonl").write_text(
        json.dumps(
            {
                "id": "tw_1",
                "collected_at": "2026-05-01T08:00:00+08:00",
                "tweet_url": "https://x.com/test/status/1",
                "content": "bulk",
            }
        ),
        encoding="utf-8",
    )
    init_runtime(home)
    db_path = home.path / "data" / "kb" / "kb.db"
    first = ingest(db_path, rebuild=True, sources=["tweets", "tweets-nas"])
    second = ingest(db_path, sources=["tweets", "tweets-nas"])
    assert first.files_scanned == 2
    assert second.files_skipped == 2
