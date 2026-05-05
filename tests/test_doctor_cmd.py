"""Tests for ``deep-daily doctor`` — PLAN v2.1 §7 Step 16."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pytest

from deep_daily.commands.doctor_cmd import (
    SEVERITY_ERROR,
    SEVERITY_OK,
    SEVERITY_WARN,
    cmd_doctor,
    format_results_json,
    format_results_text,
    run_doctor,
)
from deep_daily.home import HomeConfig


FULL_CONFIG_YAML = """\
schema_version: 1
instance:
  name: test-reader
models:
  filter: m1
llm:
  backend: multikey
reader:
  name: test-reader
  notify:
    topic_id: test.daily
collectors:
  rss:
    enabled: true
pipeline:
  cleanup_max_age_days: 14
publisher:
  default: file
  feishu:
    enabled: false
"""


def _write_home_with_full_config(
    tmp_home: Path, extras: dict | None = None
) -> HomeConfig:
    (tmp_home / "config.yaml").write_text(FULL_CONFIG_YAML, encoding="utf-8")
    (tmp_home / "configs" / "topics.yaml").write_text("pinned: []\n", encoding="utf-8")
    if extras:
        for name, content in extras.items():
            (tmp_home / "configs" / name).write_text(content, encoding="utf-8")
    return HomeConfig.load(tmp_home)


@pytest.mark.hard_gate_3
def test_run_doctor_on_fresh_home_with_full_config_reports_no_errors(
    tmp_home: Path,
) -> None:
    home = _write_home_with_full_config(
        tmp_home,
        extras={
            "sources.yaml": "sources: []\n",
            "kols.json": "[]\n",
            "profile.yaml": "prompt_snippet: hi\n",
            "active-systems.yaml": "systems: []\n",
            "news-6551-config.json": "{}\n",
        },
    )

    def fake_env(name: str) -> str | None:
        return {
            "LLM_API_BASE": "http://localhost:8000",
            "LLM_API_KEY": "sk-test",
        }.get(name)

    results = run_doctor(home, deep=False, getenv=fake_env)

    assert not any(r.severity == SEVERITY_ERROR for r in results), [
        (r.name, r.message) for r in results if r.severity == SEVERITY_ERROR
    ]
    ok_names = {r.name for r in results if r.severity == SEVERITY_OK}
    assert "home.sentinel" in ok_names
    assert "home.config_yaml" in ok_names
    assert "config.schema_version" in ok_names
    assert "data.writable" in ok_names


@pytest.mark.hard_gate_3
def test_run_doctor_flags_missing_required_env_as_error(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)

    def no_env(name: str) -> str | None:
        return None

    results = run_doctor(home, deep=False, getenv=no_env)
    errors = [r for r in results if r.severity == SEVERITY_ERROR]
    names = {r.name for r in errors}
    assert "env.LLM_API_BASE" in names
    assert "env.LLM_API_KEY" in names


@pytest.mark.hard_gate_3
def test_run_doctor_accepts_litellm_aliases_for_llm_envs(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)

    def litellm_only(name: str) -> str | None:
        return {
            "LITELLM_API_BASE": "http://localhost:8000",
            "LITELLM_API_KEY": "sk-litellm",
        }.get(name)

    results = run_doctor(home, deep=False, getenv=litellm_only)
    by_name = {r.name: r for r in results}
    assert by_name["env.LLM_API_BASE"].severity == SEVERITY_OK
    assert by_name["env.LLM_API_KEY"].severity == SEVERITY_OK
    assert "satisfied via" in by_name["env.LLM_API_BASE"].message


@pytest.mark.hard_gate_3
def test_run_doctor_requires_feishu_webhook_when_feishu_enabled(tmp_home: Path) -> None:
    cfg = FULL_CONFIG_YAML.replace(
        "feishu:\n    enabled: false",
        "feishu:\n    enabled: true\n    webhook_env: FEISHU_WEBHOOK",
    )
    (tmp_home / "config.yaml").write_text(cfg, encoding="utf-8")
    (tmp_home / "configs" / "topics.yaml").write_text("pinned: []\n", encoding="utf-8")
    home = HomeConfig.load(tmp_home)

    def llm_only(name: str) -> str | None:
        return {"LLM_API_BASE": "http://x", "LLM_API_KEY": "sk"}.get(name)

    results = run_doctor(home, deep=False, getenv=llm_only)
    error_names = {r.name for r in results if r.severity == SEVERITY_ERROR}
    assert "env.FEISHU_WEBHOOK" in error_names


@pytest.mark.hard_gate_3
def test_run_doctor_flags_missing_required_configs_file_as_error(
    tmp_home: Path,
) -> None:
    (tmp_home / "config.yaml").write_text(FULL_CONFIG_YAML, encoding="utf-8")
    home = HomeConfig.load(tmp_home)

    def env(name: str) -> str | None:
        return {"LLM_API_BASE": "x", "LLM_API_KEY": "x"}.get(name)

    results = run_doctor(home, deep=False, getenv=env)
    errors = [r for r in results if r.severity == SEVERITY_ERROR]
    assert any(r.name == "configs.topics.yaml" for r in errors)


@pytest.mark.hard_gate_3
def test_run_doctor_flags_bad_llm_backend_as_error(tmp_home: Path) -> None:
    bad_cfg = FULL_CONFIG_YAML.replace("backend: multikey", "backend: banana")
    (tmp_home / "config.yaml").write_text(bad_cfg, encoding="utf-8")
    (tmp_home / "configs" / "topics.yaml").write_text("pinned: []\n", encoding="utf-8")
    home = HomeConfig.load(tmp_home)

    def env(name: str) -> str | None:
        return {"LLM_API_BASE": "x", "LLM_API_KEY": "x"}.get(name)

    results = run_doctor(home, deep=False, getenv=env)
    names = {r.name: r for r in results if r.severity == SEVERITY_ERROR}
    assert "config.llm.backend" in names


@pytest.mark.hard_gate_3
def test_cmd_doctor_returns_zero_on_clean_home(
    tmp_home: Path, monkeypatch, capsys
) -> None:
    home = _write_home_with_full_config(tmp_home)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(deep=False, json=False)
    rc = cmd_doctor(args, home)
    captured = capsys.readouterr()
    assert rc == 0
    assert "deep-daily doctor" in captured.out
    assert "Summary:" in captured.out


@pytest.mark.hard_gate_3
def test_cmd_doctor_returns_nonzero_on_missing_env(
    tmp_home: Path, monkeypatch, capsys
) -> None:
    home = _write_home_with_full_config(tmp_home)
    for name in ("LLM_API_BASE", "LLM_API_KEY", "LITELLM_API_BASE", "LITELLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    args = argparse.Namespace(deep=False, json=False)
    rc = cmd_doctor(args, home)
    captured = capsys.readouterr()
    assert rc == 1
    assert "✗" in captured.out


@pytest.mark.hard_gate_3
def test_cmd_doctor_emits_json_when_requested(
    tmp_home: Path, monkeypatch, capsys
) -> None:
    home = _write_home_with_full_config(tmp_home)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(deep=False, json=True)
    rc = cmd_doctor(args, home)
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["home"] == str(home.path)
    assert "checks" in payload and isinstance(payload["checks"], list)
    assert "summary" in payload
    assert {"ok", "warn", "error"} == set(payload["summary"].keys())


@pytest.mark.hard_gate_3
def test_deep_probe_skipped_when_envs_absent(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)
    results = run_doctor(home, deep=True, getenv=lambda n: None)
    probe = [r for r in results if r.name == "llm.probe"]
    assert probe, "deep probe should produce a result"
    assert probe[0].severity == SEVERITY_WARN
    assert "skipped" in probe[0].message


@pytest.mark.hard_gate_3
def test_deep_probe_is_warn_on_unreachable_host(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)

    def env(name: str) -> str | None:
        return {
            "LLM_API_BASE": "http://127.0.0.1:1",
            "LLM_API_KEY": "sk-bogus",
        }.get(name)

    results = run_doctor(home, deep=True, getenv=env)
    probe = [r for r in results if r.name == "llm.probe"]
    assert probe
    assert probe[0].severity == SEVERITY_WARN


@pytest.mark.hard_gate_3
def test_format_results_text_counts_severities(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)
    results = run_doctor(home, deep=False, getenv=lambda n: None)
    text = format_results_text(home, results)
    assert text.endswith("\n")
    assert "Summary:" in text
    assert str(home.path) in text


@pytest.mark.hard_gate_3
def test_format_results_json_is_valid_json(tmp_home: Path) -> None:
    home = _write_home_with_full_config(tmp_home)
    results = run_doctor(home, deep=False, getenv=lambda n: None)
    text = format_results_json(home, results)
    parsed = json.loads(text)
    assert parsed["summary"]["ok"] + parsed["summary"]["warn"] + parsed["summary"][
        "error"
    ] == len(parsed["checks"])
