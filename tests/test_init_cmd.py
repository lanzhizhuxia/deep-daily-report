"""HARD GATE 3 pre-flight — init_cmd.

Per PLAN v2.1 §7 Step 15.

Contracts enforced:
  * Happy path: init produces a HOME that HomeConfig.load accepts.
  * Collision policy: non-empty target without --force → InitError.
  * Collision policy: re-init with --force on an existing HOME succeeds.
  * --yes mode: no prompts; uses default reader name derived from HOME dir.
  * Placeholder substitution: {reader_name} in config.yaml is replaced.
  * Templates landed: all 9 source templates produce matching output files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_daily.commands.init_cmd import InitError, run_init
from deep_daily.home import CONFIG_FILENAME, HomeConfig, SENTINEL_NAME


pytestmark = pytest.mark.hard_gate_3


def _fake_input_factory(answers):
    """Return an input() replacement that yields from ``answers`` in order."""
    it = iter(answers)

    def _fake_input(prompt: str) -> str:
        return next(it)

    return _fake_input


def test_init_creates_valid_home(tmp_path: Path):
    target = tmp_path / "my-home"
    home = run_init(target_path=target, force=False, yes=True, reader_name=None)
    assert home == target.resolve()
    loaded = HomeConfig.load(home)
    assert loaded.path == home
    assert loaded.raw_config["schema_version"] == 1


def test_init_with_yes_derives_reader_name_from_dirname(tmp_path: Path):
    target = tmp_path / "instance-alpha"
    run_init(target_path=target, force=False, yes=True, reader_name=None)

    cfg_text = (target / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert 'name: "instance-alpha"' in cfg_text


def test_init_renders_explicit_reader_name(tmp_path: Path):
    target = tmp_path / "home"
    run_init(target_path=target, force=False, yes=True, reader_name="alice")

    cfg_text = (target / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert 'name: "alice"' in cfg_text
    assert 'topic_id: "alice.daily-report"' in cfg_text


def test_init_refuses_non_empty_non_home_dir(tmp_path: Path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "random_file.txt").write_text("not ours")

    with pytest.raises(InitError) as excinfo:
        run_init(target_path=target, force=False, yes=True, reader_name=None)
    assert "not empty" in str(excinfo.value)


def test_init_refuses_non_empty_non_home_dir_even_with_force(tmp_path: Path):
    """--force is gated on the target already being a deep-daily HOME."""
    target = tmp_path / "stranger"
    target.mkdir()
    (target / "random_file.txt").write_text("not ours")

    with pytest.raises(InitError):
        run_init(target_path=target, force=True, yes=True, reader_name=None)


def test_init_force_overwrites_existing_home(tmp_path: Path):
    target = tmp_path / "home"
    run_init(target_path=target, force=False, yes=True, reader_name="first")

    run_init(target_path=target, force=True, yes=True, reader_name="second")
    cfg_text = (target / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert 'name: "second"' in cfg_text


def test_init_lays_down_all_expected_files(tmp_path: Path):
    target = tmp_path / "home"
    run_init(target_path=target, force=False, yes=True, reader_name=None)

    expected = [
        CONFIG_FILENAME,
        ".env",
        SENTINEL_NAME,
        "configs/profile.yaml",
        "configs/topics.yaml",
        "configs/sources.yaml",
        "configs/kols.json",
        "configs/active-systems.yaml",
        "configs/6551-config.json",
        "logs",
        "data",
    ]
    for rel in expected:
        assert (target / rel).exists(), f"missing after init: {rel}"


def test_init_interactive_prompts_consume_answers(tmp_path: Path):
    target = tmp_path / "home"
    fake = _fake_input_factory(["my-reader", "multikey", "y"])

    home = run_init(
        target_path=target,
        force=False,
        yes=False,
        reader_name=None,
        input_fn=fake,
    )
    cfg_text = (home / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert 'name: "my-reader"' in cfg_text
    assert 'backend: "multikey"' in cfg_text
    assert "enabled: true" in cfg_text


def test_init_sentinel_written_last(tmp_path: Path):
    """A crashed init should leave a target without a sentinel so HomeConfig.load
    rejects it, not a half-populated HOME that load() silently accepts."""
    target = tmp_path / "home"
    run_init(target_path=target, force=False, yes=True, reader_name=None)

    sentinel = target / SENTINEL_NAME
    config_yaml = target / CONFIG_FILENAME
    assert sentinel.stat().st_mtime >= config_yaml.stat().st_mtime
