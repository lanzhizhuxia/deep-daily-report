from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, cast

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Tool

from deep_daily.home import HomeConfig

from .query import collect_stats, get_item, search_text

logger = logging.getLogger("deep_daily.kb.mcp")

SERVER_NAME = "deep-daily-kb"
SERVER_VERSION = "0.3.0"
CLAUDE_CONFIG_PATH = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def create_server(*, db_path: Path) -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search_text",
                description="Search KB items by FTS5 text query and optional filters.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "source": {"type": ["string", "null"]},
                        "start_date": {"type": ["string", "null"]},
                        "end_date": {"type": ["string", "null"]},
                        "author": {"type": ["string", "null"]},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="get_item",
                description="Get one KB item plus all raw provenance references.",
                inputSchema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="stats",
                description="Get KB aggregate counts, ingest status, and provenance stats.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | CallToolResult:
        try:
            if name == "search_text":
                return {"results": await run_search(
                    db_path,
                    query=str(arguments.get("query") or ""),
                    source=arguments.get("source"),
                    start_date=arguments.get("start_date"),
                    end_date=arguments.get("end_date"),
                    author=arguments.get("author"),
                    limit=int(arguments.get("limit", 20)),
                )}
            if name == "get_item":
                return {"item": await run_get_item(db_path, str(arguments.get("id") or ""))}
            if name == "stats":
                return await run_stats(db_path)
            logger.warning("Unknown MCP tool requested: %s", name)
            return {"error": "unknown_tool", "message": f"Unknown tool: {name}"}
        except Exception as err:  # pragma: no cover
            logger.exception("MCP tool %s failed", name)
            return {"error": "internal_error", "message": str(err)}

    return server


async def run_search(
    db_path: Path | str,
    *,
    query: str,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    author: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    try:
        with _connect_read_only(Path(db_path)) as conn:
            return search_text(
                conn,
                query=query,
                source=_none_if_blank(source),
                start_date=_none_if_blank(start_date),
                end_date=_none_if_blank(end_date),
                author=_none_if_blank(author),
                limit=limit,
            )
    except Exception as err:
        logger.warning("search_text failed: %s", err)
        return []


async def run_get_item(db_path: Path | str, item_id: str) -> dict[str, Any] | None:
    try:
        with _connect_read_only(Path(db_path)) as conn:
            return get_item(conn, item_id=item_id)
    except Exception as err:
        logger.warning("get_item failed for %s: %s", item_id, err)
        return None


async def run_stats(db_path: Path | str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(collect_stats, Path(db_path))
    except Exception as err:
        logger.warning("stats failed: %s", err)
        return {
            "items_total": 0,
            "per_source": {},
            "date_range": {"earliest": None, "latest": None},
            "last_ingest": None,
            "db_size_bytes": 0,
            "provenance_stats": {
                "tweets_curated_only": 0,
                "tweets_bulk_only": 0,
                "tweets_merged": 0,
            },
        }


async def run_stdio_server(*, db_path: Path) -> None:
    server = create_server(db_path=db_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VERSION,
                capabilities=server.get_capabilities(NotificationOptions(), experimental_capabilities={}),
            ),
        )


def install_claude_desktop(home: HomeConfig, *, python_executable: str | None = None, config_path: Path | None = None) -> tuple[Path, bool, bool]:
    target_path = config_path or CLAUDE_CONFIG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existed = target_path.exists()
    current = _read_json_config(target_path) if existed else {}
    if not isinstance(current, dict):
        current = {}
    mcp_servers = current.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    current["mcpServers"] = mcp_servers

    entry = {
        "command": str(Path(python_executable or sys.executable).resolve()),
        "args": ["-m", "deep_daily", "--home", str(home.path.resolve()), "kb", "mcp"],
    }
    replaced = False
    existing_entry = mcp_servers.get(SERVER_NAME)
    if existing_entry is not None and existing_entry != entry:
        logger.warning("Replacing existing Claude Desktop MCP entry for %s", SERVER_NAME)
        replaced = True
    mcp_servers[SERVER_NAME] = entry
    target_path.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target_path, existed, replaced


def _read_json_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=1")
    return conn


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
