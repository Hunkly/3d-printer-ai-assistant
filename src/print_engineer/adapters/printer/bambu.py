"""Bambu Lab A1 LAN MQTT printer adapter (Phase 2+, read-only increment).

Strictly read-only: connects to the printer over LAN MQTT (TLS), reads the
``device/{serial}/report`` status topic, and normalizes it into
:class:`PrinterStatus`. Never publishes, never sends commands, never changes
printer state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

from print_engineer.adapters.printer.transport import (
    MqttClient,
    MqttClientFactory,
    MqttConnectionError,
    PahoMqttClientFactory,
)
from print_engineer.core.interfaces.printer import Printer
from print_engineer.core.policy import PolicyDecision
from print_engineer.core.types import (
    AMSInfo,
    PrinterState,
    PrinterStatus,
    Snapshot,
    TemperatureSetpoint,
)
from print_engineer.errors import (
    PrinterAuthFailed,
    PrinterInvalidReport,
    PrinterNotConfigured,
    PrinterOperationUnsupported,
    PrinterTimeout,
    PrinterUnreachable,
)

_BAMBU_MQTT_PORT = 8883
_BAMBU_MQTT_USERNAME = "bblp"

_GCODE_STATE_MAP: dict[str, PrinterState] = {
    "IDLE": PrinterState.IDLE,
    "RUNNING": PrinterState.PRINTING,
    "PREPARE": PrinterState.PRINTING,
    "PAUSE": PrinterState.PAUSED,
    "FINISH": PrinterState.IDLE,
    "FAILED": PrinterState.ERROR,
    "UNKNOWN": PrinterState.UNKNOWN,
}


def _float_or_none(value: Any) -> float | None:
    """Parse a numeric report field, returning ``None`` when missing/unparseable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_ams(ams: Any) -> AMSInfo | None:
    """Normalize the ``print.ams`` section into :class:`AMSInfo`.

    Tray slots use ``chr(65 + unit_index) + str(tray_index + 1)`` for each
    loaded tray (a tray dict with keys beyond ``id`` and tray ``id`` !=
    ``"254"`` external-spool sentinel). Returns ``None`` when there is no
    AMS data.
    """
    if not isinstance(ams, dict):
        return None
    units = ams.get("ams")
    if not isinstance(units, list):
        return None
    slots: list[str] = []
    for unit_index, unit in enumerate(units):
        if not isinstance(unit, dict):
            continue
        trays = unit.get("tray")
        if not isinstance(trays, list):
            continue
        for tray_index, tray in enumerate(trays):
            if not isinstance(tray, dict):
                continue
            if set(tray.keys()) <= {"id"}:
                continue  # empty tray: only an id key
            if str(tray.get("id")) == "254":
                continue  # external-spool sentinel
            slots.append(chr(65 + unit_index) + str(tray_index + 1))
    return AMSInfo(is_connected=bool(ams), slots=slots)


def _normalize_status(payload: dict[str, Any]) -> PrinterStatus:
    """Normalize a Bambu report into :class:`PrinterStatus`.

    Pure and defensive: missing fields default to ``UNKNOWN``/``None``.
    Raises :class:`PrinterInvalidReport` only when the payload is not a
    mapping or ``print`` is not a mapping.
    """
    if not isinstance(payload, dict):
        raise PrinterInvalidReport(
            "Printer payload is not a JSON object",
            details={"payload": str(payload)[:200]},
        )
    print_obj = payload.get("print")
    if not isinstance(print_obj, dict):
        raise PrinterInvalidReport(
            "Printer payload has no valid 'print' object",
            details={"payload": str(payload)[:200]},
        )
    gcode_state = print_obj.get("gcode_state")
    if isinstance(gcode_state, str):
        state = _GCODE_STATE_MAP.get(gcode_state, PrinterState.UNKNOWN)
    else:
        state = PrinterState.UNKNOWN
    mc_percent = print_obj.get("mc_percent")
    progress: float | None = None
    if mc_percent is not None:
        try:
            progress = round(float(mc_percent) / 100.0, 4)
        except (TypeError, ValueError):
            progress = None
    return PrinterStatus(
        state=state,
        is_connected=True,
        bed_temp=_float_or_none(print_obj.get("bed_temper")),
        nozzle_temp=_float_or_none(print_obj.get("nozzle_temper")),
        target_bed_temp=_float_or_none(print_obj.get("bed_target_temper")),
        target_nozzle_temp=_float_or_none(print_obj.get("nozzle_target_temper")),
        progress=progress,
        ams=_normalize_ams(print_obj.get("ams")),
    )


class BambuPrinterAdapter(Printer):
    """Read-only Bambu Lab A1 LAN MQTT adapter (Phase 2+)."""

    def __init__(
        self,
        *,
        host: str,
        serial: str,
        access_code: str,
        timeout_seconds: float = 10.0,
        client_factory: MqttClientFactory | None = None,
    ) -> None:
        missing = [
            key
            for key, value in (
                ("host", host),
                ("serial", serial),
                ("access_code", access_code),
            )
            if not value
        ]
        if missing:
            raise PrinterNotConfigured(
                "Printer connection parameters are missing",
                details={"missing": missing},
            )
        self._host = host
        self._serial = serial
        self._access_code = access_code
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or PahoMqttClientFactory()
        self._client: MqttClient | None = None

    def _build_client(self) -> MqttClient:
        return self._client_factory(
            host=self._host,
            port=_BAMBU_MQTT_PORT,
            username=_BAMBU_MQTT_USERNAME,
            password=self._access_code,
            client_id=f"print-engineer-{self._serial}",
        )

    def _raise_connection_error(self, exc: MqttConnectionError) -> NoReturn:
        if exc.reason == "auth":
            raise PrinterAuthFailed(
                "Printer rejected the access code (MQTT CONNACK rc 4/5)",
                details={
                    "host": self._host,
                    "port": _BAMBU_MQTT_PORT,
                    "hint": "Check BAMBU_ACCESS_CODE and that LAN (LAN Only) mode is enabled.",
                },
            ) from exc
        raise PrinterUnreachable(
            "Printer could not be reached over LAN MQTT",
            details={
                "host": self._host,
                "port": _BAMBU_MQTT_PORT,
                "reason": exc.reason,
            },
        ) from exc

    def _fetch_report(self, client: MqttClient) -> PrinterStatus:
        topic = f"device/{self._serial}/report"
        payload = client.fetch_report(topic, self._timeout_seconds)
        if payload is None:
            raise PrinterTimeout(
                f"No status report received within {self._timeout_seconds:.1f}s",
                details={"topic": topic, "timeout_seconds": self._timeout_seconds},
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise PrinterInvalidReport(
                "Printer payload is not valid UTF-8 JSON",
                details={"payload": repr(payload)[:200]},
            ) from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise PrinterInvalidReport(
                "Printer payload is not valid JSON",
                details={"payload": text[:200]},
            ) from None
        if not isinstance(data, dict):
            raise PrinterInvalidReport(
                "Printer payload is not a JSON object",
                details={"payload": text[:200]},
            )
        return _normalize_status(data)

    def connect(self) -> None:
        client = self._build_client()
        try:
            client.connect()
        except MqttConnectionError as exc:
            self._raise_connection_error(exc)
        self._client = client

    def get_status(self) -> PrinterStatus:
        client = self._client
        if client is None:
            client = self._build_client()
            try:
                client.connect()
                return self._fetch_report(client)
            except MqttConnectionError as exc:
                self._raise_connection_error(exc)
            finally:
                client.disconnect()
        return self._fetch_report(client)

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            finally:
                self._client = None

    def _unsupported(self, operation: str) -> NoReturn:
        raise PrinterOperationUnsupported(
            f"{operation} is not supported in the read-only printer increment",
            details={"operation": operation},
        )

    def start_print(self, project: Path, *, confirm: bool = False) -> PolicyDecision:
        self._unsupported("start_print")

    def stop_print(self, *, confirm: bool = False) -> PolicyDecision:
        self._unsupported("stop_print")

    def pause_print(self, *, confirm: bool = False) -> PolicyDecision:
        self._unsupported("pause_print")

    def resume_print(self, *, confirm: bool = False) -> PolicyDecision:
        self._unsupported("resume_print")

    def set_temperature(
        self, setpoint: TemperatureSetpoint, *, confirm: bool = False
    ) -> PolicyDecision:
        self._unsupported("set_temperature")

    def take_snapshot(self) -> Snapshot:
        self._unsupported("take_snapshot")