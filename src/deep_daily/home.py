"""HomeConfig — resolved, validated instance HOME.

Per PLAN v2.1 §3.5 (HOME resolution), §5.1 (bootstrap), §11.2 (resolve semantics).

Contract:
  - HomeConfig.load(path) validates path is a real HOME (sentinel + config.yaml) and parses config.yaml.
  - HomeConfig.resolve(cli_home=, allow_walkup=) implements the CLI resolution order.
  - No I/O at module import time (PEP 562 compliance).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SENTINEL_NAME = ".deep-daily-home"
CONFIG_FILENAME = "config.yaml"
ENV_HOME = "DEEP_DAILY_HOME"


class HomeInvalidError(RuntimeError):
    """Raised when a path does not point to a valid deep-daily instance HOME."""


class HomeNotFoundError(RuntimeError):
    """Raised when HOME could not be resolved via any supported mechanism."""


@dataclass(frozen=True)
class HomeConfig:
    path: Path
    raw_config: dict[str, Any] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.path / "data"

    @property
    def configs_dir(self) -> Path:
        return self.path / "configs"

    @property
    def logs_dir(self) -> Path:
        return self.path / "logs"

    @property
    def reader_name(self) -> str:
        return str(self.raw_config.get("reader", {}).get("name", ""))

    @classmethod
    def load(cls, path: Path | str) -> "HomeConfig":
        """Validate that `path` is a HOME and load its config.yaml.

        Raises HomeInvalidError if validation fails. Does NOT create or seed anything.
        """
        p = Path(path).expanduser()
        if not p.exists():
            raise HomeInvalidError(f"HOME does not exist: {p}")
        if not p.is_dir():
            raise HomeInvalidError(f"HOME is not a directory: {p}")

        sentinel = p / SENTINEL_NAME
        if not sentinel.exists():
            raise HomeInvalidError(
                f"Not a deep-daily HOME (missing {SENTINEL_NAME}): {p}. "
                f"Run `deep-daily init {p}` to initialize."
            )

        cfg_path = p / CONFIG_FILENAME
        if not cfg_path.exists():
            raise HomeInvalidError(f"HOME missing config.yaml: {cfg_path}")

        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise HomeInvalidError(f"config.yaml is not valid YAML: {e}") from e

        if not isinstance(raw, dict):
            raise HomeInvalidError(
                f"config.yaml must be a mapping, got {type(raw).__name__}"
            )

        schema_version = raw.get("schema_version")
        if schema_version != 1:
            raise HomeInvalidError(
                f"Unsupported config.yaml schema_version: {schema_version!r} (expected 1). "
                f"Run `deep-daily migrate-config` (future) or edit config.yaml."
            )

        return cls(path=p.resolve(), raw_config=raw)

    @classmethod
    def resolve(
        cls,
        *,
        cli_home: str | os.PathLike | None,
        allow_walkup: bool,
    ) -> "HomeConfig":
        """Resolve HOME per PLAN v2.1 §11.2.

        Order:
          1. cli_home (--home flag)
          2. $DEEP_DAILY_HOME
          3. Walk up CWD → filesystem root looking for .deep-daily-home sentinel
             (only if allow_walkup=True — see §3.5)
          4. Raise HomeNotFoundError
        """
        env_home = os.environ.get(ENV_HOME)

        if cli_home is not None:
            candidate = Path(cli_home).expanduser()
            if (
                env_home
                and Path(env_home).expanduser().resolve() != candidate.resolve()
            ):
                print(
                    f"WARNING: --home={candidate} overrides ${ENV_HOME}={env_home}",
                    file=sys.stderr,
                )
            return cls.load(candidate)

        if env_home:
            return cls.load(env_home)

        if allow_walkup:
            found = _walk_up_for_sentinel(Path.cwd())
            if found is not None:
                return cls.load(found)

        raise HomeNotFoundError(
            f"Could not resolve deep-daily HOME. Provide one of:\n"
            f"  --home <path>\n"
            f"  export {ENV_HOME}=<path>\n"
            + (
                "  (walk-up search found no .deep-daily-home sentinel)\n"
                if allow_walkup
                else ""
            )
            + f"\nTo create a new HOME, run: deep-daily init <path>"
        )


def _walk_up_for_sentinel(start: Path) -> Path | None:
    """Walk from `start` toward filesystem root looking for a .deep-daily-home sentinel.

    Stops at filesystem root or at `$HOME` (whichever is shallower) to avoid accidentally
    picking up a HOME in an unexpected ancestor.
    """
    start = start.resolve()
    user_home = Path.home().resolve()
    current = start
    while True:
        if (current / SENTINEL_NAME).exists():
            return current
        if current == current.parent:
            return None
        if current == user_home:
            return None
        current = current.parent
