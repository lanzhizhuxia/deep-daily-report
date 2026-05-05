# Backup (`deep_daily backup`)

Nightly tarball of the full HOME to NAS. Owner of PRD-001. Read this before
touching anything under `src/deep_daily/backup/` or the launchd plist.

## What gets backed up

Archive is rooted at `$DEEP_DAILY_HOME` (default `~/.local/deep-daily-daily/`). Default
deny-list (configurable in `config.yaml` under `backup.exclude`):

| Path | Why excluded |
|---|---|
| `state/**` | Lockfile, `last.json`, KB `ingest_state` — operational, shouldn't round-trip |
| `logs/**` | Local run logs; NAS is not the log sink |
| `data/dailies/.pipeline/**` | Per-day pipeline cache, regenerable |
| `data/dailies-dryrun/**` | Throwaway dry-run output |
| `data/.session-memory/**` | Ephemeral session scratch |

**Everything else is in**, including:

- `config.yaml`, `.deep-daily-home` sentinel
- `.env` (⚠️ secrets — NAS is treated as sensitive storage; backup logs a
  stderr warning every run)
- `configs/` (readers.yaml, topics.yaml, active-systems.yaml, etc.)
- `data/articles/`, `data/tweets/`, `data/dailies/*.html+json`, `data/kb.db`

Typical archive: ~67 MB gzip, ~6500–6600 files.

## Schedule

- **launchd**: `com.example.deep-daily-backup` at 03:30 daily
- **wrapper**: `~/.local/deep-daily/scripts/start-deep-daily-backup.sh`
- **plist**: `~/.local/deep-daily/launchagents/com.example.deep-daily-backup.plist`
  (symlinked into `~/Library/LaunchAgents/`)
- **retry**: single retry 30 min after first failure; exit 1 after that

## NAS layout

```
<NAS_BASE>/<NAS_DIR>/
  <INSTANCE_NAME>-YYYYMMDD-HHMMSSZ.tar.gz      # atomic; only present once fully verified
  <INSTANCE_NAME>-YYYYMMDD-HHMMSSZ.tar.gz.part # in-flight; cleaned > 48h old
```

Timestamp is UTC. `.part → .tar.gz` rename happens only after remote sha256
matches local.

Retention: 30 newest kept; older pruned per backup run.

## Local state

`~/.local/deep-daily-daily/state/backup/`:

- `backup.lock` — flock held during run (pid + timestamp inside)
- `last.json` — schema:
  ```json
  {
    "remote": "/volume1/.../<INSTANCE_NAME>-YYYYMMDD-HHMMSSZ.tar.gz",
    "size_bytes": 66812662,
    "sha256": "…",
    "files_included": 6565,
    "duration_seconds": 23,
    "ok": true,
    "finished_at": "2026-05-05T14:30:45Z"
  }
  ```
  `ok: false` records include an `error` field.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Uploaded + verified + pruned |
| 1 | Invalid HOME (missing sentinel or `config.yaml`) |
| 2 | Lock held by another pid |
| 3 | Remote sha256 mismatch after upload (`.part` kept for inspection) |
| 4 | SSH/network failure |
| 5 | Unexpected error |

## Manual operations

### Run ad-hoc

```bash
python -m deep_daily --home ~/.local/deep-daily-daily backup
```

### Dry-run (prints plan, touches nothing)

```bash
python -m deep_daily --home ~/.local/deep-daily-daily backup --dry-run
```

### Trigger via launchd

```bash
launchctl kickstart -k gui/$(id -u)/com.example.deep-daily-backup
tail -f ~/.local/deep-daily/logs/deep-daily-backup.stdout.log
```

### Restore

Synology's `internal-sftp` doesn't speak modern scp; use `ssh cat >` instead:

```bash
LATEST=$(ssh <NAS_USER>@<NAS_HOST> \
  "ls -t <NAS_BASE>/<NAS_DIR>/*.tar.gz | head -1")
ssh <NAS_USER>@<NAS_HOST> "cat '$LATEST'" > /tmp/deep-daily-restore.tar.gz

mkdir -p /tmp/restore-home
tar -xzf /tmp/deep-daily-restore.tar.gz -C /tmp/restore-home
ls /tmp/restore-home/  # config.yaml, .env, configs/, data/, .deep-daily-home
```

To restore into place: stop the daily-report launchd job, move the old HOME
aside, extract the tarball to the target path, confirm sentinel + config
exist, restart launchd jobs.

## Disabling

```bash
launchctl bootout gui/$(id -u)/com.example.deep-daily-backup
```

To re-enable later: `launchctl bootstrap gui/$(id -u) <plist-path>`.

## Cutover history

- **2026-05-05**: Replaced <legacy-bot>'s `com.example.legacy-data-backup` (which backed
  up the legacy `~/.local/deep-daily/data/` HOME) with `com.example.deep-daily-backup`
  targeting `~/.local/deep-daily-daily/`. Legacy NAS artifacts at `.../m4/` are preserved
  as read-only archives (manual cleanup scheduled 3–6 months post-cutover).

See `docs/prd/001-backup.md` for the full spec.
