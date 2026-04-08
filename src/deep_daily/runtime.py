from __future__ import annotations

import os
from pathlib import Path


def seed_if_missing(seed: Path, runtime: Path) -> None:
    if runtime.exists():
        return
    if not seed.exists():
        return
    runtime.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(runtime), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with open(seed, "rb") as src:
                os.write(fd, src.read())
        finally:
            os.close(fd)
    except FileExistsError:
        pass
