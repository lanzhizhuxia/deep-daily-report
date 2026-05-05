# Architecture

This document describes how deep-daily resolves state at runtime, and why
the separation between "tool code" and "instance HOME" is load-bearing.

## Design goals

1. **Tool ≠ Data.** The repo holds code + templates + docs. All runtime
   state — configs, keys, articles, output — lives in a user-chosen HOME.
2. **One tool, many instances.** Nothing in the tool prevents running a
   personal digest and a team digest simultaneously, in different HOMEs.
3. **No I/O at import time.** Importing `deep_daily` is safe on hostile
   filesystems, with missing env vars, or under `pytest` without any
   fixtures. All I/O starts at `init_runtime(home)`.
4. **Explicit cutover paths.** Every destructive action (migrate-legacy,
   force init) has a safety contract and a rollback story.

## HOME directory layout

```
<HOME>/
├── .deep-daily-home      # sentinel (any content) — marks this dir as a HOME
├── config.yaml           # instance config
├── .env                  # secrets
├── configs/              # business data (user-edited)
│   ├── topics.yaml
│   ├── sources.yaml
│   ├── kols.json
│   ├── active-systems.yaml
│   ├── 6551-config.json
│   └── profile.yaml
├── data/                 # runtime state (tool-managed)
│   ├── articles/
│   ├── tweets/
│   ├── tweets-nas/
│   ├── news-6551/
│   ├── dailies/          # published output
│   ├── dailies-dryrun/   # dry-run output (isolated)
│   ├── .session-memory/
│   └── reported_events.json
└── logs/
```

The `.deep-daily-home` sentinel is required — `HomeConfig.load()` refuses
to treat any directory as a HOME without it, so accidents like pointing at
`$HOME` directly are impossible.

## CLI surface

```
deep-daily init <path> [--force] [--yes] [--reader-name NAME]
deep-daily templates list
deep-daily version

deep-daily [--home PATH] run [--date DATE] [--force] [--resume] [--dry-run]
                              [--publisher file|feishu]
                              [--llm-backend openai|multikey]
                              [--model MODEL]
deep-daily [--home PATH] fetch [--collectors ...]
deep-daily [--home PATH] doctor [--deep] [--json]
deep-daily [--home PATH] migrate-legacy [--from PATH] [--force] [--dry-run]
                                        [--confirm-near-schedule]
```

Subcommands split into two classes:

- **HOME-free**: `init`, `templates`, `version`. Never touch HomeConfig,
  never call init_runtime.
- **HOME-required**: `run`, `fetch`, `doctor`, `migrate-legacy`. Resolve a
  HOME and call `init_runtime(home)` exactly once before dispatching.

## HOME resolution

`HomeConfig.resolve()` consults sources in this order:

1. `--home <path>` flag on the command line
2. `$DEEP_DAILY_HOME` environment variable
3. **Walk-up discovery** — starting at CWD, ascend toward `/` looking for
   a `.deep-daily-home` sentinel. Stops at `$HOME` or at filesystem root.
4. Otherwise, `HomeNotFoundError` with an actionable message.

**Walk-up is opt-in per subcommand.** Only `doctor` and `run --date <YYYY-MM-DD>`
enable it. `run` (no date), `fetch`, and `migrate-legacy` require explicit
`--home`/env — a daemon has no reliable CWD, and silent HOME guessing in
scheduled contexts is a large foot-gun.

If both `--home` and `$DEEP_DAILY_HOME` are set and differ, `--home` wins
and a warning goes to stderr.

## init_runtime: the narrow bootstrap contract

`init_runtime(home)` is intentionally tiny. It does exactly four things:

1. Enforces a strict singleton — calling it twice with the same HOME is a
   no-op; calling it with a different HOME in the same process is an error.
2. Ensures required `data/` subdirectories exist (articles, tweets,
   tweets-nas, news-6551, dailies, dailies-dryrun, dailies/.pipeline,
   dailies-dryrun/.pipeline, .session-memory).
3. Atomically seeds `data/twitter-kols.runtime.json` from
   `configs/kols.json` if missing (write-to-tmp + `os.replace`).
4. Binds the runtime config singleton.

It explicitly does **NOT** do:

- Env var validation (owned by `doctor`)
- Logging handler setup (done by each subcommand at entry)
- Fetching or publishing
- Auto-refreshing the reader profile

Every responsibility added to `init_runtime` becomes load-bearing for every
test and every child process. Keeping scope minimal is a deliberate
trust-maximization move.

## Dry-run semantics (strict)

`run --dry-run` is pre-cutover safety. Under dry-run, the pipeline is
**read-only with respect to state**:

| Action | Non-dry-run | Dry-run |
|---|---|---|
| Output HTML | `data/dailies/<date>.html` | `data/dailies-dryrun/<date>.html` |
| Publish | `publisher.default` | forced to `file` |
| Update `reported_events.json` | yes | **skipped** |
| Update `reader-profile.cache.yaml` | yes | **skipped** |
| Pipeline cache dir | `dailies/.pipeline` | `dailies-dryrun/.pipeline` |
| Per-reader delivered keys write | yes | **skipped** |

This isolation makes it safe to run dry-runs repeatedly during migration
diffing without poisoning production state.

## Error-class surface

Each command-layer module defines its own precondition / state-error split
so that the CLI dispatcher can map to exit codes:

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | Mid-flight failure (state may be partial; manifest is truth) |
| 2 | Precondition failure (user fixes and retries) |

For `migrate-legacy`, `MigrationPreconditionError` → rc=2 and
`MigrationStateError` → rc=1. For `doctor`, any ERROR-severity check → rc=1;
WARN-only runs → rc=0. For HOME resolution failures → rc=2.

## Prompt caching discipline

The report-writing LLM step benefits from stable system prompts across
invocations. The configs that shape the prompt (`topics.yaml`, `profile.yaml`,
`kols.json`) are read once per run — mid-run reloads would invalidate the
cache. Editing these files between runs is always safe; editing them during
a run is supported only at Python-subprocess boundaries.

## Why `MIGRATION-MANIFEST.json` lives in `data/`

The migration manifest is physically inside the target HOME, not alongside
the tool repo. Two reasons:

1. The manifest is a property of the target, not the tool. If you delete
   the target `data/` you're explicitly restarting migration.
2. A HOME can be migrated to and then moved or archived as a unit. Keeping
   the manifest inline preserves the audit trail.

On resume, the manifest is treated as a **snapshot**, not as a live plan:
every source file is re-hashed against the stored sha256 and any drift
aborts the resume. This guarantees no stale source data is silently
carried forward if the legacy pipeline ran between resume attempts.

## Testing strategy

- **HARD GATE 1** (`test_no_import_side_effects`, `test_double_init`) — the
  bootstrap contract. Until these pass, no command code is trustworthy.
- **HARD GATE 2** (`test_dry_run_isolation`) — dry-run never writes to
  production state paths. Enforced with identity-grep across path constants.
- **HARD GATE 3** — end-to-end smoke on a fresh `/tmp/test-daily` HOME.
  Covers init → doctor → run --dry-run → migrate-legacy.

See `tests/conftest.py` for the shared `tmp_home` / `isolated_runtime`
fixtures that let every command-layer test hermetically construct a HOME.
