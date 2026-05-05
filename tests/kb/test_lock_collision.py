from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.slow
def test_kb_ingest_lock_collision_and_stale_pid_recovery(tmp_home: Path) -> None:
    (tmp_home / "data" / "articles" / "hold.json").write_text(
        json.dumps(
            {
                "id": "hold-1",
                "link": "https://example.com/hold-1",
                "summary_zh": "hold",
                "fetched_at": "2026-01-01T00:00:00",
                "feed_title": "feed",
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    src_path = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_path

    lock_path = tmp_home / "state" / "kb-ingest.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, time; "
                "from deep_daily.kb.state import KBIngestLock; "
                "lock = KBIngestLock(pathlib.Path(r'%s')); "
                "lock.acquire(); "
                "time.sleep(60)"
            )
            % str(lock_path),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if lock_path.exists():
                break
            time.sleep(0.1)
        assert lock_path.exists(), "holder never acquired kb-ingest.lock"

        second = subprocess.run(
            [sys.executable, "-m", "deep_daily", "--home", str(tmp_home), "kb", "ingest"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert second.returncode == 1
        assert "Lock held:" in second.stderr
        assert "kb ingest already running with pid=" in second.stderr

        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=10)

        third = subprocess.run(
            [sys.executable, "-m", "deep_daily", "--home", str(tmp_home), "kb", "ingest"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert third.returncode == 0, third.stderr
        assert not lock_path.exists()
    finally:
        if holder.poll() is None:
            holder.send_signal(signal.SIGKILL)
            holder.wait(timeout=10)
