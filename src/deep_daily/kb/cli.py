from __future__ import annotations

import json
from typing import Any


def format_stats(stats_dict: dict[str, Any]) -> str:
    lines = [f"items_total: {stats_dict.get('items_total', 0)}"]
    per_source = stats_dict.get("per_source", {})
    if per_source:
        lines.append("per_source:")
        for source, count in sorted(per_source.items()):
            lines.append(f"  {source}: {count}")
    else:
        lines.append("per_source: {}")
    date_range = stats_dict.get("date_range", {})
    lines.append(
        "date_range: "
        f"{date_range.get('earliest') or '-'} -> {date_range.get('latest') or '-'}"
    )
    last_ingest = stats_dict.get("last_ingest") or {}
    if last_ingest:
        lines.append(
            "last_ingest: "
            f"run_id={last_ingest.get('run_id')} ok={last_ingest.get('ok')} "
            f"scanned={last_ingest.get('files_scanned', 0)} skipped={last_ingest.get('files_skipped', 0)} "
            f"ok_files={last_ingest.get('files_ok', 0)} failed={last_ingest.get('files_failed', 0)}"
        )
    else:
        lines.append("last_ingest: -")
    lines.append(f"db_size_bytes: {stats_dict.get('db_size_bytes', 0)}")
    return "\n".join(lines)


def format_stats_json(stats_dict: dict[str, Any]) -> str:
    return json.dumps(stats_dict, ensure_ascii=False, indent=2, sort_keys=True)
