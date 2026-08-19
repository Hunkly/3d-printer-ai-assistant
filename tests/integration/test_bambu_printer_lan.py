"""Explicitly gated status probes against a real Bambu Lab A1.

Passive tests never publish. The standalone refresh test is separately gated
because it may issue exactly one fixed informational pushall. No test sends a
printer-control command.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.adapters.printer.transport import PahoMqttClientFactory
from print_engineer.core.types import AMSInfo, PrinterState, PrinterStatus

_STATUS_REFRESH_OPT_IN = "RUN_BAMBU_LAN_STATUS_REFRESH_TEST"
_PASSIVE_RECEIVE_OPT_IN = "RUN_BAMBU_LAN_PASSIVE_RECEIVE_TEST"
_STATE_ACCUMULATOR_OPT_IN = "RUN_BAMBU_LAN_STATE_ACCUMULATOR_TEST"
_REQUIRED_CONFIG = ("BAMBU_IP", "BAMBU_SERIAL", "BAMBU_ACCESS_CODE")
_PASSIVE_WINDOW_SECONDS = 12.0
_PASSIVE_FETCH_SECONDS = 2.0


def _hardware_config(
    opt_in_name: str = _STATUS_REFRESH_OPT_IN,
) -> tuple[str, str, str]:
    """Return LAN credentials only after explicit hardware opt-in."""
    if os.environ.get(opt_in_name) != "1":
        pytest.skip("Bambu LAN hardware verification is not explicitly enabled")

    host = os.environ.get("BAMBU_IP")
    serial = os.environ.get("BAMBU_SERIAL")
    access_code = os.environ.get("BAMBU_ACCESS_CODE")
    missing = [
        name
        for name, value in zip(
            _REQUIRED_CONFIG, (host, serial, access_code), strict=True
        )
        if not value
    ]
    if missing:
        pytest.skip(f"Bambu LAN hardware configuration is missing: {', '.join(missing)}")

    assert host is not None
    assert serial is not None
    assert access_code is not None
    return host, serial, access_code


def _print_field_names(payload: bytes) -> list[str]:
    """Return only sanitized print-object field names from a report."""
    try:
        report: Any = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pytest.fail("Bambu LAN passive report was not valid UTF-8 JSON")
    if not isinstance(report, dict) or not isinstance(report.get("print"), dict):
        pytest.fail("Bambu LAN passive report had an invalid object structure")
    return sorted(str(name) for name in report["print"])


def test_bambu_a1_read_only_status_over_lan() -> None:
    """Request one informational status refresh and normalize its response."""
    host, serial, access_code = _hardware_config()
    adapter = BambuPrinterAdapter(
        host=host,
        serial=serial,
        access_code=access_code,
        timeout_seconds=10.0,
    )

    try:
        status = adapter.get_status()

        assert isinstance(status, PrinterStatus)
        assert status.is_connected is True
        assert isinstance(status.state, PrinterState)
        assert status.progress is None or 0.0 <= status.progress <= 1.0

        temperatures = (
            status.bed_temp,
            status.nozzle_temp,
            status.target_bed_temp,
            status.target_nozzle_temp,
        )
        assert all(value is None or isinstance(value, float) for value in temperatures)

        if status.ams is not None:
            assert isinstance(status.ams, AMSInfo)
            assert isinstance(status.ams.is_connected, bool)
            assert all(isinstance(slot, str) for slot in status.ams.slots)
    finally:
        adapter.disconnect()


def test_bambu_a1_passive_multi_report_receive_over_lan() -> None:
    """Observe sanitized passive report shapes over one real connection."""
    host, serial, access_code = _hardware_config(_PASSIVE_RECEIVE_OPT_IN)
    client = PahoMqttClientFactory()(
        host=host,
        port=8883,
        username="bblp",
        password=access_code,
        client_id=f"print-engineer-passive-{serial}",
        serial=serial,
    )
    topic = f"device/{serial}/report"
    report_count = 0
    timeout_count = 0
    observed_fields: set[str] = set()

    try:
        client.connect()
        started = time.monotonic()
        deadline = started + _PASSIVE_WINDOW_SECONDS
        while (remaining := deadline - time.monotonic()) > 0:
            payload = client.fetch_report(
                topic, min(_PASSIVE_FETCH_SECONDS, remaining)
            )
            if payload is None:
                timeout_count += 1
                continue
            report_count += 1
            field_names = _print_field_names(payload)
            observed_fields.update(field_names)
            elapsed = time.monotonic() - started
            print(f"report {report_count}: +{elapsed:.3f}s fields={field_names}")
    finally:
        client.disconnect()

    print(f"union fields={sorted(observed_fields)}")
    print(f"report count={report_count}; timeout count={timeout_count}")
    assert report_count > 0, "No passive Bambu LAN reports were received"


def test_bambu_a1_passive_state_accumulator_over_lan() -> None:
    """Observe three sanitized accumulated snapshots on one real connection."""
    host, serial, access_code = _hardware_config(_STATE_ACCUMULATOR_OPT_IN)
    adapter = BambuPrinterAdapter(
        host=host,
        serial=serial,
        access_code=access_code,
        timeout_seconds=10.0,
    )

    try:
        adapter.connect()
        started = time.monotonic()
        for ordinal in range(1, 4):
            status = adapter.get_status()
            elapsed = time.monotonic() - started
            print(
                f"snapshot {ordinal}: +{elapsed:.3f}s "
                f"state={status.state.value} progress={status.progress} "
                f"bed={status.bed_temp} nozzle={status.nozzle_temp} "
                f"target_bed={status.target_bed_temp} "
                f"target_nozzle={status.target_nozzle_temp} "
                f"ams_present={status.ams is not None}"
            )
            assert status.is_connected is True
    finally:
        adapter.disconnect()
