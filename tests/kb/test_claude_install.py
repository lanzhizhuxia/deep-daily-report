from __future__ import annotations

import json
import logging
from pathlib import Path

from deep_daily.home import HomeConfig
from deep_daily.kb.mcp import install_claude_desktop


def test_install_creates_full_skeleton(tmp_path: Path) -> None:
    home = _make_home(tmp_path / "home")
    config_path = tmp_path / "claude_desktop_config.json"
    written_path, existed, replaced = install_claude_desktop(home, python_executable="/usr/bin/python3", config_path=config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert written_path == config_path
    assert existed is False
    assert replaced is False
    assert payload["mcpServers"]["deep-daily-kb"]["command"] == "/usr/bin/python3"
    assert payload["mcpServers"]["deep-daily-kb"]["args"] == ["-m", "deep_daily", "--home", str(home.path.resolve()), "kb", "mcp"]


def test_install_merges_and_preserves_other_servers(tmp_path: Path) -> None:
    home = _make_home(tmp_path / "home")
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "other": {"command": "/bin/other", "args": ["serve"]},
                    "another": {"command": "/bin/another", "args": []},
                },
            }
        ),
        encoding="utf-8",
    )
    install_claude_desktop(home, python_executable="/usr/bin/python3", config_path=config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["theme"] == "dark"
    assert payload["mcpServers"]["other"]["command"] == "/bin/other"
    assert payload["mcpServers"]["another"]["command"] == "/bin/another"
    assert payload["mcpServers"]["deep-daily-kb"]["command"] == "/usr/bin/python3"


def test_install_overwrites_our_key_and_warns(tmp_path: Path, caplog) -> None:
    home = _make_home(tmp_path / "home")
    config_path = tmp_path / "claude_desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "deep-daily-kb": {"command": "/old/python", "args": ["-m", "old"]},
                    "other": {"command": "/bin/other", "args": ["serve"]},
                }
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="deep_daily.kb.mcp"):
        _, existed, replaced = install_claude_desktop(home, python_executable="/usr/bin/python3", config_path=config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert existed is True
    assert replaced is True
    assert payload["mcpServers"]["other"]["command"] == "/bin/other"
    assert payload["mcpServers"]["deep-daily-kb"]["command"] == "/usr/bin/python3"
    assert "Replacing existing Claude Desktop MCP entry" in caplog.text


def _make_home(path: Path) -> HomeConfig:
    path.mkdir()
    (path / ".deep-daily-home").write_text("schema_version: 1\n", encoding="utf-8")
    (path / "config.yaml").write_text("schema_version: 1\nreader:\n  name: test\n", encoding="utf-8")
    return HomeConfig.load(path)
