from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class KBLockHeldError(RuntimeError):
    pass


def now_utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class KBIngestLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._acquired = False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            pid = payload.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                raise KBLockHeldError(f"kb ingest already running with pid={pid}")
            self.lock_path.unlink(missing_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "started_ts": now_utc_iso()}).encode("utf-8"))
        finally:
            os.close(fd)
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            self.lock_path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> "KBIngestLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


def open_ingest_run(db: sqlite3.Connection, *, mode: str, sources: str | None, started_ts: str) -> int:
    cursor = db.execute(
        "INSERT INTO ingest_runs (started_ts, mode, sources, ok) VALUES (?, ?, ?, NULL)",
        (started_ts, mode, sources),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("failed to create ingest_runs row")
    return int(cursor.lastrowid)


def close_ingest_run(db: sqlite3.Connection, run: Any) -> None:
    payload = asdict(run)
    run_id = int(payload.pop("run_id"))
    assignments = ", ".join(f"{key} = ?" for key in payload)
    db.execute(
        f"UPDATE ingest_runs SET {assignments} WHERE run_id = ?",
        tuple(payload.values()) + (run_id,),
    )


def record_ingest_file(
    db: sqlite3.Connection,
    *,
    path: str,
    source: str,
    run_id: int,
    mtime: str,
    size: int,
    rows_seen: int,
    status: str,
    error: str | None,
) -> None:
    db.execute(
        """
        INSERT INTO ingest_files (
            path, source, last_run_id, last_mtime, last_size, rows_seen, status, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            source=excluded.source,
            last_run_id=excluded.last_run_id,
            last_mtime=excluded.last_mtime,
            last_size=excluded.last_size,
            rows_seen=excluded.rows_seen,
            status=excluded.status,
            error=excluded.error
        """,
        (path, source, run_id, mtime, size, rows_seen, status, error),
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
