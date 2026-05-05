from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import subprocess
from pathlib import Path

from deep_daily.backup.errors import BackupValidationError

logger = logging.getLogger("deep_daily.backup.archive")


def make_archive(data_dir: Path, archive_path: Path, exclude_patterns: tuple[str, ...]) -> int:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    command = ["tar", "-czf", str(archive_path)]
    for pattern in exclude_patterns:
        command.extend(["--exclude", pattern])
    command.extend(["-C", str(data_dir), "."])
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupValidationError(
            f"tar failed with rc={result.returncode}: {(result.stderr or result.stdout).strip()}"
        )
    size = archive_path.stat().st_size
    logger.info("created archive %s (%s bytes)", archive_path, size)
    return size


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def estimate_directory_size(data_dir: Path, exclude_patterns: tuple[str, ...]) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    for root, dirs, files in os.walk(data_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(data_dir)
        kept_dirs: list[str] = []
        for dirname in dirs:
            rel = _to_match_path(rel_root / dirname, is_dir=True)
            if not _is_excluded(rel, exclude_patterns):
                kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in files:
            rel = _to_match_path(rel_root / filename, is_dir=False)
            if _is_excluded(rel, exclude_patterns):
                continue
            stat = (root_path / filename).stat()
            total_bytes += stat.st_size
            file_count += 1
    return total_bytes, file_count


def ensure_non_empty_data_dir(data_dir: Path, exclude_patterns: tuple[str, ...]) -> tuple[int, int]:
    if not data_dir.exists() or not data_dir.is_dir():
        raise BackupValidationError(f"data dir missing: {data_dir}")
    total_bytes, file_count = estimate_directory_size(data_dir, exclude_patterns)
    if file_count == 0:
        raise BackupValidationError("refusing to back up empty dir.")
    return total_bytes, file_count


def _is_excluded(rel_path: str, exclude_patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in exclude_patterns)


def _to_match_path(rel_path: Path, *, is_dir: bool) -> str:
    rel_str = rel_path.as_posix().lstrip("./")
    if rel_str == ".":
        return ""
    return f"{rel_str}/" if is_dir else rel_str
