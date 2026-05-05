from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from deep_daily.backup.errors import BackupChecksumMismatchError, BackupNetworkError
from deep_daily.backup.retention import run_ssh_command

logger = logging.getLogger("deep_daily.backup.upload")


def upload_archive(
    *,
    archive_path: Path,
    archive_name: str,
    ssh_target: str,
    ssh_options: tuple[str, ...],
    remote_dir: str,
    local_sha256: str | None,
    skip_checksum: bool,
) -> tuple[str, str | None]:
    remote_final = f"{remote_dir.rstrip('/')}/{archive_name}"
    remote_part = f"{remote_final}.part"
    run_ssh_command(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_command=f"mkdir -p {shlex.quote(remote_dir)}",
    )

    with archive_path.open("rb") as handle:
        command = [
            "ssh",
            *[opt for item in ssh_options for opt in ("-o", item)],
            ssh_target,
            f"cat > {shlex.quote(remote_part)}",
        ]
        result = subprocess.run(command, stdin=handle, check=False, capture_output=True, text=False)
    if result.returncode != 0:
        raise BackupNetworkError(
            f"ssh upload failed rc={result.returncode}: {(result.stderr or b'').decode(errors='replace').strip()}"
        )

    remote_sha = None
    if not skip_checksum:
        if not local_sha256:
            raise BackupNetworkError("local sha256 missing while checksum verification enabled")
        remote_sha = remote_sha256(
            ssh_target=ssh_target,
            ssh_options=ssh_options,
            remote_path=remote_part,
        )
        if remote_sha != local_sha256:
            raise BackupChecksumMismatchError(
                f"checksum mismatch: local={local_sha256} remote={remote_sha}"
            )

    run_ssh_command(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_command=f"mv {shlex.quote(remote_part)} {shlex.quote(remote_final)}",
    )
    logger.info("uploaded archive to %s", remote_final)
    return remote_final, remote_sha


def remote_sha256(*, ssh_target: str, ssh_options: tuple[str, ...], remote_path: str) -> str:
    result = run_ssh_command(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_command=f"sha256sum {shlex.quote(remote_path)}",
    )
    first = result.stdout.strip().split()
    if not first:
        raise BackupNetworkError("empty sha256sum output from remote host")
    return first[0]


def list_stale_remote_parts(
    *,
    ssh_target: str,
    ssh_options: tuple[str, ...],
    remote_dir: str,
    stale_age_hours: int,
) -> list[str]:
    cmd = (
        f"find {shlex.quote(remote_dir)} -maxdepth 1 -type f "
        f"-name '<INSTANCE_NAME>-*.tar.gz.part' -mmin +{stale_age_hours * 60} -print"
    )
    result = run_ssh_command(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_command=cmd,
        allow_missing=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def clean_stale_remote_parts(
    *,
    ssh_target: str,
    ssh_options: tuple[str, ...],
    remote_dir: str,
    stale_age_hours: int,
    dry_run: bool = False,
) -> list[str]:
    stale = list_stale_remote_parts(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_dir=remote_dir,
        stale_age_hours=stale_age_hours,
    )
    if dry_run:
        return stale
    for remote_path in stale:
        try:
            run_ssh_command(
                ssh_target=ssh_target,
                ssh_options=ssh_options,
                remote_command=f"rm -f {shlex.quote(remote_path)}",
            )
        except BackupNetworkError:
            logger.warning("failed to clean stale part %s", remote_path, exc_info=True)
    return stale
