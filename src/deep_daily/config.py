from __future__ import annotations

import os
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from deep_daily.runtime import seed_if_missing


CLEANUP_MAX_AGE_DAYS = 14
PER_SOURCE_CAP = 10
TWEET_LONG_THRESHOLD = 400
LANE_CAP_TWEET_NORMAL = 15
LANE_CAP_TWEET_LONG = 8

REPORTED_EVENTS_TTL_DAYS = 7
ONGOING_CAP_PER_TOPIC = 1
TITLE_SIM_MATCH_THRESHOLD = 0.7
TITLE_SIM_GRAY_LOW = 0.4

DEFAULT_READER_SNIPPET = (
    "读者是加密货币行业从业者，关注行业整体动态和技术发展。"
)

DEFAULT_ACTIVE_SYSTEMS = (
    "RSS pipeline, browser-use automation, LLM daily digest, "
    "crypto twitter monitoring, Feishu bot, email digest, launchd service management"
)


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


def _default_data_root() -> Path:
    return Path.home() / ".david" / "data" / "rss"


def _default_configs_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "configs"


def build_app_config(
    *,
    data_root: Path | None = None,
    configs_dir: Path | None = None,
) -> AppConfig:
    root = Path(data_root) if data_root is not None else _default_data_root()
    cfg_dir = Path(configs_dir) if configs_dir is not None else _default_configs_dir()
    cfg = AppConfig(
        data_root=root,
        configs_dir=cfg_dir,
        articles_dir=root / "articles",
        tweets_dir=root / "tweets",
        tweets_nas_dir=root / "tweets-nas",
        dailies_dir=root / "dailies",
        pipeline_dir=root / "dailies" / ".pipeline",
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
    seed_if_missing(cfg.kols_seed_path, cfg.kols_path)
    return cfg


_app_config = build_app_config()

DATA_DIR = _app_config.data_root
ARTICLES_DIR = _app_config.articles_dir
TWEETS_DIR = _app_config.tweets_dir
TWEETS_NAS_DIR = _app_config.tweets_nas_dir
DAILIES_DIR = _app_config.dailies_dir
PIPELINE_DIR = _app_config.pipeline_dir
PROFILE_PATH = _app_config.profile_path
ACTIVE_SYSTEMS_PATH = _app_config.active_systems_path
TOPICS_YAML_PATH = _app_config.topics_yaml_path
DYNAMIC_TOPICS_PATH = _app_config.dynamic_topics_path
DYNAMIC_KOLS_PATH = _app_config.dynamic_kols_path
KOLS_PATH = _app_config.kols_path
_KOLS_SEED_PATH = _app_config.kols_seed_path
REPORTED_EVENTS_PATH = _app_config.reported_events_path
READERS_YAML_PATH = _app_config.readers_yaml_path
NEWS_SOURCES_YAML_PATH = _app_config.news_sources_yaml_path


def configure_paths(
    *,
    data_root: Path | None = None,
    configs_dir: Path | None = None,
) -> None:
    global _app_config
    global DATA_DIR, ARTICLES_DIR, TWEETS_DIR, TWEETS_NAS_DIR, DAILIES_DIR, PIPELINE_DIR
    global PROFILE_PATH, ACTIVE_SYSTEMS_PATH, TOPICS_YAML_PATH, DYNAMIC_TOPICS_PATH
    global DYNAMIC_KOLS_PATH, KOLS_PATH, _KOLS_SEED_PATH, REPORTED_EVENTS_PATH
    global READERS_YAML_PATH, NEWS_SOURCES_YAML_PATH
    _app_config = build_app_config(data_root=data_root, configs_dir=configs_dir)
    DATA_DIR = _app_config.data_root
    ARTICLES_DIR = _app_config.articles_dir
    TWEETS_DIR = _app_config.tweets_dir
    TWEETS_NAS_DIR = _app_config.tweets_nas_dir
    DAILIES_DIR = _app_config.dailies_dir
    PIPELINE_DIR = _app_config.pipeline_dir
    PROFILE_PATH = _app_config.profile_path
    ACTIVE_SYSTEMS_PATH = _app_config.active_systems_path
    TOPICS_YAML_PATH = _app_config.topics_yaml_path
    DYNAMIC_TOPICS_PATH = _app_config.dynamic_topics_path
    DYNAMIC_KOLS_PATH = _app_config.dynamic_kols_path
    KOLS_PATH = _app_config.kols_path
    _KOLS_SEED_PATH = _app_config.kols_seed_path
    REPORTED_EVENTS_PATH = _app_config.reported_events_path
    READERS_YAML_PATH = _app_config.readers_yaml_path
    NEWS_SOURCES_YAML_PATH = _app_config.news_sources_yaml_path


def get_app_config() -> AppConfig:
    return _app_config


def _load_reader_profile(profile_path: Path | None = None) -> str:
    path = profile_path or get_app_config().profile_path
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
    path = systems_path or get_app_config().active_systems_path
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
    app_cfg = get_app_config()

    p_path = pinned_path or app_cfg.topics_yaml_path
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

    d_path = dynamic_path or app_cfg.dynamic_topics_path
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
            print(f"  WARNING: Failed to load dynamic-topics.json: {e}", file=sys.stderr)

    return config


_READER_ID_RE = re.compile(r"^[a-z0-9-]+$")


def _load_readers_config(path: Path) -> list[ReaderConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "readers" not in raw:
        raise ValueError(f"readers.yaml must contain a 'readers' key: {path}")

    defaults = raw.get("defaults", {})
    app_cfg = get_app_config()
    data_root = Path(os.path.expanduser(defaults.get("data_root", str(app_cfg.data_root))))
    readers: list[ReaderConfig] = []
    seen_ids: set[str] = set()

    for entry in raw["readers"]:
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue

        rid = entry.get("reader_id", "")
        if not rid:
            raise ValueError("reader entry missing reader_id")
        if not _READER_ID_RE.match(rid):
            raise ValueError(f"reader_id '{rid}' must match [a-z0-9-]")
        if rid in seen_ids:
            raise ValueError(f"duplicate reader_id '{rid}'")
        seen_ids.add(rid)

        def _resolve(key: str, fallback: str | None = None) -> str:
            val = entry.get(key) or defaults.get(key) or fallback or ""
            return os.path.expanduser(val)

        profile_path_str = _resolve("profile", str(app_cfg.profile_path))
        profile_snippet = _load_reader_profile(Path(profile_path_str))

        pinned_path = Path(_resolve("topics_pinned", str(app_cfg.topics_yaml_path)))
        dynamic_path = Path(_resolve("topic_dynamic", str(app_cfg.dynamic_topics_path)))
        topic_config = _load_topic_config(pinned_path, dynamic_path)

        systems_path_str = _resolve("active_systems", str(app_cfg.active_systems_path))
        active_systems = _load_active_systems(Path(systems_path_str))

        if entry.get("output_dir"):
            out_dir = Path(os.path.expanduser(entry["output_dir"]))
        elif rid == "david":
            out_dir = app_cfg.dailies_dir
        else:
            out_dir = data_root / "readers" / rid / "dailies"

        if entry.get("cache_dir"):
            cache_dir = Path(os.path.expanduser(entry["cache_dir"]))
        else:
            cache_dir = out_dir / ".pipeline"

        notify_defaults = defaults.get("notify", {})
        notify_entry = entry.get("notify", {})
        notify = {**notify_defaults, **notify_entry}
        if not notify.get("topic_id"):
            raise ValueError(f"reader '{rid}': notify.topic_id is required")
        if not notify.get("dedupe_prefix"):
            notify["dedupe_prefix"] = f"rss_daily_report_{rid}"

        readers.append(
            ReaderConfig(
                reader_id=rid,
                profile_snippet=profile_snippet,
                topic_config=topic_config,
                output_dir=out_dir,
                cache_dir=cache_dir,
                notification=notify,
                active_systems=active_systems,
            )
        )

    if not readers:
        raise ValueError(f"No enabled readers found in {path}")
    return readers


def _build_default_reader() -> ReaderConfig:
    app_cfg = get_app_config()
    return ReaderConfig(
        reader_id="david",
        profile_snippet=_load_reader_profile(),
        topic_config=_load_topic_config(),
        output_dir=app_cfg.dailies_dir,
        cache_dir=app_cfg.pipeline_dir,
        notification={
            "topic_id": "work.rss.daily-report",
            "event_id": "rss_daily_report_ready",
            "dedupe_prefix": "rss_daily_report",
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
