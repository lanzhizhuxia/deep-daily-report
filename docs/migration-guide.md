# Migration Guide — legacy `~/.local/deep-daily` layout → v0.3.0 HOME

This guide walks you from the original "everything lives at `~/.local/deep-daily/`" layout
to a v0.3.0 instance HOME. It is written around the canonical legacy path
(`~/.local/deep-daily/legacy-data/`) but the same steps apply to any pre-v0.3.0 install.

## Scope

`migrate-legacy` handles **runtime data only**:

- `articles/`
- `tweets/`, `tweets-nas/`
- `news-6551/`
- `dailies/` (historical output)
- `.session-memory/`
- `reported_events.json` (copied last, under `LOCK_EX`)

**Business configs are NOT auto-migrated.** Files like `topics.yaml`,
`sources.yaml`, `kols.json`, and `profile.yaml` must be copied by hand. This
scope-cut was deliberate — the legacy configs live next to the tool code, and
a one-pass tool would be silently fragile once the tool repo is updated. Doing
the copy manually, once, keeps the cutover auditable.

## Step 0 — what you need

- deep-daily v0.3.0 installed and importable.
- The legacy tree still present at `~/.local/deep-daily/legacy-data/` (or wherever yours
  lives). Do NOT delete it during migration — it's your rollback.
- A target HOME path (pick any path you own).

## Step 1 — init a fresh HOME

```bash
deep-daily init ~/.local/deep-daily-daily
```

This creates the HOME with template-rendered configs. Those templates are
**placeholders** — we'll replace them with your real configs in the next step.

## Step 2 — copy business configs by hand

From the legacy tool repo (wherever you kept `topics.yaml` etc. — typically
a `configs/` or `tools/rss/` directory), copy each file into the new HOME's
`configs/` directory, overwriting the rendered placeholders:

```bash
# Adjust source paths to your legacy layout
cp /path/to/legacy/topics.yaml           ~/.local/deep-daily-daily/configs/topics.yaml
cp /path/to/legacy/sources.yaml          ~/.local/deep-daily-daily/configs/sources.yaml
cp /path/to/legacy/kols.json             ~/.local/deep-daily-daily/configs/kols.json
cp /path/to/legacy/active-systems.yaml   ~/.local/deep-daily-daily/configs/active-systems.yaml
cp /path/to/legacy/6551-config.json      ~/.local/deep-daily-daily/configs/6551-config.json
cp /path/to/legacy/profile.yaml          ~/.local/deep-daily-daily/configs/profile.yaml
```

Open `~/.local/deep-daily-daily/config.yaml` and update:

- `instance.name` and `reader.name` — your reader identity
- `reader.notify.topic_id`, `event_id`, `dedupe_prefix` — match your legacy values
- `llm.backend` — `openai` or `multikey` as before
- `publisher.feishu.enabled` — set to `true` if you were publishing to Feishu

## Step 3 — copy credentials

```bash
cp /path/to/legacy/.env ~/.local/deep-daily-daily/.env
```

Then open `~/.local/deep-daily-daily/.env` and confirm `LLM_API_BASE`, `LLM_API_KEY`,
`FEISHU_WEBHOOK` (if used), and any other keys are present.

## Step 4 — dry-run the runtime-data migration

```bash
deep-daily --home ~/.local/deep-daily-daily migrate-legacy \
    --from ~/.local/deep-daily/legacy-data \
    --dry-run
```

This prints the migration plan without touching the target:

```
DRY RUN migrate-legacy plan
  source: <HOME>/.david/data/rss
  target: <HOME>/.david-daily/data
  files : 6487
  bytes : 428904512
  plus  : reported_events.json (copied under LOCK_EX)
    articles/: 4210 files
    tweets/: 1890 files
    ...
```

Verify the file counts roughly match your expectations.

## Step 5 — execute the migration

Make sure no legacy pipeline run is active. If you have a launchd job, either
wait until after 07:15, or `launchctl bootout` it temporarily. Then:

```bash
deep-daily --home ~/.local/deep-daily-daily migrate-legacy --from ~/.local/deep-daily/legacy-data
```

What happens:

1. deep-daily probes `LOCK_EX` on `reported_events.json.lock` — if held by
   a legacy writer, aborts with a precondition error.
2. Checks `reported_events.json` mtime — if modified in the last 120 seconds,
   aborts (a legacy run may have just finished).
3. Builds a **snapshot manifest** (sha256 of every source file) at
   `<HOME>/data/MIGRATION-MANIFEST.json` with `status: in_progress`.
4. Copies and verifies every file. Each verification promotes that entry's
   status to `verified` and persists the manifest atomically.
5. Copies `reported_events.json` LAST, while holding `LOCK_EX` on the source
   lock, so a concurrent legacy writer can never corrupt the snapshot.
6. Sets manifest `status: completed` and prints a cutover runbook.

### Resumption semantics

If the migration is interrupted (Ctrl-C, crash, power loss), just re-run the
same command. The tool:

- Re-reads the manifest.
- **Re-hashes every source file** and aborts if any sha drifted, or if any
  new files appeared in the source, since the snapshot. This is deliberate —
  a stale snapshot must never silently carry old data into the target.
- Skips entries already marked `verified`.
- Continues from the first `pending` entry.

If drift is detected, delete the target `data/` subtree and re-run to take a
fresh snapshot.

## Step 6 — validate the new HOME

```bash
deep-daily --home ~/.local/deep-daily-daily doctor
```

All six check categories should be OK or WARN. Any ERROR must be resolved
before cutover.

## Step 7 — dry-run a real pipeline run

```bash
deep-daily --home ~/.local/deep-daily-daily run --date $(date +%F) --dry-run
```

Output goes to `~/.local/deep-daily-daily/data/dailies-dryrun/<date>.html`. Open it in
a browser and diff it visually against the last run from the legacy pipeline.
`reported_events.json` is **not** touched (dry-run is strict read-only for
state files).

If the output looks right, you're ready for cutover.

## Step 8 — cut over launchd

```bash
# 1. Stop the legacy job
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist

# 2. Edit the plist (or create a new one) so it points at the new HOME:
#    - ProgramArguments: /path/to/venv/bin/deep-daily run
#    - EnvironmentVariables/DEEP_DAILY_HOME: <HOME>/.david-daily

# 3. Re-bootstrap
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist
```

See [scheduling.md](scheduling.md) for the full plist template.

## Step 9 — keep the legacy tree for 30 days

Do **not** delete `~/.local/deep-daily/legacy-data/` for at least 30 days after cutover. If
you find a subtle bug (missing KOL, miscounted dedupe), rollback is just:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist
# Edit plist back to original paths
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist
```

After 30 days of green runs, it's safe to archive the legacy tree.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Source lock is held` | Legacy pipeline still running | Wait or `launchctl bootout` |
| `reported_events.json was modified Ns ago` | Legacy run just finished | Wait ~2 minutes |
| `Inside launchd window (06:45-07:15)` | Migration might race the 07:00 run | Wait until 07:15, or pass `--confirm-near-schedule` |
| `Target is not empty but has no MIGRATION-MANIFEST.json` | Target has unrelated content | Remove foreign content or pick a clean target |
| `Manifest status is "completed"` | You've already migrated here | Pass `--force` to re-migrate |
| `Source drift detected on resume` | Legacy pipeline ran between resume attempts | Delete target `data/` and restart |
| `Refusing to follow symlink` | Source tree contains a symlink | Resolve the symlink manually and retry |

## Post-cutover checklist

- [ ] Legacy launchd job stopped and removed
- [ ] New launchd job running on `DEEP_DAILY_HOME=~/.local/deep-daily-daily`
- [ ] First scheduled run succeeded (check `logs/stdout.log`)
- [ ] Output file matches expected format and size
- [ ] Feishu delivery confirmed (if enabled)
- [ ] `deep-daily doctor` clean
- [ ] 30-day legacy retention window noted on calendar
