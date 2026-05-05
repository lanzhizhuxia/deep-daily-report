"""Tests for ``deep-daily`` CLI dispatcher — PLAN v2.1 §11.1.

Contracts verified:
  - HOME-free commands (init, templates, version) never touch HomeConfig.resolve
    or init_runtime.
  - HOME-required commands resolve HOME and call init_runtime(home) once.
  - allow_walkup is True only for `doctor` and for `run --date DATE`.
  - Unresolvable HOME exits with rc=2.
  - Each subcommand forwards to its cmd_* function with (args, home).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from deep_daily import __main__ as entry


def _mk_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".deep-daily-home").write_text("schema_version: 1\n")
    (home / "config.yaml").write_text(
        "schema_version: 1\nreader:\n  name: test\nllm:\n  backend: openai\n"
    )
    (home / "configs").mkdir()
    (home / "data").mkdir()
    (home / "logs").mkdir()
    return home


# ---------------------------------------------------------------------------
# Parser shape
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_parser_accepts_all_documented_commands():
    parser = entry.build_parser()
    for cmd in [
        ["init", "/tmp/x"],
        ["templates", "list"],
        ["version"],
        ["run"],
        ["fetch"],
        ["doctor"],
        ["migrate-legacy"],
    ]:
        args = parser.parse_args(cmd)
        assert args.cmd == cmd[0]


@pytest.mark.hard_gate_3
def test_version_command_prints_version(capsys):
    rc = entry.main(["version"])
    assert rc == 0
    from deep_daily import __version__

    assert capsys.readouterr().out.strip() == __version__


# ---------------------------------------------------------------------------
# HOME-free branch never touches runtime
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_home_free_commands_skip_home_resolution(monkeypatch, capsys):
    calls = {"resolved": False, "init_runtime": False}

    def fake_resolve(**kwargs):
        calls["resolved"] = True
        raise AssertionError("resolve should not be called for HOME-free commands")

    def fake_init_runtime(home):
        calls["init_runtime"] = True

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(lambda cls, **kw: fake_resolve(**kw)))
    # version is the simplest HOME-free path
    rc = entry.main(["version"])
    assert rc == 0
    assert calls["resolved"] is False
    assert calls["init_runtime"] is False


# ---------------------------------------------------------------------------
# HOME-required branch — walk-up policy
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_run_without_date_does_not_allow_walkup(monkeypatch, tmp_path):
    home_path = _mk_home(tmp_path)
    captured: dict = {}

    def fake_resolve(cls, *, cli_home, allow_walkup):
        captured["cli_home"] = cli_home
        captured["allow_walkup"] = allow_walkup
        from deep_daily.home import HomeConfig

        return HomeConfig.load(home_path)

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    # Stub init_runtime + the run command body
    import deep_daily.config as cfg
    import deep_daily.commands.run_cmd as run_cmd

    monkeypatch.setattr(cfg, "init_runtime", lambda home: None)
    monkeypatch.setattr(run_cmd, "cmd_run", lambda args, home: 0)

    rc = entry.main(["--home", str(home_path), "run"])
    assert rc == 0
    assert captured["allow_walkup"] is False


@pytest.mark.hard_gate_3
def test_run_with_specific_date_allows_walkup(monkeypatch, tmp_path):
    home_path = _mk_home(tmp_path)
    captured: dict = {}

    def fake_resolve(cls, *, cli_home, allow_walkup):
        captured["allow_walkup"] = allow_walkup
        from deep_daily.home import HomeConfig

        return HomeConfig.load(home_path)

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    import deep_daily.config as cfg
    import deep_daily.commands.run_cmd as run_cmd

    monkeypatch.setattr(cfg, "init_runtime", lambda home: None)
    monkeypatch.setattr(run_cmd, "cmd_run", lambda args, home: 0)

    rc = entry.main(["--home", str(home_path), "run", "--date", "2025-01-01"])
    assert rc == 0
    assert captured["allow_walkup"] is True


@pytest.mark.hard_gate_3
def test_doctor_always_allows_walkup(monkeypatch, tmp_path):
    home_path = _mk_home(tmp_path)
    captured: dict = {}

    def fake_resolve(cls, *, cli_home, allow_walkup):
        captured["allow_walkup"] = allow_walkup
        from deep_daily.home import HomeConfig

        return HomeConfig.load(home_path)

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    import deep_daily.config as cfg
    import deep_daily.commands.doctor_cmd as doctor_cmd

    monkeypatch.setattr(cfg, "init_runtime", lambda home: None)
    monkeypatch.setattr(doctor_cmd, "cmd_doctor", lambda args, home: 0)

    rc = entry.main(["--home", str(home_path), "doctor"])
    assert rc == 0
    assert captured["allow_walkup"] is True


@pytest.mark.hard_gate_3
def test_migrate_legacy_never_allows_walkup(monkeypatch, tmp_path):
    home_path = _mk_home(tmp_path)
    captured: dict = {}

    def fake_resolve(cls, *, cli_home, allow_walkup):
        captured["allow_walkup"] = allow_walkup
        from deep_daily.home import HomeConfig

        return HomeConfig.load(home_path)

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    import deep_daily.config as cfg
    import deep_daily.commands.migrate_legacy_cmd as mlc

    monkeypatch.setattr(cfg, "init_runtime", lambda home: None)
    monkeypatch.setattr(mlc, "cmd_migrate_legacy", lambda args, home: 0)

    rc = entry.main(["--home", str(home_path), "migrate-legacy", "--dry-run"])
    assert rc == 0
    assert captured["allow_walkup"] is False


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_unresolvable_home_exits_rc_2(monkeypatch, capsys):
    from deep_daily.home import HomeNotFoundError

    def fake_resolve(cls, **kwargs):
        raise HomeNotFoundError("no home found")

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    rc = entry.main(["run"])
    assert rc == 2
    assert "no home found" in capsys.readouterr().err


@pytest.mark.hard_gate_3
def test_invalid_home_exits_rc_2(monkeypatch, capsys):
    from deep_daily.home import HomeInvalidError

    def fake_resolve(cls, **kwargs):
        raise HomeInvalidError("bad sentinel")

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    rc = entry.main(["--home", "/nowhere", "doctor"])
    assert rc == 2
    assert "bad sentinel" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dispatch forwarding
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_fetch_dispatch_forwards_args_and_home(monkeypatch, tmp_path):
    home_path = _mk_home(tmp_path)
    captured: dict = {}

    def fake_resolve(cls, *, cli_home, allow_walkup):
        from deep_daily.home import HomeConfig

        return HomeConfig.load(home_path)

    monkeypatch.setattr("deep_daily.home.HomeConfig.resolve", classmethod(fake_resolve))

    import deep_daily.config as cfg
    import deep_daily.commands.fetch_cmd as fetch_cmd

    monkeypatch.setattr(cfg, "init_runtime", lambda home: None)

    def capturing_fetch(args, home):
        captured["args"] = args
        captured["home_path"] = home.path
        return 0

    monkeypatch.setattr(fetch_cmd, "cmd_fetch", capturing_fetch)

    rc = entry.main(["--home", str(home_path), "fetch", "--collectors", "rss,twitter"])
    assert rc == 0
    assert captured["args"].collectors == "rss,twitter"
    assert captured["home_path"] == home_path.resolve()


# ---------------------------------------------------------------------------
# templates subcommand
# ---------------------------------------------------------------------------


@pytest.mark.hard_gate_3
def test_templates_list_prints_default_pack(capsys):
    rc = entry.main(["templates", "list"])
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert "default" in out
