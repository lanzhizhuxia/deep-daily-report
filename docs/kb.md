# Knowledge Base (`deep_daily kb`)

Historical ingest/query/MCP surface for the HOME-local SQLite knowledge base.
Owner of PRD-002. Read this before touching `src/deep_daily/kb/` or the KB
launchd plist.

## What the command does

`deep_daily kb` has four operator-facing surfaces:

- `kb ingest` — scan raw runtime files under `$DEEP_DAILY_HOME/data/`, normalize
  them, and upsert into `data/kb/kb.db`
- `kb query` — run FTS + metadata filters against `kb.db`
- `kb stats` — summarize corpus size and latest ingest counters
- `kb mcp` — run the stdio MCP server, or install Claude Desktop config via
  `--install-claude-desktop`

Database path:

- `~/.local/deep-daily-daily/data/kb/kb.db` for the maintainer's live HOME

## Schedule

- **launchd**: `com.example.deep-daily-kb-ingest` at 04:00 daily
- **wrapper**: `~/.local/deep-daily/scripts/start-deep-daily-kb-ingest.sh`
- **plist**: `~/.local/deep-daily/launchagents/com.example.deep-daily-kb-ingest.plist`
  (symlinked into `~/Library/LaunchAgents/`)
- **retry**: single retry 30 minutes after first failure; exit 1 after that
- **command**: `python -m deep_daily --home "$DEEP_DAILY_HOME" kb ingest`

## Watermark behavior

Incremental ingest is file-based. For each raw file, KB stores:

- path
- source
- last mtime
- last size
- last status

Skip rule: if the file already has `status="ok"` and both `mtime` + `size`
match the last successful ingest, that file is skipped.

Practical effect:

- unchanged files are cheap on subsequent runs
- one modified raw file does **not** force a full rebuild
- `--rebuild` ignores watermark state and recreates the DB

Latest live verification on 2026-05-05: `files_scanned=6500`,
`files_skipped=6500`, `files_ok=0`, `files_failed=0` for the scheduled
incremental run.

## Local state

`~/.local/deep-daily-daily/state/` includes:

- `kb-ingest.lock` — JSON pid lock for ingest exclusion
- ingest metadata in `kb.db` tables `ingest_runs` + `ingest_files`

Current lock semantics are simpler than backup's PRD-001 lock:

- if lock file exists and pid is alive: fail fast with `Lock held:`
- if pid is dead: remove stale lock and continue
- no host field, no force-unlock flag, no explicit host-mismatch handling

This matches the PRD-002 stale-pid recovery requirement, but not the richer
backup metadata model.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Ingest/query/stats/MCP operation succeeded |
| 1 | Lock held, ingest failure, query sqlite error, or MCP runtime failure |
| 2 | Invalid CLI value (for example bad `--since` or unsupported source) |

For `kb ingest`, `Lock held: ...` is emitted to stderr before exit 1.

## Manual operations

### Run ad-hoc incremental ingest

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb ingest
```

### Rebuild from raw files

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb ingest --rebuild
```

### Limit ingest to newer files

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb ingest --since 2026-05-01T00:00:00Z
```

### Trigger via launchd

```bash
launchctl kickstart -k gui/$(id -u)/com.example.deep-daily-kb-ingest
tail -f ~/.local/deep-daily/logs/deep-daily-kb-ingest.stdout.log
```

### Query examples

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb query "RWA" --source tweet --start 2026-04-01 --end 2026-04-30
python -m deep_daily --home ~/.local/deep-daily-daily kb query "BlackRock stablecoin" --limit 10 --json
python -m deep_daily --home ~/.local/deep-daily-daily kb query "AI" --author balajis
```

### Stats example

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb stats
python -m deep_daily --home ~/.local/deep-daily-daily kb stats --json
```

### MCP examples

```bash
python -m deep_daily --home ~/.local/deep-daily-daily kb mcp --install-claude-desktop
python -m deep_daily --home ~/.local/deep-daily-daily kb mcp
```

MCP surface shipped in v1:

- `search_text`
- `get_item`
- `stats`

## Claude Desktop integration

- installer command: `python -m deep_daily --home ~/.local/deep-daily-daily kb mcp --install-claude-desktop`
- config is merged into the user's Claude Desktop config file selected by the
  installer
- Claude Desktop restart is required after installation or updates

## Disabling

```bash
launchctl bootout gui/$(id -u)/com.example.deep-daily-kb-ingest
```

To re-enable later: `launchctl bootstrap gui/$(id -u) <plist-path>`.

## Race window with backup

Backup runs at 03:30 with one retry at 04:00. KB ingest is scheduled at 04:00.
Locks are separate, so launchd will start both if backup is retrying.

Known tradeoff for v1:

- backup may archive `data/kb/kb.db` while ingest is writing to SQLite
- current live DB reports `PRAGMA journal_mode=delete`
- SQLite recovery should still be possible via rollback journal semantics, but
  the archive can capture a mid-transaction state and is theoretically unclean

Operational recommendation: if this overlap is seen in the wild, move KB ingest
to 04:30 or later.

## Cutover history

- **2026-05-05**: M1-M5 landed; no legacy predecessor.

See `docs/prd/002-kb.md` for the full spec.
