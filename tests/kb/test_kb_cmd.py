from __future__ import annotations

import argparse
from pathlib import Path

from deep_daily import config as config_module
from deep_daily.commands.kb_cmd import cmd_kb
from deep_daily.home import HomeConfig
from deep_daily.kb.schema import bootstrap_db


def test_kb_stats_empty(isolated_runtime, capsys, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    db_path = home.path / "data" / "kb" / "kb.db"
    bootstrap_db(db_path)
    rc = cmd_kb(argparse.Namespace(kb_cmd="stats", json=False), home)
    out = capsys.readouterr().out
    assert rc == 0
    assert "items_total: 0" in out


def test_kb_ingest_success(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    config_module._runtime = config_module.RuntimeConfig(
        home=home,
        app=config_module.build_app_config(data_root=home.data_dir, configs_dir=home.configs_dir),
    )
    rc = cmd_kb(
        argparse.Namespace(kb_cmd="ingest", rebuild=True, since=None, sources="articles,tweets"),
        home,
    )
    assert rc == 0


def test_kb_ingest_invalid_since(isolated_runtime, tmp_home: Path) -> None:
    config_module._runtime = None
    home = HomeConfig.load(tmp_home)
    config_module._runtime = config_module.RuntimeConfig(
        home=home,
        app=config_module.build_app_config(data_root=home.data_dir, configs_dir=home.configs_dir),
    )
    rc = cmd_kb(
        argparse.Namespace(kb_cmd="ingest", rebuild=False, since="bad-date", sources=None),
        home,
    )
    assert rc == 2
