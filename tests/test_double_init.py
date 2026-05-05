"""HARD GATE 1b — strict singleton contract for init_runtime(home).

Per PLAN v2.1 §7 Step 5, §5.2 Strict singleton contract.

Contract (from §5.2):
  1. First init_runtime(home) call: sets runtime
  2. Second call with same (normalized) HOME: no-op, returns without error
  3. Second call with different HOME: raises RuntimeAlreadyInitializedError

Path normalization MUST handle: symlinks, trailing slash, "./", "~", relative paths.

BASELINE EXPECTATION (v0.2.0): these tests SKIP because init_runtime() does not
exist yet. They begin failing (then pass) once PLAN §7 Step 6 lands home.py with
HomeConfig and Step 7 lands init_runtime() in config.py.

DO NOT implement init_runtime() as "set once silently ignore rest" — that was v2's
weaker contract that Oracle v2 rejected. Different-HOME MUST raise.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.hard_gate_1


def _import_or_skip():
    """Import init_runtime + HomeConfig + the exception class, or skip the test
    if they don't exist yet (baseline v0.2.0 before Phase 1 Step 6/7 lands)."""
    try:
        from deep_daily import config as cfg_mod
        from deep_daily.home import HomeConfig
    except ImportError as e:
        pytest.skip(f"home.py / init_runtime not yet implemented: {e}")

    init_runtime = getattr(cfg_mod, "init_runtime", None)
    if init_runtime is None:
        pytest.skip("deep_daily.config.init_runtime not yet implemented")

    exc_class = getattr(cfg_mod, "RuntimeAlreadyInitializedError", None)
    if exc_class is None:
        pytest.skip("RuntimeAlreadyInitializedError not yet defined")

    return cfg_mod, HomeConfig, init_runtime, exc_class


@pytest.fixture
def fresh_runtime(purge_deep_daily):
    """Yield a fresh deep_daily.config module with _runtime=None, then clean up.
    Depends on purge_deep_daily so every test gets a fully re-imported module tree."""
    cfg_mod, HomeConfig, init_runtime, exc_class = _import_or_skip()
    if hasattr(cfg_mod, "_runtime"):
        cfg_mod._runtime = None
    yield cfg_mod, HomeConfig, init_runtime, exc_class
    if hasattr(cfg_mod, "_runtime"):
        cfg_mod._runtime = None


def test_first_call_sets_runtime(tmp_home: Path, fresh_runtime):
    cfg_mod, HomeConfig, init_runtime, _ = fresh_runtime
    assert cfg_mod._runtime is None, "Fixture did not reset _runtime"

    home = HomeConfig.load(tmp_home)
    init_runtime(home)

    assert cfg_mod._runtime is not None
    assert cfg_mod._runtime.home.path.resolve() == tmp_home.resolve()


def test_second_call_same_home_is_noop(tmp_home: Path, fresh_runtime):
    cfg_mod, HomeConfig, init_runtime, _ = fresh_runtime

    home1 = HomeConfig.load(tmp_home)
    init_runtime(home1)
    first_runtime = cfg_mod._runtime

    home2 = HomeConfig.load(tmp_home)
    init_runtime(home2)

    assert cfg_mod._runtime is first_runtime, (
        "Same-HOME re-init must be a no-op (§5.2 invariant). "
        "Got a new runtime object instead."
    )


def test_second_call_different_home_raises(tmp_path: Path, fresh_runtime):
    cfg_mod, HomeConfig, init_runtime, exc_class = fresh_runtime

    home_a = _make_home(tmp_path / "home_a")
    home_b = _make_home(tmp_path / "home_b")

    init_runtime(HomeConfig.load(home_a))

    with pytest.raises(exc_class) as excinfo:
        init_runtime(HomeConfig.load(home_b))

    msg = str(excinfo.value)
    assert str(home_a.resolve()) in msg
    assert str(home_b.resolve()) in msg


def test_trailing_slash_is_same_home(tmp_home: Path, fresh_runtime):
    cfg_mod, HomeConfig, init_runtime, _ = fresh_runtime

    init_runtime(HomeConfig.load(tmp_home))
    first_runtime = cfg_mod._runtime

    with_slash = Path(str(tmp_home) + os.sep)
    init_runtime(HomeConfig.load(with_slash))

    assert cfg_mod._runtime is first_runtime


def test_symlink_is_same_home(tmp_path: Path, fresh_runtime):
    cfg_mod, HomeConfig, init_runtime, _ = fresh_runtime

    real = _make_home(tmp_path / "real_home")
    link = tmp_path / "link_home"
    link.symlink_to(real, target_is_directory=True)

    init_runtime(HomeConfig.load(real))
    first_runtime = cfg_mod._runtime

    init_runtime(HomeConfig.load(link))

    assert cfg_mod._runtime is first_runtime, (
        "Symlinked path to same HOME must be treated as same HOME (§5.2 "
        "'normalized_home = home.path.resolve()')"
    )


def test_relative_path_resolves_to_same_home(
    tmp_path: Path, fresh_runtime, monkeypatch
):
    cfg_mod, HomeConfig, init_runtime, _ = fresh_runtime

    real = _make_home(tmp_path / "real_home")
    monkeypatch.chdir(tmp_path)

    init_runtime(HomeConfig.load(real))
    first_runtime = cfg_mod._runtime

    init_runtime(HomeConfig.load(Path("./real_home")))

    assert cfg_mod._runtime is first_runtime


def _make_home(path: Path) -> Path:
    """Create a minimal valid HOME at path. Mirrors tmp_home fixture but
    accepts a caller-specified path so we can have two distinct HOMEs per test."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".deep-daily-home").write_text("schema_version: 1\n")
    (path / "config.yaml").write_text(
        "schema_version: 1\nreader:\n  name: test-reader\nllm:\n  backend: multikey\n"
    )
    (path / "configs").mkdir(exist_ok=True)
    data = path / "data"
    data.mkdir(exist_ok=True)
    for sub in ("articles", "tweets", "tweets-nas", "news-6551", ".session-memory"):
        (data / sub).mkdir(exist_ok=True)
    for parent in ("dailies", "dailies-dryrun"):
        (data / parent).mkdir(exist_ok=True)
        (data / parent / ".pipeline").mkdir(exist_ok=True)
    (path / "logs").mkdir(exist_ok=True)
    return path
