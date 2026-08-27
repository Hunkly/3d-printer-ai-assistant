import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastmcp import Client
from tests.slicer_helpers import write_cube_stl

from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.config import Settings
from print_engineer.mcp.server import create_server


def test_registered_prepare_fail_closed_without_orca(tmp_path: Path) -> None:
    server = create_server(Settings(root=tmp_path))

    async def invoke() -> dict[str, object]:
        async with Client(server) as client:
            result = await client.call_tool(
                "print.prepare", {"model": str(tmp_path / "missing.stl"), "goal": "balanced"}
            )
        return json.loads(result.content[0].text)

    response = cast(Any, asyncio.run(invoke()))
    assert response["ok"] is False
    assert response["error"]["code"] == "model_missing"
    assert response["error"]["details"]["stage"] == "model_input"
    assert str(tmp_path) not in json.dumps(response)


def test_registered_prepare_ready_is_hermetic_to_orca_process(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_store = Path.home() / "AppData" / "Roaming" / "OrcaSlicer"
    if not source_store.is_dir():
        raise AssertionError("local Orca profile store is required for this acceptance test")
    profile_store = tmp_path / "orca-appdata"
    shutil.copytree(source_store, profile_store)
    model = write_cube_stl(tmp_path / "cube.stl")

    def fake_slice(_adapter: Any, job: Any) -> Any:
        assert job.output_dir is not None
        (job.output_dir / "plate_1.gcode").write_text(
            "; total layer number: 3\nG1 X1 Y1\n", encoding="utf-8"
        )
        return SimpleNamespace(return_code=0)

    monkeypatch.setattr(OrcaSlicerAdapter, "slice", fake_slice)
    settings = Settings(root=tmp_path)
    settings.slicer.orca_appdata_path = profile_store
    server = create_server(settings)

    async def invoke() -> dict[str, object]:
        async with Client(server) as client:
            result = await client.call_tool(
                "print.prepare",
                {
                    "model": str(model),
                    "goal": "balanced",
                    "printer": "Bambu Lab A1 0.4 nozzle",
                    "build_plate": "cool_plate",
                    "nozzle_diameter_mm": 0.4,
                },
            )
        return json.loads(result.content[0].text)

    response = cast(Any, asyncio.run(invoke()))
    assert response["ok"] is True
    preparation = response["preparation"]
    assert preparation["status"] == "READY"
    assert preparation["setup"]["printer"] == {
        "name": "Bambu Lab A1 0.4 nozzle", "setting_id": "GM030"
    }
    assert preparation["setup"]["process"] == {
        "name": "0.20mm Standard @BBL A1", "setting_id": "GP079"
    }
    assert preparation["setup"]["filament"] == {
        "name": "Bambu PLA Tough+ @base", "setting_id": None
    }
    assert preparation["artifact"]["size_bytes"] > 0
    assert preparation["slice"]["layer_count"] == 3
    assert "workspace" not in preparation
    assert "config" not in preparation
