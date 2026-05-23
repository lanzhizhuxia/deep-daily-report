# PRD 001: NAS Backup Migration

> Status: **DRAFT v2.1** — scope expanded from `<HOME>/data/` to full HOME after Phase 1b wet-run findings; Oracle reconsult approved (bg_aeba6353)
> Author: [maintainer]
> Reviewed by: Oracle (ses_207ff0be0ffeWE2VDjoq0iO47b → ses_207f5a226ffeXVDlqhMs4XQ3G7 → bg_aeba6353)
> Related: ISSUE-095 (HOME migration); supersedes `<legacy-bot>/scripts/data-backup.sh`

## TL;DR

Replace the broken `<legacy-bot>/scripts/data-backup.sh` with a first-class `deep_daily backup` subcommand that
archives the full `~/.local/deep-daily-daily/` HOME (config + data, excluding `state/` and `logs/`) to NAS nightly.
**This is a reliability bug fix**, not a feature — it ships independently of anything else, including PRD 002 (KB).

> **v2.1 scope note**: Phase 1b wet run revealed a restore-gap: the original `<HOME>/data/`-only scope left
> `config.yaml`, `configs/`, and `.env` outside the archive. Restoring from such an archive required manual
> HOME re-init. The scope is now the full HOME with an explicit deny-list (`state/**`, `logs/**`, and the
> pre-existing `data/` sub-excludes re-prefixed). This is a backward-incompatible archive-layout change and
> was made before cutover precisely so no old-layout archives enter production retention.

---

## 1. Problem

### 1.1 The drift

`<legacy-bot>/scripts/data-backup.sh` runs daily at 03:00 via `com.example.legacy-data-backup.plist` and uploads tar.gz
archives of `~/.local/deep-daily/data/` to NAS
`<NAS_BASE>/data-runtime/m4/`.

That is the **old HOME**. The **current HOME** is `~/.local/deep-daily-daily/` (data 259 MB, growing ~65 MB/day).
Everything important is unbacked.

Evidence on NAS as of 2026-05-05:

| File | Size | Interpretation |
|---|---|---|
| `m4-...-20260428-030005.tar.gz` | 110 MB | Some legacy data still present |
| `m4-...-20260430-030006.tar.gz` | 108 MB | |
| `m4-...-20260501-030001.tar.gz` | 91 MB | Decay begins |
| `m4-...-20260504-030346.tar.gz.part` | 3.7 MB | Upload failed mid-flight, never retried |
| `m4-...-20260505-030005.tar.gz` | 71 MB | Live but backing up near-empty dir |

Size is shrinking because less and less of the old HOME is being written to.

### 1.2 <legacy-bot> retirement

The <legacy-bot> orchestrator is being retired. Its scripts will stop running. Backup needs to live inside
`deep-daily-report` which owns the current HOME.

---

## 2. Goals & Non-Goals

### Goals

- **G1** Every byte written under `~/.local/deep-daily-daily/` (HOME root files, `configs/`, `data/`, `.env`) is on NAS
  within 24 hours. Operational state (`state/`) and local logs (`logs/`) are deliberately excluded.
- **G2** Clear failure signal — if the backup breaks, `doctor` flags it within a day.
- **G3** Zero new operational surface — one launchd plist, one subcommand, one state file.
- **G4** Independent of the KB work — backup can ship even if KB never does. KB (PRD 002) may proceed in
  parallel, not blocked by this PRD.

### Non-goals

- **NG1** No restore command. `scp` + `tar -xzf` is sufficient for rare recovery.
- **NG2** No deduplication / incremental backups (no restic, no borg). At 65 MB/day compressed, full daily
  tarballs are fine and simple.
- **NG3** No backup encryption at rest — NAS is inside home LAN; SSH key auth covers the wire.
- **NG4** No cross-host orchestration. M4 only. M2 (if ever) gets its own.
- **NG5** No automatic legacy NAS archive migration. Old `m4/*.tar.gz` are preserved as legacy artifacts
  with a scheduled manual cleanup task 3-6 months from cutover (see §9 Q3).

---

## 3. User Stories

- **U1** When <legacy-bot>'s data-backup plist is disabled, my current data keeps flowing to NAS nightly with no
  action from me.
- **U2** `deep_daily doctor` tells me the last backup time and status, so I can spot drift in minutes.
- **U3** I run `deep_daily backup --dry-run` and see exactly what would be packaged and uploaded before
  committing to a real run.
- **U4** If my NAS is offline during a scheduled run, the command fails loudly and retries the next day.
  No silent success.
- **U5** If a previous run crashed and left a lockfile, the next scheduled run detects the dead PID and
  recovers automatically — no permanent wedge.

---

## 4. Design

### 4.1 Command

```
deep_daily backup [--dry-run] [--retention N] [--skip-checksum] [--force-unlock]
```

**Upload flow** (non-dry-run, ORDERED — this order is part of the contract):

1. **Acquire lock** at `<HOME>/state/backup/backup.lock` (see §4.3 for recovery logic).
2. **Validate** `<HOME>` is a real instance — sentinel `.deep-daily-home` and `config.yaml` exist. Empty
   `data/` is acceptable (a freshly-initialized instance is a valid backup target).
3. **Clean stale `.part`** on NAS: remove any `<remote_dir>/<INSTANCE_NAME>-*.tar.gz.part` older than 48h
   (best-effort; log failures but do not abort).
4. **Create local tarball** at `<HOME>/state/backup/<INSTANCE_NAME>-<YYYYMMDD-HHMMSSZ>.tar.gz` (UTC timestamp
   in filename, fixed 16-char format). Archive is rooted at `<HOME>` (i.e. `tar -C <HOME> .`). Default
   exclusions cover operational state and data-layer caches:
   `state/**`, `logs/**`, `data/dailies/.pipeline/**`, `data/dailies-dryrun/**`, `data/.session-memory/**`.
   If `<HOME>/.env` exists, a warning is logged at backup start (the archive carries secrets).
5. **Compute local `sha256`** (unless `--skip-checksum`).
6. `ssh <nas_host> 'mkdir -p <remote_dir>'`.
7. **Stream** tarball → `<remote_dir>/<name>.tar.gz.part` via `cat | ssh ... > ...`.
8. **Hash remote `.part`**: `ssh <host> 'sha256sum <remote_dir>/<name>.tar.gz.part'`.
   Compare against local hash. **Mismatch → exit 3, leaving `.part` for investigation.**
9. **Only on match**, remote atomic rename `.part` → final via SSH.
10. **Retention prune** on NAS (see §4.5).
11. **Write `<HOME>/state/backup/last.json`** (single source of truth for "done"):
    ```json
    {
      "ts": "2026-05-05T03:30:17Z",
      "archive": "<INSTANCE_NAME>-20260505-033000Z.tar.gz",
      "size_bytes": 74893211,
      "sha256": "abc...",
      "remote": "/volume1/.../<INSTANCE_NAME>/<INSTANCE_NAME>-20260505-033000Z.tar.gz",
      "duration_s": 42,
      "files_included": 6534,
      "ok": true
    }
    ```
12. **Append to `history.jsonl`**.
13. **Remove local tmp tarball** (via `trap` cleanup, even on error exit after step 4).
14. **Release lock**.

**Atomicity note**: Only steps 9 (remote rename) and 11 (local `last.json` write) are individually atomic.
The overall backup is **not** a single transaction. The invariant is: if `last.json` says `ok: true`, the
named archive exists on NAS and has been hash-verified. Any crash between steps 4-11 leaves at worst a
stale local tarball (cleaned by `trap`) and/or a stale `.part` on NAS (cleaned by step 3 of the next run).

**Exit codes**:
- `0` success
- `1` config/validation error (bad HOME, empty dir, missing config)
- `2` network/ssh error (unreachable host, auth failure, partial upload unrecoverable)
- `3` checksum mismatch (remote `.part` hash ≠ local hash); `.part` retained for investigation
- `4` lock held by a live process (another run in progress)
- `5` lock recovery refused (stale lock detected but `--force-unlock` not specified in interactive mode)

**Dry-run**: print planned archive name, estimated size, exclusions applied, remote path, retention target,
what would be pruned, any stale `.part` that would be cleaned. Mutates nothing local or remote.

### 4.2 Config

`config.yaml`:

```yaml
backup:
  enabled: true
  nas_host: <NAS_HOST>
  nas_user: root
  nas_base: <NAS_BASE>/<NAS_DIR>
  retention: 30                     # tarballs to keep
  stale_part_age_hours: 48          # clean .part files older than this each run
  ssh_options:
    - "ConnectTimeout=10"
    - "BatchMode=yes"
    - "StrictHostKeyChecking=accept-new"
  exclude:
    - "dailies/.pipeline/**"
    - "dailies-dryrun/**"
    - ".session-memory/**"
```

Env overrides (honor existing from <legacy-bot> for zero-friction migration):

| Env | Overrides |
|---|---|
| `NAS_BACKUP_HOST` | `backup.nas_host` |
| `NAS_BACKUP_USER` | `backup.nas_user` |
| `NAS_BACKUP_BASE` | `backup.nas_base` |
| `NAS_BACKUP_RETENTION` | `backup.retention` |

### 4.3 Lockfile with stale recovery

**Lockfile format** at `<HOME>/state/backup/backup.lock` (JSON, written atomically via rename):

```json
{
  "pid": 52847,
  "started_ts": "2026-05-05T03:30:00Z",
  "host": "<hostname>",
  "command": "deep_daily backup"
}
```

**Acquire lock algorithm**:

1. If lockfile absent → create and proceed.
2. If lockfile present, parse it.
3. Check if `pid` exists on this host (`os.kill(pid, 0)` probes without signaling):
   - **PID live** → another run in progress, exit 4 ("lock held by PID <n> since <ts>").
   - **PID dead** → stale lock. Log at WARN: "recovered stale lock from dead PID <n> (started <ts>)".
     Overwrite lockfile and proceed.
4. If lockfile is unparseable (corrupted): treat as stale; log at WARN; overwrite.
5. If lockfile has `host != current host` (future multi-host scenario): exit 5 in interactive mode;
   auto-recover in scheduled mode (launchd).

**Release lock**: `os.unlink(lockfile)` in a `finally` block. Best-effort — if it fails, the next run's
stale detection handles it.

**Manual override**: `--force-unlock` skips liveness check and takes the lock. Use only if you know what
you're doing.

### 4.4 State directory

```
<HOME>/state/backup/
├── backup.lock            # JSON (see §4.3); removed on clean exit
├── last.json              # most recent run result (see §4.1)
└── history.jsonl          # append-only log; rotated at 500 lines (keep last 500)
```

### 4.5 Remote retention (deterministic, not `ls -1t`)

**Filename contract**: archives always named `<INSTANCE_NAME>-YYYYMMDD-HHMMSSZ.tar.gz` where the timestamp
segment is fixed-width, UTC, and directly comparable as a string (lexicographic sort == chronological sort).

**Retention algorithm**:

```python
# Pseudocode — implementation in backup/retention.py
def prune(ssh, remote_dir, keep_n):
    # List only files matching our exact prefix pattern
    output = ssh.run(f"ls -1 {remote_dir!r} | grep -E "
                     f"'^<INSTANCE_NAME>-[0-9]{{8}}-[0-9]{{6}}Z\\.tar\\.gz$'")
    files = output.strip().split('\n') if output.strip() else []
    files.sort(reverse=True)          # lex sort == reverse chronological
    to_delete = files[keep_n:]
    for f in to_delete:
        ssh.run(f"rm -f {shlex.quote(remote_dir)}/{shlex.quote(f)}")
    return {"kept": len(files) - len(to_delete), "deleted": to_delete}
```

**Why this is safer than `ls -1t`**:
- Locale-independent.
- Immune to mtime weirdness (NAS mtime drift, file touching).
- Regex gate ensures we only delete our own archives, never anything else.
- Deterministic in tests.

**Retention window**: 30 tarballs (see §9 Q2). At current 65 MB/day compressed, total NAS use ≈ 2 GB.

### 4.6 Module layout

```
src/deep_daily/
├── commands/backup_cmd.py          # cmd_backup(args, home) -> int
└── backup/
    ├── __init__.py
    ├── config.py                    # BackupConfig dataclass, env overrides
    ├── archive.py                   # tar creation + exclusions
    ├── upload.py                    # ssh stream upload + verify + rename
    ├── retention.py                 # remote prune (filename-based sort)
    ├── state.py                     # lockfile with stale recovery + last.json + history.jsonl
    └── errors.py                    # typed exceptions with exit codes
```

All modules pure Python. Subprocess calls for `tar`, `ssh`, `shasum -a 256`. No new third-party deps.

### 4.7 `doctor` integration

`doctor_cmd.py` adds a backup section:

```
== Backup ==
  config          enabled, host=<NAS_HOST>, retention=30
  last run        2026-05-05 03:30:17 UTC (2h ago)  ✓
  last size       71 MB
  last duration   42s
  lock status     clean
  NAS reachable   yes (ssh probe 0.8s)
```

Color codes:
- `✓` green: last run < 26h ago, ok=true, no stale lock
- `⚠` yellow: last run 26-72h ago, or no `last.json` yet, or stale lock detected
- `✗` red: last run > 72h ago, or ok=false, or NAS unreachable

NAS reachability probe is opt-in (`--deep` flag) because SSH can be slow when NAS is asleep.

---

## 5. Rollout

### Phase 1a (Day 1-2): code + tests

- Implement modules + `backup_cmd.py`.
- Unit tests mocking subprocess for tar/ssh/sha256.
- Unit tests for stale lock recovery (live PID, dead PID, corrupted lockfile).
- Unit tests for retention algorithm (exact filename match, sort order, edge cases: 0 files, 1 file, <N files).
- Integration test with real local-loopback SSH (via `ssh localhost`, guarded by env flag).
- `--dry-run` passing end-to-end against current HOME.

### Phase 1b (Day 3): wet run + restore smoke test

- Manual first real backup to **new** NAS path `/.../data-runtime/<INSTANCE_NAME>/`.
- Verify tarball listing on NAS: `ssh root@... 'tar -tzf <path> | head -20'`.
- **Restore smoke test** (required, not optional):
  1. `scp` the tarball back to a tmp dir on M4.
  2. `tar -xzf` into a scratch dir.
  3. Sample 3 files (1 article JSON, 1 tweet JSON, 1 dailies HTML) — diff against live HOME.
  4. Confirm byte-identical (modulo excluded paths).
- Verify `last.json` contents match NAS reality.

### Phase 1c (Day 4): cutover

- Ship new launchd plist `com.example.deep-daily-backup.plist`:
  ```xml
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>source ~/.local/deep-daily/env.m4 && /usr/bin/python3 -m deep_daily backup</string>
  </array>
  ```
- Wrapper script in `~/.local/deep-daily/scripts/start-deep-daily-backup.sh` (mirrors other agents).
- `launchctl bootstrap gui/$UID <plist>`.
- `launchctl bootout gui/$UID com.example.legacy-data-backup` (disable old).
- Leave old plist file on disk for 1 month as rollback hatch.

### Phase 1d (Day 5-7): observation

- Monitor 3 consecutive successful scheduled runs via `last.json`.
- Verify NAS directory populated and retention policy working (files with oldest timestamp get pruned
  when count exceeds 30).
- Run `doctor` daily — confirm green.
- Delete old `<legacy-bot>/scripts/data-backup.sh` only after 7 clean days.

---

## 6. Exit Criteria

- [ ] `deep_daily backup --dry-run` passes in Phase 1a
- [ ] Stale-lock recovery test passes (crash one run mid-flight, next run recovers)
- [ ] Checksum-mismatch test passes (corrupt the stream, confirm exit 3 and `.part` retained)
- [ ] First real backup visible in `/volume1/.../<INSTANCE_NAME>/` with valid tarball
- [ ] **Restore smoke test passes** (`tar -xzf` from NAS → files match source)
- [ ] `last.json` updated and consistent with NAS reality
- [ ] 3 consecutive scheduled runs succeed
- [ ] Old plist disabled, no old-HOME uploads occurring
- [ ] `doctor` shows green backup section
- [ ] `docs/backup.md` written (includes restore workflow)

---

## 7. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|:---:|:---:|---|
| SSH key not loaded under launchd | Medium | High | `BatchMode=yes` fails fast; `doctor` probe; document key setup in `docs/backup.md` |
| NAS asleep / network down at 03:30 | Medium | Low | Fail loudly; next day retries; no silent success |
| Tarball corruption mid-upload | Low | Medium | sha256 verification of remote `.part` before rename; `.part` retained on mismatch |
| Stale `.part` from crashed upload | Medium | Low | Step 3 cleans `.part` older than 48h each run |
| Stale lockfile from crashed run | Low | High | PID liveness check → auto-recover with WARN log |
| Concurrent run (manual + cron) | Low | Low | Lockfile with live-PID detection |
| Retention bug deletes too much | Low | High | Regex-guarded exact filename match; filename-timestamp sort (no locale dependency); unit tested with edge cases |
| Disk full on local tmp during tar | Low | Medium | `trap` cleanup; `--dry-run` reports estimated size first |

---

## 8. Out-of-scope / Future

- **Restore command**: manual workflow documented in `docs/backup.md`.
- **Backup encryption**: add age/gpg layer if threat model changes.
- **Multi-destination**: B2/S3 mirror if NAS lost in fire — v2.
- **Incremental / dedup**: restic integration — v2 if data grows to TB.
- **Alerts**: push notification to WEA on backup failure — v2 via existing notification hook.

---

## 9. Open Questions (LOCKED)

Answers decided based on Oracle v2 review (ses_207f5a226ffeXVDlqhMs4XQ3G7):

- **Q1** NAS path: `<INSTANCE_NAME>` (vs `deep-daily`)?
  **Decision: `<INSTANCE_NAME>`.** Avoids collision with any future machine; cutover from old `m4/` path
  stays operationally obvious.

- **Q2** Retention: 30 tarballs (vs 7)?
  **Decision: 30.** Storage cost is trivial (~2 GB); rollback/debug window materially better.

- **Q3** Legacy `/volume1/.../m4/` directory — what to do?
  **Decision: preserve as legacy artifact; scheduled manual cleanup 3-6 months after cutover.**
  Do not claim "natural decay" — nothing is writing there, so files will not disappear on their own.
  Add a calendar reminder at cutover for the cleanup task.

---

## Appendix A: Evidence

### A.1 Current <legacy-bot> script

Path: `~/Documents/<legacy-bot>/scripts/data-backup.sh`
Invoked by: `~/.local/deep-daily/scripts/start-data-backup-m4.sh` (launchd wrapper)
Plist: `~/Library/LaunchAgents/com.example.legacy-data-backup.plist` (schedule 03:00)

Core logic (simplified):

```bash
DATA_DIR="$HOME/.david/data"                    # ← wrong HOME
tar -czf "$archive_path" -C "$DATA_DIR" .
ssh -o BatchMode=yes <NAS_USER>@<NAS_HOST> 'mkdir -p ...'
cat "$archive_path" | ssh ... "cat > '<remote>.part'"
ssh ... "mv '.part' '<final>'"                  # ← no hash before rename
ssh ... "ls -1t *.tar.gz | tail +8 | xargs rm"  # ← locale-fragile sort, keep 7
```

Our rewrite:
- Points at the correct HOME (the actual fix).
- Adds sha256 verification of remote `.part` **before** rename (closes "invalid final file" window).
- Uses filename-timestamp sort for retention (deterministic).
- Adds lockfile with stale-PID recovery (new).
- Cleans stale `.part` proactively (new).

### A.2 NAS directory listing as of 2026-05-05

```
<NAS_BASE>/data-runtime/m4/
  total 680M  (7 tarballs + 1 .part)

 110M  m4-data-runtime-20260428-030005.tar.gz
 108M  m4-data-runtime-20260429-030005.tar.gz
 108M  m4-data-runtime-20260430-030006.tar.gz
  91M  m4-data-runtime-20260501-030001.tar.gz
  96M  m4-data-runtime-20260502-030004.tar.gz
  95M  m4-data-runtime-20260503-030005.tar.gz
 3.7M  m4-data-runtime-20260504-030346.tar.gz.part   ← failed upload, never retried
  71M  m4-data-runtime-20260505-030005.tar.gz
```

Shrinking trend confirms old-HOME is being drained without our writes hitting it.

### A.3 Oracle review log

- **v1 review** (ses_207ff0be0ffeWE2VDjoq0iO47b): split backup into its own PRD; backup-first sequencing;
  highlight lockfile + exit codes + `doctor`. ✓ all incorporated in v1 draft.
- **v2 review** (ses_207f5a226ffeXVDlqhMs4XQ3G7): approve with changes — stale lock recovery, retention
  by filename-timestamp, `.part` cleanup, hash-before-rename, restore smoke test, explicit legacy
  preservation wording. ✓ all incorporated in this v2 draft.
