# Configuration Reference

Each deep-daily HOME contains:

- `config.yaml` — instance settings (schema_version: 1)
- `.env` — API keys and secrets
- `configs/` — business data (topics, sources, KOLs, profile)

This document covers the schema of each.

## `config.yaml`

### `schema_version` (int, required)

Must be `1`. Anything else is a hard ERROR in `doctor`. A future migration
tool will introduce `schema_version: 2`.

### `instance` (mapping)

Free-form metadata about this HOME. Only `name` is used by the pipeline
(shown in some output headers); the rest is for humans.

```yaml
instance:
  name: "my-reader"
  description: "Personal tech-news digest"
  created_at: "2025-11-04T10:00:00+08:00"
  tool_version: "0.3.0"
```

### `models` (mapping)

Per-step model IDs. These are forwarded verbatim to the LLM backend. Use
OpenAI-style IDs; the multikey backend uses the same format.

```yaml
models:
  filter: "google/gemini-2.5-flash-lite"    # Step 1: topic relevance filter
  cluster: "google/gemini-2.5-flash-lite"   # Step 2: event clustering
  write: "google/gemini-3-pro-preview"      # Step 3: long-form write
  appendix: "openai/gpt-4.1-nano"           # Step 4: appendix generation
```

Override the write model at the command line with `--model <id>`.

### `llm` (mapping)

```yaml
llm:
  backend: "openai"   # or "multikey" (or "david" — deprecated alias)
```

- `openai` — single endpoint. Reads `LLM_API_BASE` and `LLM_API_KEY`.
- `multikey` — LiteLLM proxy with round-robin across multiple keys. Reads
  `LITELLM_API_BASE` and `LITELLM_API_KEYS` (newline-separated).

Override at runtime with `--llm-backend openai|multikey`.

### `reader` (mapping)

```yaml
reader:
  name: "my-reader"
  notify:
    topic_id: "my-reader.daily-report"
    event_id: "daily_report_ready"
    dedupe_prefix: "daily_report_my-reader"
```

`reader.name` is the logical identity used for per-reader output paths and
profile caching. `notify.*` keys are consumed by the Feishu publisher if
enabled.

### `profile` (mapping)

```yaml
profile:
  auto_refresh: false      # run profile generator automatically before each pipeline
  max_age_hours: 168       # cached profile TTL (7 days)
```

The reader profile lives at `configs/profile.yaml` and is summarized into
`data/reader-profile.cache.yaml` the first time it's used. Setting
`auto_refresh: true` triggers regeneration on `run` when the cache is older
than `max_age_hours`.

### `collectors` (mapping)

Per-source toggles. All collectors respect the `enabled` key; downstream keys
tune thresholds.

```yaml
collectors:
  rss:
    enabled: true
    per_source_cap: 10         # max items per feed per day
  twitter:
    enabled: true
    long_threshold: 400        # chars — above this counts as "long"
    lane_cap_normal: 15        # max normal tweets per KOL
    lane_cap_long: 8           # max long tweets per KOL
  twitter_nas:
    enabled: false             # optional NAS-backed tweet cache
  news_6551:
    enabled: false             # curated news source (requires config)
```

### `pipeline` (mapping)

Global pipeline knobs.

```yaml
pipeline:
  cleanup_max_age_days: 14          # auto-prune articles/tweets older than this
  reported_events_ttl_days: 7       # how long to remember what we reported
  ongoing_cap_per_topic: 1          # max "ongoing thread" items per topic
  title_sim_match_threshold: 0.7    # dedupe cosine threshold
```

### `publisher` (mapping)

```yaml
publisher:
  default: "file"              # "file" or "feishu"
  feishu:
    enabled: false
    webhook_env: "FEISHU_WEBHOOK"
```

- `default` — publisher used by `run` when no `--publisher` flag is passed.
- `feishu.enabled` — if true, `doctor` requires `FEISHU_WEBHOOK` to be set.
- `feishu.webhook_env` — name of the env var holding the webhook URL.

Under `--dry-run`, the publisher is **always** forced to `file`, regardless
of config.yaml or CLI flags.

## `.env`

Flat KEY=VALUE file. Read automatically at startup. Example:

```bash
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-...

# For multikey backend only
LITELLM_API_BASE=http://localhost:4000
LITELLM_API_KEYS=sk-one
sk-two
sk-three

# Optional — only needed if publisher.feishu.enabled=true
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...

# Optional — override the session-memory directory
SESSION_MEMORY_DIR=
```

`doctor` validates:

- `LLM_API_BASE` + `LLM_API_KEY` — required if `llm.backend=openai`
- `LITELLM_API_BASE` — required if `llm.backend=multikey` (also treated as
  fallback alias for `LLM_API_BASE`)
- `FEISHU_WEBHOOK` — required only if `publisher.feishu.enabled=true`

Missing required env vars are ERRORs; missing optional ones are WARNs.

## `configs/` — business data

### `topics.yaml`

Topic taxonomy the filter model assigns each article to. A list of objects:

```yaml
topics:
  - id: "ai-research"
    description: "AI research papers, new models, benchmark results"
  - id: "macro"
    description: "Central bank decisions, inflation, rates"
```

Topic `id` must be stable — it's used as a dedupe key and output section
anchor. Changing an `id` retroactively will reshuffle historical state.

### `sources.yaml`

RSS feed list:

```yaml
sources:
  - id: "techcrunch"
    url: "https://techcrunch.com/feed/"
    topic_hints: ["ai-research", "startups"]
```

### `kols.json`

Twitter account list (JSON because the legacy format was JSON):

```json
{
  "kols": [
    {
      "handle": "karpathy",
      "twitter_id": "33836629",
      "topic_hints": ["ai-research"]
    }
  ]
}
```

### `profile.yaml`

The reader's persona. Drives tone, technical depth, and priority ordering
in the write step. See `templates/default/configs/profile.yaml.tmpl` for
the expected shape.

### `active-systems.yaml` and `6551-config.json`

Optional. Used only when the corresponding collectors are enabled.

## Placeholder rendering

Templates under `templates/default/` use `{placeholder}` syntax. At `init`
time the following are filled in:

| Placeholder | Source |
|---|---|
| `{reader_name}` | `--reader-name` flag or target dir basename |
| `{llm_backend}` | `openai` (default) |
| `{feishu_enabled}` | `false` (default) |
| `{created_at}` | ISO-8601 timestamp of init |
| `{tool_version}` | `deep_daily.__version__` |

The rendered files are plain YAML/JSON — no templating at runtime. Edit them
directly.
