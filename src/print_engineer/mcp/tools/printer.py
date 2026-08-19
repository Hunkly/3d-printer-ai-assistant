"""``printer.status`` MCP tool: read-only printer status (Phase 2+).

Strictly read-only: resolves the configured printer, calls
``BambuPrinterAdapter.get_status()``, and returns the normalized status.
Never starts/stops/pauses printing, never changes temperature, never
publishes MQTT messages, never accesses the camera, never slices.

Returns ``{"ok": true, "status": {...}, "summary": "...", "assessment":
{...}}`` on success and
``{"ok": false, "error": {code, message, details}}`` on structured failure.
"""

from __future__ import annotations

from collections.abc import Callable
from math import floor, isfinite
from typing import Any, cast

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.config import Settings
from print_engineer.core.types import PrinterState, PrinterStatus
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
        "issues": [
            {"source": issue.source.value, "code": issue.code}
            for issue in status.issues
        ],
    }


_STATE_SUMMARIES = {
    PrinterState.OFFLINE: "Offline",
    PrinterState.IDLE: "Idle",
    PrinterState.PRINTING: "Printing",
    PrinterState.PAUSED: "Paused",
    PrinterState.ERROR: "Printer error",
    PrinterState.UNKNOWN: "Status unknown",
}


def _assess_status(status: PrinterStatus) -> dict[str, str]:
    """Classify only the normalized connection flag and printer state."""
    if not status.is_connected:
        return {
            "level": "error",
            "code": "printer_disconnected",
            "message": "Printer is disconnected.",
        }
    if status.state is PrinterState.OFFLINE:
        return {
            "level": "attention",
            "code": "printer_offline",
            "message": "Printer reports an offline state.",
        }
    if status.state is PrinterState.IDLE:
        return {
            "level": "info",
            "code": "printer_idle",
            "message": "Printer is idle.",
        }
    if status.state is PrinterState.PRINTING:
        return {
            "level": "info",
            "code": "printer_printing",
            "message": "Printer is printing.",
        }
    if status.state is PrinterState.PAUSED:
        return {
            "level": "attention",
            "code": "printer_paused",
            "message": "Printer is paused.",
        }
    if status.state is PrinterState.ERROR:
        return {
            "level": "error",
            "code": "printer_error",
            "message": "Printer reports an error state.",
        }
    return {
        "level": "unknown",
        "code": "printer_state_unknown",
        "message": "Printer state is unknown.",
    }


def _format_temperature(value: float | None) -> str | None:
    """Format one finite temperature for compact status presentation."""
    if value is None or not isfinite(value):
        return None
    formatted = format(value, ".1f")
    if formatted.endswith(".0"):
        return formatted[:-2]
    return formatted


def _temperature_fragment(
    name: str, current: float | None, target: float | None
) -> str | None:
    current_text = _format_temperature(current)
    target_text = _format_temperature(target)
    if current_text is not None and target_text is not None:
        return f"{name} {current_text} / {target_text} °C"
    if current_text is not None:
        return f"{name} {current_text} °C"
    if target_text is not None:
        return f"{name} target {target_text} °C"
    return None


def _format_status_summary(status: PrinterStatus) -> str:
    """Return a deterministic human-readable view of normalized status."""
    if not status.is_connected:
        return "Printer disconnected"

    fragments = [_STATE_SUMMARIES[status.state]]
    active_job_state = status.state in {
        PrinterState.PRINTING,
        PrinterState.PAUSED,
    }

    if (
        active_job_state
        and status.progress is not None
        and isfinite(status.progress)
    ):
        display_progress = min(max(status.progress, 0.0), 1.0)
        percent = floor(display_progress * 100.0 + 0.5)
        fragments.append(f"{percent}% complete")

    if active_job_state:
        if status.current_layer is not None and status.total_layers is not None:
            fragments.append(
                f"Layer {status.current_layer} / {status.total_layers}"
            )
        elif status.current_layer is not None:
            fragments.append(f"Layer {status.current_layer}")
        elif status.total_layers is not None:
            fragments.append(f"Total layers {status.total_layers}")

    if active_job_state:
        if status.remaining_time_minutes is not None:
            fragments.append(
                f"About {status.remaining_time_minutes} min remaining"
            )

    nozzle = _temperature_fragment(
        "Nozzle", status.nozzle_temp, status.target_nozzle_temp
    )
    if nozzle is not None:
        fragments.append(nozzle)

    bed = _temperature_fragment("Bed", status.bed_temp, status.target_bed_temp)
    if bed is not None:
        fragments.append(bed)

    if status.ams is not None:
        fragments.append(
            "AMS connected" if status.ams.is_connected else "AMS not connected"
        )

    return " · ".join(fragments)


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
        return {
            "ok": True,
            "status": _serialize_status(status),
            "summary": _format_status_summary(status),
            "assessment": _assess_status(status),
        }


def build_tools(settings: Settings) -> dict[str, Callable[..., dict[str, Any]]]:
    """Return the ``printer.*`` tool callables bound to *settings*."""
    tools = PrinterTools(settings)
    return {
        "printer.status": tools.status,
    }
