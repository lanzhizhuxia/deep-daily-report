from __future__ import annotations

import argparse
import subprocess

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


def test_backup_empty_dir_returns_1(tmp_path, capsys):
    home = _mk_home(tmp_path, with_data=False)
    args = argparse.Namespace(dry_run=True, retention=None, skip_checksum=False, force_unlock=False)
    rc = cmd_backup(args, home)
    assert rc == 1
    assert "refusing to back up empty dir." in capsys.readouterr().err


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
