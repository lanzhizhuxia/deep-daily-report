from __future__ import annotations

import argparse
import subprocess
import tarfile

from deep_daily.commands.backup_cmd import cmd_backup
from deep_daily.home import HomeConfig


CONFIG = """\
schema_version: 1
reader:
  name: test
backup:
  enabled: true
  nas_host: 127.0.0.1
  nas_user: root
  nas_base: /tmp/backups
  retention: 30
"""


def _mk_home(tmp_path, *, with_data: bool = True) -> HomeConfig:
    (tmp_path / ".deep-daily-home").write_text("ok\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "logs").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    if with_data:
        sample = data_dir / "articles" / "a.json"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_text("{}", encoding="utf-8")
    return HomeConfig.load(tmp_path)


def test_backup_dry_run_end_to_end(monkeypatch, tmp_path, capsys):
    home = _mk_home(tmp_path)
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] deep_daily backup" in out
    assert "remote path:" in out
    assert "would scan remote for .part files older than 48h" in out
    assert "would keep 30 newest matching archives, prune older ones" in out


def test_backup_dry_run_opens_no_sockets_or_subprocess(monkeypatch, tmp_path, capsys):
    home = _mk_home(tmp_path)

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called during dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] deep_daily backup" in out


def test_backup_missing_config_returns_1(tmp_path, capsys):
    (tmp_path / ".deep-daily-home").write_text("ok\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("schema_version: 1\nreader:\n  name: t\n", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    home = HomeConfig.load(tmp_path)
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    assert rc == 1
    assert "backup config missing" in capsys.readouterr().err


def test_backup_empty_data_dir_now_accepted(tmp_path, capsys):
    home = _mk_home(tmp_path, with_data=False)
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] deep_daily backup" in out


def test_backup_lock_held_returns_4(monkeypatch, tmp_path, capsys):
    home = _mk_home(tmp_path)
    monkeypatch.setattr(
        "deep_daily.commands.backup_cmd.backup_lock",
        lambda *args, **kwargs: (_ for _ in ()).throw(__import__("deep_daily.backup.errors", fromlist=["BackupLockHeldError"]).BackupLockHeldError("lock held")),
    )
    args = argparse.Namespace(dry_run=False, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    assert rc == 4
    assert "lock held" in capsys.readouterr().err


def test_backup_dry_run_warns_when_env_present(tmp_path, capsys):
    home = _mk_home(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    captured = capsys.readouterr()
    assert rc == 0
    assert "archive will include .env" in captured.err


def test_backup_produces_home_root_archive_layout(monkeypatch, tmp_path):
    home = _mk_home(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "configs" / "readers.yaml").write_text("readers: []\n", encoding="utf-8")
    (tmp_path / "state" / "backup").mkdir(parents=True)
    (tmp_path / "state" / "backup" / "old.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs" / "run.log").write_text("log\n", encoding="utf-8")

    from contextlib import contextmanager

    @contextmanager
    def fake_lock(*_args, **_kwargs):
        yield None

    monkeypatch.setattr("deep_daily.commands.backup_cmd.backup_lock", fake_lock)
    monkeypatch.setattr(
        "deep_daily.commands.backup_cmd.clean_stale_remote_parts",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "deep_daily.commands.backup_cmd.upload_archive",
        lambda **kwargs: (kwargs["remote_dir"] + "/" + kwargs["archive_name"], kwargs.get("local_sha256")),
    )
    monkeypatch.setattr(
        "deep_daily.commands.backup_cmd.prune_remote_archives",
        lambda **kwargs: None,
    )

    from deep_daily.commands import backup_cmd as mod

    preserved: dict[str, str] = {}
    real_make_archive = mod.make_archive

    def make_and_copy(source_dir, archive_path, excludes):
        size = real_make_archive(source_dir, archive_path, excludes)
        copy_path = archive_path.with_suffix(archive_path.suffix + ".copy")
        copy_path.write_bytes(archive_path.read_bytes())
        preserved["copy"] = str(copy_path)
        return size

    monkeypatch.setattr(mod, "make_archive", make_and_copy)

    args = argparse.Namespace(dry_run=False, retention=None, skip_checksum=True, force_unlock=False)
    rc = cmd_backup(args, home)
    assert rc == 0, f"cmd_backup failed: rc={rc}"

    with tarfile.open(preserved["copy"], "r:gz") as tar:
        names = tar.getnames()

    assert "./config.yaml" in names
    assert "./.deep-daily-home" in names
    assert "./.env" in names
    assert "./configs/readers.yaml" in names
    assert not any(n.startswith("./state/") for n in names)
    assert not any(n.startswith("./logs/") for n in names)
