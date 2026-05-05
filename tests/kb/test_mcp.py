from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest, ListToolsResult

from deep_daily.kb.mcp import create_server, run_get_item, run_search, run_stats
from deep_daily.kb.normalize import normalize_tweet_bulk, normalize_tweet_curated
from deep_daily.kb.query import _sha1_json
from deep_daily.kb.refs import upsert_raw_ref
from deep_daily.kb.schema import bootstrap_db


@pytest.mark.asyncio
async def test_server_advertises_exact_three_tools(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    server = create_server(db_path=db_path)
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    tools = cast(ListToolsResult, result.root).tools
    assert [tool.name for tool in tools] == ["search_text", "get_item", "stats"]
    search_schema = tools[0].inputSchema
    assert search_schema["required"] == ["query"]
    assert search_schema["properties"]["limit"]["default"] == 20


@pytest.mark.asyncio
async def test_search_text_round_trip_and_sorting(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_item(conn, "tweet:3", "tweet", "2026-04-16T00:00:00Z", "carol", "RWA 3")
        _insert_item(conn, "tweet:2", "tweet", "2026-04-15T00:00:00Z", "bob", "RWA 2")
        _insert_item(conn, "tweet:1", "tweet", "2026-04-14T00:00:00Z", "alice", "RWA 1")
        _insert_item(conn, "tweet:0", "tweet", "2026-04-13T00:00:00Z", "dave", "RWA 0")
        _insert_item(conn, "article:1", "article", "2026-04-12T00:00:00Z", "feed", "RWA article")

    result = await run_search(db_path, query="RWA", limit=5)
    assert len(result) == 5
    assert [row["id"] for row in result] == ["tweet:3", "tweet:2", "tweet:1", "tweet:0", "article:1"]
    assert all("snippet" in row for row in result)


@pytest.mark.asyncio
async def test_get_item_existing_and_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    raw_path = tmp_path / "tweet.json"
    payload = {
        "id": "tw_1",
        "event_time": "2026-04-14T00:00:00Z",
        "tweet_url": "https://x.com/1",
        "content": "hello world",
    }
    raw_path.write_text(json.dumps(payload), encoding="utf-8")
    item = normalize_tweet_curated(raw_path, payload)

    with sqlite3.connect(db_path) as conn:
        _insert_normalized_item(conn, item)
        upsert_raw_ref(conn, item.id, "curated", str(raw_path), ".", item.raw_hash, "2026-04-14T00:00:00Z", "2026-04-14T00:00:00Z")
        conn.commit()

    detail = await run_get_item(db_path, item.id)
    assert detail is not None
    assert detail["id"] == item.id
    assert len(detail["raws"]) == 1
    assert detail["raws"][0]["raw"] == payload
    assert await run_get_item(db_path, "tweet:missing") is None


@pytest.mark.asyncio
async def test_it6_file_missing_other_provenance_unaffected(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    curated_path = tmp_path / "curated.json"
    bulk_path = tmp_path / "bulk.jsonl"
    curated_payload = {
        "id": "tw_42",
        "event_time": "2026-04-14T00:00:00Z",
        "tweet_url": "https://x.com/42",
        "content": "curated",
    }
    bulk_payload = {
        "id": "tw_42",
        "collected_at": "2026-04-14T08:00:00",
        "tweet_url": "https://x.com/42",
        "content": "bulk",
    }
    curated_path.write_text(json.dumps(curated_payload), encoding="utf-8")
    bulk_path.write_text(json.dumps(bulk_payload) + "\n", encoding="utf-8")
    item = normalize_tweet_curated(curated_path, curated_payload)

    with sqlite3.connect(db_path) as conn:
        _insert_normalized_item(conn, item, has_curated=1, has_bulk=1, preferred_src="curated")
        upsert_raw_ref(conn, item.id, "curated", str(curated_path), ".", item.raw_hash, "2026-04-14T00:00:00Z", "2026-04-14T00:00:00Z")
        bulk_item = normalize_tweet_bulk(bulk_path, bulk_payload)
        upsert_raw_ref(conn, item.id, "bulk", str(bulk_path), "1", bulk_item.raw_hash, "2026-04-14T00:00:00Z", "2026-04-14T00:00:00Z")
        conn.commit()

    curated_path.unlink()
    detail = await run_get_item(db_path, item.id)
    assert detail is not None
    assert detail["raws"][0]["provenance"] == "curated"
    assert detail["raws"][0]["raw_unavailable"] is True
    assert detail["raws"][0]["raw_unavailable_reason"] == "file_missing"
    assert detail["raws"][1]["provenance"] == "bulk"
    assert detail["raws"][1]["raw_unavailable"] is False


@pytest.mark.asyncio
async def test_it7_hash_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    raw_path = tmp_path / "tweet.json"
    payload = {
        "id": "tw_7",
        "event_time": "2026-04-14T00:00:00Z",
        "tweet_url": "https://x.com/7",
        "content": "before",
    }
    raw_path.write_text(json.dumps(payload), encoding="utf-8")
    item = normalize_tweet_curated(raw_path, payload)
    with sqlite3.connect(db_path) as conn:
        _insert_normalized_item(conn, item)
        upsert_raw_ref(conn, item.id, "curated", str(raw_path), ".", item.raw_hash, "2026-04-14T00:00:00Z", "2026-04-14T00:00:00Z")
        conn.commit()
    raw_path.write_text(json.dumps({**payload, "content": "after"}), encoding="utf-8")

    detail = await run_get_item(db_path, item.id)
    assert detail is not None
    assert detail["raws"][0]["raw_unavailable"] is True
    assert detail["raws"][0]["raw_unavailable_reason"] == "hash_mismatch"


@pytest.mark.asyncio
async def test_locator_out_of_range_and_read_error(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    jsonl_path = tmp_path / "bulk.jsonl"
    payload = {
        "id": "tw_8",
        "collected_at": "2026-04-14T08:00:00",
        "tweet_url": "https://x.com/8",
        "content": "bulk",
    }
    jsonl_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    item = normalize_tweet_bulk(jsonl_path, payload)
    protected_path = tmp_path / "protected.json"
    protected_payload = {
        "id": "tw_9",
        "event_time": "2026-04-14T00:00:00Z",
        "tweet_url": "https://x.com/9",
        "content": "protected",
    }
    protected_path.write_text(json.dumps(protected_payload), encoding="utf-8")
    protected_item = normalize_tweet_curated(protected_path, protected_payload)

    with sqlite3.connect(db_path) as conn:
        _insert_normalized_item(conn, item)
        upsert_raw_ref(conn, item.id, "bulk", str(jsonl_path), "999", item.raw_hash, "2026-04-14T00:00:00Z", "2026-04-14T00:00:00Z")
        _insert_normalized_item(conn, protected_item)
        upsert_raw_ref(
            conn,
            protected_item.id,
            "curated",
            str(protected_path),
            ".",
            protected_item.raw_hash,
            "2026-04-14T00:00:00Z",
            "2026-04-14T00:00:00Z",
        )
        conn.commit()

    out_of_range = await run_get_item(db_path, item.id)
    assert out_of_range is not None
    assert out_of_range["raws"][0]["raw_unavailable_reason"] == "locator_out_of_range"

    os.chmod(protected_path, 0)
    try:
        protected = await run_get_item(db_path, protected_item.id)
    finally:
        os.chmod(protected_path, 0o644)
    assert protected is not None
    assert protected["raws"][0]["raw_unavailable"] is True
    assert protected["raws"][0]["raw_unavailable_reason"].startswith("read_error:")


@pytest.mark.asyncio
async def test_mcp_safety_and_fts_error(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        _insert_item(conn, "tweet:1", "tweet", "2026-04-14T00:00:00Z", "alice", "safe content")

    server = create_server(db_path=db_path)
    handler = server.request_handlers[CallToolRequest]
    injection = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="search_text", arguments={"query": "safe", "author": "alice' OR 1=1 --", "limit": 5}),
        )
    )
    result = cast("Any", injection.root)
    assert result.isError is False
    assert result.structuredContent == {"results": []}

    invalid = await run_search(db_path, query="AND")
    assert invalid == []

    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only=1")
            conn.execute("CREATE TABLE nope(id INTEGER)")


@pytest.mark.asyncio
async def test_stats_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "kb.db"
    bootstrap_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO ingest_runs (started_ts, finished_ts, mode, files_scanned, files_skipped, files_ok, files_failed, rows_inserted, rows_updated, rows_refs_added, ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-05-05T00:00:00Z", "2026-05-05T00:01:00Z", "incremental", 2, 1, 1, 0, 1, 0, 0, 1),
        )
        _insert_item(conn, "tweet:1", "tweet", "2026-04-14T00:00:00Z", "alice", "safe content")

    stats = await run_stats(db_path)
    assert stats["items_total"] == 1
    assert stats["last_ingest"]["run_id"] == 1


def _insert_item(conn: sqlite3.Connection, item_id: str, source: str, event_ts: str, author: str | None, body: str | None) -> None:
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


def _insert_normalized_item(
    conn: sqlite3.Connection,
    item,
    *,
    has_curated: int = 1,
    has_bulk: int = 0,
    preferred_src: str = "curated",
) -> None:
    conn.execute(
        "INSERT INTO items (id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh, url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item.id,
            item.source,
            item.native_id,
            item.event_ts,
            item.fetched_ts,
            item.author,
            item.title,
            item.body,
            item.body_zh,
            item.url,
            item.category,
            item.relevance,
            has_curated,
            has_bulk,
            preferred_src,
            item.event_ts,
            item.event_ts,
        ),
    )
