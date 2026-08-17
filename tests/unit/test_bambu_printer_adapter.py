"""Unit tests for the Bambu LAN MQTT printer adapter (Phase 2+, read-only).

Hermetic: uses a fake MqttClient/factory; no network, no physical printer.
The fake exposes only the MqttClient protocol surface (connect/fetch_report/
disconnect) — there is no ``publish``, so this increment cannot accidentally
publish.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.adapters.printer.transport import MqttConnectionError
from print_engineer.core.types import PrinterState, PrinterStatus, TemperatureSetpoint
from print_engineer.errors import (
    PrinterAuthFailed,
    PrinterInvalidReport,
    PrinterNotConfigured,
    PrinterOperationUnsupported,
    PrinterTimeout,
    PrinterUnreachable,
)

HOST = "10.0.0.5"
SERIAL = "S1"
ACCESS_CODE = "1234"

_UNSET = object()


class _FakeClient:
    """Fake MqttClient: records lifecycle, returns a canned payload."""

    def __init__(
        self,
        factory: _FakeFactory,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
    ) -> None:
        self.factory = factory
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.disconnect_count = 0

    def connect(self) -> None:
        if self.factory.connect_error is not None:
            raise self.factory.connect_error

    def fetch_report(self, topic: str, timeout_seconds: float) -> bytes | None:
        return self.factory.payload

    def disconnect(self) -> None:
        self.disconnect_count += 1


class _FakeFactory:
    """Fake MqttClientFactory recording every constructed client."""

    def __init__(self) -> None:
        self.clients: list[_FakeClient] = []
        self.payload: bytes | None = None
        self.connect_error: MqttConnectionError | None = None

    def __call__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
    ) -> _FakeClient:
        client = _FakeClient(
            self,
            host=host,
            port=port,
            username=username,
            password=password,
            client_id=client_id,
        )
        self.clients.append(client)
        return client


def _payload(print_obj: dict[str, Any]) -> bytes:
    return json.dumps({"print": print_obj}).encode("utf-8")


def _make_adapter(
    *,
    factory: _FakeFactory | None = None,
    host: str = HOST,
    serial: str = SERIAL,
    access_code: str = ACCESS_CODE,
) -> BambuPrinterAdapter:
    return BambuPrinterAdapter(
        host=host,
        serial=serial,
        access_code=access_code,
        client_factory=factory or _FakeFactory(),
    )


def _status(
    print_obj: dict[str, Any] | None = None,
    *,
    payload: Any = _UNSET,
    factory: _FakeFactory | None = None,
) -> tuple[PrinterStatus, BambuPrinterAdapter, _FakeFactory]:
    """Build an adapter over a fake client and return its status."""
    f = factory or _FakeFactory()
    if payload is _UNSET:
        f.payload = _payload(print_obj or {})
    else:
        f.payload = payload
    adapter = _make_adapter(factory=f)
    return adapter.get_status(), adapter, f


# --- Configuration ---------------------------------------------------------


def test_missing_host_raises() -> None:
    with pytest.raises(PrinterNotConfigured) as exc_info:
        _make_adapter(host="")
    assert exc_info.value.to_dict()["details"]["missing"] == ["host"]


def test_missing_serial_raises() -> None:
    with pytest.raises(PrinterNotConfigured) as exc_info:
        _make_adapter(serial="")
    assert exc_info.value.to_dict()["details"]["missing"] == ["serial"]


def test_missing_access_code_raises() -> None:
    with pytest.raises(PrinterNotConfigured) as exc_info:
        _make_adapter(access_code="")
    assert exc_info.value.to_dict()["details"]["missing"] == ["access_code"]


def test_multiple_missing_fields_raise() -> None:
    with pytest.raises(PrinterNotConfigured) as exc_info:
        _make_adapter(host="", serial="", access_code="")
    assert exc_info.value.to_dict()["details"]["missing"] == [
        "host",
        "serial",
        "access_code",
    ]


# --- Client construction ---------------------------------------------------


def test_client_construction() -> None:
    factory = _FakeFactory()
    _status(print_obj={"gcode_state": "IDLE"}, factory=factory)
    assert len(factory.clients) == 1
    client = factory.clients[0]
    assert client.host == HOST
    assert client.port == 8883
    assert client.username == "bblp"
    assert client.password == ACCESS_CODE
    assert client.client_id == f"print-engineer-{SERIAL}"


# --- Status normalization --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IDLE", PrinterState.IDLE),
        ("RUNNING", PrinterState.PRINTING),
        ("PREPARE", PrinterState.PRINTING),
        ("PAUSE", PrinterState.PAUSED),
        ("FINISH", PrinterState.IDLE),
        ("FAILED", PrinterState.ERROR),
        ("UNKNOWN", PrinterState.UNKNOWN),
    ],
)
def test_gcode_state_mapping(raw: str, expected: PrinterState) -> None:
    status, _, _ = _status({"gcode_state": raw})
    assert status.state == expected


def test_missing_gcode_state_is_unknown() -> None:
    status, _, _ = _status({})
    assert status.state == PrinterState.UNKNOWN


def test_non_string_gcode_state_is_unknown() -> None:
    status, _, _ = _status({"gcode_state": 42})
    assert status.state == PrinterState.UNKNOWN


def test_unknown_string_gcode_state_is_unknown() -> None:
    status, _, _ = _status({"gcode_state": "BOGUS"})
    assert status.state == PrinterState.UNKNOWN


# --- Progress --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(50, 0.5), ("25.5", 0.255)],
)
def test_progress_parsed(raw: Any, expected: float) -> None:
    status, _, _ = _status({"mc_percent": raw})
    assert status.progress == expected


def test_progress_missing_is_none() -> None:
    status, _, _ = _status({})
    assert status.progress is None


def test_progress_invalid_is_none() -> None:
    status, _, _ = _status({"mc_percent": "abc"})
    assert status.progress is None


# --- Temperatures ----------------------------------------------------------

_TEMP_FIELDS: list[tuple[str, str]] = [
    ("nozzle_temper", "nozzle_temp"),
    ("nozzle_target_temper", "target_nozzle_temp"),
    ("bed_temper", "bed_temp"),
    ("bed_target_temper", "target_bed_temp"),
]


@pytest.mark.parametrize(("field", "attr"), _TEMP_FIELDS)
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(220.5, 220.5), ("220.5", 220.5), (220, 220.0)],
)
def test_temperature_parsed(field: str, attr: str, raw: Any, expected: float) -> None:
    status, _, _ = _status({field: raw})
    assert getattr(status, attr) == expected


@pytest.mark.parametrize(("field", "attr"), _TEMP_FIELDS)
def test_temperature_missing_is_none(field: str, attr: str) -> None:
    status, _, _ = _status({})
    assert getattr(status, attr) is None


@pytest.mark.parametrize(("field", "attr"), _TEMP_FIELDS)
def test_temperature_unparseable_is_none(field: str, attr: str) -> None:
    status, _, _ = _status({field: "hot"})
    assert getattr(status, attr) is None


# --- AMS -------------------------------------------------------------------


def test_ams_loaded_trays() -> None:
    print_obj = {
        "ams": {
            "ams": [
                {
                    "tray": [
                        {"id": "1", "nozzle_temper": 0},
                        {"id": "2", "nozzle_temper": 0},
                    ]
                },
                {"tray": [{"id": "1", "nozzle_temper": 0}]},
            ]
        }
    }
    status, _, _ = _status(print_obj)
    assert status.ams is not None
    assert status.ams.is_connected is True
    assert status.ams.slots == ["A1", "A2", "B1"]


def test_ams_tray_with_only_id_excluded() -> None:
    print_obj = {
        "ams": {
            "ams": [
                {"tray": [{"id": "1"}, {"id": "2", "nozzle_temper": 0}]},
            ]
        }
    }
    status, _, _ = _status(print_obj)
    assert status.ams is not None
    assert status.ams.slots == ["A2"]


def test_ams_id_254_excluded() -> None:
    print_obj = {
        "ams": {
            "ams": [
                {
                    "tray": [
                        {"id": "254", "nozzle_temper": 0},
                        {"id": "1", "nozzle_temper": 0},
                    ]
                },
            ]
        }
    }
    status, _, _ = _status(print_obj)
    assert status.ams is not None
    assert status.ams.slots == ["A2"]


def test_ams_missing_is_none() -> None:
    status, _, _ = _status({})
    assert status.ams is None


# --- Errors ----------------------------------------------------------------


def test_auth_failure_raises() -> None:
    factory = _FakeFactory()
    factory.connect_error = MqttConnectionError("auth")
    with pytest.raises(PrinterAuthFailed):
        _status(factory=factory)


def test_unreachable_raises() -> None:
    factory = _FakeFactory()
    factory.connect_error = MqttConnectionError("unreachable")
    with pytest.raises(PrinterUnreachable):
        _status(factory=factory)


def test_no_report_raises_timeout() -> None:
    with pytest.raises(PrinterTimeout):
        _status(payload=None)


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xfe\x00",  # invalid UTF-8
        b"{not json",  # invalid JSON
        b"[1, 2, 3]",  # non-object JSON
        b'{"foo": 1}',  # missing print object
        b'{"print": "nope"}',  # print not a mapping
    ],
)
def test_invalid_payload_raises(payload: bytes) -> None:
    with pytest.raises(PrinterInvalidReport):
        _status(payload=payload)


# --- Lifecycle -------------------------------------------------------------


def test_disconnect_after_success() -> None:
    factory = _FakeFactory()
    _status(print_obj={"gcode_state": "RUNNING"}, factory=factory)
    assert factory.clients[0].disconnect_count == 1


def test_disconnect_after_auth_failure() -> None:
    factory = _FakeFactory()
    factory.connect_error = MqttConnectionError("auth")
    with pytest.raises(PrinterAuthFailed):
        _status(factory=factory)
    assert factory.clients[0].disconnect_count == 1


def test_disconnect_after_unreachable_failure() -> None:
    factory = _FakeFactory()
    factory.connect_error = MqttConnectionError("unreachable")
    with pytest.raises(PrinterUnreachable):
        _status(factory=factory)
    assert factory.clients[0].disconnect_count == 1


def test_disconnect_after_timeout() -> None:
    factory = _FakeFactory()
    with pytest.raises(PrinterTimeout):
        _status(factory=factory, payload=None)
    assert factory.clients[0].disconnect_count == 1


def test_disconnect_after_invalid_payload() -> None:
    factory = _FakeFactory()
    with pytest.raises(PrinterInvalidReport):
        _status(factory=factory, payload=b"{not json")
    assert factory.clients[0].disconnect_count == 1


# --- Unsupported operations ------------------------------------------------


_UNSUPPORTED_CALLS: list[tuple[str, Any]] = [
    ("start_print", lambda a: a.start_print(Path("model.stl"))),
    ("stop_print", lambda a: a.stop_print()),
    ("pause_print", lambda a: a.pause_print()),
    ("resume_print", lambda a: a.resume_print()),
    (
        "set_temperature",
        lambda a: a.set_temperature(
            TemperatureSetpoint(component="nozzle", target_celsius=200.0)
        ),
    ),
    ("take_snapshot", lambda a: a.take_snapshot()),
]


@pytest.mark.parametrize(
    "call",
    [call for _, call in _UNSUPPORTED_CALLS],
    ids=[name for name, _ in _UNSUPPORTED_CALLS],
)
def test_unsupported_operations_raise(call: Any) -> None:
    factory = _FakeFactory()
    adapter = _make_adapter(factory=factory)
    with pytest.raises(PrinterOperationUnsupported):
        call(adapter)
    assert factory.clients == []


# --- Read-only regression guard --------------------------------------------


def test_fake_transport_exposes_no_publish_operation() -> None:
    assert not hasattr(_FakeClient, "publish")
    assert not hasattr(_FakeFactory, "publish")
