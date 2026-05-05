"""``deep-daily migrate-legacy`` — copy legacy runtime data into a new HOME.

Per PLAN v2.1 §5.3, §5.4, §7 Step 18, §12.1 (Oracle v3 addendum).

Scope (Oracle v3 scope-cut): RUNTIME DATA ONLY. Business configs
(topics.yaml, sources.yaml, kols.json, etc.) are copied manually by the user
before cutover — see docs/migration-guide.md. A --legacy-configs-from flag
is deferred to a follow-up if one-pass becomes necessary.

Safety contracts (Oracle v3 locked):
  1. Manifest is a SNAPSHOT. On resume, every source file is re-hashed; any
     drift from the manifest → MigrationPreconditionError. Never carry a
     stale source dataset forward silently.
  2. reported_events.json is copied LAST, under fcntl LOCK_EX on
     <source>/reported_events.json.lock. LOCK_SH is insufficient against a
     concurrent pipeline writer.
  3. Symlinks in source → abort. Source tree is known to be plain files.
  4. shutil.copy2 (content + mtime + mode). No xattrs.
  5. --dry-run is print-only. No dry-run manifest artifact.
  6. Two error classes: MigrationPreconditionError (user fixes and retries)
     and MigrationStateError (mid-flight failure, manifest is truth).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deep_daily import __version__
from deep_daily.home import HomeConfig


DEFAULT_LEGACY_SOURCE = "~/.local/deep-daily/legacy-data"
MANIFEST_FILENAME = "MIGRATION-MANIFEST.json"
EXCLUDED_FILE_PATTERNS = (
    "reported_events.json",
    "reported_events.json.lock",
)
EXCLUDED_NAME_SUFFIXES = (".tmp", ".lock")
EXCLUDED_DIR_NAMES = frozenset({".pipeline"})
FRESHNESS_SECONDS = 120
LAUNCHD_WINDOW_START = (6, 45)
LAUNCHD_WINDOW_END = (7, 15)

MANIFEST_SCHEMA_VERSION = 1


class MigrationPreconditionError(RuntimeError):
    """Raised BEFORE any destructive work. User fixes and re-invokes."""


class MigrationStateError(RuntimeError):
    """Raised MID-FLIGHT. Target state may be partial; manifest is the truth."""


@dataclass
class ManifestEntry:
    rel_path: str
    src_sha256: str
    size: int
    status: str = "pending"  # pending | copied | verified

    def to_dict(self) -> dict:
        return {
            "rel_path": self.rel_path,
            "src_sha256": self.src_sha256,
            "size": self.size,
            "status": self.status,
        }


@dataclass
class Manifest:
    schema_version: int
    source_path: str
    target_path: str
    tool_version: str
    started_at: str
    completed_at: str | None
    status: str  # in_progress | completed
    entries: list[ManifestEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "tool_version": self.tool_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Manifest":
        if not isinstance(raw, dict):
            raise MigrationStateError(f"manifest is not a JSON object: {type(raw).__name__}")
        if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise MigrationStateError(
                f"manifest schema_version={raw.get('schema_version')!r} "
                f"(expected {MANIFEST_SCHEMA_VERSION})"
            )
        return cls(
            schema_version=raw["schema_version"],
            source_path=raw["source_path"],
            target_path=raw["target_path"],
            tool_version=raw.get("tool_version", ""),
            started_at=raw.get("started_at", ""),
            completed_at=raw.get("completed_at"),
            status=raw.get("status", "in_progress"),
            entries=[
                ManifestEntry(
                    rel_path=e["rel_path"],
                    src_sha256=e["src_sha256"],
                    size=int(e["size"]),
                    status=str(e.get("status", "pending")),
                )
                for e in raw.get("entries", [])
            ],
        )


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    # 64 KiB blocks: balances syscall overhead vs memory for ~6500 small files.
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_excluded(rel_path: Path) -> bool:
    for part in rel_path.parts:
        if part in EXCLUDED_DIR_NAMES:
            return True
    name = rel_path.name
    if name in EXCLUDED_FILE_PATTERNS:
        return True
    for suffix in EXCLUDED_NAME_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _inside_launchd_window(now: _dt.datetime | None = None) -> bool:
    now = now or _dt.datetime.now()
    start = now.replace(hour=LAUNCHD_WINDOW_START[0], minute=LAUNCHD_WINDOW_START[1], second=0, microsecond=0)
    end = now.replace(hour=LAUNCHD_WINDOW_END[0], minute=LAUNCHD_WINDOW_END[1], second=0, microsecond=0)
    return start <= now <= end


def _assert_no_symlinks(source: Path, rel_path: Path) -> None:
    current = source
    for part in rel_path.parts:
        current = current / part
        if current.is_symlink():
            raise MigrationPreconditionError(
                f"Refusing to follow symlink: {current}. Resolve it manually "
                f"in the source tree and re-run migrate-legacy."
            )


def _walk_source_files(source: Path) -> list[Path]:
    """Return all regular files under source as paths relative to source.

    Oracle v3 §12.1 item 3: any symlink encountered aborts the migration.
    """
    rels: list[Path] = []
    for root, dirs, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        # Skip excluded directories in-place.
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]
        for d in dirs:
            full = root_path / d
            if full.is_symlink():
                raise MigrationPreconditionError(
                    f"Refusing to follow directory symlink: {full}"
                )
        for f in files:
            full = root_path / f
            if full.is_symlink():
                raise MigrationPreconditionError(
                    f"Refusing to follow file symlink: {full}"
                )
            rel = full.relative_to(source)
            if _is_excluded(rel):
                continue
            rels.append(rel)
    return rels


def _probe_source_lock(lock_path: Path) -> None:
    """Acquire and immediately release LOCK_EX | LOCK_NB on the source lock file.

    Contention → MigrationPreconditionError: a legacy pipeline run is active.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as err:
        raise MigrationPreconditionError(
            f"Cannot open lock file {lock_path} for probe: {err}"
        ) from err
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise MigrationPreconditionError(
                f"Source lock is held: {lock_path}. A legacy pipeline run is "
                f"active. Wait for it to finish (or `launchctl bootout` the "
                f"job) and re-run."
            ) from err
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _assert_reported_events_idle(source: Path) -> None:
    re_path = source / "reported_events.json"
    if not re_path.exists():
        return
    age = _dt.datetime.now().timestamp() - re_path.stat().st_mtime
    if age < FRESHNESS_SECONDS:
        raise MigrationPreconditionError(
            f"reported_events.json was modified {age:.0f}s ago "
            f"(< {FRESHNESS_SECONDS}s threshold). The legacy pipeline may "
            f"have just finished a run. Wait a moment and retry."
        )


def _assert_target_empty_or_resumable(
    target: Path, *, force: bool, manifest_path: Path
) -> Manifest | None:
    """Return an existing Manifest if target is a valid in-progress target.

    Rules (Oracle v3):
      - Empty target → proceed, return None.
      - Target contains only MIGRATION-MANIFEST.json → proceed in resume mode.
      - Target contains files AND a manifest with status=in_progress → resume mode.
      - Target contains files AND no manifest → stranger, never overwrite.
      - Target contains files AND manifest status=completed → refuse without --force.
    """
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        return None

    if not target.is_dir():
        raise MigrationPreconditionError(f"Target is not a directory: {target}")

    entries = [p for p in target.iterdir() if p.name != ".DS_Store"]
    if not entries:
        return None

    if not manifest_path.exists():
        raise MigrationPreconditionError(
            f"Target is not empty but has no {MANIFEST_FILENAME}: {target}. "
            f"Refusing to touch an unrelated directory."
        )

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise MigrationStateError(
            f"Manifest exists but cannot be parsed ({manifest_path}): {err}"
        ) from err

    manifest = Manifest.from_dict(raw)

    if manifest.status == "completed":
        if force:
            return None
        raise MigrationPreconditionError(
            f"Target was previously migrated (manifest status=completed): {target}. "
            f"Pass --force to re-run into the same target."
        )

    if manifest.status == "in_progress":
        return manifest

    raise MigrationStateError(
        f"Manifest status is {manifest.status!r} (expected in_progress|completed)"
    )


def _build_manifest_snapshot(
    source: Path,
    target: Path,
    rel_paths: list[Path],
) -> Manifest:
    entries: list[ManifestEntry] = []
    for rel in sorted(rel_paths):
        full = source / rel
        size = full.stat().st_size
        sha = _sha256_of_file(full)
        entries.append(ManifestEntry(rel_path=str(rel), src_sha256=sha, size=size))
    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        source_path=str(source),
        target_path=str(target),
        tool_version=__version__,
        started_at=_now(),
        completed_at=None,
        status="in_progress",
        entries=entries,
    )


def _verify_resume_snapshot_matches_source(manifest: Manifest, source: Path) -> None:
    """Re-hash every manifest entry's source; abort on any drift.

    Oracle v3 §12.1 item 1: the manifest is a snapshot, not a live plan.
    """
    if Path(manifest.source_path) != source:
        raise MigrationPreconditionError(
            f"Manifest source_path={manifest.source_path!r} does not match "
            f"--from {source}. Refusing to resume against a different source."
        )
    drifted: list[str] = []
    missing: list[str] = []
    for entry in manifest.entries:
        full = source / entry.rel_path
        if not full.exists():
            missing.append(entry.rel_path)
            continue
        sha = _sha256_of_file(full)
        if sha != entry.src_sha256:
            drifted.append(entry.rel_path)
    if missing or drifted:
        msg_parts = []
        if missing:
            msg_parts.append(f"{len(missing)} manifest entries no longer exist in source (e.g. {missing[:3]})")
        if drifted:
            msg_parts.append(f"{len(drifted)} manifest entries drifted since snapshot (e.g. {drifted[:3]})")
        raise MigrationPreconditionError(
            "Source drift detected on resume: "
            + "; ".join(msg_parts)
            + ". Delete the target and restart migrate-legacy to take a fresh snapshot."
        )


def _copy_and_verify(
    source: Path, target: Path, entry: ManifestEntry, manifest: Manifest, manifest_path: Path
) -> None:
    src_file = source / entry.rel_path
    dst_file = target / entry.rel_path
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src_file, dst_file)
    except OSError as err:
        raise MigrationStateError(f"Copy failed for {entry.rel_path}: {err}") from err
    actual = _sha256_of_file(dst_file)
    if actual != entry.src_sha256:
        raise MigrationStateError(
            f"sha256 mismatch on {entry.rel_path}: "
            f"expected {entry.src_sha256}, got {actual}"
        )
    entry.status = "verified"
    _atomic_write_json(manifest_path, manifest.to_dict())


def _copy_reported_events_under_lock(source: Path, target: Path) -> None:
    """Copy reported_events.json and its lock file while holding LOCK_EX."""
    lock_path = source / "reported_events.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise MigrationPreconditionError(
                "Lost race for reported_events.json.lock between preflight and "
                "final copy. Re-run migrate-legacy when the pipeline is idle."
            ) from err
        _assert_reported_events_idle(source)
        src = source / "reported_events.json"
        if not src.exists():
            return
        dst = target / "reported_events.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_sha = _sha256_of_file(src)
        try:
            shutil.copy2(src, dst)
        except OSError as err:
            raise MigrationStateError(f"reported_events.json copy failed: {err}") from err
        dst_sha = _sha256_of_file(dst)
        if dst_sha != src_sha:
            raise MigrationStateError(
                f"reported_events.json sha256 mismatch after copy: "
                f"expected {src_sha}, got {dst_sha}"
            )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _print_plan(manifest: Manifest) -> None:
    total_bytes = sum(e.size for e in manifest.entries)
    print(f"DRY RUN migrate-legacy plan")
    print(f"  source: {manifest.source_path}")
    print(f"  target: {manifest.target_path}")
    print(f"  files : {len(manifest.entries)}")
    print(f"  bytes : {total_bytes}")
    print(f"  plus  : reported_events.json (copied under LOCK_EX)")
    by_top: dict[str, int] = {}
    for entry in manifest.entries:
        top = entry.rel_path.split("/", 1)[0]
        by_top[top] = by_top.get(top, 0) + 1
    for top, count in sorted(by_top.items(), key=lambda kv: -kv[1]):
        print(f"    {top}/: {count} files")


def _print_cutover_runbook(home: HomeConfig) -> None:
    print()
    print("Migration complete. Next steps:")
    print(f"  1. deep-daily --home {home.path} doctor")
    print(f"  2. deep-daily --home {home.path} run --date $(date +%%F) --dry-run")
    print(f"  3. Diff dry-run output against legacy output.")
    print(f"  4. Stop the legacy launchd job:")
    print(f"       launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist")
    print(f"  5. Update the plist to export DEEP_DAILY_HOME={home.path} and re-bootstrap.")
    print(f"  6. Keep the source tree at {DEFAULT_LEGACY_SOURCE} for 30 days as rollback.")


def run_migrate_legacy(
    home: HomeConfig,
    *,
    source_path: Path,
    force: bool,
    dry_run: bool,
    confirm_near_schedule: bool,
) -> int:
    source = source_path.expanduser().resolve()
    if not source.exists():
        raise MigrationPreconditionError(f"Legacy source does not exist: {source}")
    if not source.is_dir():
        raise MigrationPreconditionError(f"Legacy source is not a directory: {source}")

    target = home.data_dir.resolve()
    manifest_path = target / MANIFEST_FILENAME

    _probe_source_lock(source / "reported_events.json.lock")
    _assert_reported_events_idle(source)

    if _inside_launchd_window() and not confirm_near_schedule:
        raise MigrationPreconditionError(
            "Inside launchd window (06:45-07:15). The legacy pipeline may "
            "fire imminently. Pass --confirm-near-schedule to proceed or "
            "wait until after 07:15."
        )

    existing = _assert_target_empty_or_resumable(
        target, force=force, manifest_path=manifest_path
    )

    rel_paths = _walk_source_files(source)

    if existing is not None and not force:
        _verify_resume_snapshot_matches_source(existing, source)
        manifest = existing
        manifest_known = {e.rel_path for e in manifest.entries}
        discovered = {str(r) for r in rel_paths}
        new_files = discovered - manifest_known
        if new_files:
            raise MigrationPreconditionError(
                f"Resume: source gained {len(new_files)} new files since the "
                f"snapshot (e.g. {sorted(new_files)[:3]}). Delete the target "
                f"and restart migrate-legacy to take a fresh snapshot."
            )
    else:
        manifest = _build_manifest_snapshot(source, target, rel_paths)

    if dry_run:
        _print_plan(manifest)
        return 0

    _atomic_write_json(manifest_path, manifest.to_dict())

    pending = [e for e in manifest.entries if e.status != "verified"]
    for i, entry in enumerate(pending, 1):
        if i == 1 or i % 500 == 0 or i == len(pending):
            print(f"  copying {i}/{len(pending)}: {entry.rel_path}", file=sys.stderr)
        _copy_and_verify(source, target, entry, manifest, manifest_path)

    _copy_reported_events_under_lock(source, target)

    manifest.completed_at = _now()
    manifest.status = "completed"
    _atomic_write_json(manifest_path, manifest.to_dict())

    _print_cutover_runbook(home)
    return 0


def cmd_migrate_legacy(args: argparse.Namespace, home: HomeConfig) -> int:
    source = Path(getattr(args, "from_path", None) or DEFAULT_LEGACY_SOURCE)
    try:
        return run_migrate_legacy(
            home,
            source_path=source,
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            confirm_near_schedule=bool(getattr(args, "confirm_near_schedule", False)),
        )
    except MigrationPreconditionError as err:
        print(f"migrate-legacy precondition failed: {err}", file=sys.stderr)
        return 2
    except MigrationStateError as err:
        print(f"migrate-legacy aborted mid-flight: {err}", file=sys.stderr)
        print(
            f"  Manifest at {home.data_dir / MANIFEST_FILENAME} is authoritative. "
            f"Re-run migrate-legacy to resume from the last verified entry.",
            file=sys.stderr,
        )
        return 1
