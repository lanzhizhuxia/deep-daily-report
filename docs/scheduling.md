# Scheduling

The scheduler calls the deep-daily CLI with a fully-resolved HOME. Two rules
make or break every scheduled job:

1. **`DEEP_DAILY_HOME` must be set explicitly** in the scheduler's environment,
   OR `--home <path>` must be passed on the command line. Walk-up discovery is
   intentionally disabled for non-interactive subcommands — a daemon has no
   reliable CWD.
2. **`PATH` must include the Python venv's `bin/` directory** so the
   `deep-daily` console script resolves.

## launchd (macOS)

The canonical template for a 07:00 daily run:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.example.daily-report</string>

    <key>ProgramArguments</key>
    <array>
        <string><HOME>/path/to/venv/bin/deep-daily</string>
        <string>run</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>DEEP_DAILY_HOME</key>
        <string><HOME>/.my-daily</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>7</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string><HOME>/.my-daily/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string><HOME>/.my-daily/logs/stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Save as `~/Library/LaunchAgents/com.example.daily-report.plist` and load:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist
launchctl print gui/$(id -u)/com.example.daily-report  # verify it's loaded
```

To unload:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.daily-report.plist
```

### Known pitfall: env vars do not inherit from your shell

`launchctl` ignores your shell's environment. Every env var the job needs must
be listed under `<key>EnvironmentVariables</key>`. This is the #1 cause of
"works in iTerm, silently fails at 07:00" bugs.

If you keep credentials in `<HOME>/.env` (and you should), deep-daily reads
that file at startup — nothing special needed in the plist for those. Only
`DEEP_DAILY_HOME` and `PATH` are required in the plist itself.

### Pre-flight with doctor

A safer launchd wrapper runs `doctor --json` first and aborts on error. Put
this in a tiny shell script and point `ProgramArguments` at the script:

```bash
#!/bin/bash
set -euo pipefail
export DEEP_DAILY_HOME="<HOME>/.my-daily"
VENV=<HOME>/path/to/venv/bin

if ! "$VENV/deep-daily" doctor --json > "$DEEP_DAILY_HOME/logs/doctor.json"; then
    echo "doctor failed — aborting run" >&2
    exit 1
fi

exec "$VENV/deep-daily" run
```

## cron (Linux)

```cron
# minute hour dom mon dow command
0 7 * * * DEEP_DAILY_HOME=/home/you/.my-daily /home/you/venv/bin/deep-daily run >> /home/you/.my-daily/logs/stdout.log 2>> /home/you/.my-daily/logs/stderr.log
```

Same rule — cron inherits a minimal environment. Always set `DEEP_DAILY_HOME`
inline (or via `/etc/environment` if you maintain it) rather than expecting
shell rc files to load.

## systemd timer (Linux, preferred over cron)

`/etc/systemd/system/deep-daily.service`:

```ini
[Unit]
Description=deep-daily daily digest

[Service]
Type=oneshot
User=you
Environment=DEEP_DAILY_HOME=/home/you/.my-daily
ExecStart=/home/you/venv/bin/deep-daily run
StandardOutput=append:/home/you/.my-daily/logs/stdout.log
StandardError=append:/home/you/.my-daily/logs/stderr.log
```

`/etc/systemd/system/deep-daily.timer`:

```ini
[Unit]
Description=deep-daily daily trigger

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now deep-daily.timer
```

## Observability

Tail the logs while a run is in flight:

```bash
tail -F ~/.my-daily/logs/stdout.log ~/.my-daily/logs/stderr.log
```

For a scripted health check, parse `doctor --json`:

```bash
deep-daily --home ~/.my-daily doctor --json | jq '.summary'
# {"ok": 12, "warn": 1, "error": 0}
```

## Interacting with `migrate-legacy`

If you're still running the legacy launchd job and preparing to cut over, the
`migrate-legacy` command refuses to run between **06:45 and 07:15** unless you
pass `--confirm-near-schedule`. This guard exists so you don't accidentally
race the 07:00 run while copying data out from under it.

See [migration-guide.md](migration-guide.md) for the full cutover runbook.
