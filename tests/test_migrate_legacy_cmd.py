"""Tests for ``deep-daily migrate-legacy`` — PLAN v2.1 §7 Step 18, §12.1.

Oracle v3 addendum contracts covered:
  - Manifest is a snapshot; resume re-hashes and aborts on source drift.
  - LOCK_EX on source/reported_events.json.lock (contention blocks precondition).
  - Symlinks in source → abort.
  - --dry-run is print-only, no manifest artifact.
  - Two error classes: Precondition (rc=2), State (rc=1).
  - Status: in_progress | completed (top-level field, not inferred).
  - Target empty-or-resumable semantics.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
from pathlib import Path

import pytest

from deep_daily.commands import migrate_legacy_cmd as mlc
from deep_daily.home import HomeConfig


def _mk_home_empty_data(tmp_path: Path) -> HomeConfig:
    """Build a valid HOME whose data/ is empty — migrate-legacy target shape."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".deep-daily-home").write_text("schema_version: 1\n")
    (home / "config.yaml").write_text(
        "schema_version: 1\nreader:\n  name: test\nllm:\n  backend: openai\n"
    )
    (home / "configs").mkdir()
    (home / "data").mkdir()
    (home / "logs").mkdir()
    return HomeConfig.load(home)


def _seed_legacy_source(src: Path, *, extra_files: dict[str, str] | None = None) -> None:
    """Populate a plausible legacy source tree."""
    src.mkdir(parents=True, exist_ok=True)
    (src / "articles").mkdir()
    (src / "articles" / "2025-01-01.json").write_text('{"a": 1}', encoding="utf-8")
    (src / "articles" / "2025-01-02.json").write_text('{"a": 2}', encoding="utf-8")
    (src / "tweets").mkdir()
    (src / "tweets" / "t.json").write_text('{"t": 1}', encoding="utf-8")
    (src / "reported_events.json").write_text('{"events": []}', encoding="utf-8")
    # Backdate reported_events so freshness check passes
    old = _dt.datetime.now().timestamp() - 3600
    os.utime(src / "reported_events.json", (old, old))
    # Noise that must be excluded
    (src / "articles" / "scratch.tmp").write_text("tmp", encoding="utf-8")
    (src / ".pipeline").mkdir()
    (src / ".pipeline" / "cache.json").write_text('{"c": 1}', encoding="utf-8")
    if extra_files:
        for rel, content in extra_files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        from_path=None,
        force=False,
        dry_run=False,
        confirm_near_schedule=True,  # bypass launchd-window guard for tests
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_dry_run_prints_plan_and_writes_no_manifest(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    rc = mlc.cmd_migrate_legacy(
        _args(from_path=str(src), dry_run=True), home
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "articles/" in out
    manifest_path = home.data_dir / mlc.MANIFEST_FILENAME
    assert not manifest_path.exists(), "dry-run must not write manifest"


@pytest.mark.hard_gate_3
def test_end_to_end_copies_runtime_data_and_writes_completed_manifest(tmp_path):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 0

    # Runtime files copied
    assert (home.data_dir / "articles" / "2025-01-01.json").read_text() == '{"a": 1}'
    assert (home.data_dir / "articles" / "2025-01-02.json").read_text() == '{"a": 2}'
    assert (home.data_dir / "tweets" / "t.json").read_text() == '{"t": 1}'
    assert (home.data_dir / "reported_events.json").read_text() == '{"events": []}'

    # Excluded files NOT copied
    assert not (home.data_dir / "articles" / "scratch.tmp").exists()
    assert not (home.data_dir / ".pipeline").exists()
    assert not (home.data_dir / "reported_events.json.lock").exists()

    # Manifest status = completed
    manifest = json.loads((home.data_dir / mlc.MANIFEST_FILENAME).read_text())
    assert manifest["status"] == "completed"
    assert manifest["completed_at"] is not None
    assert all(e["status"] == "verified" for e in manifest["entries"])


# ---------------------------------------------------------------------------
# Precondition guards
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_missing_source_returns_precondition_error(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    rc = mlc.cmd_migrate_legacy(
        _args(from_path=str(tmp_path / "nonexistent")), home
    )
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


@pytest.mark.hard_gate_3
def test_symlinked_file_in_source_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)
    # Introduce a symlink
    target_real = tmp_path / "elsewhere.json"
    target_real.write_text("external", encoding="utf-8")
    (src / "articles" / "linked.json").symlink_to(target_real)

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    assert "symlink" in capsys.readouterr().err.lower()


@pytest.mark.hard_gate_3
def test_source_lock_held_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    lock_path = src / "reported_events.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()
    fd = os.open(str(lock_path), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert rc == 2
    assert "lock" in capsys.readouterr().err.lower()


@pytest.mark.hard_gate_3
def test_recent_reported_events_mtime_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)
    # Re-touch to now — freshness check should fire
    (src / "reported_events.json").touch()

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "reported_events" in err and "ago" in err


@pytest.mark.hard_gate_3
def test_launchd_window_requires_confirmation(tmp_path, capsys, monkeypatch):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    # Force inside-window
    monkeypatch.setattr(mlc, "_inside_launchd_window", lambda now=None: True)
    rc = mlc.cmd_migrate_legacy(
        _args(from_path=str(src), confirm_near_schedule=False), home
    )
    assert rc == 2
    assert "launchd" in capsys.readouterr().err.lower()


@pytest.mark.hard_gate_3
def test_launchd_window_bypassed_by_confirm_flag(tmp_path, monkeypatch):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    monkeypatch.setattr(mlc, "_inside_launchd_window", lambda now=None: True)
    rc = mlc.cmd_migrate_legacy(
        _args(from_path=str(src), confirm_near_schedule=True), home
    )
    assert rc == 0


# ---------------------------------------------------------------------------
# Target state rules
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_target_nonempty_without_manifest_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)
    # Plant unrelated content in target
    (home.data_dir / "stranger.txt").write_text("not ours", encoding="utf-8")

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    assert "not empty" in capsys.readouterr().err.lower()


@pytest.mark.hard_gate_3
def test_completed_target_refuses_without_force(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    # First run completes
    assert mlc.cmd_migrate_legacy(_args(from_path=str(src)), home) == 0

    # Second run without --force
    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    assert "completed" in capsys.readouterr().err.lower()


@pytest.mark.hard_gate_3
def test_completed_target_allows_force(tmp_path):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    assert mlc.cmd_migrate_legacy(_args(from_path=str(src)), home) == 0
    assert mlc.cmd_migrate_legacy(_args(from_path=str(src), force=True), home) == 0


# ---------------------------------------------------------------------------
# Resume semantics (Oracle v3 item 1: manifest is snapshot, re-hash on resume)
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_resume_with_source_drift_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    # Build a manifest as if a previous run stopped after snapshotting
    rel_paths = mlc._walk_source_files(src)
    manifest = mlc._build_manifest_snapshot(src, home.data_dir, rel_paths)
    manifest_path = home.data_dir / mlc.MANIFEST_FILENAME
    home.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Mutate a source file — drift
    (src / "articles" / "2025-01-01.json").write_text('{"a": 999}', encoding="utf-8")

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "drift" in err


@pytest.mark.hard_gate_3
def test_resume_with_new_source_file_aborts(tmp_path, capsys):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    rel_paths = mlc._walk_source_files(src)
    manifest = mlc._build_manifest_snapshot(src, home.data_dir, rel_paths)
    manifest_path = home.data_dir / mlc.MANIFEST_FILENAME
    home.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Add a NEW file to source after snapshot
    (src / "articles" / "2025-01-03.json").write_text('{"new": 1}', encoding="utf-8")

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "new files" in err or "gained" in err


@pytest.mark.hard_gate_3
def test_resume_with_clean_snapshot_completes(tmp_path):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    # Seed manifest (all pending) — simulates a crash BEFORE any entry was verified
    rel_paths = mlc._walk_source_files(src)
    manifest = mlc._build_manifest_snapshot(src, home.data_dir, rel_paths)
    manifest_path = home.data_dir / mlc.MANIFEST_FILENAME
    home.data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 0
    final = json.loads(manifest_path.read_text())
    assert final["status"] == "completed"
    assert all(e["status"] == "verified" for e in final["entries"])


@pytest.mark.hard_gate_3
def test_resume_skips_already_verified_entries(tmp_path, monkeypatch):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    rel_paths = mlc._walk_source_files(src)
    manifest = mlc._build_manifest_snapshot(src, home.data_dir, rel_paths)

    # Mark first entry as already verified + physically place the file
    home.data_dir.mkdir(parents=True, exist_ok=True)
    verified_entry = manifest.entries[0]
    src_file = src / verified_entry.rel_path
    dst_file = home.data_dir / verified_entry.rel_path
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    dst_file.write_text(src_file.read_text(), encoding="utf-8")
    verified_entry.status = "verified"

    manifest_path = home.data_dir / mlc.MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Track _copy_and_verify calls to confirm verified entry is skipped
    calls: list[str] = []
    real_copy = mlc._copy_and_verify

    def tracking_copy(source, target, entry, manifest_obj, mpath):
        calls.append(entry.rel_path)
        return real_copy(source, target, entry, manifest_obj, mpath)

    monkeypatch.setattr(mlc, "_copy_and_verify", tracking_copy)

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 0
    assert verified_entry.rel_path not in calls
    # All other entries should have been copied
    assert len(calls) == len(manifest.entries) - 1


# ---------------------------------------------------------------------------
# State-error surface (Oracle v3 item 6)
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_sha_mismatch_during_copy_returns_state_error(tmp_path, capsys, monkeypatch):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    # Force the post-copy sha256 to mismatch by patching _sha256_of_file
    # to return a constant AFTER snapshot is built (track call count)
    real_sha = mlc._sha256_of_file
    call_state = {"n": 0}

    def flaky_sha(path):
        call_state["n"] += 1
        real_hash = real_sha(path)
        # After the snapshot is fully hashed (~ n files + 1 for the first copy verify)
        # we corrupt a later verification only.
        if call_state["n"] > 100:  # never triggers here; we use a direct patch below
            return "deadbeef" * 8
        return real_hash

    # Simpler path: directly monkey-patch shutil.copy2 to write wrong bytes
    import shutil as _shutil

    original_copy2 = _shutil.copy2
    corrupted_once = {"done": False}

    def corrupting_copy2(src_p, dst_p, *a, **kw):
        result = original_copy2(src_p, dst_p, *a, **kw)
        if not corrupted_once["done"] and str(dst_p).endswith(".json"):
            Path(dst_p).write_text("CORRUPTED", encoding="utf-8")
            corrupted_once["done"] = True
        return result

    monkeypatch.setattr(mlc.shutil, "copy2", corrupting_copy2)

    rc = mlc.cmd_migrate_legacy(_args(from_path=str(src)), home)
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "sha256 mismatch" in err or "aborted mid-flight" in err


# ---------------------------------------------------------------------------
# Manifest hygiene
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_manifest_schema_shape(tmp_path):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    assert mlc.cmd_migrate_legacy(_args(from_path=str(src)), home) == 0

    manifest = json.loads((home.data_dir / mlc.MANIFEST_FILENAME).read_text())
    assert manifest["schema_version"] == 1
    for key in (
        "source_path",
        "target_path",
        "tool_version",
        "started_at",
        "completed_at",
        "status",
        "entries",
    ):
        assert key in manifest, f"missing manifest key: {key}"
    assert manifest["status"] == "completed"
    assert isinstance(manifest["entries"], list)
    for entry in manifest["entries"]:
        assert set(entry.keys()) >= {"rel_path", "src_sha256", "size", "status"}


@pytest.mark.hard_gate_3
def test_excluded_patterns_never_in_manifest(tmp_path):
    home = _mk_home_empty_data(tmp_path)
    src = tmp_path / "legacy"
    _seed_legacy_source(src)

    assert mlc.cmd_migrate_legacy(_args(from_path=str(src)), home) == 0
    manifest = json.loads((home.data_dir / mlc.MANIFEST_FILENAME).read_text())
    rel_paths = {e["rel_path"] for e in manifest["entries"]}
    assert "reported_events.json" not in rel_paths  # copied separately under LOCK_EX
    assert not any(r.endswith(".tmp") for r in rel_paths)
    assert not any(r.startswith(".pipeline/") for r in rel_paths)
    assert not any(".pipeline/" in r for r in rel_paths)
