from __future__ import annotations

import subprocess

import pytest

from deep_daily.backup.errors import BackupChecksumMismatchError
from deep_daily.backup.upload import upload_archive


def test_upload_hashes_part_before_rename(monkeypatch, tmp_path):
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"hello")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ssh" and command[-1].startswith("mkdir -p"):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "ssh" and command[-1].startswith("cat >"):
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        if command[0] == "ssh" and command[-1].startswith("sha256sum"):
            return subprocess.CompletedProcess(command, 0, stdout="abc123  file.part\n", stderr="")
        if command[0] == "ssh" and command[-1].startswith("mv "):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("deep_daily.backup.upload.subprocess.run", fake_run)
    monkeypatch.setattr("deep_daily.backup.retention.subprocess.run", fake_run)

    remote_path, remote_sha = upload_archive(
        archive_path=archive,
        archive_name="m4-deep-daily-20260505-030000Z.tar.gz",
        ssh_target="root@example",
        ssh_options=("BatchMode=yes",),
        remote_dir="/remote/base",
        local_sha256="abc123",
        skip_checksum=False,
    )

    assert remote_sha == "abc123"
    assert remote_path.endswith("m4-deep-daily-20260505-030000Z.tar.gz")
    assert calls[1][-1].endswith(".part'") or ".part" in calls[1][-1]
    assert calls[2][-1].startswith("sha256sum")
    assert calls[3][-1].startswith("mv ")


def test_upload_mismatch_raises_exit_3(monkeypatch, tmp_path):
    archive = tmp_path / "backup.tar.gz"
    archive.write_bytes(b"hello")

    def fake_run(command, **kwargs):
        if command[0] == "ssh" and command[-1].startswith("cat >"):
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        if command[0] == "ssh" and command[-1].startswith("mkdir -p"):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "ssh" and command[-1].startswith("sha256sum"):
            return subprocess.CompletedProcess(command, 0, stdout="deadbeef  file.part\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("deep_daily.backup.upload.subprocess.run", fake_run)
    monkeypatch.setattr("deep_daily.backup.retention.subprocess.run", fake_run)

    with pytest.raises(BackupChecksumMismatchError) as exc:
        upload_archive(
            archive_path=archive,
            archive_name="m4-deep-daily-20260505-030000Z.tar.gz",
            ssh_target="root@example",
            ssh_options=("BatchMode=yes",),
            remote_dir="/remote/base",
            local_sha256="abc123",
            skip_checksum=False,
        )

    assert exc.value.exit_code == 3
