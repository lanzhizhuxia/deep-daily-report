from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from deep_daily.backup.errors import BackupConfigError
from deep_daily.home import HomeConfig

DEFAULT_EXCLUDE = (
    "state/**",
    "logs/**",
    "data/dailies/.pipeline/**",
    "data/dailies-dryrun/**",
    "data/.session-memory/**",
)
DEFAULT_SSH_OPTIONS = (
    "ConnectTimeout=10",
    "BatchMode=yes",
    "StrictHostKeyChecking=accept-new",
)


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool
    nas_host: str
    nas_user: str
    nas_base: str
    retention: int
    stale_part_age_hours: int
    ssh_options: tuple[str, ...] = field(default_factory=tuple)
    exclude: tuple[str, ...] = field(default_factory=tuple)

    @property
    def remote_dir(self) -> str:
        return self.nas_base.rstrip("/")


def load_backup_config(home: HomeConfig, env: Mapping[str, str] | None = None) -> BackupConfig:
    raw_backup = home.raw_config.get("backup") or {}
    if not isinstance(raw_backup, dict):
        raise BackupConfigError("config.yaml backup section must be a mapping")

    env_map = env or os.environ
    enabled = bool(raw_backup.get("enabled", True))
    nas_host = str(env_map.get("NAS_BACKUP_HOST") or raw_backup.get("nas_host") or "").strip()
    nas_user = str(env_map.get("NAS_BACKUP_USER") or raw_backup.get("nas_user") or "").strip()
    nas_base = str(env_map.get("NAS_BACKUP_BASE") or raw_backup.get("nas_base") or "").strip()
    retention_raw = env_map.get("NAS_BACKUP_RETENTION") or raw_backup.get("retention", 30)

    try:
        retention = int(retention_raw)
    except (TypeError, ValueError) as err:
        raise BackupConfigError(f"backup.retention must be an integer, got {retention_raw!r}") from err

    try:
        stale_part_age_hours = int(raw_backup.get("stale_part_age_hours", 48))
    except (TypeError, ValueError) as err:
        raise BackupConfigError("backup.stale_part_age_hours must be an integer") from err

    ssh_options_raw = raw_backup.get("ssh_options", list(DEFAULT_SSH_OPTIONS))
    if not isinstance(ssh_options_raw, list) or not all(isinstance(v, str) for v in ssh_options_raw):
        raise BackupConfigError("backup.ssh_options must be a list of strings")

    exclude_raw = raw_backup.get("exclude", list(DEFAULT_EXCLUDE))
    if not isinstance(exclude_raw, list) or not all(isinstance(v, str) for v in exclude_raw):
        raise BackupConfigError("backup.exclude must be a list of strings")

    if not enabled:
        raise BackupConfigError("backup is disabled in config.yaml")
    if not nas_host or not nas_user or not nas_base:
        raise BackupConfigError("backup config missing nas_host, nas_user, or nas_base")
    if retention < 0:
        raise BackupConfigError("backup.retention must be >= 0")
    if stale_part_age_hours < 0:
        raise BackupConfigError("backup.stale_part_age_hours must be >= 0")

    return BackupConfig(
        enabled=enabled,
        nas_host=nas_host,
        nas_user=nas_user,
        nas_base=nas_base,
        retention=retention,
        stale_part_age_hours=stale_part_age_hours,
        ssh_options=tuple(ssh_options_raw),
        exclude=tuple(exclude_raw),
    )


def backup_state_dir(home: HomeConfig) -> Path:
    return home.path / "state" / "backup"
