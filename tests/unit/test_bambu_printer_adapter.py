"""Hermetic unit tests for the Bambu LAN MQTT printer adapter.

Uses fake transport boundaries; no network or physical printer is involved.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import print_engineer.adapters.printer.bambu as bambu_module
import print_engineer.adapters.printer.transport as transport_module
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
        serial: str,
    ) -> None:
        self.factory = factory
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.serial = serial
        self.connect_count = 0
        self.disconnect_count = 0
        self.refresh_count = 0
        self.fetch_timeouts: list[float] = []
        self.events: list[str] = []

    def connect(self) -> None:
        self.events.append("connect")
        self.connect_count += 1
        if self.factory.connect_error is not None:
            raise self.factory.connect_error

    def request_status_refresh(self) -> bool:
        self.events.append("refresh")
        if self.factory.refresh_error is not None:
            raise self.factory.refresh_error
        self.refresh_count += 1
        return True

    def fetch_report(self, topic: str, timeout_seconds: float) -> bytes | None:
        self.events.append("fetch")
        self.fetch_timeouts.append(timeout_seconds)
        if self.factory.payloads:
            return self.factory.payloads.pop(0)
        payload = self.factory.payload
        self.factory.payload = None
        return payload

    def disconnect(self) -> None:
        self.events.append("disconnect")
        self.disconnect_count += 1
        if self.factory.disconnect_error is not None:
            raise self.factory.disconnect_error


class _FakeFactory:
    """Fake MqttClientFactory recording every constructed client."""

    def __init__(self) -> None:
        self.clients: list[_FakeClient] = []
        self.payload: bytes | None = None
        self.payloads: list[bytes] = []
        self.connect_error: MqttConnectionError | None = None
        self.refresh_error: MqttConnectionError | None = None
        self.disconnect_error: Exception | None = None

    def __call__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        client_id: str,
        serial: str,
    ) -> _FakeClient:
        client = _FakeClient(
            self,
            host=host,
            port=port,
            username=username,
            password=password,
            client_id=client_id,
            serial=serial,
        )
        self.clients.append(client)
        return client


class _SuccessReasonCode:
    is_failure = False
    value = 0


class _AdapterPahoClient:
    """Fake external Paho boundary used with the real production transport."""

    def __init__(
        self,
        *,
        subscribe_payloads: list[bytes] | None = None,
        publish_payloads: list[bytes] | None = None,
    ) -> None:
        self.on_connect: Any = None
        self.on_connect_fail: Any = None
        self.on_message: Any = None
        self.subscribe_payloads = subscribe_payloads or []
        self.publish_payloads = publish_payloads or []
        self.subscriptions: list[tuple[str, int]] = []
        self.publishes: list[tuple[str, str, int]] = []
        self.disconnect_count = 0
        self.loop_stop_count = 0

    def tls_set(self, **_kwargs: Any) -> None:
        pass

    def username_pw_set(self, _username: str, _password: str) -> None:
        pass

    def connect(self, _host: str, _port: int, *, keepalive: int) -> None:
        pass

    def loop_start(self) -> None:
        self.on_connect(self, None, None, _SuccessReasonCode(), None)

    def loop_stop(self) -> None:
        self.loop_stop_count += 1

    def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
        self.subscriptions.append((topic, qos))
        for payload in self.subscribe_payloads:
            self._emit(topic, payload)
        return (transport_module.mqtt.MQTT_ERR_SUCCESS, 1)

    def publish(self, topic: str, payload: str, qos: int) -> Any:
        self.publishes.append((topic, payload, qos))
        report_topic = f"device/{SERIAL}/report"
        for report in self.publish_payloads:
            self._emit(report_topic, report)
        return SimpleNamespace(rc=transport_module.mqtt.MQTT_ERR_SUCCESS)

    def disconnect(self) -> None:
        self.disconnect_count += 1

    def _emit(self, topic: str, payload: bytes) -> None:
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload))


@pytest.fixture
def isolated_production_refresh_state() -> Iterator[None]:
    with transport_module._refresh_eligibility_lock:
        transport_module._refresh_next_eligible.clear()
    try:
        yield
    finally:
        with transport_module._refresh_eligibility_lock:
            transport_module._refresh_next_eligible.clear()


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
    assert client.serial == SERIAL
    assert client.refresh_count == 1


def test_real_transport_cooldown_survives_adapter_recreation(
    monkeypatch: pytest.MonkeyPatch,
    isolated_production_refresh_state: None,
) -> None:
    first_paho = _AdapterPahoClient(
        publish_payloads=[
            _payload({"command": "push_status", "msg": 0, "gcode_state": "RUNNING"})
        ]
    )
    second_paho = _AdapterPahoClient(
        subscribe_payloads=[
            _payload({"bed_temper": 42}),
            _payload({"gcode_state": "IDLE"}),
        ]
    )
    paho_clients = iter([first_paho, second_paho])
    monkeypatch.setattr(
        transport_module.mqtt,
        "Client",
        lambda *_args, **_kwargs: next(paho_clients),
    )

    first = BambuPrinterAdapter(host=HOST, serial=SERIAL, access_code=ACCESS_CODE)
    second = BambuPrinterAdapter(host=HOST, serial=SERIAL, access_code=ACCESS_CODE)

    assert first.get_status().state == PrinterState.PRINTING
    second_status = second.get_status()

    assert second_status.state == PrinterState.IDLE
    assert second_status.bed_temp == 42.0
    assert len(first_paho.publishes) == 1
    assert second_paho.publishes == []
    assert len(first_paho.publishes) + len(second_paho.publishes) == 1
    assert first_paho.disconnect_count == 1
    assert second_paho.disconnect_count == 1
    assert first_paho.loop_stop_count == 1
    assert second_paho.loop_stop_count == 1


def test_refresh_failure_maps_to_unreachable_and_disconnects() -> None:
    factory = _FakeFactory()
    factory.refresh_error = MqttConnectionError("unreachable")

    with pytest.raises(PrinterUnreachable):
        _status(factory=factory)

    client = factory.clients[0]
    assert client.refresh_count == 0
    assert client.events == ["connect", "refresh", "disconnect"]


def test_connection_failure_does_not_reach_refresh() -> None:
    factory = _FakeFactory()
    factory.connect_error = MqttConnectionError("unreachable")
    with pytest.raises(PrinterUnreachable):
        _status(factory=factory)
    assert factory.clients[0].refresh_count == 0
    assert "refresh" not in factory.clients[0].events

    factory.connect_error = None
    factory.payload = _payload({"gcode_state": "IDLE"})
    assert _make_adapter(factory=factory).get_status().state == PrinterState.IDLE
    assert factory.clients[1].refresh_count == 1


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


# --- Layers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(10, 10), (0, 0), ("10", 10), ("0", 0), (" 10 ", 10), (250, 250)],
)
def test_layer_values_are_normalized(raw: Any, expected: int) -> None:
    status, _, _ = _status({"layer_num": raw, "total_layer_num": raw})
    assert status.current_layer == expected
    assert status.total_layers == expected


@pytest.mark.parametrize(
    "raw",
    [True, False, -1, 10.0, None, "", " ", "-1", "+1", "10.5", "1e2", "abc", [], {}],
)
def test_malformed_layer_values_are_unavailable(raw: Any) -> None:
    status, _, _ = _status({"layer_num": raw, "total_layer_num": raw})
    assert status.current_layer is None
    assert status.total_layers is None


def test_layer_fields_missing_are_unavailable() -> None:
    status, _, _ = _status({"gcode_state": "IDLE"})
    assert status.current_layer is None
    assert status.total_layers is None


def test_layer_values_accumulate_independently_and_preserve_valid_values() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "RUNNING", "layer_num": 10, "total_layer_num": 100}),
        _payload({"mc_percent": 25}),
        _payload({"layer_num": 11}),
        _payload({"layer_num": "bad", "total_layer_num": 10.0}),
        _payload({"total_layer_num": 9}),
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()

    initial = adapter.get_status()
    sparse = adapter.get_status()
    partial = adapter.get_status()
    malformed = adapter.get_status()
    independent = adapter.get_status()

    assert (initial.current_layer, initial.total_layers) == (10, 100)
    assert (sparse.current_layer, sparse.total_layers) == (10, 100)
    assert (partial.current_layer, partial.total_layers) == (11, 100)
    assert (malformed.current_layer, malformed.total_layers) == (11, 100)
    assert (independent.current_layer, independent.total_layers) == (11, 9)


# --- Remaining time --------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [(139, 139), (0, 0)])
def test_remaining_time_accepts_exact_non_negative_integers(
    raw: Any, expected: int
) -> None:
    status, _, _ = _status({"mc_remaining_time": raw})
    assert status.remaining_time_minutes == expected


@pytest.mark.parametrize(
    "raw",
    [True, False, -1, 139.0, "139", " 139 ", "0", None, "", "bad", [], {}],
)
def test_remaining_time_rejects_unsupported_values(raw: Any) -> None:
    status, _, _ = _status({"mc_remaining_time": raw})
    assert status.remaining_time_minutes is None


def test_missing_remaining_time_is_unavailable() -> None:
    status, _, _ = _status({"gcode_state": "IDLE"})
    assert status.remaining_time_minutes is None


def test_remaining_time_accumulates_sparse_reports_and_estimate_revisions() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "RUNNING", "mc_remaining_time": 139}),
        _payload({"mc_percent": 25}),
        _payload({"mc_remaining_time": 138}),
        _payload({"mc_remaining_time": 145}),
        _payload({"mc_remaining_time": "bad"}),
        _payload({"mc_remaining_time": 0}),
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()

    assert adapter.get_status().remaining_time_minutes == 139
    assert adapter.get_status().remaining_time_minutes == 139
    assert adapter.get_status().remaining_time_minutes == 138
    assert adapter.get_status().remaining_time_minutes == 145
    assert adapter.get_status().remaining_time_minutes == 145
    assert adapter.get_status().remaining_time_minutes == 0


@pytest.mark.parametrize("raw", [True, False, -1, 139.0, "139", None, [], {}])
def test_malformed_remaining_time_preserves_last_valid_value(raw: Any) -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "RUNNING", "mc_remaining_time": 139}),
        _payload({"mc_remaining_time": raw}),
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()

    assert adapter.get_status().remaining_time_minutes == 139
    assert adapter.get_status().remaining_time_minutes == 139


def test_remaining_time_is_independent_of_printer_state() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "PAUSE", "mc_remaining_time": 52}),
        _payload({"gcode_state": "RUNNING", "mc_remaining_time": 139}),
        _payload({"gcode_state": "IDLE"}),
        _payload({"gcode_state": "FINISH", "mc_remaining_time": 0}),
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()

    paused = adapter.get_status()
    printing = adapter.get_status()
    idle = adapter.get_status()
    finished = adapter.get_status()

    assert paused.state == PrinterState.PAUSED
    assert paused.remaining_time_minutes == 52
    assert printing.state == PrinterState.PRINTING
    assert printing.remaining_time_minutes == 139
    assert idle.state == PrinterState.IDLE
    assert idle.remaining_time_minutes == 139
    assert finished.state == PrinterState.IDLE
    assert finished.remaining_time_minutes == 0


def test_full_status_preserves_existing_fields_with_remaining_time() -> None:
    status, _, _ = _status(
        {
            "gcode_state": "RUNNING",
            "mc_percent": 42,
            "bed_temper": 55,
            "nozzle_temper": 220.5,
            "bed_target_temper": 60,
            "nozzle_target_temper": 220,
            "layer_num": 10,
            "total_layer_num": 100,
            "mc_remaining_time": 139,
            "ams": {"ams": [{"tray": [{"id": "1", "nozzle_temper": 0}]}]},
        }
    )
    assert status.state == PrinterState.PRINTING
    assert status.progress == 0.42
    assert (status.bed_temp, status.nozzle_temp) == (55.0, 220.5)
    assert (status.target_bed_temp, status.target_nozzle_temp) == (60.0, 220.0)
    assert status.ams == bambu_module.AMSInfo(is_connected=True, slots=["A1"])
    assert (status.current_layer, status.total_layers) == (10, 100)
    assert status.remaining_time_minutes == 139


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


def test_retained_connection_reads_distinct_reports_on_one_client() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "RUNNING", "mc_percent": 10}),
        _payload({"gcode_state": "PAUSE", "mc_percent": 20}),
    ]
    adapter = _make_adapter(factory=factory)

    adapter.connect()
    first = adapter.get_status()
    second = adapter.get_status()

    assert len(factory.clients) == 1
    client = factory.clients[0]
    assert client.connect_count == 1
    assert client.disconnect_count == 0
    assert client.refresh_count == 0
    assert first.state == PrinterState.PRINTING
    assert first.progress == 0.1
    assert second.state == PrinterState.PAUSED
    assert second.progress == 0.2

    adapter.disconnect()
    assert client.disconnect_count == 1


def test_retained_connection_accumulates_sparse_reports() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload(
            {"gcode_state": "RUNNING", "bed_temper": 65, "nozzle_temper": 220}
        ),
        _payload({"mc_percent": 25}),
    ]
    adapter = _make_adapter(factory=factory)

    adapter.connect()
    first = adapter.get_status()
    second = adapter.get_status()

    assert len(factory.clients) == 1
    assert first.state == PrinterState.PRINTING
    assert first.progress is None
    assert second.state == PrinterState.PRINTING
    assert second.bed_temp == 65.0
    assert second.nozzle_temp == 220.0
    assert second.progress == 0.25
    assert factory.clients[0].disconnect_count == 0
    assert factory.clients[0].refresh_count == 0

    adapter.disconnect()
    assert factory.clients[0].disconnect_count == 1


def test_warm_sparse_refresh_fetches_exactly_one_report() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload(
            {
                "gcode_state": "RUNNING",
                "mc_percent": 51,
                "bed_temper": 65,
                "nozzle_temper": 220,
            }
        )
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)

    factory.payloads = [_payload({"nozzle_temper": 219.8})]
    status = adapter.get_status()

    assert len(client.fetch_timeouts) - fetches_before == 1
    assert client.fetch_timeouts[-1] == 10.0
    assert status.state == PrinterState.PRINTING
    assert status.progress == 0.51
    assert status.bed_temp == 65.0
    assert status.nozzle_temp == 219.8


def test_valid_partial_report_makes_retained_session_warm() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"mc_percent": 51, "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    partial = adapter.get_status()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)

    factory.payloads = [_payload({"nozzle_temper": 220})]
    status = adapter.get_status()

    assert partial.state == PrinterState.UNKNOWN
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert status.state == PrinterState.UNKNOWN
    assert status.progress == 0.51
    assert status.bed_temp == 65.0
    assert status.nozzle_temp == 220.0


def test_warm_unmodeled_delta_returns_without_another_fetch() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    before = adapter.get_status()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)

    factory.payloads = [_payload({"wifi_signal": "-48dBm"})]
    after = adapter.get_status()

    assert len(client.fetch_timeouts) - fetches_before == 1
    assert after == before


def test_warm_malformed_modeled_delta_returns_last_known_good() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"gcode_state": "RUNNING", "nozzle_temper": 220})
    ]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)

    factory.payloads = [_payload({"nozzle_temper": "invalid"})]
    status = adapter.get_status()

    assert len(client.fetch_timeouts) - fetches_before == 1
    assert status.nozzle_temp == 220.0


def test_retained_timeout_preserves_accumulator_for_later_report() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    first = adapter.get_status()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)

    with pytest.raises(PrinterTimeout):
        adapter.get_status()
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert client.disconnect_count == 0

    factory.payloads = [_payload({"mc_percent": 50})]
    fetches_before = len(client.fetch_timeouts)
    later = adapter.get_status()
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert first.bed_temp == later.bed_temp == 65.0
    assert later.state == PrinterState.PRINTING
    assert later.progress == 0.5


def test_disconnect_reconnect_resets_accumulator() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    assert adapter.get_status().bed_temp == 65.0
    adapter.disconnect()

    factory.payloads = [_payload({"gcode_state": "IDLE"})]
    adapter.connect()
    status = adapter.get_status()
    assert status.state == PrinterState.IDLE
    assert status.bed_temp is None
    assert len(factory.clients) == 2


def test_repeated_connect_preserves_warm_session() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()
    adapter.connect()
    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)
    factory.payloads = [_payload({"mc_percent": 25})]
    status = adapter.get_status()

    assert len(factory.clients) == 1
    assert client.connect_count == 1
    assert client.refresh_count == 0
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert status.state == PrinterState.PRINTING
    assert status.bed_temp == 65.0
    assert status.progress == 0.25


@pytest.mark.parametrize(
    "invalid_payload",
    [
        b"\xff\xfe\x00",
        b"{not json",
        b"[]",
        b"{}",
        b'{"print": "invalid"}',
    ],
)
def test_invalid_report_does_not_corrupt_retained_accumulator(
    invalid_payload: bytes,
) -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()

    client = factory.clients[0]
    fetches_before = len(client.fetch_timeouts)
    factory.payloads = [invalid_payload]
    with pytest.raises(PrinterInvalidReport):
        adapter.get_status()
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert client.disconnect_count == 0

    factory.payloads = [_payload({"mc_percent": 10})]
    fetches_before = len(client.fetch_timeouts)
    status = adapter.get_status()
    assert len(client.fetch_timeouts) - fetches_before == 1
    assert status.state == PrinterState.PRINTING
    assert status.bed_temp == 65.0
    assert status.progress == 0.1


def test_standalone_combines_sparse_reports_until_state_ready() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"bed_temper": 65}),
        _payload({"nozzle_temper": 220}),
        _payload({"gcode_state": "RUNNING"}),
    ]
    status, _, _ = _status(factory=factory)
    assert status.state == PrinterState.PRINTING
    assert status.bed_temp == 65.0
    assert status.nozzle_temp == 220.0


def test_standalone_full_snapshot_marker_uses_existing_accumulator() -> None:
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"bed_temper": 65}),
        _payload(
            {
                "command": "push_status",
                "msg": 0,
                "nozzle_temper": 220,
                "mc_percent": 25,
            }
        ),
        _payload({"bed_temper": 99}),
    ]

    status, _, _ = _status(factory=factory)

    assert status.state == PrinterState.UNKNOWN
    assert status.bed_temp == 65.0
    assert status.nozzle_temp == 220.0
    assert status.progress == 0.25
    assert len(factory.clients[0].fetch_timeouts) == 2


@pytest.mark.parametrize(
    "marker",
    [
        {"command": "other", "msg": 0},
        {"command": "push_status", "msg": 1},
        {"command": "push_status", "msg": False},
    ],
)
def test_standalone_rejects_non_exact_full_snapshot_marker(
    marker: dict[str, Any],
) -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({**marker, "bed_temper": 65})]

    status, _, _ = _status(factory=factory)

    assert status.bed_temp == 65.0
    assert len(factory.clients[0].fetch_timeouts) > 1


def test_standalone_uses_one_total_monotonic_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([100.0, 100.0, 101.0])
    monkeypatch.setattr(bambu_module, "monotonic", lambda: next(times))
    factory = _FakeFactory()
    factory.payloads = [
        _payload({"bed_temper": 65}),
        _payload({"gcode_state": "RUNNING"}),
    ]

    _status(factory=factory)

    assert factory.clients[0].fetch_timeouts == [10.0, 9.0]


def test_standalone_returns_partial_after_telemetry_without_state() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"bed_temper": 65})]
    status, _, _ = _status(factory=factory)
    assert status.is_connected is True
    assert status.state == PrinterState.UNKNOWN
    assert status.bed_temp == 65.0


def test_failed_connect_does_not_leak_previous_accumulator() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()
    adapter.disconnect()

    factory.connect_error = MqttConnectionError("unreachable")
    with pytest.raises(PrinterUnreachable):
        adapter.connect()
    factory.connect_error = None
    factory.payloads = [_payload({"gcode_state": "IDLE"})]
    adapter.connect()
    assert adapter.get_status().bed_temp is None


def test_disconnect_exception_still_resets_session() -> None:
    factory = _FakeFactory()
    factory.payloads = [_payload({"gcode_state": "RUNNING", "bed_temper": 65})]
    adapter = _make_adapter(factory=factory)
    adapter.connect()
    adapter.get_status()

    factory.disconnect_error = RuntimeError("disconnect failed")
    with pytest.raises(RuntimeError, match="disconnect failed"):
        adapter.disconnect()

    factory.disconnect_error = None
    factory.payloads = [_payload({"gcode_state": "IDLE"})]
    adapter.connect()
    status = adapter.get_status()
    assert status.state == PrinterState.IDLE
    assert status.bed_temp is None


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
