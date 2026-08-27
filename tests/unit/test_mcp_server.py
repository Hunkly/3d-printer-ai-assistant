"""Tests for the MCP server and its system.* tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.server.server import FastMCP

from print_engineer.config import Settings
from print_engineer.mcp.server import _prepare_tool_description, create_server


@pytest.fixture
def server(tmp_root: Path) -> FastMCP:
    settings = Settings.load(root=tmp_root)
    return create_server(settings)


def test_server_registers_system_tools(server: FastMCP) -> None:
    async def run() -> set[str]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools}

    names = asyncio.run(run())
    assert {"system.info", "system.health", "print.prepare"} <= names
    assert len(names) == len(set(names))


def test_call_system_health(server: FastMCP) -> None:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool("system.health", {})
        return result.content[0].text

    text = asyncio.run(run())
    assert '"status"' in text
    assert "ok" in text


def test_call_system_info(server: FastMCP) -> None:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool("system.info", {})
        return result.content[0].text

    text = asyncio.run(run())
    assert "print-engineer" in text
    assert "python" in text
def test_public_prepare_description_is_preparation_only() -> None:
    description = _prepare_tool_description("print.prepare")
    assert "Does not upload" in description
    assert "start printing" in description
    assert "explicit material" in description.lower()
