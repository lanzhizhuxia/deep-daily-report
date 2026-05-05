from __future__ import annotations

import tarfile

import pytest

from deep_daily.backup.archive import ensure_valid_home, make_archive
from deep_daily.backup.errors import BackupValidationError
from deep_daily.home import HomeConfig


CONFIG = """\
schema_version: 1
reader:
  name: test
"""


def _mk_home(tmp_path, *, with_data: bool = True):
    (tmp_path / ".deep-daily-home").write_text("ok\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "logs").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    if with_data:
        (data_dir / "articles").mkdir()
        (data_dir / "articles" / "keep.json").write_text("{}", encoding="utf-8")
    return HomeConfig.load(tmp_path)


def test_make_archive_rooted_at_home_with_home_files(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / ".deep-daily-home").write_text("ok\n", encoding="utf-8")
    (home_dir / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (home_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (home_dir / "configs").mkdir()
    (home_dir / "configs" / "readers.yaml").write_text("readers: []\n", encoding="utf-8")
    data_dir = home_dir / "data"
    data_dir.mkdir()
    (data_dir / "articles").mkdir()
    (data_dir / "articles" / "keep.json").write_text("{}", encoding="utf-8")

    archive_path = tmp_path / "backup.tar.gz"
    make_archive(home_dir, archive_path, ())

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()

    assert "./config.yaml" in names
    assert "./.deep-daily-home" in names
    assert "./.env" in names
    assert "./configs/readers.yaml" in names
    assert "./data/articles/keep.json" in names


def test_make_archive_applies_default_excludes_for_state_and_logs(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    data_dir = home_dir / "data"
    data_dir.mkdir()
    keep_file = data_dir / "articles" / "keep.json"
    pipeline_file = data_dir / "dailies" / ".pipeline" / "2026-05-05" / "cache.json"
    dryrun_file = data_dir / "dailies-dryrun" / "out.html"
    session_file = data_dir / ".session-memory" / "memo.txt"
    state_file = home_dir / "state" / "backup" / "last.json"
    logs_file = home_dir / "logs" / "run.log"
    for path in [keep_file, pipeline_file, dryrun_file, session_file, state_file, logs_file]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    archive_path = tmp_path / "backup.tar.gz"
    make_archive(
        home_dir,
        archive_path,
        (
            "state/**",
            "logs/**",
            "data/dailies/.pipeline/**",
            "data/dailies-dryrun/**",
            "data/.session-memory/**",
        ),
    )

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()

    assert "./data/articles/keep.json" in names
    assert "./config.yaml" in names
    assert "./data/dailies/.pipeline/2026-05-05/cache.json" not in names
    assert "./data/dailies-dryrun/out.html" not in names
    assert "./data/.session-memory/memo.txt" not in names
    assert "./state/backup/last.json" not in names
    assert "./logs/run.log" not in names


def test_ensure_valid_home_accepts_empty_data_dir(tmp_path):
    home = _mk_home(tmp_path, with_data=False)
    total_bytes, file_count = ensure_valid_home(home, ())
    assert file_count >= 2
    assert total_bytes > 0


def test_ensure_valid_home_rejects_missing_sentinel(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "configs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / ".deep-daily-home").write_text("ok\n", encoding="utf-8")
    home = HomeConfig.load(tmp_path)
    (tmp_path / ".deep-daily-home").unlink()

    with pytest.raises(BackupValidationError, match="missing .deep-daily-home"):
        ensure_valid_home(home, ())
