from __future__ import annotations

import argparse
import logging
import sys
import time

from deep_daily.backup.archive import compute_sha256, ensure_valid_home, make_archive
from deep_daily.backup.config import backup_state_dir, load_backup_config
from deep_daily.backup.errors import BackupError
from deep_daily.backup.retention import prune_remote_archives
from deep_daily.backup.state import append_history, archive_timestamp, backup_lock, utc_timestamp, write_last_json
from deep_daily.backup.upload import clean_stale_remote_parts, upload_archive
from deep_daily.home import HomeConfig

logger = logging.getLogger("deep_daily.backup.command")


def cmd_backup(args: argparse.Namespace, home: HomeConfig) -> int:
    try:
        config = load_backup_config(home)
        retention = int(getattr(args, "retention", None) or config.retention)
        archive_name = f"<INSTANCE_NAME>-{archive_timestamp()}.tar.gz"
        state_dir = backup_state_dir(home)
        source_dir = home.path
        ssh_target = f"{config.nas_user}@{config.nas_host}"
        remote_final = f"{config.remote_dir}/{archive_name}"
        estimated_bytes, estimated_files = ensure_valid_home(home, config.exclude)
        if (source_dir / ".env").exists():
            print(
                "warning: archive will include .env (contains secrets); "
                "NAS storage should be treated as sensitive",
                file=sys.stderr,
            )

        if getattr(args, "dry_run", False):
            _print_dry_run(
                archive_name=archive_name,
                estimated_bytes=estimated_bytes,
                estimated_files=estimated_files,
                exclude=config.exclude,
                remote_final=remote_final,
                retention=retention,
                stale_part_age_hours=config.stale_part_age_hours,
            )
            return 0

        start = time.time()
        skip_checksum = bool(getattr(args, "skip_checksum", False))
        archive_path = state_dir / archive_name
        with backup_lock(
            state_dir,
            force_unlock=bool(getattr(args, "force_unlock", False)),
            interactive=sys.stdout.isatty(),
        ):
            clean_stale_remote_parts(
                ssh_target=ssh_target,
                ssh_options=config.ssh_options,
                remote_dir=config.remote_dir,
                stale_age_hours=config.stale_part_age_hours,
            )
            size_bytes = make_archive(source_dir, archive_path, config.exclude)
            sha256 = None if skip_checksum else compute_sha256(archive_path)
            remote_path, remote_sha = upload_archive(
                archive_path=archive_path,
                archive_name=archive_name,
                ssh_target=ssh_target,
                ssh_options=config.ssh_options,
                remote_dir=config.remote_dir,
                local_sha256=sha256,
                skip_checksum=skip_checksum,
            )
            prune_remote_archives(
                ssh_target=ssh_target,
                ssh_options=config.ssh_options,
                remote_dir=config.remote_dir,
                keep_n=retention,
            )
            payload = {
                "ts": utc_timestamp(),
                "archive": archive_name,
                "size_bytes": size_bytes,
                "sha256": sha256 or "",
                "remote": remote_path,
                "duration_s": int(round(time.time() - start)),
                "files_included": estimated_files,
                "ok": True,
            }
            if remote_sha and not sha256:
                payload["sha256"] = remote_sha
            write_last_json(state_dir, payload)
            append_history(state_dir, payload)
        archive_path.unlink(missing_ok=True)
        print(f"Backup complete: {remote_path}")
        return 0
    except BackupError as err:
        print(str(err), file=sys.stderr)
        return err.exit_code
    except Exception as err:
        print(str(err), file=sys.stderr)
        return 1


def _print_dry_run(
    *,
    archive_name: str,
    estimated_bytes: int,
    estimated_files: int,
    exclude: tuple[str, ...],
    remote_final: str,
    retention: int,
    stale_part_age_hours: int,
) -> None:
    print("[dry-run] deep_daily backup")
    print(f"archive: {archive_name}")
    print(f"estimated size bytes: {estimated_bytes}")
    print(f"estimated files: {estimated_files}")
    print("exclusions:")
    for item in exclude:
        print(f"  - {item}")
    print(f"remote path: {remote_final}")
    print(f"retention keep: {retention}")
    print(
        "stale .part cleanup: would scan remote for .part files older than "
        f"{stale_part_age_hours}h"
    )
    print(f"retention policy: would keep {retention} newest matching archives, prune older ones")
