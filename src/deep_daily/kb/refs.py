from __future__ import annotations

import sqlite3
from typing import Any


RawRefRow = dict[str, Any]


def upsert_raw_ref(
    db: sqlite3.Connection,
    item_id: str,
    provenance: str,
    path: str,
    locator: str,
    content_hash: str,
    mtime: str,
    now_ts: str,
) -> bool:
    existing = get_raw_ref(db, item_id, provenance)
    first_seen_ts = now_ts if existing is None else str(existing["first_seen_ts"])
    db.execute(
        """
        INSERT INTO item_raw_refs (
            item_id, provenance, raw_path, raw_locator, raw_hash, raw_mtime, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id, provenance) DO UPDATE SET
            raw_path=excluded.raw_path,
            raw_locator=excluded.raw_locator,
            raw_hash=excluded.raw_hash,
            raw_mtime=excluded.raw_mtime,
            first_seen_ts=item_raw_refs.first_seen_ts,
            last_seen_ts=excluded.last_seen_ts
        """,
        (item_id, provenance, path, locator, content_hash, mtime, first_seen_ts, now_ts),
    )
    return existing is None


def get_raw_ref(db: sqlite3.Connection, item_id: str, provenance: str) -> RawRefRow | None:
    row = db.execute(
        "SELECT item_id, provenance, raw_path, raw_locator, raw_hash, raw_mtime, first_seen_ts, last_seen_ts "
        "FROM item_raw_refs WHERE item_id = ? AND provenance = ?",
        (item_id, provenance),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row | tuple[Any, ...]) -> RawRefRow:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    keys = [
        "item_id",
        "provenance",
        "raw_path",
        "raw_locator",
        "raw_hash",
        "raw_mtime",
        "first_seen_ts",
        "last_seen_ts",
    ]
    return dict(zip(keys, row, strict=True))
