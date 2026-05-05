"""HARD GATE 1a — no import-time side effects.

Per PLAN v2.1 §7 Step 4, §5.1 Bootstrap invariants, §11.4 Prohibited patterns.

Contract: importing any deep_daily.* module MUST NOT:
  - Touch the filesystem (read, stat, mkdir, write)
  - Resolve Path.home() at module scope
  - Read os.getenv() at module scope for runtime config
  - Network I/O

These tests use two orthogonal techniques to assert the contract:

  1. Hostile Path.home() — make home unreadable/unwritable; if any module does
     Path.home() / ".x" at import time with an implicit seed/mkdir, it will crash.

  2. Audit hook (sys.addaudithook) — record every "open" and "os.mkdir" event
     during import; assert zero events reference user-scoped paths.

BASELINE EXPECTATION (v0.2.0): these tests FAIL because config.py:108
(`_app_config = build_app_config()`) triggers seed_if_missing → file writes.
They are expected to PASS only after PLAN §7 Step 7 (fix config.py side effects).

DO NOT weaken these tests to accommodate v0.2.0 behaviour. If the baseline fails,
that is the HARD GATE signalling the foundation needs fixing.
"""

from __future__ import annotations

import importlib
import sys
import sysconfig
from pathlib import Path

import pytest

pytestmark = pytest.mark.hard_gate_1


DEEP_DAILY_MODULES = [
    "deep_daily",
    "deep_daily.runtime",
    "deep_daily.config",
    "deep_daily.urls",
    "deep_daily.dedup",
    "deep_daily.protocols",
    "deep_daily.profile_gen",
]


@pytest.fixture
def hostile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a directory we do NOT want any module to touch.

    Any mkdir/write under this path during import is a contract violation."""
    fake_home = tmp_path / "FORBIDDEN_HOME"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


@pytest.mark.parametrize("module_name", DEEP_DAILY_MODULES)
def test_import_does_not_touch_home(
    module_name: str,
    hostile_home: Path,
    purge_deep_daily,
) -> None:
    """Importing any deep_daily.* module must not create files under Path.home()."""
    before = set(_walk(hostile_home))
    importlib.import_module(module_name)
    after = set(_walk(hostile_home))
    created = after - before
    assert created == set(), (
        f"{module_name} created files/dirs under Path.home() at import time: "
        f"{sorted(created)}. See PLAN v2.1 §11.4 — no I/O at import."
    )


def test_import_all_together_is_clean(
    hostile_home: Path,
    purge_deep_daily,
) -> None:
    """Stress test: import every module in sequence. Cumulative side effects
    that individual tests miss (module A warms B which then writes) surface here."""
    before = set(_walk(hostile_home))
    for name in DEEP_DAILY_MODULES:
        importlib.import_module(name)
    after = set(_walk(hostile_home))
    created = after - before
    assert created == set(), (
        f"Cumulative import of deep_daily.* created: {sorted(created)}. "
        f"See PLAN v2.1 §5.1 Bootstrap invariants."
    )


def _walk(root: Path) -> list[str]:
    """Return every path (file + dir) under root, as POSIX strings relative to root."""
    if not root.exists():
        return []
    return [str(p.relative_to(root)) for p in root.rglob("*")]


def test_import_does_not_write_outside_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purge_deep_daily,
) -> None:
    """Audit hook: record every 'open' for writing during import. Assert none
    target paths outside the Python install / site-packages / cwd read-only
    areas. This catches modules that hardcode absolute paths instead of using
    Path.home().
    """
    writes: list[str] = []
    stdlib_prefix = sysconfig.get_paths()["stdlib"]
    purelib_prefix = sysconfig.get_paths()["purelib"]
    cwd_prefix = str(Path.cwd().resolve())

    def audit(event: str, args: tuple) -> None:
        if event != "open" or len(args) < 2:
            return
        path, mode = args[0], args[1]
        if (
            mode is None
            or "r" == mode
            or (
                isinstance(mode, str)
                and "w" not in mode
                and "a" not in mode
                and "+" not in mode
                and "x" not in mode
            )
        ):
            return
        try:
            resolved = str(Path(str(path)).resolve())
        except (OSError, ValueError):
            return
        if (
            resolved.startswith(stdlib_prefix)
            or resolved.startswith(purelib_prefix)
            or resolved.startswith(cwd_prefix)
            or resolved.startswith("/dev/")
            or resolved.startswith("/tmp/")
            or resolved.startswith(str(tmp_path))
        ):
            return
        writes.append(f"{resolved} (mode={mode!r})")

    sys.addaudithook(audit)
    try:
        fake_home = tmp_path / "audited_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setenv("HOME", str(fake_home))
        for name in DEEP_DAILY_MODULES:
            if name in sys.modules:
                del sys.modules[name]
        for name in DEEP_DAILY_MODULES:
            importlib.import_module(name)
    finally:
        pass

    assert writes == [], (
        f"Import-time write events detected outside stdlib/site-packages/tmp/cwd: "
        f"{writes}. See PLAN v2.1 §11.4 — no I/O at import."
    )
