"""Hermetic tests for Bambu sparse-report state accumulation."""

from __future__ import annotations

import pytest

from print_engineer.adapters.printer.bambu import _BambuStatusAccumulator
from print_engineer.core.types import PrinterState


def _loaded_ams() -> dict[str, object]:
    return {"ams": [{"tray": [{"id": "1", "type": "PLA"}]}]}


def test_empty_snapshot_is_unknown_and_disconnected() -> None:
    status = _BambuStatusAccumulator().snapshot()
    assert status.state == PrinterState.UNKNOWN
    assert status.is_connected is False
    assert status.bed_temp is None
    assert status.nozzle_temp is None
    assert status.target_bed_temp is None
    assert status.target_nozzle_temp is None
    assert status.progress is None
    assert status.ams is None


def test_sparse_deltas_merge_normalized_fields() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply(
        {"gcode_state": "RUNNING", "bed_temper": 65, "nozzle_temper": "220"}
    )
    accumulator.apply({"mc_percent": 73})

    status = accumulator.snapshot()
    assert status.state == PrinterState.PRINTING
    assert status.bed_temp == 65.0
    assert status.nozzle_temp == 220.0
    assert status.progress == 0.73
    assert status.is_connected is True


def test_missing_fields_retain_every_cached_value() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply(
        {
            "gcode_state": "PAUSE",
            "bed_temper": 60,
            "nozzle_temper": 215,
            "bed_target_temper": 65,
            "nozzle_target_temper": 220,
            "mc_percent": 50,
            "ams": _loaded_ams(),
        }
    )
    before = accumulator.snapshot()
    accumulator.apply({"wifi_signal": "-48dBm"})
    assert accumulator.snapshot() == before


@pytest.mark.parametrize(
    ("field", "attribute"),
    [
        ("bed_temper", "bed_temp"),
        ("nozzle_temper", "nozzle_temp"),
        ("bed_target_temper", "target_bed_temp"),
        ("nozzle_target_temper", "target_nozzle_temp"),
    ],
)
def test_malformed_temperature_retains_last_good(
    field: str, attribute: str
) -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({field: 42})
    accumulator.apply({field: "invalid"})
    assert getattr(accumulator.snapshot(), attribute) == 42.0


def test_malformed_fields_never_observed_remain_unknown() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply(
        {
            "gcode_state": 5,
            "mc_percent": "invalid",
            "bed_temper": "invalid",
            "nozzle_temper": object(),
            "bed_target_temper": None,
            "nozzle_target_temper": [],
            "ams": None,
        }
    )
    status = accumulator.snapshot()
    assert status.state == PrinterState.UNKNOWN
    assert accumulator.ready is False
    assert status.progress is None
    assert status.bed_temp is None
    assert status.nozzle_temp is None
    assert status.target_bed_temp is None
    assert status.target_nozzle_temp is None
    assert status.ams is None


def test_malformed_progress_and_ams_retain_last_good() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({"mc_percent": 40, "ams": _loaded_ams()})
    before = accumulator.snapshot()
    accumulator.apply({"mc_percent": "invalid", "ams": "invalid"})
    after = accumulator.snapshot()
    assert after.progress == before.progress == 0.4
    assert after.ams == before.ams


def test_literal_unknown_updates_state_but_invalid_states_do_not() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({"gcode_state": "RUNNING"})
    accumulator.apply({"gcode_state": "BOGUS"})
    accumulator.apply({"gcode_state": 7})
    assert accumulator.snapshot().state == PrinterState.PRINTING

    accumulator.apply({"gcode_state": "UNKNOWN"})
    assert accumulator.snapshot().state == PrinterState.UNKNOWN
    assert accumulator.ready is True


def test_pause_resume_and_sparse_state_retention() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({"gcode_state": "RUNNING", "bed_temper": 60})
    accumulator.apply({"gcode_state": "PAUSE"})
    assert accumulator.snapshot().state == PrinterState.PAUSED
    accumulator.apply({"mc_percent": 25})
    assert accumulator.snapshot().state == PrinterState.PAUSED
    accumulator.apply({"gcode_state": "PREPARE"})
    status = accumulator.snapshot()
    assert status.state == PrinterState.PRINTING
    assert status.bed_temp == 60.0
    assert status.progress == 0.25


@pytest.mark.parametrize(
    ("terminal", "expected_state"),
    [
        ("IDLE", PrinterState.IDLE),
        ("FINISH", PrinterState.IDLE),
        ("FAILED", PrinterState.ERROR),
    ],
)
def test_terminal_state_clears_only_progress(
    terminal: str, expected_state: PrinterState
) -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply(
        {
            "gcode_state": "RUNNING",
            "mc_percent": 73,
            "bed_temper": 64,
            "nozzle_temper": 220,
            "bed_target_temper": 65,
            "nozzle_target_temper": 220,
            "ams": _loaded_ams(),
        }
    )
    accumulator.apply({"gcode_state": terminal})
    status = accumulator.snapshot()
    assert status.state == expected_state
    assert status.progress is None
    assert status.bed_temp == 64.0
    assert status.nozzle_temp == 220.0
    assert status.target_bed_temp == 65.0
    assert status.target_nozzle_temp == 220.0
    assert status.ams is not None


def test_terminal_same_delta_progress_and_later_target_zero_are_valid() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply(
        {
            "gcode_state": "RUNNING",
            "mc_percent": 73,
            "bed_target_temper": 65,
            "nozzle_target_temper": 220,
        }
    )
    accumulator.apply({"gcode_state": "FINISH", "mc_percent": 100})
    assert accumulator.snapshot().progress == 1.0
    accumulator.apply({"bed_target_temper": 0, "nozzle_target_temper": "0"})
    status = accumulator.snapshot()
    assert status.target_bed_temp == 0.0
    assert status.target_nozzle_temp == 0.0


def test_malformed_targets_after_terminal_retain_last_good() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({"bed_target_temper": 65, "nozzle_target_temper": 220})
    accumulator.apply(
        {
            "gcode_state": "IDLE",
            "bed_target_temper": "invalid",
            "nozzle_target_temper": None,
        }
    )
    status = accumulator.snapshot()
    assert status.target_bed_temp == 65.0
    assert status.target_nozzle_temp == 220.0


def test_valid_ams_is_cached_across_missing_and_malformed_deltas() -> None:
    accumulator = _BambuStatusAccumulator()
    accumulator.apply({"ams": _loaded_ams()})
    expected = accumulator.snapshot().ams
    accumulator.apply({"bed_temper": 60})
    assert accumulator.snapshot().ams == expected
    accumulator.apply({"ams": None})
    assert accumulator.snapshot().ams == expected
