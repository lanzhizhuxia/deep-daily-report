from __future__ import annotations

import json
import socket

import pytest

from deep_daily.backup.errors import BackupLockHeldError, BackupLockRecoveryRefusedError
from deep_daily.backup.state import append_history, backup_lock, write_last_json


def test_stale_lock_dead_pid_recovers(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lock = state_dir / "backup.lock"
    lock.write_text(
        json.dumps({"pid": 123, "started_ts": "2026-05-05T03:00:00Z", "host": socket.gethostname()}),
        encoding="utf-8",
    )

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr("deep_daily.backup.state.os.kill", fake_kill)
    with backup_lock(state_dir, interactive=False):
        assert lock.exists()
    assert not lock.exists()


def test_live_lock_raises(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lock = state_dir / "backup.lock"
    lock.write_text(
        json.dumps({"pid": 123, "started_ts": "2026-05-05T03:00:00Z", "host": socket.gethostname()}),
        encoding="utf-8",
    )
    monkeypatch.setattr("deep_daily.backup.state.os.kill", lambda pid, sig: None)
    with pytest.raises(BackupLockHeldError):
        with backup_lock(state_dir, interactive=False):
            pass


def test_corrupt_lock_treated_as_stale(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "backup.lock").write_text("{broken", encoding="utf-8")
    with backup_lock(state_dir, interactive=False):
        pass


def test_cross_host_refuses_in_interactive_mode(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lock = state_dir / "backup.lock"
    lock.write_text(
        json.dumps({"pid": 123, "started_ts": "2026-05-05T03:00:00Z", "host": "other-host"}),
        encoding="utf-8",
    )
    with pytest.raises(BackupLockRecoveryRefusedError):
        with backup_lock(state_dir, interactive=True):
            pass


def test_last_json_atomic_write_and_history_rotation(tmp_path):
    state_dir = tmp_path / "state"
    payload = {"ok": True}
    last = write_last_json(state_dir, payload)
    assert json.loads(last.read_text(encoding="utf-8")) == payload
    assert not (state_dir / "last.json.tmp").exists()

    for idx in range(505):
        append_history(state_dir, {"idx": idx}, max_lines=500)
    history_lines = (state_dir / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(history_lines) == 500
    assert json.loads(history_lines[0])["idx"] == 5
