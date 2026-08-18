"""Tests for the ``printer.status`` MCP tool (Phase 2+, read-only).

Hermetic: the real ``BambuPrinterAdapter`` is replaced with a fake that
captures constructor arguments, so no MQTT connection and no physical
printer are ever involved. Connection-parameter resolution is tested
against the real ``_connection_params`` path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.server.server import FastMCP

from print_engineer.config import BambuSecrets, PrinterConfig, Settings
from print_engineer.core.types import AMSInfo, PrinterState, PrinterStatus
from print_engineer.errors import (
    PrinterAuthFailed,
    PrinterError,
    PrinterInvalidReport,
    PrinterTimeout,
    PrinterUnreachable,
)
from print_engineer.mcp.server import create_server
from print_engineer.mcp.tools.printer import _format_status_summary


def _ok_status() -> PrinterStatus:
    return PrinterStatus(
        state=PrinterState.PRINTING,
        is_connected=True,
        bed_temp=55.0,
        nozzle_temp=220.5,
        target_bed_temp=60.0,
        target_nozzle_temp=220.0,
        progress=0.42,
        ams=AMSInfo(is_connected=True, slots=["A1", "A2"]),
        current_layer=10,
        total_layers=100,
        remaining_time_minutes=139,
    )


class _FakeAdapter:
    """Hermetic stand-in for BambuPrinterAdapter (no MQTT, no network).

    Records every constructed instance so tests can assert exactly which
    host/serial/access_code were resolved and passed in.
    """

    instances: list[_FakeAdapter] = []
    exc: PrinterError | None = None
    status: PrinterStatus = _ok_status()

    def __init__(
        self,
        *,
        host: str,
        serial: str,
        access_code: str,
        timeout_seconds: float = 10.0,
        client_factory: Any = None,
    ) -> None:
        self.host = host
        self.serial = serial
        self.access_code = access_code
        self.get_status_calls = 0
        _FakeAdapter.instances.append(self)

    def get_status(self) -> PrinterStatus:
        self.get_status_calls += 1
        if self.exc is not None:
            raise self.exc
        return self.status


def _settings(
    tmp_root: Path,
    *,
    host: str | None = None,
    serial: str | None = None,
    secrets_ip: str | None = None,
    secrets_serial: str | None = None,
    access_code: str | None = None,
) -> Settings:
    return Settings(
        root=tmp_root,
        printer=PrinterConfig(host=host, serial=serial),
        secrets=BambuSecrets(
            ip=secrets_ip, serial=secrets_serial, access_code=access_code
        ),
    )


def _server_with(settings: Settings) -> FastMCP:
    return create_server(settings)


@pytest.fixture
def server(tmp_root) -> FastMCP:  # type: ignore[no-untyped-def]
    return _server_with(
        _settings(
            tmp_root,
            host="10.0.0.5",
            serial="A01",
            access_code="1234",
        )
    )


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAdapter]:
    import print_engineer.mcp.tools.printer as printer_module

    _FakeAdapter.instances = []
    _FakeAdapter.exc = None
    _FakeAdapter.status = _ok_status()
    monkeypatch.setattr(printer_module, "BambuPrinterAdapter", _FakeAdapter)
    return _FakeAdapter


def _call_tool(server: FastMCP, name: str, arguments: dict[str, object]) -> str:
    async def run() -> str:
        async with Client(server) as client:
            result = await client.call_tool(name, arguments)
        return result.content[0].text

    return asyncio.run(run())


def _call_status(server: FastMCP) -> dict[str, Any]:
    return json.loads(_call_tool(server, "printer.status", {}))


def test_server_registers_printer_status(server: FastMCP) -> None:
    async def run() -> set[str]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools}

    names = asyncio.run(run())
    assert "printer.status" in names


def test_secrets_ip_overrides_printer_host(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(
            tmp_root,
            host="10.0.0.99",
            serial="A01",
            secrets_ip="10.0.0.5",
            access_code="1234",
        )
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    assert fake_adapter.instances[-1].host == "10.0.0.5"


def test_secrets_serial_overrides_printer_serial(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(
            tmp_root,
            host="10.0.0.5",
            serial="A01",
            secrets_serial="S9",
            access_code="1234",
        )
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    assert fake_adapter.instances[-1].serial == "S9"


def test_printer_host_used_when_secrets_ip_missing(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(tmp_root, host="10.0.0.99", serial="A01", access_code="1234")
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    assert fake_adapter.instances[-1].host == "10.0.0.99"


def test_printer_serial_used_when_secrets_serial_missing(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(tmp_root, host="10.0.0.5", serial="A01", access_code="1234")
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    assert fake_adapter.instances[-1].serial == "A01"


def test_access_code_comes_from_secrets(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(tmp_root, host="10.0.0.5", serial="A01", access_code="1234")
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    assert fake_adapter.instances[-1].access_code == "1234"


def test_adapter_receives_exact_resolved_connection_parameters(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(
        _settings(
            tmp_root,
            host="10.0.0.99",
            serial="CONFIG-SERIAL",
            secrets_ip="10.0.0.5",
            secrets_serial="SECRET-SERIAL",
            access_code="secret-code",
        )
    )
    payload = _call_status(server)
    assert payload["ok"] is True
    adapter = fake_adapter.instances[-1]
    assert (adapter.host, adapter.serial, adapter.access_code) == (
        "10.0.0.5",
        "SECRET-SERIAL",
        "secret-code",
    )


def test_missing_host_returns_printer_not_configured(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root, serial="A01", access_code="1234"))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == ["host"]
    assert fake_adapter.instances == []


def test_missing_serial_returns_printer_not_configured(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root, host="10.0.0.5", access_code="1234"))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == ["serial"]
    assert fake_adapter.instances == []


def test_missing_access_code_returns_printer_not_configured(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root, host="10.0.0.5", serial="A01"))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == ["access_code"]
    assert fake_adapter.instances == []


def test_missing_multiple_values_reports_all_keys(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == [
        "host",
        "serial",
        "access_code",
    ]
    assert fake_adapter.instances == []


def test_printer_status_ok_serializes_all_fields(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = None
    payload = _call_status(server)
    assert payload["ok"] is True
    assert payload["status"]["state"] == "printing"
    assert payload["status"]["is_connected"] is True
    assert payload["status"]["bed_temp"] == 55.0
    assert payload["status"]["nozzle_temp"] == 220.5
    assert payload["status"]["target_bed_temp"] == 60.0
    assert payload["status"]["target_nozzle_temp"] == 220.0
    assert payload["status"]["progress"] == 0.42
    assert payload["status"]["ams"] == {"is_connected": True, "slots": ["A1", "A2"]}
    assert payload["status"]["current_layer"] == 10
    assert payload["status"]["total_layers"] == 100
    assert payload["status"]["remaining_time_minutes"] == 139
    assert payload["summary"] == (
        "Printing · 42% complete · Layer 10 / 100 · About 139 min remaining"
        " · Nozzle 220.5 / 220 °C · Bed 55 / 60 °C · AMS connected"
    )
    assert len(fake_adapter.instances) == 1
    assert fake_adapter.instances[0].get_status_calls == 1


def test_printer_status_serializes_unavailable_layers(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus()
    payload = _call_status(server)
    assert payload["ok"] is True
    assert payload["status"]["current_layer"] is None
    assert payload["status"]["total_layers"] is None
    assert payload["status"]["remaining_time_minutes"] is None
    assert payload["status"]["state"] == "unknown"
    assert payload["status"]["is_connected"] is False
    assert payload["summary"] == "Printer disconnected"


def test_printer_status_unreachable(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = PrinterUnreachable(
        "Printer could not be reached over LAN MQTT",
        details={"host": "10.0.0.1", "port": 8883, "reason": "unreachable"},
    )
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_unreachable"
    assert payload["error"]["details"]["host"] == "10.0.0.1"
    assert payload["error"]["details"]["reason"] == "unreachable"
    assert "summary" not in payload


def test_printer_status_auth_failed(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = PrinterAuthFailed(
        "Printer rejected the access code (MQTT CONNACK rc 4/5)",
        details={"hint": "Check BAMBU_ACCESS_CODE."},
    )
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_auth_failed"
    assert payload["error"]["details"]["hint"] == "Check BAMBU_ACCESS_CODE."
    assert "summary" not in payload


def test_printer_status_timeout(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = PrinterTimeout(
        "No status report received within 10.0s",
        details={"topic": "device/S1/report", "timeout_seconds": 10.0},
    )
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_timeout"
    assert payload["error"]["details"]["timeout_seconds"] == 10.0
    assert "summary" not in payload


def test_printer_status_invalid_report(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = PrinterInvalidReport(
        "Printer payload is not valid JSON",
        details={"payload": "{not json"},
    )
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_invalid_report"
    assert payload["error"]["details"]["payload"] == "{not json"
    assert "summary" not in payload


def test_printer_status_serialization(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.exc = None
    payload = _call_status(server)
    # state is a plain string, not an enum; ams is a dict or None
    assert isinstance(payload["status"]["state"], str)
    assert isinstance(payload["status"]["ams"], dict)
    assert isinstance(payload["status"]["ams"]["slots"], list)
    # JSON round-trip is stable
    assert json.loads(json.dumps(payload)) == payload


def test_full_status_summary_exact() -> None:
    status = PrinterStatus(
        state=PrinterState.PRINTING,
        is_connected=True,
        progress=0.73,
        current_layer=184,
        total_layers=252,
        remaining_time_minutes=32,
        nozzle_temp=220.03125,
        target_nozzle_temp=220.0,
        bed_temp=54.9375,
        target_bed_temp=55.0,
        ams=AMSInfo(is_connected=True, slots=["A1"]),
    )
    assert _format_status_summary(status) == (
        "Printing · 73% complete · Layer 184 / 252 · About 32 min remaining"
        " · Nozzle 220 / 220 °C · Bed 54.9 / 55 °C · AMS connected"
    )


def test_disconnected_summary_suppresses_stale_fragments_without_mutation(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    status = PrinterStatus(
        state=PrinterState.PRINTING,
        is_connected=False,
        progress=0.5,
        current_layer=10,
        total_layers=100,
        remaining_time_minutes=20,
        nozzle_temp=220.0,
        target_nozzle_temp=220.0,
        bed_temp=55.0,
        target_bed_temp=55.0,
        ams=AMSInfo(is_connected=True, slots=["A1"]),
    )
    fake_adapter.status = status

    payload = _call_status(server)

    assert payload["summary"] == "Printer disconnected"
    assert payload["status"] == {
        "state": "printing",
        "is_connected": False,
        "bed_temp": 55.0,
        "nozzle_temp": 220.0,
        "target_bed_temp": 55.0,
        "target_nozzle_temp": 220.0,
        "progress": 0.5,
        "ams": {"is_connected": True, "slots": ["A1"]},
        "current_layer": 10,
        "total_layers": 100,
        "remaining_time_minutes": 20,
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (PrinterState.OFFLINE, "Offline"),
        (PrinterState.IDLE, "Idle"),
        (PrinterState.PRINTING, "Printing"),
        (PrinterState.PAUSED, "Paused"),
        (PrinterState.ERROR, "Printer error"),
        (PrinterState.UNKNOWN, "Status unknown"),
    ],
)
def test_connected_state_summary_wording(
    state: PrinterState, expected: str
) -> None:
    assert _format_status_summary(
        PrinterStatus(state=state, is_connected=True)
    ) == expected


@pytest.mark.parametrize(
    ("progress", "fragment"),
    [
        (None, None),
        (0.0, "0% complete"),
        (0.724, "72% complete"),
        (0.725, "73% complete"),
        (0.734, "73% complete"),
        (0.735, "74% complete"),
        (1.0, "100% complete"),
        (-0.1, "0% complete"),
        (1.2, "100% complete"),
        (float("nan"), None),
        (float("inf"), None),
        (float("-inf"), None),
    ],
)
def test_progress_summary_formatting(
    progress: float | None, fragment: str | None
) -> None:
    summary = _format_status_summary(
        PrinterStatus(
            state=PrinterState.UNKNOWN,
            is_connected=True,
            progress=progress,
        )
    )
    if fragment is None:
        assert summary == "Status unknown"
    else:
        assert summary == f"Status unknown · {fragment}"


@pytest.mark.parametrize(
    ("current", "total", "expected"),
    [
        (184, 252, "Printing · Layer 184 / 252"),
        (184, None, "Printing · Layer 184"),
        (None, 252, "Printing · Total layers 252"),
        (None, None, "Printing"),
        (253, 252, "Printing · Layer 253 / 252"),
    ],
)
def test_layer_summary_formatting(
    current: int | None, total: int | None, expected: str
) -> None:
    assert _format_status_summary(
        PrinterStatus(
            state=PrinterState.PRINTING,
            is_connected=True,
            current_layer=current,
            total_layers=total,
        )
    ) == expected


@pytest.mark.parametrize(
    ("state", "remaining", "expected"),
    [
        (PrinterState.PRINTING, 32, "Printing · About 32 min remaining"),
        (PrinterState.PAUSED, 32, "Paused · About 32 min remaining"),
        (PrinterState.PRINTING, 0, "Printing · About 0 min remaining"),
        (PrinterState.PAUSED, 0, "Paused · About 0 min remaining"),
        (PrinterState.IDLE, 139, "Idle"),
        (PrinterState.ERROR, 139, "Printer error"),
        (PrinterState.UNKNOWN, 139, "Status unknown"),
        (PrinterState.OFFLINE, 139, "Offline"),
    ],
)
def test_remaining_time_summary_state_filtering(
    state: PrinterState, remaining: int, expected: str
) -> None:
    assert _format_status_summary(
        PrinterStatus(
            state=state,
            is_connected=True,
            remaining_time_minutes=remaining,
        )
    ) == expected


def test_idle_summary_preserves_structured_remaining_time(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus(
        state=PrinterState.IDLE,
        is_connected=True,
        remaining_time_minutes=139,
    )
    payload = _call_status(server)
    assert payload["summary"] == "Idle"
    assert payload["status"]["remaining_time_minutes"] == 139


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (54.9375, "54.9"),
        (220.03125, "220"),
        (55.0, "55"),
        (54.95, "55"),
        (20.04, "20"),
        (20.06, "20.1"),
    ],
)
def test_temperature_summary_precision(value: float, expected: str) -> None:
    assert _format_status_summary(
        PrinterStatus(
            state=PrinterState.IDLE,
            is_connected=True,
            nozzle_temp=value,
        )
    ) == f"Idle · Nozzle {expected} °C"


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (220.0, 225.0, "Nozzle 220 / 225 °C"),
        (220.0, None, "Nozzle 220 °C"),
        (None, 225.0, "Nozzle target 225 °C"),
        (220.0, float("nan"), "Nozzle 220 °C"),
        (float("nan"), 225.0, "Nozzle target 225 °C"),
        (float("inf"), float("-inf"), None),
    ],
)
def test_nozzle_temperature_summary_partials(
    current: float | None, target: float | None, expected: str | None
) -> None:
    summary = _format_status_summary(
        PrinterStatus(
            state=PrinterState.IDLE,
            is_connected=True,
            nozzle_temp=current,
            target_nozzle_temp=target,
        )
    )
    expected_summary = "Idle" if expected is None else f"Idle · {expected}"
    assert summary == expected_summary
    assert "nan" not in summary
    assert "inf" not in summary


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (55.0, 60.0, "Bed 55 / 60 °C"),
        (55.0, None, "Bed 55 °C"),
        (None, 60.0, "Bed target 60 °C"),
        (None, None, None),
    ],
)
def test_bed_temperature_summary_partials(
    current: float | None, target: float | None, expected: str | None
) -> None:
    summary = _format_status_summary(
        PrinterStatus(
            state=PrinterState.IDLE,
            is_connected=True,
            bed_temp=current,
            target_bed_temp=target,
        )
    )
    expected_summary = "Idle" if expected is None else f"Idle · {expected}"
    assert summary == expected_summary


@pytest.mark.parametrize(
    ("ams", "expected"),
    [
        (AMSInfo(is_connected=True, slots=["A1", "A2"]), "AMS connected"),
        (AMSInfo(is_connected=False, slots=["A1"]), "AMS not connected"),
        (None, None),
    ],
)
def test_ams_summary_formatting(ams: AMSInfo | None, expected: str | None) -> None:
    summary = _format_status_summary(
        PrinterStatus(state=PrinterState.IDLE, is_connected=True, ams=ams)
    )
    expected_summary = "Idle" if expected is None else f"Idle · {expected}"
    assert summary == expected_summary


def test_summary_is_deterministic() -> None:
    status = _ok_status()
    assert _format_status_summary(status) == _format_status_summary(status)
