"""``deep-daily init`` — bootstrap a new instance HOME.

Per PLAN v2.1 §5.1 (bootstrap), §7 Step 15 (init_cmd contract).

Responsibilities:
  1. Resolve target path (must be absolute or CWD-relative; user-provided).
  2. Collision policy: path may exist only if it is empty OR ``--force`` was
     passed AND the path already looks like a deep-daily HOME (sentinel
     present). Non-empty strangers never get overwritten.
  3. In interactive mode, prompt for reader_name, llm_backend, feishu_enabled.
     ``--yes`` skips prompts and uses safe defaults.
  4. Render ``config.yaml`` from the template using the collected values.
  5. Copy all static templates from ``templates/default/`` into the HOME.
  6. Write the ``.deep-daily-home`` sentinel last (after everything else has
     landed) so a crashed init leaves a clearly-invalid HOME rather than a
     half-populated one that ``HomeConfig.load`` happily accepts.
  7. ``HomeConfig.load(home)`` must succeed on the resulting HOME. If it
     does not, init raises and the user sees a traceback rather than a silent
     corruption.

What init does NOT do (belongs to later phases / other commands):
  - Data subdirectory creation: ``init_runtime`` handles that on first
    ``run``/``fetch``, keeping init stateless about runtime concerns.
  - Env var validation: ``doctor`` owns this per PLAN v2.1 §11.3.
  - LLM reachability probes: ``doctor --deep`` owns this.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Callable

from deep_daily import __version__
from deep_daily.home import CONFIG_FILENAME, HomeConfig, SENTINEL_NAME


TEMPLATE_PACKAGE = "deep_daily"
TEMPLATE_SUBDIR = "_templates_default"
_TEMPLATES_DIR_NAME = "templates/default"


class InitError(RuntimeError):
    """Raised when init_cmd cannot proceed safely."""


def _templates_root() -> Path:
    """Resolve the on-disk templates/default directory.

    v0.3.0-dev ships templates under the repo root, not inside the package, so
    resources.files() is not the primary lookup. Fall back to a path relative
    to this file (src/deep_daily/commands/init_cmd.py → ../../.. / templates/default).
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent
    candidate = repo_root / _TEMPLATES_DIR_NAME
    if candidate.is_dir():
        return candidate
    raise InitError(
        f"Cannot locate templates directory. Expected: {candidate}. "
        f"This is a packaging/installation bug, not a user error."
    )


def _assert_safe_target(target: Path, *, force: bool) -> None:
    if target.exists():
        if not target.is_dir():
            raise InitError(f"Target exists and is not a directory: {target}")
        existing = [p for p in target.iterdir() if p.name != ".DS_Store"]
        if not existing:
            return
        sentinel = target / SENTINEL_NAME
        if sentinel.exists() and force:
            return
        raise InitError(
            f"Target is not empty: {target}\n"
            f"  - use `deep-daily init {target} --force` only if the target is "
            f"already a deep-daily HOME you want to re-render\n"
            f"  - refusing to touch an unrelated non-empty directory"
        )


def _prompt(msg: str, default: str, *, input_fn: Callable[[str], str] = input) -> str:
    suffix = f" [{default}]" if default else ""
    got = input_fn(f"{msg}{suffix}: ").strip()
    return got or default


def _gather_values(
    target: Path,
    *,
    yes: bool,
    reader_name: str | None,
    input_fn: Callable[[str], str] = input,
) -> dict[str, str]:
    default_reader = reader_name or target.name
    if yes:
        reader = default_reader
        backend = "openai"
        feishu = "false"
    else:
        reader = reader_name or _prompt(
            "Reader name", default_reader, input_fn=input_fn
        )
        backend = _prompt("LLM backend (openai|multikey)", "openai", input_fn=input_fn)
        if backend not in ("openai", "multikey"):
            raise InitError(
                f"Invalid llm backend: {backend!r} (expected openai|multikey)"
            )
        feishu_raw = _prompt("Enable Feishu publisher? (y/N)", "n", input_fn=input_fn)
        feishu = "true" if feishu_raw.lower().startswith("y") else "false"

    return {
        "reader_name": reader,
        "llm_backend": backend,
        "feishu_enabled": feishu,
        "created_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool_version": __version__,
    }


def _render(template_text: str, values: dict[str, str]) -> str:
    """Apply brace-delimited placeholder substitution to a template string.

    We use str.format with a defensive replacement loop instead of .format(),
    because the template YAML contains literal ``{`` characters inside
    backend option lists that we do not want treated as format fields. A
    .replace() pass per known key sidesteps that hazard entirely.
    """
    out = template_text
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _copy_with_render(src: Path, dst: Path, values: dict[str, str]) -> None:
    """Copy src → dst, rendering placeholders if the file contains any.

    ``.tmpl`` files always get the rendering pass; other files are copied
    byte-for-byte. This keeps the plumbing agnostic to whether a template
    actually uses placeholders (some configs/*.tmpl do not).
    """
    if src.suffix == ".tmpl":
        dst.parent.mkdir(parents=True, exist_ok=True)
        rendered = _render(src.read_text(encoding="utf-8"), values)
        dst.write_text(rendered, encoding="utf-8")
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _populate_home(target: Path, templates_root: Path, values: dict[str, str]) -> None:
    _copy_with_render(
        templates_root / "config.yaml.tmpl",
        target / CONFIG_FILENAME,
        values,
    )
    _copy_with_render(
        templates_root / "env.tmpl",
        target / ".env",
        values,
    )

    configs_src = templates_root / "configs"
    configs_dst = target / "configs"
    configs_dst.mkdir(parents=True, exist_ok=True)
    for tmpl in sorted(configs_src.iterdir()):
        if not tmpl.is_file():
            continue
        out_name = (
            tmpl.name[: -len(".tmpl")] if tmpl.name.endswith(".tmpl") else tmpl.name
        )
        _copy_with_render(tmpl, configs_dst / out_name, values)

    (target / "logs").mkdir(parents=True, exist_ok=True)
    (target / "data").mkdir(parents=True, exist_ok=True)

    sentinel_src = templates_root / "sentinel.tmpl"
    (target / SENTINEL_NAME).write_text(
        _render(sentinel_src.read_text(encoding="utf-8"), values),
        encoding="utf-8",
    )


def run_init(
    *,
    target_path: Path,
    force: bool,
    yes: bool,
    reader_name: str | None,
    input_fn: Callable[[str], str] = input,
) -> Path:
    target = target_path.expanduser().resolve()
    _assert_safe_target(target, force=force)
    target.mkdir(parents=True, exist_ok=True)

    templates_root = _templates_root()
    values = _gather_values(target, yes=yes, reader_name=reader_name, input_fn=input_fn)

    _populate_home(target, templates_root, values)

    loaded = HomeConfig.load(target)
    assert loaded.path == target, (
        f"HomeConfig.load resolved to {loaded.path} but init wrote to {target}. "
        "This is a bug — init must produce a HOME that load() recognizes."
    )
    return target


def cmd_init(args: argparse.Namespace) -> None:
    try:
        home = run_init(
            target_path=Path(args.path),
            force=bool(getattr(args, "force", False)),
            yes=bool(getattr(args, "yes", False)),
            reader_name=getattr(args, "reader_name", None),
        )
    except InitError as err:
        print(f"deep-daily init failed: {err}", file=sys.stderr)
        sys.exit(2)

    print(f"Initialized deep-daily HOME at {home}")
    print(f"  - edit {home}/.env to provide API keys")
    print(f"  - edit {home}/configs/*.yaml to define your topics / sources / KOLs")
    print(f"  - then run: deep-daily --home {home} doctor")
