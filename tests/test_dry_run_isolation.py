"""HARD GATE 2 — dry-run cache isolation contract.

Per PLAN v2.1 §7 Step 13, §5.5 --dry-run semantics.

Oracle v2 marked dry-run cache isolation as a hard requirement. The rationale:

  Intermediate artifacts under ``.pipeline/`` encode filtered inputs, clustering
  decisions, and model outputs. A shared cache would let a prod run reuse
  dry-run state (produced with different flags and potentially different
  inputs), silently undermining the whole point of dry-run: comparing prod and
  dry-run outputs should be comparing two independent pipeline executions.

Four contracts this module enforces (PLAN v2.1 Step 13):

  (a) Prod runs never write under ``data/dailies-dryrun/``.
  (b) Dry-runs never write under ``data/dailies/``.
  (c) A dry-run's cache path does not reference the prod ``.pipeline/`` tree
      — no silent reads of prod state.
  (d) Escalation fallback: setting ``DEEP_DAILY_DRYRUN_DISABLE_CACHE=1``
      redirects dry-run cache writes to a parallel dead-letter directory.
      This is wired but gated, per PLAN v2.1 §5.5 escalation trigger. Step 13
      asserts the mechanism exists; Step 17 decides when to flip the flag.

These tests MUST stay green for HARD GATE 2 to pass.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.hard_gate_2


def _init_runtime_at(home_path: Path) -> None:
    """Load HomeConfig from ``home_path`` and bind the process-wide runtime.

    Each test depends on ``isolated_runtime`` to reset the singleton afterwards,
    so calling this once per test is safe.
    """
    from deep_daily import config as cfg_mod
    from deep_daily.home import HomeConfig

    home = HomeConfig.load(home_path)
    cfg_mod.init_runtime(home)


def test_prod_output_stays_outside_dryrun_tree(tmp_home, isolated_runtime):
    """Contract (a): prod run's reader config must point inside ``dailies/``,
    never under ``dailies-dryrun/``."""
    _init_runtime_at(tmp_home)
    from deep_daily import config as cfg_mod

    reader = cfg_mod.build_default_reader_from_home(dry_run=False)

    dryrun_root = (tmp_home / "data" / "dailies-dryrun").resolve()
    assert dryrun_root not in reader.output_dir.resolve().parents
    assert reader.output_dir.resolve() != dryrun_root
    assert dryrun_root not in reader.cache_dir.resolve().parents
    assert reader.cache_dir.resolve() != dryrun_root


def test_dryrun_output_stays_inside_dryrun_tree(tmp_home, isolated_runtime):
    """Contract (b): dry-run's reader config must point inside ``dailies-dryrun/``
    (both output_dir and cache_dir), never touching the prod ``dailies/`` tree."""
    _init_runtime_at(tmp_home)
    from deep_daily import config as cfg_mod

    reader = cfg_mod.build_default_reader_from_home(dry_run=True)

    dryrun_root = (tmp_home / "data" / "dailies-dryrun").resolve()
    prod_root = (tmp_home / "data" / "dailies").resolve()

    assert (
        dryrun_root in reader.output_dir.resolve().parents
        or reader.output_dir.resolve() == dryrun_root
    )
    assert (
        dryrun_root in reader.cache_dir.resolve().parents
        or reader.cache_dir.resolve() == dryrun_root
    )

    assert reader.output_dir.resolve() != prod_root
    assert prod_root not in reader.output_dir.resolve().parents
    assert reader.cache_dir.resolve() != prod_root
    assert prod_root not in reader.cache_dir.resolve().parents


def test_dryrun_cache_does_not_alias_prod_cache(tmp_home, isolated_runtime):
    """Contract (c): the path used for dry-run cache reads/writes is strictly
    distinct from the prod cache path. Nothing about a dry-run touches the
    prod ``.pipeline/`` tree, either for reads or writes."""
    _init_runtime_at(tmp_home)
    from deep_daily import config as cfg_mod

    prod = cfg_mod.build_default_reader_from_home(dry_run=False)
    dry = cfg_mod.build_default_reader_from_home(dry_run=True)

    assert prod.cache_dir.resolve() != dry.cache_dir.resolve()

    assert dry.cache_dir.resolve() not in prod.cache_dir.resolve().parents
    assert prod.cache_dir.resolve() not in dry.cache_dir.resolve().parents


def test_escalation_flag_redirects_dryrun_cache(
    tmp_home, isolated_runtime, monkeypatch
):
    """Contract (d): when ``DEEP_DAILY_DRYRUN_DISABLE_CACHE=1`` is set, the
    dry-run cache_dir is redirected to a disabled sink that no prod code path
    reads from. The mechanism is wired in Step 13 and gated behind the env flag;
    Step 17 will decide whether to flip it for real runs."""
    _init_runtime_at(tmp_home)
    from deep_daily import config as cfg_mod

    default = cfg_mod.build_default_reader_from_home(dry_run=True)
    expected_default = (tmp_home / "data" / "dailies-dryrun" / ".pipeline").resolve()
    assert default.cache_dir.resolve() == expected_default

    monkeypatch.setenv("DEEP_DAILY_DRYRUN_DISABLE_CACHE", "1")
    escalated = cfg_mod.build_default_reader_from_home(dry_run=True)
    expected_escalated = (
        tmp_home / "data" / "dailies-dryrun" / ".pipeline-disabled"
    ).resolve()
    assert escalated.cache_dir.resolve() == expected_escalated

    prod_pipeline = (tmp_home / "data" / "dailies" / ".pipeline").resolve()
    assert escalated.cache_dir.resolve() != prod_pipeline
    assert prod_pipeline not in escalated.cache_dir.resolve().parents
