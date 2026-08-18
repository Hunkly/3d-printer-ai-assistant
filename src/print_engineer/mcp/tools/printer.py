"""``printer.status`` MCP tool: read-only printer status (Phase 2+).

Strictly read-only: resolves the configured printer, calls
``BambuPrinterAdapter.get_status()``, and returns the normalized status.
Never starts/stops/pauses printing, never changes temperature, never
publishes MQTT messages, never accesses the camera, never slices.

Returns ``{"ok": true, "status": {...}}`` on success and
``{"ok": false, "error": {code, message, details}}`` on structured failure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.config import Settings
from print_engineer.core.types import PrinterStatus
from print_engineer.errors import PrinterError, PrinterNotConfigured


def _connection_params(settings: Settings) -> tuple[str, str, str]:
    """Resolve printer connection parameters (secrets first, config fallback).

    Returns ``(host, serial, access_code)``. Raises
    :class:`PrinterNotConfigured` with ``details={"missing": [...]}`` when any
    parameter is missing.
    """
    host = settings.secrets.ip or settings.printer.host
    serial = settings.secrets.serial or settings.printer.serial
    access_code = settings.secrets.access_code
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
    return cast(str, host), cast(str, serial), cast(str, access_code)


def _serialize_status(status: PrinterStatus) -> dict[str, Any]:
    """Serialize :class:`PrinterStatus` into JSON-compatible data."""
    ams = None
    if status.ams is not None:
        ams = {
            "is_connected": status.ams.is_connected,
            "slots": list(status.ams.slots),
        }
    return {
        "state": status.state.value,
        "is_connected": status.is_connected,
        "bed_temp": status.bed_temp,
        "nozzle_temp": status.nozzle_temp,
        "target_bed_temp": status.target_bed_temp,
        "target_nozzle_temp": status.target_nozzle_temp,
        "progress": status.progress,
        "ams": ams,
        "current_layer": status.current_layer,
        "total_layers": status.total_layers,
        "remaining_time_minutes": status.remaining_time_minutes,
    }


class PrinterTools:
    """Bound MCP tool implementations for one settings object."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def status(self) -> dict[str, Any]:
        """Read-only printer status (never changes printer state)."""
        try:
            host, serial, access_code = _connection_params(self._settings)
            adapter = BambuPrinterAdapter(
                host=host,
                serial=serial,
                access_code=access_code,
            )
            status = adapter.get_status()
        except PrinterError as exc:
            return {"ok": False, "error": exc.to_dict()}
        return {"ok": True, "status": _serialize_status(status)}


def build_tools(settings: Settings) -> dict[str, Callable[..., dict[str, Any]]]:
    """Return the ``printer.*`` tool callables bound to *settings*."""
    tools = PrinterTools(settings)
    return {
        "printer.status": tools.status,
    }
