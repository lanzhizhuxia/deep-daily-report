"""``deep-daily doctor`` — instance health check.

Per PLAN v2.1 §7 Step 16, §11.3 (env validation moved here from init_runtime).

Responsibilities (one HOME, read-only):
  1. Verify HOME layout matches §3.3 (sentinel, config.yaml, configs/, data/, logs/).
  2. Validate ``config.yaml`` has the required top-level sections and types.
  3. Report configs/ file presence (topics.yaml is required for a run; others warn).
  4. Validate env vars from ``doctor.required_env`` / ``doctor.optional_env`` in
     config.yaml, with sensible fallback defaults if the ``doctor`` section is
     absent. Conditional requirements (e.g. FEISHU_WEBHOOK only when Feishu is
     enabled) are enforced.
  5. Confirm ``data/`` is writable by attempting a probe file create+delete.
  6. ``--deep``: LLM reachability probe against the configured backend. Short
     timeout, counts latency; any failure here is a WARNING, not an error, so
     that a temporarily offline key does not turn doctor into an outage.

Output:
  - Default: human-readable with ✓/⚠/✗ prefixes + summary line.
  - ``--json``: single JSON array of check records (for scripting / Phase 2
    launchd pre-flight checks).

Exit status:
  0 if no errors (warnings OK), 1 if any error.

Doctor never writes to prod runtime state. It may create and immediately remove
a single probe file under ``data/`` to verify write permissions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from deep_daily.home import CONFIG_FILENAME, HomeConfig, SENTINEL_NAME


SEVERITY_OK = "ok"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"


@dataclass
class CheckResult:
    name: str
    severity: str
    message: str
    detail: str | None = None


# Per-backend required env vars. When config.yaml has llm.backend, only the
# matching set is enforced; omitted vars are treated as optional.
_BACKEND_REQUIRED_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("LLM_API_BASE", "LLM_API_KEY"),
    "multikey": ("LITELLM_API_BASE",),
}

DEFAULT_OPTIONAL_ENV = (
    "LLM_API_BASE",
    "LLM_API_KEY",
    "LITELLM_API_BASE",
    "LITELLM_API_KEY",
    "LITELLM_API_KEYS",
    "OPENNEWS_TOKEN",
    "FEISHU_WEBHOOK",
    "RSS_SERVER_BASE",
    "RSS_HMAC_SECRET",
)

REQUIRED_CONFIG_SECTIONS = (
    "instance",
    "models",
    "llm",
    "reader",
    "collectors",
    "pipeline",
    "publisher",
)

REQUIRED_CONFIG_FILES = ("topics.yaml",)
OPTIONAL_CONFIG_FILES = (
    "sources.yaml",
    "kols.json",
    "profile.yaml",
    "active-systems.yaml",
    "6551-config.json",
)

REQUIRED_DATA_SUBDIRS = (
    "articles",
    "tweets",
    "tweets-nas",
    "news-6551",
    "dailies",
    "dailies-dryrun",
)


def _check_home_layout(home: HomeConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    path = home.path

    if not (path / SENTINEL_NAME).exists():
        results.append(
            CheckResult(
                "home.sentinel",
                SEVERITY_ERROR,
                f"missing {SENTINEL_NAME}",
                str(path),
            )
        )
    else:
        results.append(CheckResult("home.sentinel", SEVERITY_OK, "present"))

    if not (path / CONFIG_FILENAME).exists():
        results.append(
            CheckResult(
                "home.config_yaml",
                SEVERITY_ERROR,
                "config.yaml missing",
                str(path / CONFIG_FILENAME),
            )
        )
    else:
        results.append(CheckResult("home.config_yaml", SEVERITY_OK, "present"))

    for sub in ("configs", "data", "logs"):
        p = path / sub
        if not p.is_dir():
            results.append(
                CheckResult(
                    f"home.{sub}",
                    SEVERITY_ERROR,
                    f"{sub}/ directory missing",
                    str(p),
                )
            )
        else:
            results.append(CheckResult(f"home.{sub}", SEVERITY_OK, f"{sub}/ present"))

    return results


def _check_config_schema(home: HomeConfig) -> list[CheckResult]:
    raw = home.raw_config
    results: list[CheckResult] = []

    schema_version = raw.get("schema_version")
    if schema_version == 1:
        results.append(
            CheckResult("config.schema_version", SEVERITY_OK, "schema_version=1")
        )
    else:
        results.append(
            CheckResult(
                "config.schema_version",
                SEVERITY_ERROR,
                f"unsupported schema_version={schema_version!r}",
            )
        )

    for section in REQUIRED_CONFIG_SECTIONS:
        value = raw.get(section)
        if value is None:
            results.append(
                CheckResult(
                    f"config.{section}",
                    SEVERITY_WARN,
                    f"missing top-level '{section}' section",
                )
            )
        elif not isinstance(value, dict):
            results.append(
                CheckResult(
                    f"config.{section}",
                    SEVERITY_ERROR,
                    f"'{section}' must be a mapping, got {type(value).__name__}",
                )
            )
        else:
            results.append(CheckResult(f"config.{section}", SEVERITY_OK, "present"))

    reader_name = str(raw.get("reader", {}).get("name") or "").strip()
    if not reader_name:
        results.append(
            CheckResult(
                "config.reader.name",
                SEVERITY_ERROR,
                "reader.name is empty — pipeline needs an identity string",
            )
        )

    backend = str(raw.get("llm", {}).get("backend") or "").strip()
    if backend == "david":
        results.append(
            CheckResult(
                "config.llm.backend",
                SEVERITY_WARN,
                "llm.backend=david is deprecated; switch to multikey",
            )
        )
    elif backend not in ("openai", "multikey"):
        results.append(
            CheckResult(
                "config.llm.backend",
                SEVERITY_ERROR,
                f"llm.backend must be openai|multikey, got {backend!r}",
            )
        )

    return results


def _check_config_files(home: HomeConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    configs = home.configs_dir
    for name in REQUIRED_CONFIG_FILES:
        p = configs / name
        if p.exists():
            results.append(
                CheckResult(f"configs.{name}", SEVERITY_OK, f"{name} present")
            )
        else:
            results.append(
                CheckResult(
                    f"configs.{name}",
                    SEVERITY_ERROR,
                    f"required {name} missing",
                    str(p),
                )
            )
    for name in OPTIONAL_CONFIG_FILES:
        p = configs / name
        if p.exists():
            results.append(
                CheckResult(f"configs.{name}", SEVERITY_OK, f"{name} present")
            )
        else:
            results.append(
                CheckResult(
                    f"configs.{name}",
                    SEVERITY_WARN,
                    f"optional {name} missing",
                    str(p),
                )
            )
    return results


def _check_env_vars(
    home: HomeConfig, *, getenv: Callable[[str], str | None] = os.environ.get
) -> list[CheckResult]:
    results: list[CheckResult] = []
    raw = home.raw_config
    doctor_section = raw.get("doctor") or {}
    backend = str(raw.get("llm", {}).get("backend") or "openai").strip()

    required = tuple(
        doctor_section.get("required_env")
        or _BACKEND_REQUIRED_ENV.get(backend, ("LLM_API_BASE", "LLM_API_KEY"))
    )
    optional = tuple(doctor_section.get("optional_env") or DEFAULT_OPTIONAL_ENV)

    # multikey backend needs at least one key source beyond just LITELLM_API_BASE.
    _multikey_key_vars = ("LITELLM_API_KEYS", "LITELLM_API_KEY")
    if backend == "multikey":
        have_key = any(getenv(v) for v in _multikey_key_vars)
        for name in required:
            value = getenv(name)
            if value:
                results.append(CheckResult(f"env.{name}", SEVERITY_OK, "set"))
            else:
                results.append(
                    CheckResult(
                        f"env.{name}",
                        SEVERITY_ERROR,
                        "required env var is not set",
                    )
                )
        if not have_key:
            results.append(
                CheckResult(
                    "env.LITELLM_API_KEYS",
                    SEVERITY_ERROR,
                    "multikey backend needs LITELLM_API_KEYS or LITELLM_API_KEY",
                )
            )
    else:
        # openai: no LITELLM_* alias fallback — must have LLM_* directly.
        for name in required:
            value = getenv(name)
            if value:
                results.append(CheckResult(f"env.{name}", SEVERITY_OK, "set"))
            else:
                results.append(
                    CheckResult(
                        f"env.{name}",
                        SEVERITY_ERROR,
                        "required env var is not set",
                    )
                )

    for name in optional:
        value = getenv(name)
        results.append(
            CheckResult(
                f"env.{name}",
                SEVERITY_OK if value else SEVERITY_WARN,
                "set" if value else "not set (optional)",
            )
        )

    publisher = raw.get("publisher") or {}
    feishu = publisher.get("feishu") or {}
    if feishu.get("enabled"):
        webhook_env = feishu.get("webhook_env") or "FEISHU_WEBHOOK"
        if not getenv(webhook_env):
            results.append(
                CheckResult(
                    f"env.{webhook_env}",
                    SEVERITY_ERROR,
                    "Feishu publisher enabled but webhook env var is not set",
                )
            )

    return results


def _check_data_writable(home: HomeConfig) -> list[CheckResult]:
    probe = home.data_dir / ".doctor-probe"
    try:
        home.data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as err:
        return [
            CheckResult(
                "data.writable",
                SEVERITY_ERROR,
                f"data/ is not writable: {err}",
                str(home.data_dir),
            )
        ]
    for sub in REQUIRED_DATA_SUBDIRS:
        p = home.data_dir / sub
        if not p.is_dir():
            return [
                CheckResult(
                    f"data.{sub}",
                    SEVERITY_WARN,
                    f"data/{sub}/ missing (init_runtime will create on first run)",
                    str(p),
                )
            ]
    return [CheckResult("data.writable", SEVERITY_OK, "writable + subdirs present")]


_BACKUP_STALE_HOURS = 26


def _check_backup(home: HomeConfig) -> list[CheckResult]:
    last_path = home.path / "state" / "backup" / "last.json"
    if not last_path.is_file():
        return [
            CheckResult(
                "backup.last",
                SEVERITY_WARN,
                "no backup has run yet",
                str(last_path),
            )
        ]
    try:
        payload = json.loads(last_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return [
            CheckResult(
                "backup.last",
                SEVERITY_ERROR,
                f"last.json unreadable: {err}",
                str(last_path),
            )
        ]
    ok_flag = payload.get("ok")
    ts = payload.get("ts") or payload.get("finished_at")
    if ok_flag is False:
        return [
            CheckResult(
                "backup.last",
                SEVERITY_ERROR,
                f"last run failed at {ts}: {payload.get('error', 'no detail')}",
                str(last_path),
            )
        ]
    if ts is None:
        return [
            CheckResult(
                "backup.last",
                SEVERITY_WARN,
                "last.json missing timestamp",
                str(last_path),
            )
        ]
    try:
        last_dt = _parse_iso_utc(ts)
    except ValueError:
        return [
            CheckResult(
                "backup.last",
                SEVERITY_WARN,
                f"unparseable timestamp: {ts}",
                str(last_path),
            )
        ]
    from datetime import datetime, timezone  # noqa: F811 — keep local alias consistent

    age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
    size_mb = (payload.get("size_bytes") or 0) / 1_048_576
    archive = payload.get("archive") or "(unknown)"
    detail = f"{archive} {size_mb:.0f}MB age={age_hours:.1f}h"
    if age_hours > _BACKUP_STALE_HOURS:
        return [
            CheckResult(
                "backup.last",
                SEVERITY_ERROR,
                f"last successful backup is {age_hours:.1f}h old (> {_BACKUP_STALE_HOURS}h threshold)",
                detail,
            )
        ]
    return [CheckResult("backup.last", SEVERITY_OK, f"last success {age_hours:.1f}h ago", detail)]


def _parse_iso_utc(ts: str):
    from datetime import datetime, timezone

    s = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_KB_STALE_HOURS = 26


def _check_kb(home: HomeConfig) -> list[CheckResult]:
    db_path = home.path / "data" / "kb" / "kb.db"
    if not db_path.is_file():
        return [
            CheckResult(
                "kb.db",
                SEVERITY_WARN,
                "kb.db not present (run `kb ingest` to build)",
                str(db_path),
            )
        ]
    try:
        from deep_daily.kb.query import collect_stats
    except ImportError as err:
        return [CheckResult("kb.db", SEVERITY_ERROR, f"kb module import failed: {err}")]
    try:
        stats = collect_stats(db_path)
    except Exception as err:
        return [
            CheckResult(
                "kb.db",
                SEVERITY_ERROR,
                f"stats query failed: {err}",
                str(db_path),
            )
        ]
    results: list[CheckResult] = []
    items_total = cast(int, stats.get("items_total") or 0)
    size_mb = cast(int, stats.get("db_size_bytes") or 0) / 1_048_576
    per_source = cast(dict[str, int], stats.get("per_source") or {})
    src_summary = ", ".join(f"{k}={v}" for k, v in sorted(per_source.items()))
    results.append(
        CheckResult(
            "kb.items",
            SEVERITY_OK,
            f"{items_total:,} items ({size_mb:.0f}MB)",
            src_summary,
        )
    )
    last = cast(dict[str, Any], stats.get("last_ingest") or {})
    last_ts = last.get("ts") or last.get("finished_ts") or last.get("started_ts")
    last_ok = last.get("ok")
    if last_ts is None:
        results.append(CheckResult("kb.ingest", SEVERITY_WARN, "no ingest runs recorded"))
    else:
        try:
            last_dt = _parse_iso_utc(last_ts)
        except ValueError:
            results.append(CheckResult("kb.ingest", SEVERITY_WARN, f"unparseable ts: {last_ts}"))
        else:
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
            files = last.get("files_scanned", 0)
            skipped = last.get("files_skipped", 0)
            detail = f"run={last.get('run_id')} scanned={files} skipped={skipped}"
            if last_ok is False:
                results.append(
                    CheckResult("kb.ingest", SEVERITY_ERROR, f"last ingest failed ({age_hours:.1f}h ago)", detail)
                )
            elif age_hours > _KB_STALE_HOURS:
                results.append(
                    CheckResult(
                        "kb.ingest",
                        SEVERITY_ERROR,
                        f"last ingest is {age_hours:.1f}h old (> {_KB_STALE_HOURS}h threshold)",
                        detail,
                    )
                )
            else:
                results.append(
                    CheckResult("kb.ingest", SEVERITY_OK, f"last success {age_hours:.1f}h ago", detail)
                )
    prov = cast(dict[str, Any], stats.get("provenance_stats") or {})
    merged = prov.get("tweets_merged")
    if merged is not None:
        results.append(
            CheckResult(
                "kb.provenance",
                SEVERITY_OK,
                f"tweets_merged={merged} curated_only={prov.get('tweets_curated_only', 0)} bulk_only={prov.get('tweets_bulk_only', 0)}",
            )
        )
    return results


def _check_llm_deep(
    home: HomeConfig, *, getenv: Callable[[str], str | None] = os.environ.get
) -> list[CheckResult]:
    backend = str(home.raw_config.get("llm", {}).get("backend") or "openai").strip()
    if backend == "multikey":
        api_base = getenv("LITELLM_API_BASE")
        api_key = getenv("LITELLM_API_KEY") or next(
            (getenv(v) for v in ("LITELLM_API_KEYS",) if getenv(v)), None
        )
    else:
        api_base = getenv("LLM_API_BASE")
        api_key = getenv("LLM_API_KEY")
    if not (api_base and api_key):
        hint = (
            "LITELLM_API_BASE + LITELLM_API_KEY"
            if backend == "multikey"
            else "LLM_API_BASE + LLM_API_KEY"
        )
        return [
            CheckResult(
                "llm.probe",
                SEVERITY_WARN,
                f"skipped: {hint} not set",
            )
        ]
    try:
        import urllib.error
        import urllib.request
    except ImportError:  # pragma: no cover — stdlib always available
        return [CheckResult("llm.probe", SEVERITY_WARN, "urllib unavailable")]

    url = api_base.rstrip("/") + "/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "deep-daily doctor",
        },
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if 200 <= resp.status < 300:
                return [
                    CheckResult(
                        "llm.probe",
                        SEVERITY_OK,
                        f"reachable ({resp.status}, {elapsed_ms}ms)",
                    )
                ]
            return [
                CheckResult(
                    "llm.probe",
                    SEVERITY_WARN,
                    f"upstream returned HTTP {resp.status}",
                )
            ]
    except urllib.error.URLError as err:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return [
            CheckResult(
                "llm.probe",
                SEVERITY_WARN,
                f"unreachable after {elapsed_ms}ms: {err.reason}",
            )
        ]
    except Exception as err:  # pragma: no cover — defensive against odd urllib errors
        return [
            CheckResult(
                "llm.probe",
                SEVERITY_WARN,
                f"probe failed: {err}",
            )
        ]


def run_doctor(
    home: HomeConfig,
    *,
    deep: bool = False,
    getenv: Callable[[str], str | None] = os.environ.get,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.extend(_check_home_layout(home))
    results.extend(_check_config_schema(home))
    results.extend(_check_config_files(home))
    results.extend(_check_env_vars(home, getenv=getenv))
    results.extend(_check_data_writable(home))
    results.extend(_check_backup(home))
    results.extend(_check_kb(home))
    if deep:
        results.extend(_check_llm_deep(home, getenv=getenv))
    return results


_SEVERITY_GLYPH = {
    SEVERITY_OK: "✓",
    SEVERITY_WARN: "⚠",
    SEVERITY_ERROR: "✗",
}


def format_results_text(home: HomeConfig, results: list[CheckResult]) -> str:
    lines: list[str] = []
    lines.append(f"deep-daily doctor — {home.path}")
    lines.append("")
    errors = warns = oks = 0
    for r in results:
        glyph = _SEVERITY_GLYPH.get(r.severity, "?")
        suffix = f"  ({r.detail})" if r.detail else ""
        lines.append(f"  {glyph} {r.name}: {r.message}{suffix}")
        if r.severity == SEVERITY_ERROR:
            errors += 1
        elif r.severity == SEVERITY_WARN:
            warns += 1
        else:
            oks += 1
    lines.append("")
    lines.append(f"Summary: {oks} ok, {warns} warn, {errors} error")
    return "\n".join(lines) + "\n"


def format_results_json(home: HomeConfig, results: list[CheckResult]) -> str:
    payload: dict[str, Any] = {
        "home": str(home.path),
        "checks": [asdict(r) for r in results],
        "summary": {
            "ok": sum(1 for r in results if r.severity == SEVERITY_OK),
            "warn": sum(1 for r in results if r.severity == SEVERITY_WARN),
            "error": sum(1 for r in results if r.severity == SEVERITY_ERROR),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def cmd_doctor(args: argparse.Namespace, home: HomeConfig) -> int:
    results = run_doctor(home, deep=bool(getattr(args, "deep", False)))
    if getattr(args, "json", False):
        sys.stdout.write(format_results_json(home, results))
    else:
        sys.stdout.write(format_results_text(home, results))
    has_error = any(r.severity == SEVERITY_ERROR for r in results)
    return 1 if has_error else 0
