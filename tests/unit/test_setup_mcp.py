"""Tests for the ``print.filament_candidates`` and ``print.setup`` MCP tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.server.server import FastMCP

from print_engineer.config import Settings
from print_engineer.core.recommendation import (
    FilamentCandidateMatrix,
    RecommendationGoal,
    RecommendationMode,
    ResolvedPrintContext,
    ResolvedPrinter,
    SetupRecommendation,
)
from print_engineer.mcp.server import create_server


def _resolved() -> ResolvedPrintContext:
    return ResolvedPrintContext(
        slicer_kind="orca_slicer",
        printer=ResolvedPrinter(
            name="Bambu Lab A1",
            supported_nozzle_mm=[0.4],
            nozzle_diameter_mm=0.4,
            default_print_profile="0.20mm Standard @BBL A1",
        ),
        nozzle_diameter_mm=0.4,
    )


class _FakeResolver:
    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def resolve(self, intent: Any) -> ResolvedPrintContext:
        return _resolved()

    def adapter(self, slicer_kind: str) -> object:
        return object()


class _FakeMatrixBuilder:
    def __init__(self, settings: Any, adapter: object) -> None:
        self._settings = settings

    def build(
        self,
        resolved: ResolvedPrintContext,
        *,
        goal: RecommendationGoal,
        vendor: str | None = None,
        material: str | None = None,
    ) -> FilamentCandidateMatrix:
        return FilamentCandidateMatrix(
            slicer_kind="orca_slicer",
            printer=resolved.printer,
            goal=goal,
            candidates=[],
            rejected=[],
            warnings=[],
        )


class _FakeSetupEngine:
    def __init__(self, settings: Any, llm: Any | None = None) -> None:
        self._settings = settings

    def recommend(self, request: Any) -> SetupRecommendation:
        return SetupRecommendation(
            goal=request.goal,
            context=_resolved(),
            matrix=FilamentCandidateMatrix(
                slicer_kind="orca_slicer",
                printer=_resolved().printer,
                goal=request.goal,
                candidates=[],
            ),
            mode=RecommendationMode.DETERMINISTIC,
            summary="fake setup result",
        )


@pytest.fixture
def server(tmp_root) -> FastMCP:  # type: ignore[no-untyped-def]
    return create_server(Settings.load(root=tmp_root))


def _call_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> str:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments)
        return result.content[0].text

    return asyncio.run(run())


def test_server_registers_new_print_tools(server: FastMCP) -> None:
    async def run() -> set[str]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools}

    names = asyncio.run(run())
    assert "print.filament_candidates" in names
    assert "print.setup" in names


def test_filament_candidates_ok(
    monkeypatch: pytest.MonkeyPatch, server: FastMCP
) -> None:
    import print_engineer.mcp.tools.recommend as recommend_module

    monkeypatch.setattr(recommend_module, "PrintContextResolver", _FakeResolver)
    monkeypatch.setattr(recommend_module, "FilamentMatrixBuilder", _FakeMatrixBuilder)

    text = _call_tool(
        server,
        "print.filament_candidates",
        {"printer": "Bambu Lab A1", "goal": "balanced"},
    )
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["matrix"]["slicer_kind"] == "orca_slicer"
    assert payload["matrix"]["candidates"] == []


def test_filament_candidates_invalid_slicer(server: FastMCP) -> None:
    text = _call_tool(server, "print.filament_candidates", {"slicer_kind": "nope"})
    payload = json.loads(text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "slicer_error"


def test_setup_ok(monkeypatch: pytest.MonkeyPatch, server: FastMCP) -> None:
    import print_engineer.mcp.tools.recommend as recommend_module

    monkeypatch.setattr(recommend_module, "SetupEngine", _FakeSetupEngine)

    text = _call_tool(server, "print.setup", {"printer": "Bambu Lab A1"})
    payload = json.loads(text)
    assert payload["ok"] is True
    assert payload["setup"]["summary"] == "fake setup result"
    assert payload["setup"]["mode"] == "deterministic"
