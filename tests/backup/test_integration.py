from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import uuid
from pathlib import Path

import pytest


def test_ssh_localhost_round_trip(tmp_path: Path):
    if os.environ.get("DEEP_DAILY_BACKUP_IT") != "1":
        pytest.skip("set DEEP_DAILY_BACKUP_IT=1 to run live SSH tests")

    source = tmp_path / "source.bin"
    source.write_bytes(os.urandom(1024))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    remote_base = f"/tmp/deep-daily-backup-it-{uuid.uuid4().hex}"
    remote_part = f"{remote_base}.part"
    remote_final = f"{remote_base}.tar.gz"

    subprocess.run(
        ["ssh", "localhost", f"rm -f {shlex.quote(remote_part)} {shlex.quote(remote_final)}"],
        check=False,
        capture_output=True,
        text=True,
    )
    with source.open("rb") as handle:
        uploaded = subprocess.run(
            ["ssh", "localhost", f"cat > {shlex.quote(remote_part)}"],
            stdin=handle,
            check=False,
            capture_output=True,
            text=False,
        )
    assert uploaded.returncode == 0, uploaded.stderr.decode(errors="replace")

    hashed = subprocess.run(
        ["ssh", "localhost", f"sha256sum {shlex.quote(remote_part)}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert hashed.returncode == 0, hashed.stderr
    assert hashed.stdout.strip().split()[0] == digest

    renamed = subprocess.run(
        ["ssh", "localhost", f"mv {shlex.quote(remote_part)} {shlex.quote(remote_final)}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert renamed.returncode == 0, renamed.stderr

    verified = subprocess.run(
        ["ssh", "localhost", f"sha256sum {shlex.quote(remote_final)}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.strip().split()[0] == digest

    cleanup = subprocess.run(
        ["ssh", "localhost", f"rm -f {shlex.quote(remote_final)}"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert cleanup.returncode == 0, cleanup.stderr
