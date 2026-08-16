"""Tests for the ``model.analyze`` MCP tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.server.server import FastMCP
from tests.model_helpers import box_mesh, write_ascii_stl

from print_engineer.config import Settings
from print_engineer.mcp.server import create_server


@pytest.fixture
def server(tmp_root: Path) -> FastMCP:
    settings = Settings.load(root=tmp_root)
    return create_server(settings)


def _call_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> str:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments)
        return result.content[0].text

    return asyncio.run(run())


def test_server_registers_model_tool(server: FastMCP) -> None:
    async def run() -> set[str]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools}

    names = asyncio.run(run())
    assert "model.analyze" in names


def test_analyze_cube(tmp_path: Path, server: FastMCP) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    text = _call_tool(server, "model.analyze", {"model": str(path)})

    payload = json.loads(text)
    assert payload["ok"] is True
    analysis = payload["analysis"]
    assert analysis["format"] == "stl"
    assert analysis["volume_mm3"] == pytest.approx(8000.0)
    assert analysis["dimensions_mm"] == pytest.approx([20, 20, 20])
    assert analysis["topology"]["watertight"] is True


def test_analyze_cube_with_threshold(tmp_path: Path, server: FastMCP) -> None:
    path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
    arguments = {"model": str(path), "overhang_threshold_degrees": 0.0}
    text = _call_tool(server, "model.analyze", arguments)

    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["analysis"]["overhang"]["area_mm2"] == pytest.approx(400.0)


def test_analyze_missing_file(tmp_path: Path, server: FastMCP) -> None:
    text = _call_tool(server, "model.analyze", {"model": str(tmp_path / "missing.stl")})

    payload = json.loads(text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_model"
    assert payload["error"]["details"]["reason"] == "not_found"
