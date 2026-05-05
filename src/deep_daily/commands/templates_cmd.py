"""``deep-daily templates`` — enumerate bundled template packs.

Per PLAN v2.1 §3.6. Currently only the ``default`` pack ships; leaving the
action surface in place makes future packs (minimal / rwa / team) a pure
addition under ``templates/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_TEMPLATES_ROOT_NAME = "templates"


def _templates_root() -> Path:
    """Resolve the repo-root ``templates/`` directory.

    Mirrors the lookup in init_cmd._templates_root(); kept separate so changes
    to packaging (e.g. moving templates into the wheel) can be made command
    by command rather than via a shared helper that might not apply to both.
    """
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent.parent
    return repo_root / _TEMPLATES_ROOT_NAME


def list_templates() -> list[str]:
    root = _templates_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def cmd_templates(args: argparse.Namespace) -> int:
    action = getattr(args, "action", None) or "list"
    if action == "list":
        packs = list_templates()
        if not packs:
            print("(no template packs found)", file=sys.stderr)
            return 1
        for name in packs:
            print(name)
        return 0
    print(f"Unknown templates action: {action!r}", file=sys.stderr)
    return 2
