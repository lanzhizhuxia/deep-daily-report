#!/usr/bin/env bash
# HARD GATE 3 — End-to-end smoke on a throwaway HOME.
#
# Per PLAN v2.1 §7 Step 21. This script is the scriptable twin of the
# manual verification and is safe to re-run any time (it rm -rf's the target
# first). Exits non-zero at the first failure.
#
# Usage:
#   scripts/smoke_hard_gate_3.sh [TARGET]
#
# TARGET defaults to /tmp/test-daily.
set -euo pipefail

TARGET="${1:-/tmp/test-daily}"
DATE="${SMOKE_DATE:-2026-05-05}"

command -v deep-daily >/dev/null || {
    echo "deep-daily not on PATH — activate your venv first" >&2
    exit 127
}

printf '\n[1/4] clean target at %s\n' "$TARGET"
rm -rf "$TARGET"

printf '\n[2/4] init\n'
deep-daily init "$TARGET" --yes --reader-name smoke

printf '\n[3/4] re-init without --force (must abort rc=2)\n'
set +e
deep-daily init "$TARGET" --yes
rc=$?
set -e
if [ "$rc" -ne 2 ]; then
    echo "FAIL: expected rc=2 from duplicate init, got $rc" >&2
    exit 1
fi

printf '\n[4a/4] doctor\n'
deep-daily --home "$TARGET" doctor

printf '\n[4b/4] run --dry-run --date %s\n' "$DATE"
deep-daily --home "$TARGET" run --dry-run --date "$DATE"

# Dry-run isolation: prod paths must stay untouched.
# dailies/.pipeline is created by init_runtime as part of the baseline
# directory scaffold — its presence is expected. Any other entry is a leak.
leak=$(ls -A "$TARGET/data/dailies" 2>/dev/null | grep -v '^\.pipeline$' || true)
if [ -n "$leak" ]; then
    echo "FAIL: dry-run leaked into dailies/: $leak" >&2
    exit 1
fi
if [ ! -f "$TARGET/data/dailies-dryrun/$DATE.html" ]; then
    echo "FAIL: expected dry-run HTML at dailies-dryrun/$DATE.html" >&2
    exit 1
fi
if [ -f "$TARGET/data/reported_events.json" ]; then
    echo "FAIL: dry-run wrote reported_events.json (must be read-only)" >&2
    exit 1
fi

printf '\nHARD GATE 3 PASSED.\n'
