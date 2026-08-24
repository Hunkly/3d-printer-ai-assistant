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
from print_engineer.core.types import (
    AMSInfo,
    PrinterIssue,
    PrinterIssueSource,
    PrinterState,
    PrinterStatus,
)
from print_engineer.errors import (
    PrinterAuthFailed,
    PrinterError,
    PrinterInvalidReport,
    PrinterTimeout,
    PrinterUnreachable,
)
from print_engineer.mcp.server import create_server
from print_engineer.mcp.tools.printer import (
    PrinterTools,
    _assess_status,
    _format_status_summary,
)


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
    issue_metadata_paths: tuple[Path, ...] = (),
) -> Settings:
    return Settings(
        root=tmp_root,
        printer=PrinterConfig(
            host=host, serial=serial, issue_metadata_paths=issue_metadata_paths
        ),
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


def _write_issue_resource(
    path: Path,
    *,
    locale: str = "en",
    family: str = "A01",
    entries: list[dict[str, str]] | None = None,
) -> bytes:
    raw = json.dumps(
        {
            "schema_version": 1,
            "vendor": "bambu_lab",
            "vendor_dataset_version": "202608141853",
            "device_family": family,
            "locale": locale,
            "entries": entries
            or [
                {
                    "source": "hms",
                    "lookup_key": "0300123400020056",
                    "message": "HMS explanation",
                },
                {
                    "source": "print_error",
                    "lookup_key": "0012ABCD",
                    "message": "Print error explanation",
                },
            ],
        },
        separators=(",", ":"),
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _call_issue_info(
    server: FastMCP,
    *,
    source: str = "hms",
    code: str = "0300123400020056",
    locale: str = "en",
    allow_english_fallback: bool = False,
) -> dict[str, Any]:
    return json.loads(
        _call_tool(
            server,
            "printer.issue_info",
            {
                "source": source,
                "code": code,
                "locale": locale,
                "allow_english_fallback": allow_english_fallback,
            },
        )
    )


def test_server_registers_issue_info_with_four_required_fields(
    server: FastMCP,
) -> None:
    async def run() -> Any:
        async with Client(server) as client:
            tools = await client.list_tools()
        matches = [tool for tool in tools if tool.name == "printer.issue_info"]
        assert len(matches) == 1
        return matches[0]

    tool = asyncio.run(run())
    assert set(tool.inputSchema["properties"]) == {
        "source",
        "code",
        "locale",
        "allow_english_fallback",
    }
    assert set(tool.inputSchema["required"]) == {
        "source",
        "code",
        "locale",
        "allow_english_fallback",
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"source": "hms", "code": "0300123400020056", "locale": "en"},
        {
            "source": "hms",
            "code": 123,
            "locale": "en",
            "allow_english_fallback": False,
        },
        {
            "source": "hms",
            "code": "0300123400020056",
            "locale": "en",
            "allow_english_fallback": False,
            "extra": True,
        },
    ],
    ids=["missing-required", "wrong-primitive-type", "unexpected-extra"],
)
def test_fastmcp_rejects_issue_info_schema_before_tool_body(
    tmp_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
) -> None:
    def body_sentinel(
        self: PrinterTools,
        source: str,
        code: str,
        locale: str,
        allow_english_fallback: bool,
    ) -> dict[str, Any]:
        raise AssertionError("issue_info body was reached")

    monkeypatch.setattr(PrinterTools, "issue_info", body_sentinel)
    server = _server_with(_settings(tmp_root))
    with pytest.raises(Exception) as caught:
        _call_tool(server, "printer.issue_info", arguments)
    assert not isinstance(caught.value, AssertionError)
    assert "issue_info_invalid_request" not in str(caught.value)


def test_issue_info_resolves_hms_and_print_error_without_adapter(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    path = tmp_root / "issues.json"
    raw = _write_issue_resource(path)
    server = _server_with(
        _settings(
            tmp_root,
            serial="A01-serial",
            issue_metadata_paths=(path,),
        )
    )

    hms = _call_issue_info(server)
    assert hms["issue"] == {"source": "hms", "code": "0300123400020056"}
    assert hms["resolved"] is True
    assert hms["metadata"] == {
        "message": "HMS explanation",
        "locale": "en",
        "vendor": "bambu_lab",
        "vendor_dataset_version": "202608141853",
        "resource_schema_version": 1,
        "provenance_origin": "user_supplied",
        "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
    }
    assert "path" not in json.dumps(hms)

    print_error = _call_issue_info(
        server, source="print_error", code="0012ABCD"
    )
    assert print_error["metadata"]["message"] == "Print error explanation"
    assert fake_adapter.instances == []


@pytest.mark.parametrize(
    ("source", "code", "expected"),
    [
        ("hms", "0300123400020056", None),
        ("hms", "03001234000200ab", "invalid_hms_code"),
        ("hms", "030012340002005", "invalid_hms_code"),
        ("hms", "030012340002005G", "invalid_hms_code"),
        ("hms", " 0300123400020056", "invalid_hms_code"),
        ("hms", "0x300123400020056", "invalid_hms_code"),
        ("print_error", "00000001", None),
        ("print_error", "7FFFFFFF", None),
        ("print_error", "0012abcd", "invalid_print_error_code"),
        ("print_error", "0012ABC", "invalid_print_error_code"),
        ("print_error", "12AB34G6", "invalid_print_error_code"),
        ("print_error", "00000000", "invalid_print_error_code"),
        ("print_error", "80000000", "invalid_print_error_code"),
        ("print_error", " 0012ABCD", "invalid_print_error_code"),
        ("print_error", "0x12ABCD", "invalid_print_error_code"),
    ],
)
def test_issue_info_complete_code_validation_matrix(
    tmp_root: Path, source: str, code: str, expected: str | None
) -> None:
    payload = _call_issue_info(
        _server_with(_settings(tmp_root)), source=source, code=code
    )
    if expected is None:
        assert payload["ok"] is True
    else:
        assert payload == {
            "ok": False,
            "error": {
                "code": "issue_info_invalid_request",
                "message": "Invalid printer issue lookup request.",
                "details": {"field": "code", "reason": expected},
            },
        }


@pytest.mark.parametrize("locale", ["EN", "en-us", "e", "en_US", " uk-UA"])
def test_issue_info_locale_validation_matrix(tmp_root: Path, locale: str) -> None:
    payload = _call_issue_info(_server_with(_settings(tmp_root)), locale=locale)
    assert payload == {
        "ok": False,
        "error": {
            "code": "issue_info_invalid_request",
            "message": "Invalid printer issue lookup request.",
            "details": {"field": "locale", "reason": "invalid_locale"},
        },
    }


def test_issue_info_invalid_validation_order_is_source_code_locale(
    tmp_root: Path,
) -> None:
    payload = _call_issue_info(
        _server_with(_settings(tmp_root)),
        source="wrong",
        code="bad",
        locale="EN",
    )
    assert payload["error"]["details"] == {
        "field": "source",
        "reason": "unsupported_source",
    }


@pytest.mark.parametrize(
    ("source", "code", "locale", "expected"),
    [
        ("other", "0300123400020056", "en", {"field": "source", "reason": "unsupported_source"}),
        ("HMS", "0300123400020056", "en", {"field": "source", "reason": "unsupported_source"}),
        ("hms", "030012340002005g", "en", {"field": "code", "reason": "invalid_hms_code"}),
        ("hms", "03001234", "en", {"field": "code", "reason": "invalid_hms_code"}),
        ("print_error", "00000000", "en", {"field": "code", "reason": "invalid_print_error_code"}),
        ("print_error", "12AB34G6", "en", {"field": "code", "reason": "invalid_print_error_code"}),
        ("hms", "0300123400020056", "EN", {"field": "locale", "reason": "invalid_locale"}),
    ],
)
def test_issue_info_invalid_request_contract(
    tmp_root: Path,
    source: str,
    code: str,
    locale: str,
    expected: dict[str, str],
) -> None:
    payload = _call_issue_info(
        _server_with(_settings(tmp_root)),
        source=source,
        code=code,
        locale=locale,
    )
    assert payload == {
        "ok": False,
        "error": {
            "code": "issue_info_invalid_request",
            "message": "Invalid printer issue lookup request.",
            "details": expected,
        },
    }


def test_issue_info_unresolved_and_english_fallback_are_publicly_stable(
    tmp_root: Path,
) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path, locale="en")
    server = _server_with(
        _settings(tmp_root, serial="A01", issue_metadata_paths=(path,))
    )
    unresolved = _call_issue_info(server, locale="de")
    assert unresolved == {
        "ok": True,
        "issue": {"source": "hms", "code": "0300123400020056"},
        "resolved": False,
        "metadata": None,
        "reason": "no_match",
    }
    fallback = _call_issue_info(server, locale="de", allow_english_fallback=True)
    assert fallback["resolved"] is True
    assert fallback["metadata"]["locale"] == "en"


@pytest.mark.parametrize("kind", ["missing", "non-regular", "malformed"])
def test_issue_info_metadata_failures_are_redacted(
    tmp_root: Path, kind: str
) -> None:
    path = tmp_root / ("private-name.json" if kind == "malformed" else "missing.json")
    if kind == "non-regular":
        path.mkdir(parents=True)
    elif kind == "malformed":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"private": "resource-content"}', encoding="utf-8")
    payload = _call_issue_info(
        _server_with(_settings(tmp_root, issue_metadata_paths=(path,)))
    )
    assert payload == {
        "ok": False,
        "error": {
            "code": "issue_info_metadata_invalid",
            "message": "Configured printer issue metadata is invalid.",
            "details": {},
        },
    }
    serialized = json.dumps(payload)
    assert str(path) not in serialized
    assert path.name not in serialized
    assert "resource-content" not in serialized
    assert "IssueMetadata" not in serialized
    assert "source_reference" not in serialized
    assert "serial" not in serialized
    assert "access" not in serialized
    assert "host" not in serialized


def test_issue_info_empty_metadata_config_is_normal_no_match(tmp_root: Path) -> None:
    payload = _call_issue_info(_server_with(_settings(tmp_root)))
    assert payload == {
        "ok": True,
        "issue": {"source": "hms", "code": "0300123400020056"},
        "resolved": False,
        "metadata": None,
        "reason": "no_match",
    }


def test_issue_info_loads_metadata_once_per_invocation_and_does_not_cache(
    tmp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import print_engineer.mcp.tools.printer as printer_module

    path = tmp_root / "issues.json"
    _write_issue_resource(path)
    calls = 0
    original = printer_module.load_issue_metadata

    def spy(paths: tuple[Path, ...]) -> Any:
        nonlocal calls
        calls += 1
        return original(paths)

    monkeypatch.setattr(printer_module, "load_issue_metadata", spy)
    tool = PrinterTools(_settings(tmp_root, serial="A01", issue_metadata_paths=(path,)))
    first = tool.issue_info("hms", "0300123400020056", "en", False)
    path.write_bytes(_write_issue_resource(path, entries=[{
        "source": "hms", "lookup_key": "0300123400020056", "message": "changed"
    }]))
    second = tool.issue_info("hms", "0300123400020056", "en", False)
    assert first["metadata"]["message"] == "HMS explanation"
    assert second["metadata"]["message"] == "changed"
    assert calls == 2


def test_issue_info_serial_precedence_selects_observable_family(tmp_root: Path) -> None:
    a01 = tmp_root / "a01.json"
    b02 = tmp_root / "b02.json"
    _write_issue_resource(a01, family="A01")
    _write_issue_resource(b02, family="B02", entries=[{
        "source": "hms", "lookup_key": "0300123400020056", "message": "secret family"
    }])
    payload = _call_issue_info(
        _server_with(_settings(
            tmp_root, serial="A01-config", secrets_serial="B02-secret",
            issue_metadata_paths=(a01, b02),
        ))
    )
    assert payload["metadata"]["message"] == "secret family"
    assert "serial" not in json.dumps(payload)


@pytest.mark.parametrize("serial", [None, "A", "A0!", "C03-serial"])
def test_issue_info_invalid_or_unmapped_serial_is_no_match(
    tmp_root: Path, serial: str | None
) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path)
    payload = _call_issue_info(
        _server_with(_settings(tmp_root, serial=serial, issue_metadata_paths=(path,)))
    )
    assert payload["resolved"] is False
    assert payload["reason"] == "no_match"


def test_issue_info_case_preserves_family_for_resolution(tmp_root: Path) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path, family="a01")
    # Family matching preserves case; a lower-case configured family matches a
    # lower-case serial and is not normalized to upper-case.
    payload = _call_issue_info(
        _server_with(_settings(tmp_root, serial="a01-serial", issue_metadata_paths=(path,)))
    )
    assert payload["resolved"] is True
    assert payload["metadata"]["locale"] == "en"


def test_issue_info_resolution_matrix_and_source_isolation(tmp_root: Path) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path, entries=[
        {"source": "hms", "lookup_key": "0300123400020056", "message": "HMS"},
        {"source": "print_error", "lookup_key": "00000001", "message": "PE"},
    ])
    server = _server_with(_settings(tmp_root, serial="A01", issue_metadata_paths=(path,)))
    hms = _call_issue_info(server)
    assert hms["resolved"] is True
    assert hms["issue"] == {"source": "hms", "code": "0300123400020056"}
    assert hms["metadata"]["message"] == "HMS"
    print_error = _call_issue_info(server, source="print_error", code="00000001")
    assert print_error["resolved"] is True
    assert print_error["issue"] == {"source": "print_error", "code": "00000001"}
    assert print_error["metadata"]["message"] == "PE"
    isolated = _call_issue_info(server, source="hms", code="0102030405060708")
    assert isolated == {
        "ok": True,
        "issue": {"source": "hms", "code": "0102030405060708"},
        "resolved": False,
        "metadata": None,
        "reason": "no_match",
    }


def test_issue_info_exact_locale_precedes_english_and_rejects_unrelated_locale(
    tmp_root: Path,
) -> None:
    en = tmp_root / "en.json"
    uk = tmp_root / "uk.json"
    _write_issue_resource(en, locale="en")
    _write_issue_resource(uk, locale="uk-UA", entries=[{
        "source": "hms", "lookup_key": "0300123400020056", "message": "Українське"
    }])
    server = _server_with(_settings(tmp_root, serial="A01", issue_metadata_paths=(en, uk)))
    exact = _call_issue_info(server, locale="uk-UA", allow_english_fallback=True)
    assert exact["metadata"]["message"] == "Українське"
    assert exact["metadata"]["locale"] == "uk-UA"
    fallback = _call_issue_info(server, locale="fr", allow_english_fallback=True)
    assert fallback["metadata"]["locale"] == "en"
    disabled = _call_issue_info(server, locale="fr", allow_english_fallback=False)
    assert disabled["resolved"] is False
    unrelated = _call_issue_info(server, locale="de", allow_english_fallback=False)
    assert unrelated["resolved"] is False


@pytest.mark.parametrize("code", ["0102030405060708", "00000001"])
def test_issue_info_unknown_code_is_no_match(tmp_root: Path, code: str) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path)
    payload = _call_issue_info(
        _server_with(_settings(tmp_root, serial="A01", issue_metadata_paths=(path,))),
        source="print_error" if code == "00000001" else "hms",
        code=code,
    )
    assert payload["ok"] is True
    assert payload["resolved"] is False
    assert payload["reason"] == "no_match"


def test_issue_info_family_mismatch_and_determinism(tmp_root: Path) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path)
    server = _server_with(_settings(tmp_root, serial="B02", issue_metadata_paths=(path,)))
    first = _call_issue_info(server)
    second = _call_issue_info(server)
    assert first == second
    assert first["resolved"] is False
    assert json.loads(json.dumps(first)) == first


def test_issue_info_offline_sentinels_are_not_touched(
    tmp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import print_engineer.adapters.printer.bambu as bambu_module
    import print_engineer.adapters.printer.transport as transport_module
    import print_engineer.mcp.tools.printer as printer_module

    path = tmp_root / "issues.json"
    _write_issue_resource(path)

    def fail_boundary(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline boundary touched")

    def fail_status(self: PrinterTools) -> dict[str, Any]:
        raise AssertionError("offline boundary touched")

    monkeypatch.setattr(printer_module, "BambuPrinterAdapter", fail_boundary)
    monkeypatch.setattr(printer_module, "_connection_params", fail_boundary)
    monkeypatch.setattr(PrinterTools, "status", fail_status)
    monkeypatch.setattr(bambu_module, "PahoMqttClientFactory", fail_boundary)
    monkeypatch.setattr(transport_module, "PahoMqttClient", fail_boundary)
    payload = _call_issue_info(
        _server_with(_settings(tmp_root, serial="A01", issue_metadata_paths=(path,)))
    )
    assert payload["resolved"] is True


def test_issue_info_does_not_change_printer_status_lifecycle(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    path = tmp_root / "issues.json"
    _write_issue_resource(path)
    server = _server_with(_settings(
        tmp_root, host="10.0.0.5", serial="A01", access_code="1234",
        issue_metadata_paths=(path,),
    ))
    issue = _call_issue_info(server)
    status = _call_status(server)
    assert issue["resolved"] is True
    assert status["status"]["issues"] == []
    assert status["summary"] == _format_status_summary(_ok_status())
    assert status["assessment"]["code"] == "printer_printing"
    assert len(fake_adapter.instances) == 1
    assert fake_adapter.instances[0].get_status_calls == 1


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
    assert "summary" not in payload
    assert "assessment" not in payload
    assert fake_adapter.instances == []


def test_missing_serial_returns_printer_not_configured(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root, host="10.0.0.5", access_code="1234"))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == ["serial"]
    assert "summary" not in payload
    assert "assessment" not in payload
    assert fake_adapter.instances == []


def test_missing_access_code_returns_printer_not_configured(
    tmp_root: Path, fake_adapter: type[_FakeAdapter]
) -> None:
    server = _server_with(_settings(tmp_root, host="10.0.0.5", serial="A01"))
    payload = _call_status(server)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "printer_not_configured"
    assert payload["error"]["details"]["missing"] == ["access_code"]
    assert "summary" not in payload
    assert "assessment" not in payload
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
    assert "summary" not in payload
    assert "assessment" not in payload
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
    assert payload["status"]["issues"] == []
    assert payload["summary"] == (
        "Printing · 42% complete · Layer 10 / 100 · About 139 min remaining"
        " · Nozzle 220.5 / 220 °C · Bed 55 / 60 °C · AMS connected"
    )
    assert payload["assessment"] == {
        "level": "info",
        "code": "printer_printing",
        "message": "Printer is printing.",
    }
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
    assert payload["assessment"] == {
        "level": "error",
        "code": "printer_disconnected",
        "message": "Printer is disconnected.",
    }


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
    assert "assessment" not in payload


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
    assert "assessment" not in payload


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
    assert "assessment" not in payload


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
    assert "assessment" not in payload


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


def test_paused_full_status_summary_keeps_active_job_fragments() -> None:
    status = PrinterStatus(
        state=PrinterState.PAUSED,
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
        "Paused · 73% complete · Layer 184 / 252 · About 32 min remaining"
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
    assert payload["assessment"] == {
        "level": "error",
        "code": "printer_disconnected",
        "message": "Printer is disconnected.",
    }
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
        "issues": [],
    }


def test_printer_status_serializes_opaque_issues_without_affecting_presentation(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus(
        state=PrinterState.IDLE,
        is_connected=True,
        progress=1.0,
        issues=(
            PrinterIssue(PrinterIssueSource.HMS, "0300123400020056"),
            PrinterIssue(PrinterIssueSource.HMS, "0102030405060708"),
            PrinterIssue(PrinterIssueSource.PRINT_ERROR, "0012ABCD"),
        ),
    )

    payload = _call_status(server)

    assert payload["status"]["issues"] == [
        {"source": "hms", "code": "0300123400020056"},
        {"source": "hms", "code": "0102030405060708"},
        {"source": "print_error", "code": "0012ABCD"},
    ]
    assert all(set(issue) == {"source", "code"} for issue in payload["status"]["issues"])
    assert payload["status"]["progress"] == 1.0
    assert payload["summary"] == "Idle"
    assert payload["assessment"] == {
        "level": "info",
        "code": "printer_idle",
        "message": "Printer is idle.",
    }
    assert len(fake_adapter.instances) == 1
    assert fake_adapter.instances[0].get_status_calls == 1


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
            state=PrinterState.PRINTING,
            is_connected=True,
            progress=progress,
        )
    )
    if fragment is None:
        assert summary == "Printing"
    else:
        assert summary == f"Printing · {fragment}"


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


def test_real_a1_idle_summary_suppresses_job_fragments_without_mutation(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus(
        state=PrinterState.IDLE,
        is_connected=True,
        progress=1.0,
        current_layer=80,
        total_layers=80,
        remaining_time_minutes=0,
        nozzle_temp=27.3125,
        target_nozzle_temp=0.0,
        bed_temp=27.84375,
        target_bed_temp=0.0,
        ams=AMSInfo(is_connected=True),
    )

    payload = _call_status(server)

    assert payload["summary"] == (
        "Idle · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected"
    )
    assert payload["status"]["progress"] == 1.0
    assert payload["status"]["current_layer"] == 80
    assert payload["status"]["total_layers"] == 80
    assert payload["status"]["remaining_time_minutes"] == 0
    assert payload["assessment"] == {
        "level": "info",
        "code": "printer_idle",
        "message": "Printer is idle.",
    }
    assert len(fake_adapter.instances) == 1
    assert fake_adapter.instances[0].get_status_calls == 1


@pytest.mark.parametrize(
    ("state", "lead"),
    [
        (PrinterState.IDLE, "Idle"),
        (PrinterState.ERROR, "Printer error"),
        (PrinterState.UNKNOWN, "Status unknown"),
        (PrinterState.OFFLINE, "Offline"),
    ],
)
def test_non_active_state_summary_suppresses_job_fragments_only(
    state: PrinterState,
    lead: str,
    server: FastMCP,
    fake_adapter: type[_FakeAdapter],
) -> None:
    fake_adapter.status = PrinterStatus(
        state=state,
        is_connected=True,
        progress=0.5,
        current_layer=10,
        total_layers=100,
        remaining_time_minutes=20,
        nozzle_temp=27.3125,
        target_nozzle_temp=0.0,
        bed_temp=27.84375,
        target_bed_temp=0.0,
        ams=AMSInfo(is_connected=True, slots=["A1"]),
    )

    payload = _call_status(server)

    assert payload["summary"] == (
        f"{lead} · Nozzle 27.3 / 0 °C · Bed 27.8 / 0 °C · AMS connected"
    )
    assert payload["status"]["progress"] == 0.5
    assert payload["status"]["current_layer"] == 10
    assert payload["status"]["total_layers"] == 100
    assert payload["status"]["remaining_time_minutes"] == 20


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


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            PrinterState.OFFLINE,
            {
                "level": "attention",
                "code": "printer_offline",
                "message": "Printer reports an offline state.",
            },
        ),
        (
            PrinterState.IDLE,
            {
                "level": "info",
                "code": "printer_idle",
                "message": "Printer is idle.",
            },
        ),
        (
            PrinterState.PRINTING,
            {
                "level": "info",
                "code": "printer_printing",
                "message": "Printer is printing.",
            },
        ),
        (
            PrinterState.PAUSED,
            {
                "level": "attention",
                "code": "printer_paused",
                "message": "Printer is paused.",
            },
        ),
        (
            PrinterState.ERROR,
            {
                "level": "error",
                "code": "printer_error",
                "message": "Printer reports an error state.",
            },
        ),
        (
            PrinterState.UNKNOWN,
            {
                "level": "unknown",
                "code": "printer_state_unknown",
                "message": "Printer state is unknown.",
            },
        ),
    ],
)
def test_connected_status_assessment_table(
    state: PrinterState, expected: dict[str, str]
) -> None:
    assert _assess_status(PrinterStatus(state=state, is_connected=True)) == expected


@pytest.mark.parametrize("state", [PrinterState.PRINTING, PrinterState.ERROR])
def test_disconnected_assessment_takes_precedence(state: PrinterState) -> None:
    assert _assess_status(PrinterStatus(state=state, is_connected=False)) == {
        "level": "error",
        "code": "printer_disconnected",
        "message": "Printer is disconnected.",
    }


def test_assessment_ignores_unrelated_telemetry() -> None:
    minimal = PrinterStatus(state=PrinterState.PAUSED, is_connected=True)
    populated = PrinterStatus(
        state=PrinterState.PAUSED,
        is_connected=True,
        bed_temp=55.0,
        nozzle_temp=220.0,
        target_bed_temp=60.0,
        target_nozzle_temp=225.0,
        progress=0.75,
        ams=AMSInfo(is_connected=True, slots=["A1", "A2"]),
        current_layer=75,
        total_layers=100,
        remaining_time_minutes=45,
    )
    assert _assess_status(minimal) == _assess_status(populated) == {
        "level": "attention",
        "code": "printer_paused",
        "message": "Printer is paused.",
    }


def test_successful_error_state_has_assessment(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus(
        state=PrinterState.ERROR,
        is_connected=True,
        progress=0.5,
    )

    payload = _call_status(server)

    assert payload["ok"] is True
    assert payload["status"]["state"] == "error"
    assert payload["summary"] == "Printer error"
    assert payload["status"]["progress"] == 0.5
    assert payload["assessment"] == {
        "level": "error",
        "code": "printer_error",
        "message": "Printer reports an error state.",
    }


def test_disconnected_error_state_preserves_structured_state(
    server: FastMCP, fake_adapter: type[_FakeAdapter]
) -> None:
    fake_adapter.status = PrinterStatus(
        state=PrinterState.ERROR,
        is_connected=False,
        remaining_time_minutes=20,
    )

    payload = _call_status(server)

    assert payload["ok"] is True
    assert payload["status"]["state"] == "error"
    assert payload["status"]["remaining_time_minutes"] == 20
    assert payload["summary"] == "Printer disconnected"
    assert payload["assessment"] == {
        "level": "error",
        "code": "printer_disconnected",
        "message": "Printer is disconnected.",
    }


def test_assessment_is_deterministic() -> None:
    status = PrinterStatus(state=PrinterState.UNKNOWN, is_connected=True)
    assert _assess_status(status) == _assess_status(status)
