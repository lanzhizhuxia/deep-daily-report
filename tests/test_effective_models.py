"""Tests for per-instance model resolution (Phase 2.5 Oracle fix).

Protects the invariant: editing config.yaml's ``models.*`` actually changes
which model the pipeline calls. Before this fix, ``models.*`` was dead code.

Precedence chain under test (from PLAN §7 Phase 2.5, Oracle v3):
  WRITE:    --model CLI  > $DAILY_WRITE_MODEL    > config.yaml models.write    > default
  FILTER:   (no CLI)     > $DAILY_FILTER_MODEL   > config.yaml models.filter   > default
  CLUSTER:  (no CLI)     > $DAILY_CLUSTER_MODEL  > config.yaml models.cluster  > default
  APPENDIX: (no CLI)     > $DAILY_APPENDIX_MODEL > config.yaml models.appendix > default
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest


def test_resolve_falls_back_to_defaults_when_all_sources_empty(isolated_runtime):
    """All 4 slots pick DEFAULT_MODELS when config, env, and CLI are all absent."""
    from deep_daily.config import DEFAULT_MODELS, resolve_effective_models

    result = resolve_effective_models(raw_config={}, env={}, cli_write_model=None)

    assert result.filter == DEFAULT_MODELS["filter"]
    assert result.cluster == DEFAULT_MODELS["cluster"]
    assert result.write == DEFAULT_MODELS["write"]
    assert result.appendix == DEFAULT_MODELS["appendix"]


def test_resolve_prefers_config_yaml_over_defaults(isolated_runtime):
    """config.yaml models.* override hardcoded defaults."""
    from deep_daily.config import resolve_effective_models

    raw = {
        "models": {
            "filter": "anthropic/claude-haiku-5",
            "cluster": "openai/gpt-5-nano",
            "write": "google/gemini-3.1-pro-preview",
            "appendix": "openai/gpt-4.1-mini",
        }
    }
    result = resolve_effective_models(raw_config=raw, env={}, cli_write_model=None)

    assert result.filter == "anthropic/claude-haiku-5"
    assert result.cluster == "openai/gpt-5-nano"
    assert result.write == "google/gemini-3.1-pro-preview"
    assert result.appendix == "openai/gpt-4.1-mini"


def test_env_overrides_config_for_all_four_slots(isolated_runtime):
    """Env vars beat config.yaml (backward compat for existing shell setups)."""
    from deep_daily.config import resolve_effective_models

    raw = {
        "models": {
            "filter": "cfg-f",
            "cluster": "cfg-c",
            "write": "cfg-w",
            "appendix": "cfg-a",
        }
    }
    env = {
        "DAILY_FILTER_MODEL": "env-f",
        "DAILY_CLUSTER_MODEL": "env-c",
        "DAILY_WRITE_MODEL": "env-w",
        "DAILY_APPENDIX_MODEL": "env-a",
    }
    result = resolve_effective_models(raw_config=raw, env=env, cli_write_model=None)

    assert result.filter == "env-f"
    assert result.cluster == "env-c"
    assert result.write == "env-w"
    assert result.appendix == "env-a"


def test_cli_model_flag_beats_env_and_config_but_write_only(isolated_runtime):
    """--model CLI flag wins for WRITE only. Other slots ignore it by design."""
    from deep_daily.config import resolve_effective_models

    raw = {"models": {"filter": "cfg-f", "write": "cfg-w"}}
    env = {"DAILY_FILTER_MODEL": "env-f", "DAILY_WRITE_MODEL": "env-w"}

    result = resolve_effective_models(raw_config=raw, env=env, cli_write_model="cli-w")

    assert result.write == "cli-w"
    assert result.filter == "env-f"


def test_singleton_raises_before_set(isolated_runtime):
    """get_effective_models() must raise if resolver wasn't called first.

    Guards against pipeline code sneaking a read without the CLI bootstrap
    doing set_effective_models() first.
    """
    import deep_daily.config as c

    importlib.reload(c)

    with pytest.raises(RuntimeError, match="has not been resolved"):
        c.get_effective_models()


def test_config_yaml_edit_changes_pipeline_model_integration(
    tmp_home, isolated_runtime, monkeypatch
):
    """INTEGRATION: full path from instance config.yaml → pipeline's resolved model.

    This is THE test that would have caught the Phase 2.5 bug. Before the fix,
    ``models.write`` in config.yaml was dead text — editing it had zero effect.
    """
    (tmp_home / "config.yaml").write_text(
        "schema_version: 1\n"
        "reader:\n  name: test\n"
        "llm:\n  backend: multikey\n"
        "models:\n"
        "  filter: test-filter-model\n"
        "  cluster: test-cluster-model\n"
        "  write: test-write-model\n"
        "  appendix: test-appendix-model\n",
        encoding="utf-8",
    )

    for var in (
        "DAILY_FILTER_MODEL",
        "DAILY_CLUSTER_MODEL",
        "DAILY_WRITE_MODEL",
        "DAILY_APPENDIX_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    from deep_daily.config import resolve_effective_models
    from deep_daily.home import HomeConfig

    home = HomeConfig.load(tmp_home)
    result = resolve_effective_models(
        raw_config=home.raw_config, env={}, cli_write_model=None
    )

    assert result.filter == "test-filter-model"
    assert result.cluster == "test-cluster-model"
    assert result.write == "test-write-model"
    assert result.appendix == "test-appendix-model"


def test_template_defaults_match_code_defaults_no_drift():
    """Template config.yaml.tmpl ``models:`` block must list the same values as
    code's DEFAULT_MODELS dict. If these ever drift, new instances get
    misleading user-facing defaults."""
    from deep_daily.config import DEFAULT_MODELS

    repo_root = Path(__file__).parent.parent
    tmpl = repo_root / "templates" / "default" / "config.yaml.tmpl"
    assert tmpl.exists(), f"template missing: {tmpl}"

    text = tmpl.read_text(encoding="utf-8")

    match = re.search(r"^models:\s*\n((?:[ \t]+.*\n)+)", text, re.MULTILINE)
    assert match, "templates/default/config.yaml.tmpl has no `models:` block"
    block = match.group(1)

    for slot, expected in DEFAULT_MODELS.items():
        slot_match = re.search(
            rf'^\s+{slot}:\s*["\']?([^"\'\n]+)["\']?\s*$', block, re.MULTILINE
        )
        assert slot_match, f"template missing `models.{slot}` line"
        actual = slot_match.group(1).strip()
        assert actual == expected, (
            f"drift on models.{slot}: template has {actual!r}, "
            f"code DEFAULT_MODELS has {expected!r}"
        )
