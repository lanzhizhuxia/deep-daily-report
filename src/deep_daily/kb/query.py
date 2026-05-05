from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("deep_daily.kb.query")

ItemSummary = dict[str, Any]


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


def _date_floor(value: str) -> str:
    dt = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _date_ceil(value: str) -> str:
    dt = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return dt.replace(hour=23, minute=59, second=59, microsecond=0).isoformat().replace("+00:00", "Z")
