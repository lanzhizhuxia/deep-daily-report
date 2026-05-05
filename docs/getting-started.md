# Getting Started

deep-daily is a tool for generating topic-filtered daily digests from RSS feeds,
Twitter accounts, and curated news sources. It is installed **once** as code,
but each "digest subject" lives in its own **HOME directory** (like
`git init <dir>` or `terraform init`): configs, runtime data, API keys, and
logs are all scoped to a single HOME.

This separation means one installation of deep-daily can drive multiple
independent digests — e.g. a personal tech-news digest and a team RWA-markets
digest — without their state ever crossing.

## Prerequisites

- Python 3.11+ with access to the `deep_daily` package (install this repo
  in editable mode: `pip install -e .` inside a virtualenv).
- An OpenAI-compatible LLM endpoint (`LLM_API_BASE` + `LLM_API_KEY`), or a
  LiteLLM multi-key proxy if you want round-robin across several keys.
- Optional: a Feishu webhook URL if you want to push the rendered report to
  a Feishu chat.

## 1. Create an instance HOME

Pick any directory on disk. It does **not** need to be inside the tool repo.

```bash
deep-daily init ~/.my-daily
```

This creates:

```
~/.my-daily/
├── .deep-daily-home    # sentinel — marks this dir as a valid HOME
├── config.yaml         # instance config (schema_version: 1)
├── .env                # API keys and secrets (gitignore this)
├── configs/            # topic/source/KOL definitions you'll edit next
│   ├── topics.yaml
│   ├── sources.yaml
│   ├── kols.json
│   ├── active-systems.yaml
│   ├── news-6551-config.json
│   └── profile.yaml
├── data/               # runtime state (articles, tweets, output, caches)
└── logs/
```

## 2. Point deep-daily at the HOME

Every HOME-required subcommand (`run`, `fetch`, `doctor`, `migrate-legacy`)
needs to know which HOME to use. There are three ways:

| Method | Example | When to use |
|---|---|---|
| `--home` flag | `deep-daily --home ~/.my-daily run` | Explicit, always wins |
| `DEEP_DAILY_HOME` env var | `export DEEP_DAILY_HOME=~/.my-daily` | Interactive shells, launchd |
| Walk-up discovery | (no flag, CWD is inside HOME) | Interactive inspection only |

Walk-up only activates for `doctor` and `run --date <YYYY-MM-DD>`. For scheduled
runs (launchd/cron) **always** set `DEEP_DAILY_HOME` or pass `--home` — see
[scheduling.md](scheduling.md).

## 3. Fill in credentials and config

Edit `~/.my-daily/.env` — at minimum, set `LLM_API_BASE` and `LLM_API_KEY`.

Edit `~/.my-daily/configs/*.yaml` to define:

- **topics.yaml** — the topic taxonomy the filter model uses
- **sources.yaml** — RSS feeds to poll
- **kols.json** — Twitter accounts to pull
- **profile.yaml** — the reader's persona (drives the write step's voice)

See [config-reference.md](config-reference.md) for the complete schema.

## 4. Verify the setup

```bash
deep-daily --home ~/.my-daily doctor
```

`doctor` runs six layers of checks (HOME layout, config.yaml schema, configs/
file presence, required env vars, data/ writability, optional LLM probe with
`--deep`). Green means you're ready. See [doctor output reference](#doctor-severity-levels)
below.

## 5. Generate today's digest

```bash
deep-daily --home ~/.my-daily run
```

Common flags:

- `--date 2025-11-01` — generate for a specific date
- `--dry-run` — write to `dailies-dryrun/` only, skip publish, skip state writes
- `--force` — ignore caches and regenerate from scratch
- `--resume` — resume from the most recent cached pipeline step
- `--publisher file|feishu` — override the default publisher
- `--llm-backend openai|multikey` — override `llm.backend` from config.yaml

Output lands at `~/.my-daily/data/dailies/<date>.html` (or `dailies-dryrun/`
under `--dry-run`).

## doctor severity levels

- **OK** — green, no action needed.
- **WARN** — yellow, non-fatal. `doctor` still exits 0. Common causes:
  optional env var missing (FEISHU_WEBHOOK without feishu enabled), LLM
  probe failed under `--deep` (upstream hiccup, not your config).
- **ERROR** — red, exit 1. A hard failure — HOME is malformed, required env
  var missing, schema_version mismatch, data/ not writable.

Scheduling entry points (launchd, cron) should call `doctor --json` at startup
and abort on exit code 1.

## Next steps

- [scheduling.md](scheduling.md) — launchd / cron setup
- [migration-guide.md](migration-guide.md) — migrating from the legacy `~/.local/deep-daily` layout
- [config-reference.md](config-reference.md) — full config.yaml schema
- [architecture.md](architecture.md) — how HOME resolution, init_runtime, and dispatch fit together
