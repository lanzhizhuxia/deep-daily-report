from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, TypedDict

from deep_daily.config import get_app_config
from deep_daily.pipeline import _get_llm


DEFAULT_SESSION_MEMORY_DIR = "<HOME>/.local/share/session-memory"
PROFILE_MAX_AGE_SECONDS = 24 * 60 * 60
CONTEXT_CHAR_LIMIT = 4000
ACTIVE_DAYS = 30
TOP_PROJECTS = 12
MAX_DECISIONS_PER_PROJECT = 3
MAX_UNFINISHED_ITEMS = 15
PROFILE_NAME = "<profile>"
KEYWORD_RE = re.compile(
    r"(crypto|加密|defi|defi|rwa|稳定币|stablecoin|btc|bitcoin|eth|ethereum|sol|"
    r"funding|arb|arbitrage|套利|资金费率|协议|protocol|agent|llm|ai|模型|onchain|链上|twitter)",
    re.IGNORECASE,
)
DATE_HEADING_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})")


class ProfileInputs(TypedDict):
    role: str
    projects: list[str]
    high_priority: list[str]
    medium_priority: list[str]
    context: str


def maybe_refresh_profile(*, max_age_seconds: int = PROFILE_MAX_AGE_SECONDS) -> bool:
    session_dir = _session_memory_dir()
    if not session_dir.is_dir():
        return False

    profile_path = get_app_config().profile_path
    try:
        if (
            profile_path.exists()
            and time.time() - profile_path.stat().st_mtime < max_age_seconds
        ):
            return False
    except OSError:
        pass

    try:
        return generate_profile(session_dir=session_dir, profile_path=profile_path)
    except Exception:
        return False


def generate_profile(
    *, session_dir: Path | None = None, profile_path: Path | None = None
) -> bool:
    session_root = (
        Path(session_dir) if session_dir is not None else _session_memory_dir()
    )
    if not session_root.is_dir():
        return False

    target_path = (
        Path(profile_path)
        if profile_path is not None
        else get_app_config().profile_path
    )
    extracted = _extract_profile_inputs(session_root)
    if not extracted["context"]:
        return False

    try:
        prompt_snippet = _generate_prompt_snippet(extracted["context"])
    except Exception:
        return False

    rendered = _render_profile_yaml(
        role=extracted["role"],
        high_priority=extracted["high_priority"],
        medium_priority=extracted["medium_priority"],
        projects=extracted["projects"],
        prompt_snippet=prompt_snippet,
        session_dir=session_root,
    )
    _atomic_write_text(target_path, rendered)
    return True


def _session_memory_dir() -> Path:
    raw = os.environ.get("SESSION_MEMORY_DIR", DEFAULT_SESSION_MEMORY_DIR)
    return Path(raw).expanduser()


def _extract_profile_inputs(session_dir: Path) -> ProfileInputs:
    work_lines = _read_work_identity(session_dir / "工作画像.md")
    active_projects = _read_active_projects(session_dir / "项目时间线.md")
    weekly_projects = _read_weekly_projects(session_dir / "本周重点.md")
    project_decisions = _read_project_decisions(
        session_dir / "决策日志.md", active_projects + weekly_projects
    )
    unfinished = _read_unfinished_threads(session_dir / "未完成线索.md")

    role = _extract_role(work_lines)

    all_project_names = _dedupe_keep_order(
        [p["name"] for p in active_projects] + [p["name"] for p in weekly_projects]
    )
    all_project_names = [n for n in all_project_names if n.lower() != "unknown"][
        :TOP_PROJECTS
    ]

    high_priority = all_project_names[:6]
    medium_priority = all_project_names[6:TOP_PROJECTS]

    context_parts = []
    if work_lines:
        context_parts.append(
            "身份与工作背景:\n" + "\n".join(f"- {l}" for l in work_lines[:10])
        )

    if active_projects:
        context_parts.append(
            "最近30天活跃项目 (按活跃度排序):\n"
            + "\n".join(
                f"- {p['name']} ({p['sessions']} sessions)" for p in active_projects
            )
        )

    weekly_file = session_dir / "本周重点.md"
    if weekly_file.exists():
        weekly_text = _read_weekly_summary(weekly_file)
        if weekly_text:
            context_parts.append(f"本周重点:\n{weekly_text}")

    if project_decisions:
        context_parts.append(
            "各项目关键决策:\n"
            + "\n".join(
                f"- [{proj}] {d}"
                for proj, decisions in project_decisions.items()
                for d in decisions
            )
        )
    if unfinished:
        context_parts.append(
            "当前未完成线索:\n" + "\n".join(f"- {l}" for l in unfinished)
        )

    context = "\n\n".join(context_parts)
    if len(context) > CONTEXT_CHAR_LIMIT:
        context = context[:CONTEXT_CHAR_LIMIT]

    return {
        "role": role,
        "projects": all_project_names,
        "high_priority": high_priority,
        "medium_priority": medium_priority,
        "context": context,
    }


def _read_work_identity(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines: list[str] = []
    in_section = False
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not in_section:
                stripped = line.strip()
                if stripped.startswith("## 核心画像") or stripped.startswith("## 其他"):
                    in_section = True
                continue
            if line.startswith("## "):
                break
            cleaned = _clean_markdown_text(line)
            if cleaned:
                lines.append(cleaned)
    return lines


def _read_active_projects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    cutoff = dt.date.today() - dt.timedelta(days=ACTIVE_DAYS)
    project_sessions: dict[str, int] = {}
    current_project: str | None = None
    current_date: dt.date | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current_project = name if name and len(name) > 1 else None
            continue
        m = DATE_HEADING_RE.match(line.strip())
        if m:
            try:
                current_date = dt.date.fromisoformat(m.group(1))
            except ValueError:
                current_date = None
            continue
        if (
            current_date
            and current_date >= cutoff
            and current_project
            and line.strip().startswith("- ")
        ):
            project_sessions[current_project] = (
                project_sessions.get(current_project, 0) + 1
            )
    ranked = sorted(project_sessions.items(), key=lambda x: -x[1])
    return [{"name": name, "sessions": count} for name, count in ranked[:TOP_PROJECTS]]


def _read_project_decisions(
    path: Path, active_projects: list[dict[str, Any]]
) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    active_names = {p["name"] for p in active_projects}
    text = path.read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}
    current_project: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current_project = name if name in active_names else None
            continue
        if current_project and "**决定**:" in line:
            cleaned = _clean_markdown_text(line.split("**决定**:")[-1])
            if cleaned and len(cleaned) > 5:
                result.setdefault(current_project, [])
                if len(result[current_project]) < MAX_DECISIONS_PER_PROJECT:
                    result[current_project].append(cleaned)
    return result


def _read_weekly_projects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    project_counts: dict[str, int] = {}
    bracket_re = re.compile(r"^\s*-\s*\[([^\]]+)\]")
    with path.open(encoding="utf-8") as f:
        for raw in f:
            m = bracket_re.match(raw)
            if m:
                name = m.group(1).strip()
                if name and name != "unknown" and name != "Unknown":
                    project_counts[name] = project_counts.get(name, 0) + 1
    ranked = sorted(project_counts.items(), key=lambda x: -x[1])
    return [{"name": name, "sessions": count} for name, count in ranked]


def _read_weekly_summary(path: Path) -> str:
    lines: list[str] = []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("<!--") or line.startswith("# "):
                continue
            if line.strip():
                lines.append(line)
            if len(lines) >= 30:
                break
    return "\n".join(lines)


def _read_unfinished_threads(path: Path) -> list[str]:
    if not path.exists():
        return []
    items: list[str] = []
    with path.open(encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.lstrip().startswith("- "):
                continue
            cleaned = _clean_markdown_text(line)
            if KEYWORD_RE.search(cleaned):
                items.append(cleaned)
            if len(items) >= MAX_UNFINISHED_ITEMS:
                break
    return items


def _extract_role(work_lines: list[str]) -> str:
    for line in work_lines:
        match = re.search(r"角色[:：]\s*(.+)", line)
        if match:
            return match.group(1).strip()
        match = re.search(r"用户是([^，。；—]+)", line)
        if match:
            return match.group(1).strip()
        match = re.search(r"负责([^，。；]+)", line)
        if match:
            return f"负责{match.group(1).strip()}"
    return "Crypto/AI operator"


def _generate_prompt_snippet(extracted_context: str) -> str:
    model = os.environ.get("DAILY_FILTER_MODEL", "")
    prompt = (
        "Based on the following work context of a crypto industry professional, generate a concise "
        "reader profile (3-5 sentences) that describes:\n"
        "1. Their role and primary responsibilities\n"
        "2. Specific projects, protocols, and technologies they actively work with\n"
        "3. What topics in crypto/AI/DeFi they would find most relevant for a daily news digest\n\n"
        f"Work context:\n{extracted_context}\n\n"
        "Output ONLY the profile description, no JSON, no markdown, no headers."
    )
    content = _get_llm().chat(
        [
            {
                "role": "system",
                "content": "You write concise reader profiles for relevance ranking.",
            },
            {"role": "user", "content": prompt},
        ],
        model=model,
        temperature=0.3,
        max_tokens=1024,
    )
    normalized = re.sub(r"\s+", " ", content).strip().strip('"')
    if not normalized:
        raise RuntimeError("Empty profile snippet")
    return normalized


def _render_profile_yaml(
    *,
    role: str,
    high_priority: list[str],
    medium_priority: list[str],
    projects: list[str],
    prompt_snippet: str,
    session_dir: Path,
) -> str:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Generated by deep_daily.profile_gen from session-memory",
        f"# Last updated: {timestamp}",
        f"# Source: {session_dir}",
        "",
        "identity:",
        f"  name: {_yaml_string(PROFILE_NAME)}",
        f"  role: {_yaml_string(role)}",
        "",
        "focus:",
        "  high_priority:",
    ]
    lines.extend(_yaml_list_lines(high_priority, indent="    "))
    lines.append("  medium_priority:")
    lines.extend(_yaml_list_lines(medium_priority, indent="    "))
    lines.append("")
    lines.append("projects:")
    lines.extend(_yaml_list_lines(projects, indent="  "))
    lines.append("")
    lines.append("prompt_snippet: >")
    lines.extend(_folded_block_lines(prompt_snippet, indent="  "))
    lines.append("")
    return "\n".join(lines)


def _yaml_list_lines(values: list[str], *, indent: str) -> list[str]:
    cleaned = [value for value in values if value]
    if not cleaned:
        return [f'{indent}- ""']
    return [f"{indent}- {_yaml_string(value)}" for value in cleaned]


def _folded_block_lines(value: str, *, indent: str) -> list[str]:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return [f"{indent}"]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= 88:
            current = candidate
            continue
        lines.append(f"{indent}{current}")
        current = word
    if current:
        lines.append(f"{indent}{current}")
    return lines


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _clean_markdown_text(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^-\s*\[[^\]]+\]\s*", "", text)
    text = re.sub(r"^-\s*", "", text)
    text = re.sub(r"<!--.*?-->", "", text)
    text = re.sub(r"\[(?:CC|OC)-MEM\]", "", text)
    text = re.sub(r"\[(?:CC|OC)\]", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        handle.write(content)
        tmp_path = Path(handle.name)
    tmp_path.replace(path)
