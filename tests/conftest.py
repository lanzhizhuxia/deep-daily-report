"""Shared pytest fixtures for deep-daily tests.

Per PLAN v2.1 §7 Step 3 — fixtures that support:
  - tmp_home: creates a valid-looking HOME with .deep-daily-home sentinel + minimal config.yaml
  - purge_deep_daily: clears sys.modules so fresh imports re-run module-level code
  - isolated_runtime: resets deep_daily.config._runtime singleton between tests

test_no_import_side_effects is intentionally designed to FAIL against v0.2.0 code
(config.py performs I/O at import time). That failure is the baseline HARD GATE 1
enforces — DO NOT "fix" the test to pass on v0.2.0; fix config.py instead.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest


def _purge_deep_daily_modules() -> None:
    """Remove every deep_daily.* module from sys.modules so a fresh import re-runs
    module-level code. Used by tests that observe import-time behaviour."""
    for name in list(sys.modules):
        if name == "deep_daily" or name.startswith("deep_daily."):
            del sys.modules[name]


@pytest.fixture
def purge_deep_daily() -> Iterator[None]:
    """Purge deep_daily.* from sys.modules both before and after the test.

    Any test that imports deep_daily submodules and wants to observe import-time
    behaviour should depend on this fixture (directly or transitively)."""
    _purge_deep_daily_modules()
    yield
    _purge_deep_daily_modules()


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """Create a minimally valid instance HOME in tmp_path and return its path.

    Matches the target directory layout per PLAN v2.1 §3.3. All command-layer
    tests (init/doctor/run/migrate-legacy) use this fixture — keep the layout
    in sync with §3.3 if it changes.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / ".deep-daily-home").write_text("schema_version: 1\n")
    (home / "config.yaml").write_text(
        "schema_version: 1\nreader:\n  name: test-reader\nllm:\n  backend: multikey\n"
    )
    (home / "configs").mkdir()

    data = home / "data"
    data.mkdir()
    for sub in ("articles", "tweets", "tweets-nas", "news-6551", ".session-memory"):
        (data / sub).mkdir()
    for parent in ("dailies", "dailies-dryrun"):
        (data / parent).mkdir()
        (data / parent / ".pipeline").mkdir()

    (home / "logs").mkdir()
    return home


@pytest.fixture
def isolated_runtime(purge_deep_daily) -> Iterator[None]:
    """Reset the config._runtime singleton after the test runs.

    NOTE: This fixture MUST NOT import deep_daily.config at setup time — some
    tests (test_no_import_side_effects) assert that import itself is a no-op
    under hostile filesystems, and we must not warm the import cache.
    """
    yield
    cfg = sys.modules.get("deep_daily.config")
    if cfg is not None:
        if hasattr(cfg, "_runtime"):
            setattr(cfg, "_runtime", None)
        if hasattr(cfg, "_effective_models"):
            setattr(cfg, "_effective_models", None)
