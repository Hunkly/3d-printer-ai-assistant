"""Explicitly opted-in hardware verification for Bambu A1 LAN status."""

from __future__ import annotations

import os

import pytest

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.core.types import AMSInfo, PrinterState, PrinterStatus


def test_bambu_printer_lan_status() -> None:
    """Fetch and normalize one subscription-only report from a real printer."""
    if os.environ.get("RUN_BAMBU_LAN_HARDWARE_TEST") != "1":
        pytest.skip("Bambu LAN hardware test requires explicit opt-in")

    required_names = ("BAMBU_IP", "BAMBU_SERIAL", "BAMBU_ACCESS_CODE")
    values = {name: os.environ.get(name) for name in required_names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")

    host = values["BAMBU_IP"]
    serial = values["BAMBU_SERIAL"]
    access_code = values["BAMBU_ACCESS_CODE"]
    assert host is not None
    assert serial is not None
    assert access_code is not None

    adapter = BambuPrinterAdapter(
        host=host,
        serial=serial,
        access_code=access_code,
        timeout_seconds=10.0,
    )
    try:
        status = adapter.get_status()
    finally:
        adapter.disconnect()

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
