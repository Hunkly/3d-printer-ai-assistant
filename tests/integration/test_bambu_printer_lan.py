"""Explicitly gated read-only probe against a real Bambu Lab A1.

This test never publishes or sends printer commands. It is skipped unless the
operator deliberately enables the hardware gate and supplies all LAN secrets.
"""

from __future__ import annotations

import os

import pytest

from print_engineer.adapters.printer.bambu import BambuPrinterAdapter
from print_engineer.core.types import AMSInfo, PrinterState, PrinterStatus

_HARDWARE_OPT_IN = "RUN_BAMBU_LAN_HARDWARE_TEST"
_REQUIRED_CONFIG = ("BAMBU_IP", "BAMBU_SERIAL", "BAMBU_ACCESS_CODE")


def _hardware_config() -> tuple[str, str, str]:
    """Return LAN credentials only after explicit hardware opt-in."""
    if os.environ.get(_HARDWARE_OPT_IN) != "1":
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


def test_bambu_a1_read_only_status_over_lan() -> None:
    """Read and normalize one real status report without changing printer state."""
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
