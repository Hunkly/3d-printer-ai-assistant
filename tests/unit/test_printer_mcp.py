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
    )


class _FakeAdapter:
    """Hermetic stand-in for BambuPrinterAdapter (no MQTT, no network).

    Records every constructed instance so tests can assert exactly which
    host/serial/access_code were resolved and passed in.
    """

    instances: list[_FakeAdapter] = []
    exc: PrinterError | None = None

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
        _FakeAdapter.instances.append(self)

    def get_status(self) -> PrinterStatus:
        if self.exc is not None:
            raise self.exc
        return _ok_status()


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
