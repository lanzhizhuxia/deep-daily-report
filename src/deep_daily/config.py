"""Runtime configuration module.

Per PLAN v2.1 §5.2 (strict singleton contract), §11.3 (init_runtime responsibilities),
§11.4 (prohibited patterns).

Key invariants (HARD GATE 1 enforces):
  - NO I/O at module import time. build_app_config() is pure.
  - One init_runtime(home) call per process. Same-HOME re-call is a no-op;
    different-HOME raises RuntimeAlreadyInitializedError.
  - Legacy `config.ARTICLES_DIR`-style attribute reads are served via PEP 562
    __getattr__, which raises RuntimeError if accessed before init_runtime().

Legacy constants (DATA_DIR, ARTICLES_DIR, ...) are a Phase 1 compatibility shim
for ~30 call sites in pipeline.py. They will be migrated to explicit
get_runtime().app.<field> access in Phase 2. DO NOT extend _LEGACY_CONST_MAP
with new attributes — add new fields to AppConfig and use get_runtime() directly.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

import yaml

if TYPE_CHECKING:
    from deep_daily.home import HomeConfig


CLEANUP_MAX_AGE_DAYS = 14
PER_SOURCE_CAP = 10
TWEET_LONG_THRESHOLD = 400
LANE_CAP_TWEET_NORMAL = 15
LANE_CAP_TWEET_LONG = 8

REPORTED_EVENTS_TTL_DAYS = 7
ONGOING_CAP_PER_TOPIC = 1
TITLE_SIM_MATCH_THRESHOLD = 0.7
TITLE_SIM_GRAY_LOW = 0.4

DEFAULT_READER_SNIPPET = "读者是加密货币行业从业者，关注行业整体动态和技术发展。"

DEFAULT_ACTIVE_SYSTEMS = "daily news digest pipeline"


# ---------------------------------------------------------------- model config
# Per Oracle v3 (session ses_2099a0e7fffeXJqUbsDN4TIX4r, 2026-05-05):
# Per-instance config.yaml is the source of truth for model selection, with env
# vars as backward-compat override and --model CLI flag kept for WRITE only.
#
# Precedence (high to low):
#   WRITE:    --model CLI  → $DAILY_WRITE_MODEL    → config.yaml models.write    → DEFAULT_MODELS["write"]
#   FILTER:   (no CLI)     → $DAILY_FILTER_MODEL   → config.yaml models.filter   → DEFAULT_MODELS["filter"]
#   CLUSTER:  (no CLI)     → $DAILY_CLUSTER_MODEL  → config.yaml models.cluster  → DEFAULT_MODELS["cluster"]
#   APPENDIX: (no CLI)     → $DAILY_APPENDIX_MODEL → config.yaml models.appendix → DEFAULT_MODELS["appendix"]
#
# These hardcoded defaults are the canonical fallbacks. templates/default/
# config.yaml.tmpl mirrors these values for user visibility, but code owns the
# truth. Do not duplicate literals across sites — always read from here.
DEFAULT_MODELS: dict[str, str] = {
    "filter": "google/gemini-2.5-flash-lite",
    "cluster": "google/gemini-2.5-flash-lite",
    "write": "google/gemini-3-pro-preview",
    "appendix": "openai/gpt-4.1-nano",
}


@dataclass(frozen=True)
class EffectiveModels:
    """Resolved model choices for a single run.

    Immutable snapshot — constructed once at command entry (run_cmd.py) after
    HOME is loaded and CLI args are parsed. Pipeline code reads from this via
    :func:`get_effective_models` rather than hitting env vars ad hoc.
    """

    filter: str
    cluster: str
    write: str
    appendix: str


def resolve_effective_models(
    raw_config: Mapping[str, Any],
    env: Mapping[str, str],
    cli_write_model: str | None,
) -> EffectiveModels:
    """Resolve the effective model set per the precedence chain above.

    Pure function — no I/O, no singleton mutation. Call once per run from
    command entry and pass the result around (or store it via
    :func:`set_effective_models`).
    """
    cfg = raw_config.get("models") or {}
    if not isinstance(cfg, Mapping):
        cfg = {}

    def pick(slot: str, env_var: str, cli: str | None = None) -> str:
        if cli:
            return cli
        env_val = env.get(env_var)
        if env_val:
            return env_val
        cfg_val = cfg.get(slot)
        if cfg_val:
            return str(cfg_val)
        return DEFAULT_MODELS[slot]

    return EffectiveModels(
        filter=pick("filter", "DAILY_FILTER_MODEL"),
        cluster=pick("cluster", "DAILY_CLUSTER_MODEL"),
        write=pick("write", "DAILY_WRITE_MODEL", cli_write_model),
        appendix=pick("appendix", "DAILY_APPENDIX_MODEL"),
    )


_effective_models: EffectiveModels | None = None


def set_effective_models(models: EffectiveModels) -> None:
    """Bind the process-wide EffectiveModels. Idempotent on identical input,
    rebinds otherwise (supports test reconfiguration)."""
    global _effective_models
    _effective_models = models


def get_effective_models() -> EffectiveModels:
    """Return the active EffectiveModels. Raises if :func:`set_effective_models`
    has not been called — this means the CLI bootstrap skipped the resolution
    step, which is a bug."""
    if _effective_models is None:
        raise RuntimeError(
            "EffectiveModels has not been resolved. The CLI entry point must "
            "call set_effective_models(resolve_effective_models(...)) before "
            "invoking the pipeline. See config.py or run_cmd.py for the pattern."
        )
    return _effective_models


def reset_effective_models_for_tests() -> None:
    """Test-only helper. DO NOT call from production code."""
    global _effective_models
    _effective_models = None


@dataclass
class ReaderConfig:
    reader_id: str
    profile_snippet: str
    topic_config: dict
    output_dir: Path
    cache_dir: Path
    notification: dict
    active_systems: str


@dataclass
class AppConfig:
    data_root: Path
    configs_dir: Path
    articles_dir: Path
    tweets_dir: Path
    tweets_nas_dir: Path
    dailies_dir: Path
    pipeline_dir: Path
    dailies_dryrun_dir: Path
    pipeline_dryrun_dir: Path
    profile_path: Path
    active_systems_path: Path
    topics_yaml_path: Path
    dynamic_topics_path: Path
    dynamic_kols_path: Path
    kols_path: Path
    kols_seed_path: Path
    reported_events_path: Path
    readers_yaml_path: Path
    news_sources_yaml_path: Path


@dataclass
class RuntimeConfig:
    home: "HomeConfig"
    app: AppConfig


class RuntimeAlreadyInitializedError(RuntimeError):
    """Raised when init_runtime() is called a second time with a different HOME."""


_runtime: RuntimeConfig | None = None


def build_app_config(*, data_root: Path, configs_dir: Path) -> AppConfig:
    """Pure factory — construct AppConfig from two root paths. No I/O."""
    root = Path(data_root)
    cfg_dir = Path(configs_dir)
    return AppConfig(
        data_root=root,
        configs_dir=cfg_dir,
        articles_dir=root / "articles",
        tweets_dir=root / "tweets",
        tweets_nas_dir=root / "tweets-nas",
        dailies_dir=root / "dailies",
        pipeline_dir=root / "dailies" / ".pipeline",
        dailies_dryrun_dir=root / "dailies-dryrun",
        pipeline_dryrun_dir=root / "dailies-dryrun" / ".pipeline",
        profile_path=root / "reader-profile.yaml",
        active_systems_path=root / "active-systems.yaml",
        topics_yaml_path=cfg_dir / "topics.yaml",
        dynamic_topics_path=root / "dynamic-topics.json",
        dynamic_kols_path=root / "dynamic-kols.json",
        kols_path=root / "twitter-kols.json",
        kols_seed_path=cfg_dir / "twitter-kols.json.seed",
        reported_events_path=root / "reported_events.json",
        readers_yaml_path=cfg_dir / "readers.yaml",
        news_sources_yaml_path=cfg_dir / "news-sources.yaml",
    )


def seed_runtime_files_if_missing(app: AppConfig) -> None:
    """Idempotent, atomic seeding of runtime files that must exist before the
    pipeline runs. Currently: twitter-kols.json from twitter-kols.json.seed.

    Called from init_runtime(), not from build_app_config(). Safe to call many
    times — only writes when target is missing. Atomic via tmp + os.replace.
    """
    if app.kols_path.exists():
        return
    seed = app.kols_seed_path
    if not seed.exists():
        return
    app.kols_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = app.kols_path.with_suffix(app.kols_path.suffix + ".tmp")
    tmp.write_bytes(seed.read_bytes())
    os.replace(tmp, app.kols_path)


def _ensure_data_dirs(home: "HomeConfig") -> None:
    """Create all required data/ subdirs per PLAN v2.1 §3.3. Idempotent."""
    data = home.data_dir
    for sub in ("articles", "tweets", "tweets-nas", "news-6551", ".session-memory"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    for parent in ("dailies", "dailies-dryrun"):
        (data / parent).mkdir(parents=True, exist_ok=True)
        (data / parent / ".pipeline").mkdir(parents=True, exist_ok=True)


def init_runtime(home: "HomeConfig") -> None:
    """Bind the process-wide runtime configuration to `home`. See PLAN v2.1 §5.2.

    First call sets _runtime. Second call with same (normalized) HOME is a no-op.
    Second call with a different HOME raises RuntimeAlreadyInitializedError.
    """
    global _runtime
    normalized = home.path.resolve()

    if _runtime is not None:
        existing = _runtime.home.path.resolve()
        if existing == normalized:
            return
        raise RuntimeAlreadyInitializedError(
            f"init_runtime called twice with different HOMEs: "
            f"existing={existing}, new={normalized}. "
            f"One process = one HOME (PLAN v2.1 §5.2)."
        )

    _ensure_data_dirs(home)
    app = build_app_config(data_root=home.data_dir, configs_dir=home.configs_dir)
    seed_runtime_files_if_missing(app)
    _runtime = RuntimeConfig(home=home, app=app)


def get_runtime() -> RuntimeConfig:
    if _runtime is None:
        raise RuntimeError(
            "init_runtime() has not been called. This is a bootstrap/import "
            "ordering bug: the CLI entry point must call init_runtime(home) "
            "before any pipeline code runs. See PLAN v2.1 §5.1 / §11.1."
        )
    return _runtime


def get_app_config() -> AppConfig:
    """Return the active AppConfig. Phase 1 compatibility accessor — prefer
    get_runtime().app in new code."""
    return get_runtime().app


_LEGACY_CONST_MAP: dict[str, Callable[[RuntimeConfig], Any]] = {
    "DATA_DIR": lambda r: r.app.data_root,
    "ARTICLES_DIR": lambda r: r.app.articles_dir,
    "TWEETS_DIR": lambda r: r.app.tweets_dir,
    "TWEETS_NAS_DIR": lambda r: r.app.tweets_nas_dir,
    "DAILIES_DIR": lambda r: r.app.dailies_dir,
    "PIPELINE_DIR": lambda r: r.app.pipeline_dir,
    "PROFILE_PATH": lambda r: r.app.profile_path,
    "ACTIVE_SYSTEMS_PATH": lambda r: r.app.active_systems_path,
    "TOPICS_YAML_PATH": lambda r: r.app.topics_yaml_path,
    "DYNAMIC_TOPICS_PATH": lambda r: r.app.dynamic_topics_path,
    "DYNAMIC_KOLS_PATH": lambda r: r.app.dynamic_kols_path,
    "KOLS_PATH": lambda r: r.app.kols_path,
    "REPORTED_EVENTS_PATH": lambda r: r.app.reported_events_path,
    "READERS_YAML_PATH": lambda r: r.app.readers_yaml_path,
    "NEWS_SOURCES_YAML_PATH": lambda r: r.app.news_sources_yaml_path,
}


def __getattr__(name: str) -> Any:
    """PEP 562 lazy accessor for legacy path constants. See module docstring."""
    if name in _LEGACY_CONST_MAP:
        return _LEGACY_CONST_MAP[name](get_runtime())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _load_reader_profile(profile_path: Path | None = None) -> str:
    path = profile_path or get_runtime().app.profile_path
    if not path.exists():
        return DEFAULT_READER_SNIPPET
    try:
        text = path.read_text(encoding="utf-8")
        mtime = path.stat().st_mtime
        if time.time() - mtime > 86400:
            print("  reader-profile.yaml is stale (>24h)", file=sys.stderr)

        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("prompt_snippet:"):
                rest = line[len("prompt_snippet:") :].strip()
                if rest and rest not in (">", "|", ">-", "|-"):
                    if (rest.startswith('"') and rest.endswith('"')) or (
                        rest.startswith("'") and rest.endswith("'")
                    ):
                        return rest[1:-1]
                    return rest
                block_lines: list[str] = []
                for subsequent in lines[i + 1 :]:
                    if subsequent and (subsequent[0] == " " or subsequent[0] == "\t"):
                        block_lines.append(subsequent.strip())
                    elif not subsequent.strip():
                        block_lines.append("")
                    else:
                        break
                return "\n".join(block_lines).strip()
        return DEFAULT_READER_SNIPPET
    except Exception:
        return DEFAULT_READER_SNIPPET


def _load_active_systems(systems_path: Path | None = None) -> str:
    path = systems_path or get_runtime().app.active_systems_path
    if not path.exists():
        return DEFAULT_ACTIVE_SYSTEMS
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "systems" in raw:
            parts: list[str] = []
            for s in raw["systems"]:
                if isinstance(s, dict) and s.get("name"):
                    name = s["name"]
                    detail = s.get("detail", "")
                    parts.append(f"{name} ({detail})" if detail else name)
            return ", ".join(parts) if parts else DEFAULT_ACTIVE_SYSTEMS
        return DEFAULT_ACTIVE_SYSTEMS
    except Exception:
        return DEFAULT_ACTIVE_SYSTEMS


def _load_topic_config(
    pinned_path: Path | None = None,
    dynamic_path: Path | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"pinned": [], "dynamic": [], "dynamic_max": 4}
    app = get_runtime().app

    p_path = pinned_path or app.topics_yaml_path
    if p_path.exists():
        try:
            raw = yaml.safe_load(p_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                config["dynamic_max"] = int(raw.get("dynamic_max", 4))
                for t in raw.get("pinned", []):
                    if isinstance(t, dict) and t.get("slug"):
                        config["pinned"].append(
                            {
                                "slug": t["slug"],
                                "label": t.get("label", ""),
                                "keywords": t.get("keywords", []),
                                "exclude_if_in": t.get("exclude_if_in", []),
                                "news_search_terms": t.get("news_search_terms", []),
                            }
                        )
        except Exception as e:
            print(f"  WARNING: Failed to load topics.yaml: {e}", file=sys.stderr)

    d_path = dynamic_path or app.dynamic_topics_path
    if d_path.exists():
        try:
            with open(d_path, encoding="utf-8") as f:
                raw_dyn = json.load(f)
            for t in raw_dyn.get("topics", []):
                if isinstance(t, dict) and t.get("slug"):
                    config["dynamic"].append(
                        {
                            "slug": t["slug"],
                            "label": t.get("label", t["slug"]),
                            "keywords": t.get("keywords", []),
                        }
                    )
            config["dynamic"] = config["dynamic"][: config["dynamic_max"]]
        except Exception as e:
            print(
                f"  WARNING: Failed to load dynamic-topics.json: {e}", file=sys.stderr
            )

    return config


def build_default_reader_from_home(*, dry_run: bool = False) -> ReaderConfig:
    """Construct the single ReaderConfig for this HOME.

    Post multi-reader removal (PLAN v2.1 §2.1): one HOME = one reader. Reader
    identity comes from config.yaml (reader.name, reader.notify.*), not from a
    hardcoded id or a readers.yaml file. Paths come from the active runtime.

    When dry_run=True, output_dir and cache_dir point at the isolated
    ``dailies-dryrun/`` tree (PLAN v2.1 §5.5). This guarantees dry-run writes
    never touch the prod ``dailies/`` tree and never share step caches with
    prod — a contract Oracle v2 marked as required.
    """
    runtime = get_runtime()
    raw = runtime.home.raw_config.get("reader", {}) or {}
    notify = raw.get("notify", {}) or {}

    reader_id = str(raw.get("name") or runtime.home.path.name)
    topic_id = notify.get("topic_id") or f"{reader_id}.daily-report"
    dedupe_prefix = notify.get("dedupe_prefix") or f"daily_report_{reader_id}"
    event_id = notify.get("event_id") or "daily_report_ready"

    if dry_run:
        output_dir = runtime.app.dailies_dryrun_dir
        # Escalation fallback per PLAN v2.1 §5.5: if the isolated .pipeline/ tree
        # proves too coupled to prod, set DEEP_DAILY_DRYRUN_DISABLE_CACHE=1 to
        # route dry-run cache writes to a parallel dead-letter dir that no prod
        # code path reads from. Wired now; gated behind the env flag.
        if os.environ.get("DEEP_DAILY_DRYRUN_DISABLE_CACHE"):
            cache_dir = runtime.app.dailies_dryrun_dir / ".pipeline-disabled"
        else:
            cache_dir = runtime.app.pipeline_dryrun_dir
    else:
        output_dir = runtime.app.dailies_dir
        cache_dir = runtime.app.pipeline_dir

    return ReaderConfig(
        reader_id=reader_id,
        profile_snippet=_load_reader_profile(),
        topic_config=_load_topic_config(),
        output_dir=output_dir,
        cache_dir=cache_dir,
        notification={
            "topic_id": topic_id,
            "event_id": event_id,
            "dedupe_prefix": dedupe_prefix,
        },
        active_systems=_load_active_systems(),
    )


LANE_CONFIGS = {
    "rss": {"label": "📰 深度文章", "max_topics": 3, "skip_1b": True},
    "tweet-long": {
        "label": "🧵 推特深度",
        "max_topics": 2,
        "skip_1b": lambda mats: len(mats) < 20,
    },
    "tweet-normal": {"label": "🐦 推特速览", "max_topics": 3, "skip_1b": False},
}
