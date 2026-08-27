"""MCP server: the interface the AI (OpenCode) talks to.

Runs on stdio transport. ``--check`` starts the server in-process, verifies tool
registration, prints a summary, and exits without blocking.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from fastmcp import Client, FastMCP

from print_engineer import __version__
from print_engineer.config import Settings
from print_engineer.logging_setup import setup_logging
from print_engineer.utils.paths import ensure_dirs


def create_server(settings: Settings) -> FastMCP:
    """Build and configure the MCP server for *settings*."""
    ensure_dirs([settings.storage.db_dir, settings.storage.workspace_dir, settings.logging.log_dir])

    mcp = FastMCP(settings.mcp.server_name, version=__version__)

    from print_engineer.mcp.tools import model, prepare, printer, recommend, slicer, system

    mcp.tool(name="system.info", description="Project metadata (name, version, runtime).")(
        system.system_info
    )
    mcp.tool(name="system.health", description="Health probe; returns ok when serving.")(
        system.system_health
    )
    for name, tool in slicer.build_tools(settings).items():
        mcp.tool(name=name, description=_slicer_tool_description(name))(tool)
    for name, tool in model.build_tools(settings).items():
        mcp.tool(name=name, description=_model_tool_description(name))(tool)
    for name, tool in recommend.build_tools(settings).items():
        mcp.tool(name=name, description=_recommend_tool_description(name))(tool)
    for name, tool in prepare.build_tools(settings).items():
        mcp.tool(name=name, description=_prepare_tool_description(name))(tool)
    for name, tool in printer.build_tools(settings).items():
        mcp.tool(name=name, description=_printer_tool_description(name))(tool)
    return mcp


def _recommend_tool_description(name: str) -> str:
    descriptions = {
        "print.recommend": (
            "AI-assisted print recommendations for a model. Combines measured model "
            "geometry, current slicer settings, optional slice statistics, and (when "
            "enabled) local LLM reasoning. Read-only: never modifies profiles, the "
            "model, or the printer."
        ),
        "print.filament_candidates": (
            "Enumerate and rank the locally-installed filament profiles for a resolved "
            "print context (printer/nozzle/build plate). Compatibility filters and "
            "vendor/material filters are applied; rankings are deterministic. Read-only: "
            "never slices, never applies settings, never touches the printer."
        ),
        "print.setup": (
            "Four-layer setup recommendation (material, filament, nozzle, process) for a "
            "resolved printer and goal. Deterministic ranking is authoritative; an "
            "optional local-LLM narrative must quote profile facts verbatim or is dropped. "
            "Read-only: never slices, applies nothing, never touches the printer."
        ),
    }
    return descriptions.get(name, "Print recommendation tool.")


def _printer_tool_description(name: str) -> str:
    descriptions = {
        "printer.status": (
            "Read-only printer status over LAN MQTT: state, connectivity, "
            "temperatures, progress, and AMS slots. Never starts, stops, pauses, "
            "or resumes printing; never publishes MQTT messages; never slices."
        ),
        "printer.issue_info": (
            "Resolve one supplied Bambu printer issue against configured local metadata. "
            "Read-only and local-only: never connects to or changes the printer."
        ),
    }
    return descriptions.get(name, "Printer tool.")


def _prepare_tool_description(name: str) -> str:
    return (
        "Locally prepares and slices a model with the supported Orca pipeline and returns a "
        "verified preparation result. Does not upload, start printing, or modify printer "
        "state. Explicit material is a hard constraint; omitted material permits deterministic "
        "compatible selection."
    )


def _model_tool_description(name: str) -> str:
    descriptions = {
        "model.analyze": (
            "Deterministic geometry analysis of an STL or 3MF model: dimensions, "
            "volume, surface area, topology, orientation, overhang, and thin-wall "
            "estimates. No slicing and no printer interaction."
        ),
    }
    return descriptions.get(name, "Model analysis tool.")


def _slicer_tool_description(name: str) -> str:
    descriptions = {
        "slicer.list": "List installed slicers with detected versions and capabilities.",
        "slicer.info": "Details for one slicer, including profile counts.",
        "slicer.validate": "Validate a model file against a slicer (no slicing).",
        "slicer.slice": "Slice a model with the given process/filament/printer profiles.",
    }
    return descriptions.get(name, "Slicer tool.")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="print-engineer-mcp", description="MCP server for print-engineer"
    )
    parser.add_argument("--config", help="Path to a config YAML file.")
    parser.add_argument(
        "--check", action="store_true", help="Start, verify, and exit (non-blocking)."
    )
    args = parser.parse_args()

    settings = Settings.load(args.config)
    setup_logging(settings.logging, settings.app.log_level)
    log = logging.getLogger("print_engineer.mcp")
    log.info("starting MCP server version=%s root=%s", __version__, settings.root)

    mcp = create_server(settings)
    if args.check:
        names = _registered_tool_names(mcp)
        print(f"OK: {len(names)} tool(s) registered ({', '.join(names)})")
        return 0

    mcp.run()
    return 0


def _registered_tool_names(mcp: FastMCP) -> list[str]:
    async def run() -> list[str]:
        async with Client(mcp) as client:
            tools = await client.list_tools()
        return [tool.name for tool in tools]

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
