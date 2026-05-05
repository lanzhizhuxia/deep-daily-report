from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("deep_daily.kb.query")

ItemSummary = dict[str, Any]
ItemDetail = dict[str, Any]
RawRefDetail = dict[str, Any]

PROVENANCE_ORDER = {"curated": 0, "bulk": 1, "single": 2}


def search_text(
    db: sqlite3.Connection,
    *,
    query: str,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    author: str | None = None,
    limit: int = 20,
) -> list[ItemSummary]:
    clamped_limit = min(100, max(1, limit))
    sql = [
        "SELECT i.id, i.source, i.event_ts, i.author, i.title,",
        "snippet(items_fts, -1, '[...]', '[...]', '...', 32) AS snippet,",
        "i.url, i.preferred_src",
        "FROM items_fts",
        "JOIN items AS i ON i.rowid = items_fts.rowid",
        "WHERE items_fts MATCH ?",
    ]
    params: list[Any] = [query]

    if source is not None:
        sql.append("AND i.source = ?")
        params.append(source)
    if start_date is not None:
        sql.append("AND i.event_ts >= ?")
        params.append(_date_floor(start_date))
    if end_date is not None:
        sql.append("AND i.event_ts <= ?")
        params.append(_date_ceil(end_date))
    if author is not None:
        sql.append("AND LOWER(COALESCE(i.author, '')) LIKE ?")
        params.append(f"%{author.lower()}%")

    sql.append("ORDER BY i.event_ts DESC, i.id ASC LIMIT ?")
    params.append(clamped_limit)

    try:
        rows = db.execute("\n".join(sql), tuple(params)).fetchall()
    except sqlite3.OperationalError as err:
        logger.warning("FTS query failed for %r: %s", query, err)
        return []

    return [
        {
            "id": str(row[0]),
            "source": str(row[1]),
            "event_ts": str(row[2]),
            "author": row[3],
            "title": row[4],
            "snippet": row[5] or "",
            "url": row[6],
            "preferred_src": str(row[7]),
        }
        for row in rows
    ]


def get_item(db: sqlite3.Connection, *, item_id: str) -> ItemDetail | None:
    db.row_factory = sqlite3.Row
    item_row = db.execute(
        "SELECT id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh, url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts "
        "FROM items WHERE id = ?",
        (item_id,),
    ).fetchone()
    if item_row is None:
        return None

    raw_rows = db.execute(
        "SELECT provenance, raw_path, raw_locator, raw_hash, raw_mtime, first_seen_ts, last_seen_ts "
        "FROM item_raw_refs WHERE item_id = ?",
        (item_id,),
    ).fetchall()

    raws = [_hydrate_raw_ref(row) for row in raw_rows]
    raws.sort(key=lambda row: (PROVENANCE_ORDER.get(str(row["provenance"]), 999), str(row["provenance"])))

    return {
        "id": str(item_row["id"]),
        "source": str(item_row["source"]),
        "native_id": str(item_row["native_id"]),
        "event_ts": str(item_row["event_ts"]),
        "fetched_ts": str(item_row["fetched_ts"]),
        "author": item_row["author"],
        "title": item_row["title"],
        "body": item_row["body"],
        "body_zh": item_row["body_zh"],
        "url": item_row["url"],
        "category": item_row["category"],
        "relevance": item_row["relevance"],
        "has_curated": int(item_row["has_curated"] or 0),
        "has_bulk": int(item_row["has_bulk"] or 0),
        "preferred_src": str(item_row["preferred_src"]),
        "first_seen_ts": str(item_row["first_seen_ts"]),
        "last_seen_ts": str(item_row["last_seen_ts"]),
        "raws": raws,
    }


def collect_stats(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {
            "items_total": 0,
            "per_source": {},
            "date_range": {"earliest": None, "latest": None},
            "last_ingest": None,
            "db_size_bytes": 0,
            "provenance_stats": {
                "tweets_curated_only": 0,
                "tweets_bulk_only": 0,
                "tweets_merged": 0,
            },
        }

    conn = sqlite3.connect(db_path)
    try:
        items_total = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        per_source = {
            str(row[0]): int(row[1])
            for row in conn.execute("SELECT source, COUNT(*) FROM items GROUP BY source ORDER BY source")
        }
        date_row = conn.execute("SELECT MIN(event_ts), MAX(event_ts) FROM items").fetchone()
        provenance_row = conn.execute(
            "SELECT "
            "SUM(CASE WHEN source='tweet' AND has_curated=1 AND has_bulk=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN source='tweet' AND has_bulk=1 AND has_curated=0 THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN source='tweet' AND has_curated=1 AND has_bulk=1 THEN 1 ELSE 0 END) "
            "FROM items"
        ).fetchone()
        last_run_row = conn.execute(
            "SELECT run_id, started_ts, finished_ts, files_scanned, files_skipped, files_ok, files_failed, rows_inserted, rows_updated, rows_refs_added, ok "
            "FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    last_ingest = None
    if last_run_row is not None:
        last_ingest = {
            "run_id": int(last_run_row[0]),
            "started_ts": last_run_row[1],
            "ts": last_run_row[2],
            "files_scanned": int(last_run_row[3]),
            "files_skipped": int(last_run_row[4]),
            "files_ok": int(last_run_row[5]),
            "files_failed": int(last_run_row[6]),
            "rows_inserted": int(last_run_row[7]),
            "rows_updated": int(last_run_row[8]),
            "rows_refs_added": int(last_run_row[9]),
            "ok": bool(last_run_row[10]) if last_run_row[10] is not None else None,
        }

    return {
        "items_total": items_total,
        "per_source": per_source,
        "date_range": {"earliest": date_row[0], "latest": date_row[1]},
        "last_ingest": last_ingest,
        "db_size_bytes": db_path.stat().st_size,
        "provenance_stats": {
            "tweets_curated_only": int(provenance_row[0] or 0),
            "tweets_bulk_only": int(provenance_row[1] or 0),
            "tweets_merged": int(provenance_row[2] or 0),
        },
    }


def _hydrate_raw_ref(row: sqlite3.Row) -> RawRefDetail:
    raw_path = str(row["raw_path"])
    raw_locator = row["raw_locator"]
    raw_hash = row["raw_hash"]
    raw_unavailable, raw_unavailable_reason, raw_payload = _load_raw_payload(
        Path(raw_path),
        raw_locator,
        raw_hash,
    )
    return {
        "provenance": str(row["provenance"]),
        "raw_path": raw_path,
        "raw_locator": raw_locator,
        "raw_hash": raw_hash,
        "raw_mtime": row["raw_mtime"],
        "first_seen_ts": row["first_seen_ts"],
        "last_seen_ts": row["last_seen_ts"],
        "raw_unavailable": raw_unavailable,
        "raw_unavailable_reason": raw_unavailable_reason,
        "raw": raw_payload,
    }


def _load_raw_payload(path: Path, locator: Any, expected_hash: Any) -> tuple[bool, str | None, Any | None]:
    try:
        if not path.exists():
            return True, "file_missing", None
    except OSError as err:
        return True, _format_read_error(err), None

    try:
        payload = _read_raw_record(path, locator)
    except ValueError:
        return True, "locator_out_of_range", None
    except OSError as err:
        return True, _format_read_error(err), None

    if expected_hash is not None and _sha1_json(payload) != str(expected_hash):
        return True, "hash_mismatch", None
    return False, None, payload


def _read_raw_record(path: Path, locator: Any) -> Any:
    if locator in (None, "."):
        return _read_json_file(path)

    locator_str = str(locator)
    if locator_str.isdigit():
        return _read_jsonl_line(path, int(locator_str))
    return _read_nested_json_group(path, locator_str)


def _read_json_file(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_line(path: Path, line_number: int) -> Any:
    import json

    if line_number < 1:
        raise ValueError("line number out of range")
    with path.open("r", encoding="utf-8") as handle:
        for current, line in enumerate(handle, start=1):
            if current == line_number:
                return json.loads(line)
    raise ValueError("line number out of range")


def _read_nested_json_group(path: Path, group_key: str) -> Any:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    group = data.get(group_key)
    if not isinstance(group, list):
        raise ValueError("group locator out of range")
    return group


def _sha1_json(payload: Any) -> str:
    import hashlib
    import json

    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _format_read_error(err: OSError) -> str:
    import errno

    code = errno.errorcode.get(err.errno, repr(err)) if err.errno is not None else repr(err)
    return f"read_error: {code}"


def _date_floor(value: str) -> str:
    dt = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _date_ceil(value: str) -> str:
    dt = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return dt.replace(hour=23, minute=59, second=59, microsecond=0).isoformat().replace("+00:00", "Z")
