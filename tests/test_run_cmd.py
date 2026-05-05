"""Tests for ``deep-daily run`` orchestration \u2014 PLAN v2.1 \u00a77 Step 17, \u00a75.5.

Pipeline internals are mocked. These tests verify the orchestration contract:
  - LLM backend selection from config.yaml + --llm-backend override
  - Publisher selection with dry-run forcing FilePublisher
  - dry_run flag forwarded to pipeline.cmd_generate via args
  - Profile refresh skipped under dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from deep_daily.commands import run_cmd
from deep_daily.home import HomeConfig


CONFIG_OPENAI = """\
schema_version: 1
llm:
  backend: openai
reader:
  name: test
publisher:
  default: file
"""

CONFIG_MULTIKEY = """\
schema_version: 1
llm:
  backend: multikey
reader:
  name: test
publisher:
  default: file
"""


def _mk_home(tmp_home: Path, config_yaml: str) -> HomeConfig:
    (tmp_home / "config.yaml").write_text(config_yaml, encoding="utf-8")
    return HomeConfig.load(tmp_home)


@pytest.fixture
def stub_pipeline(monkeypatch):
    calls: dict = {}

    def fake_configure(*, llm=None, publisher=None):
        calls["llm"] = llm
        calls["publisher"] = publisher

    def fake_cmd_generate(args):
        calls["args"] = args

    def fake_maybe_refresh():
        calls["refreshed"] = True

    import deep_daily.pipeline as pipeline
    import deep_daily.profile_gen as profile_gen

    monkeypatch.setattr(pipeline, "configure", fake_configure)
    monkeypatch.setattr(pipeline, "cmd_generate", fake_cmd_generate)
    monkeypatch.setattr(profile_gen, "maybe_refresh_profile", fake_maybe_refresh)
    return calls


@pytest.mark.hard_gate_3
def test_run_picks_openai_backend_by_default(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_OPENAI)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=False,
        llm_backend=None,
        publisher=None,
    )
    rc = run_cmd.cmd_run(args, home)
    assert rc == 0

    from deep_daily.backends.openai_compat import OpenAICompatibleBackend

    assert isinstance(stub_pipeline["llm"], OpenAICompatibleBackend)


@pytest.mark.hard_gate_3
def test_run_picks_multikey_backend_when_configured(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_MULTIKEY)
    monkeypatch.setenv("LITELLM_API_BASE", "http://x")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=False,
        llm_backend=None,
        publisher=None,
    )
    rc = run_cmd.cmd_run(args, home)
    assert rc == 0

    from deep_daily.backends.litellm_multikey import LiteLLMMultiKeyBackend

    assert isinstance(stub_pipeline["llm"], LiteLLMMultiKeyBackend)


@pytest.mark.hard_gate_3
def test_cli_override_wins_over_config_backend(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_OPENAI)
    monkeypatch.setenv("LITELLM_API_BASE", "http://x")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=False,
        llm_backend="multikey",
        publisher=None,
    )
    run_cmd.cmd_run(args, home)
    from deep_daily.backends.litellm_multikey import LiteLLMMultiKeyBackend

    assert isinstance(stub_pipeline["llm"], LiteLLMMultiKeyBackend)


@pytest.mark.hard_gate_3
def test_dry_run_forces_file_publisher_even_when_feishu_default(
    tmp_home: Path,
    stub_pipeline,
    monkeypatch,
) -> None:
    cfg = CONFIG_OPENAI.replace("default: file", "default: feishu")
    home = _mk_home(tmp_home, cfg)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=True,
        llm_backend=None,
        publisher=None,
    )
    run_cmd.cmd_run(args, home)

    from deep_daily.publishers.file_publisher import FilePublisher

    assert isinstance(stub_pipeline["publisher"], FilePublisher)


@pytest.mark.hard_gate_3
def test_dry_run_forwards_flag_to_cmd_generate(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_OPENAI)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=True,
        llm_backend=None,
        publisher=None,
    )
    run_cmd.cmd_run(args, home)

    forwarded = stub_pipeline["args"]
    assert forwarded.dry_run is True


@pytest.mark.hard_gate_3
def test_dry_run_skips_profile_refresh(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_OPENAI)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=True,
        llm_backend=None,
        publisher=None,
    )
    run_cmd.cmd_run(args, home)
    assert "refreshed" not in stub_pipeline


@pytest.mark.hard_gate_3
def test_prod_run_invokes_profile_refresh(
    tmp_home: Path, stub_pipeline, monkeypatch
) -> None:
    home = _mk_home(tmp_home, CONFIG_OPENAI)
    monkeypatch.setenv("LLM_API_BASE", "http://x")
    monkeypatch.setenv("LLM_API_KEY", "sk")

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=False,
        llm_backend=None,
        publisher=None,
    )
    run_cmd.cmd_run(args, home)
    assert stub_pipeline.get("refreshed") is True


@pytest.mark.hard_gate_3
def test_unknown_backend_raises(tmp_home: Path, monkeypatch) -> None:
    cfg = CONFIG_OPENAI.replace("backend: openai", "backend: banana")
    home = _mk_home(tmp_home, cfg)

    args = argparse.Namespace(
        date=None,
        force=False,
        resume=False,
        model=None,
        dry_run=False,
        llm_backend=None,
        publisher=None,
    )
    with pytest.raises(RuntimeError, match="Unknown llm.backend"):
        run_cmd.cmd_run(args, home)
