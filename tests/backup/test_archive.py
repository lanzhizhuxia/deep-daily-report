from __future__ import annotations

import tarfile

from deep_daily.backup.archive import ensure_non_empty_data_dir, make_archive


def test_make_archive_excludes_configured_paths(tmp_path):
    data_dir = tmp_path / "data"
    keep_file = data_dir / "articles" / "keep.json"
    excluded_file = data_dir / "dailies" / ".pipeline" / "2026-05-05" / "cache.json"
    dryrun_file = data_dir / "dailies-dryrun" / "out.html"
    session_file = data_dir / ".session-memory" / "memo.txt"
    for path in [keep_file, excluded_file, dryrun_file, session_file]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    archive_path = tmp_path / "backup.tar.gz"
    make_archive(
        data_dir,
        archive_path,
        ("dailies/.pipeline/**", "dailies-dryrun/**", ".session-memory/**"),
    )

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()

    assert "./articles/keep.json" in names
    assert "./dailies/.pipeline/2026-05-05/cache.json" not in names
    assert "./dailies-dryrun/out.html" not in names
    assert "./.session-memory/memo.txt" not in names


def test_ensure_non_empty_data_dir_rejects_empty_after_exclusions(tmp_path):
    data_dir = tmp_path / "data"
    only_excluded = data_dir / "dailies-dryrun" / "x.txt"
    only_excluded.parent.mkdir(parents=True, exist_ok=True)
    only_excluded.write_text("x", encoding="utf-8")

    try:
        ensure_non_empty_data_dir(data_dir, ("dailies-dryrun/**",))
    except Exception as err:
        assert str(err) == "refusing to back up empty dir."
    else:
        raise AssertionError("expected empty-dir validation error")
