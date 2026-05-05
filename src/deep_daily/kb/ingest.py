from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from deep_daily.config import get_runtime

from .normalize import NormalizedItem, normalize_article, normalize_tweet_curated
from .refs import upsert_raw_ref
from .schema import bootstrap_db, rebuild_db
from .state import (
    KBIngestLock,
    close_ingest_run,
    now_utc_iso,
    open_ingest_run,
    record_ingest_file,
)

logger = logging.getLogger("deep_daily.kb.ingest")

Normalizer = Callable[[Path, dict[str, Any]], NormalizedItem]
SOURCE_SPECS: dict[str, tuple[str, str, Normalizer]] = {
    "articles": ("article", "articles", normalize_article),
    "tweets": ("tweet", "tweets", normalize_tweet_curated),
}


@dataclass(frozen=True)
class IngestResult:
    run_id: int
    started_ts: str
    finished_ts: str | None
    mode: str
    sources: str | None
    files_scanned: int
    files_skipped: int
    files_ok: int
    files_failed: int
    rows_inserted: int
    rows_updated: int
    rows_refs_added: int
    ok: int | None


@dataclass
class IngestCounters:
    run_id: int
    started_ts: str
    finished_ts: str | None
    mode: str
    sources: str | None
    files_scanned: int = 0
    files_skipped: int = 0
    files_ok: int = 0
    files_failed: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_refs_added: int = 0
    ok: int | None = None

    def freeze(self) -> IngestResult:
        return IngestResult(
            run_id=self.run_id,
            started_ts=self.started_ts,
            finished_ts=self.finished_ts,
            mode=self.mode,
            sources=self.sources,
            files_scanned=self.files_scanned,
            files_skipped=self.files_skipped,
            files_ok=self.files_ok,
            files_failed=self.files_failed,
            rows_inserted=self.rows_inserted,
            rows_updated=self.rows_updated,
            rows_refs_added=self.rows_refs_added,
            ok=self.ok,
        )


def ingest(
    db_path: Path,
    sources: list[str] | None = None,
    rebuild: bool = False,
    since: str | None = None,
) -> IngestResult:
    runtime = get_runtime()
    source_keys = _resolve_sources(sources)
    since_dt = _parse_since(since)
    if rebuild:
        rebuild_db(db_path)
    else:
        bootstrap_db(db_path)

    with KBIngestLock(runtime.home.path / "state" / "kb-ingest.lock"):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        started_ts = now_utc_iso()
        counters = IngestCounters(
            run_id=open_ingest_run(
                conn,
                mode="rebuild" if rebuild else "incremental",
                sources=",".join(source_keys) if source_keys else None,
                started_ts=started_ts,
            ),
            started_ts=started_ts,
            finished_ts=None,
            mode="rebuild" if rebuild else "incremental",
            sources=",".join(source_keys) if source_keys else None,
        )
        try:
            for source_key in source_keys:
                _ingest_source(conn, counters, source_key, since_dt, ignore_watermark=rebuild)
            counters.finished_ts = now_utc_iso()
            counters.ok = 1
            final = counters.freeze()
            close_ingest_run(conn, final)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("last_ingest_ts", final.finished_ts),
            )
            conn.commit()
            conn.close()
            return final
        except Exception:
            counters.finished_ts = now_utc_iso()
            counters.ok = 0
            failed = counters.freeze()
            close_ingest_run(conn, failed)
            conn.commit()
            conn.close()
            raise


def collect_stats(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {
            "items_total": 0,
            "per_source": {},
            "date_range": {"earliest": None, "latest": None},
            "last_ingest": None,
            "db_size_bytes": 0,
        }
    conn = sqlite3.connect(db_path)
    items_total = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
    per_source = {
        str(row[0]): int(row[1])
        for row in conn.execute("SELECT source, COUNT(*) FROM items GROUP BY source ORDER BY source")
    }
    date_row = conn.execute("SELECT MIN(event_ts), MAX(event_ts) FROM items").fetchone()
    last_run_row = conn.execute(
        "SELECT run_id, started_ts, finished_ts, files_scanned, files_skipped, files_ok, files_failed, rows_inserted, rows_updated, rows_refs_added, ok "
        "FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
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
    conn.close()
    return {
        "items_total": items_total,
        "per_source": per_source,
        "date_range": {"earliest": date_row[0], "latest": date_row[1]},
        "last_ingest": last_ingest,
        "db_size_bytes": db_path.stat().st_size,
    }


def _ingest_source(
    conn: sqlite3.Connection,
    counters: IngestCounters,
    source_key: str,
    since_dt: datetime | None,
    *,
    ignore_watermark: bool,
) -> None:
    source_value, dir_name, normalizer = SOURCE_SPECS[source_key]
    runtime = get_runtime()
    base_dir = runtime.home.data_dir / dir_name
    for path in _iter_files(base_dir):
        stat = path.stat()
        counters.files_scanned += 1
        mtime_iso = _stat_mtime_iso(stat.st_mtime)
        if since_dt is not None and datetime.fromisoformat(mtime_iso.replace("Z", "+00:00")) < since_dt:
            continue
        prev = _get_ingest_file(conn, str(path))
        if (
            not ignore_watermark
            and prev is not None
            and prev["status"] == "ok"
            and prev["last_mtime"] == mtime_iso
            and int(prev["last_size"]) == stat.st_size
        ):
            counters.files_skipped += 1
            continue
        try:
            raw_json = json.loads(path.read_text(encoding="utf-8"))
            item = normalizer(path, raw_json)
            inserted = _upsert_item(conn, item)
            if inserted:
                counters.rows_inserted += 1
            else:
                counters.rows_updated += 1
            ref_added = upsert_raw_ref(
                conn,
                item.id,
                "single",
                str(path),
                ".",
                item.raw_hash,
                mtime_iso,
                now_utc_iso(),
            )
            if ref_added:
                counters.rows_refs_added += 1
            record_ingest_file(
                conn,
                path=str(path),
                source=source_value,
                run_id=counters.run_id,
                mtime=mtime_iso,
                size=stat.st_size,
                rows_seen=1,
                status="ok",
                error=None,
            )
            counters.files_ok += 1
        except Exception as err:
            logger.exception("Failed ingest for %s", path)
            record_ingest_file(
                conn,
                path=str(path),
                source=source_value,
                run_id=counters.run_id,
                mtime=mtime_iso,
                size=stat.st_size,
                rows_seen=0,
                status="error",
                error=str(err),
            )
            counters.files_failed += 1


def _upsert_item(conn: sqlite3.Connection, item: NormalizedItem) -> bool:
    existing = _get_item(conn, item.id)
    now_ts = now_utc_iso()
    first_seen_ts = now_ts if existing is None else str(existing["first_seen_ts"])
    conn.execute(
        """
        INSERT INTO items (
            id, source, native_id, event_ts, fetched_ts, author, title, body, body_zh,
            url, category, relevance, has_curated, has_bulk, preferred_src, first_seen_ts, last_seen_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source=excluded.source,
            native_id=excluded.native_id,
            event_ts=excluded.event_ts,
            fetched_ts=excluded.fetched_ts,
            author=excluded.author,
            title=excluded.title,
            body=excluded.body,
            body_zh=excluded.body_zh,
            url=excluded.url,
            category=excluded.category,
            relevance=excluded.relevance,
            has_curated=excluded.has_curated,
            has_bulk=excluded.has_bulk,
            preferred_src=excluded.preferred_src,
            first_seen_ts=items.first_seen_ts,
            last_seen_ts=excluded.last_seen_ts
        """,
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
            item.has_curated,
            item.has_bulk,
            item.preferred_src,
            first_seen_ts,
            now_ts,
        ),
    )
    return existing is None


def _get_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, first_seen_ts FROM items WHERE id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "first_seen_ts": row[1]}


def _get_ingest_file(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT path, status, last_mtime, last_size FROM ingest_files WHERE path = ?",
        (path,),
    ).fetchone()
    if row is None:
        return None
    return {
        "path": row[0],
        "status": row[1],
        "last_mtime": row[2],
        "last_size": row[3],
    }


def _iter_files(base_dir: Path) -> Iterable[Path]:
    return sorted(base_dir.glob("*.json"))


def _resolve_sources(sources: list[str] | None) -> list[str]:
    if not sources:
        return ["articles", "tweets"]
    invalid = [source for source in sources if source not in SOURCE_SPECS]
    if invalid:
        raise ValueError(f"Unsupported sources: {', '.join(invalid)}")
    return sources


def _parse_since(since: str | None) -> datetime | None:
    if since is None:
        return None
    dt = datetime.fromisoformat(since)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _stat_mtime_iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
