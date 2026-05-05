from __future__ import annotations

import json
import logging
import os
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from deep_daily.backup.errors import BackupLockHeldError, BackupLockRecoveryRefusedError

logger = logging.getLogger("deep_daily.backup.state")

LOCK_NAME = "backup.lock"
LAST_NAME = "last.json"
HISTORY_NAME = "history.jsonl"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_timestamp(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def archive_timestamp(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%SZ")


def ensure_state_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


@contextmanager
def backup_lock(state_dir: Path, *, force_unlock: bool = False, interactive: bool | None = None) -> Iterator[dict[str, Any]]:
    ensure_state_dir(state_dir)
    lock_path = state_dir / LOCK_NAME
    current = _current_lock_payload()
    acquire_lock(lock_path, current, force_unlock=force_unlock, interactive=interactive)
    try:
        yield current
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("failed to remove lockfile %s", lock_path, exc_info=True)


def acquire_lock(lock_path: Path, payload: dict[str, Any], *, force_unlock: bool = False, interactive: bool | None = None) -> None:
    if force_unlock:
        _write_json_atomic(lock_path, payload)
        return
    if not lock_path.exists():
        _write_json_atomic(lock_path, payload)
        return
    stale_reason = _stale_reason(lock_path)
    if stale_reason is None:
        existing = _read_json(lock_path)
        raise BackupLockHeldError(
            f"lock held by PID {existing.get('pid')} since {existing.get('started_ts')}"
        )
    if stale_reason == "host-mismatch":
        is_interactive = sys.stdout.isatty() if interactive is None else interactive
        if is_interactive:
            raise BackupLockRecoveryRefusedError("stale lock on different host; re-run with --force-unlock")
    logger.warning("recovering stale lock %s (%s)", lock_path, stale_reason)
    _write_json_atomic(lock_path, payload)


def write_last_json(state_dir: Path, payload: dict[str, Any]) -> Path:
    ensure_state_dir(state_dir)
    path = state_dir / LAST_NAME
    _write_json_atomic(path, payload)
    return path


def append_history(state_dir: Path, payload: dict[str, Any], *, max_lines: int = 500) -> Path:
    ensure_state_dir(state_dir)
    path = state_dir / HISTORY_NAME
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    rotate_history(path, max_lines=max_lines)
    return path


def rotate_history(path: Path, *, max_lines: int = 500) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= max_lines:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _current_lock_payload() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "started_ts": utc_timestamp(),
        "host": socket.gethostname(),
        "command": "deep_daily backup",
    }


def _stale_reason(lock_path: Path) -> str | None:
    try:
        payload = _read_json(lock_path)
    except Exception:
        return "corrupt"
    host = str(payload.get("host") or "")
    if host and host != socket.gethostname():
        return "host-mismatch"
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return "corrupt"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead-pid"
    except PermissionError:
        return None
    except OSError:
        return "dead-pid"
    return None


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("json payload must be object")
    return raw


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
