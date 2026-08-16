"""Tests for the ``print.recommend`` MCP tool (Phase 3A)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.server.server import FastMCP

from print_engineer.config import Settings
from print_engineer.core.recommendation import (
    RecommendationGoal,
    RecommendationMode,
    RecommendationSet,
)
from print_engineer.mcp.server import create_server


class _FakeEngine:
    def __init__(self, settings: Any, llm: Any = None) -> None:
        self._settings = settings

    def recommend(self, request: Any) -> RecommendationSet:
        return RecommendationSet(
            goal=RecommendationGoal.BALANCED,
            recommendations=[],
            summary="fake deterministic result",
            warnings=[],
            mode=RecommendationMode.DETERMINISTIC,
        )


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


def test_server_registers_recommend_tool(server: FastMCP) -> None:
    async def run() -> set[str]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools}

    names = asyncio.run(run())
    assert "print.recommend" in names


def test_recommend_ok(monkeypatch: pytest.MonkeyPatch, server: FastMCP, tmp_path: Path) -> None:
    import print_engineer.mcp.tools.recommend as recommend_module

    monkeypatch.setattr(recommend_module, "RecommendationEngine", _FakeEngine)
    path = tmp_path / "part.stl"
    path.write_text("solid m\nendsolid m\n", encoding="utf-8")

    text = _call_tool(server, "print.recommend", {"model": str(path)})

    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["recommendations"]["mode"] == "deterministic"
    assert payload["recommendations"]["goal"] == "balanced"


def test_recommend_invalid_goal(
    monkeypatch: pytest.MonkeyPatch, server: FastMCP, tmp_path: Path
) -> None:
    import print_engineer.mcp.tools.recommend as recommend_module

    monkeypatch.setattr(recommend_module, "RecommendationEngine", _FakeEngine)
    path = tmp_path / "part.stl"
    path.write_text("solid m\nendsolid m\n", encoding="utf-8")

    text = _call_tool(server, "print.recommend", {"model": str(path), "goal": "cheap"})

    payload = json.loads(text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "slicer_error"
    assert "invalid recommendation request" in payload["error"]["message"]


def test_recommend_missing_model_errors(server: FastMCP, tmp_path: Path) -> None:
    text = _call_tool(server, "print.recommend", {"model": str(tmp_path / "missing.stl")})

    payload = json.loads(text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_model"
    assert payload["error"]["details"]["reason"] == "not_found"
