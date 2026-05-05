from __future__ import annotations

import json
from pathlib import Path

from deep_daily.kb.state import KBIngestLock


def test_stale_lock_recovery(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "kb-ingest.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(json.dumps({"pid": 999999, "started_ts": "2026-01-01T00:00:00Z"}))
    lock = KBIngestLock(lock_path)
    lock.acquire()
    assert lock_path.exists()
    lock.release()
    assert not lock_path.exists()
