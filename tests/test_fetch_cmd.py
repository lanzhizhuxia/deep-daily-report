"""Tests for ``deep-daily fetch`` \u2014 PLAN v2.1 \u00a77 Step 17, \u00a73.6."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from deep_daily.commands import fetch_cmd
from deep_daily.home import HomeConfig


def _mk_home(tmp_home: Path) -> HomeConfig:
    (tmp_home / "config.yaml").write_text(
        "schema_version: 1\nreader:\n  name: test\n",
        encoding="utf-8",
    )
    return HomeConfig.load(tmp_home)


@pytest.mark.hard_gate_3
def test_fetch_invokes_collect_shared_and_summarizes(
    tmp_home: Path, monkeypatch, capsys
) -> None:
    home = _mk_home(tmp_home)
    calls: dict = {}

    def fake_collect_shared(date_str: str, *, force: bool, resume: bool) -> dict:
        calls["date"] = date_str
        calls["force"] = force
        calls["resume"] = resume
        return {
            "materials": [
                {"source": "rss"},
                {"source": "rss"},
                {"source": "twitter"},
            ]
        }

    import deep_daily.pipeline as pipeline

    monkeypatch.setattr(pipeline, "collect_shared", fake_collect_shared)

    args = argparse.Namespace(date="2026-05-05", force=True, resume=False)
    rc = fetch_cmd.cmd_fetch(args, home)
    out = capsys.readouterr().out

    assert rc == 0
    assert calls == {"date": "2026-05-05", "force": True, "resume": False}
    assert "Fetched 3 materials total" in out
    assert "rss: 2" in out
    assert "twitter: 1" in out


@pytest.mark.hard_gate_3
def test_fetch_invalid_date_returns_two(tmp_home: Path, capsys) -> None:
    home = _mk_home(tmp_home)
    args = argparse.Namespace(date="not-a-date", force=False, resume=False)
    rc = fetch_cmd.cmd_fetch(args, home)
    err = capsys.readouterr().err
    assert rc == 2
    assert "Invalid date format" in err


@pytest.mark.hard_gate_3
def test_fetch_propagates_collector_failure_as_rc_one(
    tmp_home: Path, monkeypatch, capsys
) -> None:
    home = _mk_home(tmp_home)

    def explode(date_str: str, *, force: bool, resume: bool) -> dict:
        raise RuntimeError("collector boom")

    import deep_daily.pipeline as pipeline

    monkeypatch.setattr(pipeline, "collect_shared", explode)

    args = argparse.Namespace(date="2026-05-05", force=False, resume=False)
    rc = fetch_cmd.cmd_fetch(args, home)
    err = capsys.readouterr().err
    assert rc == 1
    assert "Fetch failed" in err
    assert "collector boom" in err


@pytest.mark.hard_gate_3
def test_fetch_defaults_date_to_today_when_absent(tmp_home: Path, monkeypatch) -> None:
    import datetime

    home = _mk_home(tmp_home)
    seen: dict = {}

    def fake_collect(date_str: str, *, force: bool, resume: bool) -> dict:
        seen["date"] = date_str
        return {"materials": []}

    import deep_daily.pipeline as pipeline

    monkeypatch.setattr(pipeline, "collect_shared", fake_collect)

    args = argparse.Namespace(date=None, force=False, resume=False)
    rc = fetch_cmd.cmd_fetch(args, home)
    assert rc == 0
    assert seen["date"] == datetime.date.today().isoformat()
