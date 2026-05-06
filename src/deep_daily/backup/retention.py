from __future__ import annotations

import logging
import re
import shlex
import subprocess

from deep_daily.backup.errors import BackupNetworkError

logger = logging.getLogger("deep_daily.backup.retention")

ARCHIVE_NAME_RE = re.compile(r"^m4-deep-daily-\d{8}-\d{6}Z\.tar\.gz$")


def select_prune_candidates(files: list[str], keep_n: int) -> list[str]:
    matching = [name for name in files if ARCHIVE_NAME_RE.match(name)]
    matching.sort(reverse=True)
    if keep_n < 0:
        keep_n = 0
    return matching[keep_n:]


def prune_remote_archives(
    *,
    ssh_target: str,
    ssh_options: tuple[str, ...],
    remote_dir: str,
    keep_n: int,
    dry_run: bool = False,
) -> dict[str, list[str] | int]:
    files = list_remote_dir(ssh_target=ssh_target, ssh_options=ssh_options, remote_dir=remote_dir)
    to_delete = select_prune_candidates(files, keep_n)
    if dry_run:
        return {"kept": max(0, len([f for f in files if ARCHIVE_NAME_RE.match(f)]) - len(to_delete)), "deleted": to_delete}
    for name in to_delete:
        remote_path = f"{remote_dir.rstrip('/')}/{name}"
        run_ssh_command(
            ssh_target=ssh_target,
            ssh_options=ssh_options,
            remote_command=f"rm -f {shlex.quote(remote_path)}",
        )
    logger.info("retention deleted %s archives", len(to_delete))
    return {"kept": len([f for f in files if ARCHIVE_NAME_RE.match(f)]) - len(to_delete), "deleted": to_delete}


def list_remote_dir(*, ssh_target: str, ssh_options: tuple[str, ...], remote_dir: str) -> list[str]:
    result = run_ssh_command(
        ssh_target=ssh_target,
        ssh_options=ssh_options,
        remote_command=f"ls -1 {shlex.quote(remote_dir)}",
        allow_missing=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def run_ssh_command(
    *,
    ssh_target: str,
    ssh_options: tuple[str, ...],
    remote_command: str,
    allow_missing: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = ["ssh", *[opt for item in ssh_options for opt in ("-o", item)], ssh_target, remote_command]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if allow_missing and ("No such file or directory" in message or "cannot access" in message):
            return subprocess.CompletedProcess(command, 0, "", "")
        raise BackupNetworkError(f"ssh command failed rc={result.returncode}: {message}")
    return result
