"""Tests for the structured slicer error hierarchy."""

from __future__ import annotations

import pytest

from print_engineer.errors import (
    InvalidModel,
    InvalidProfile,
    PrintEngineerError,
    SliceFailed,
    SlicerError,
    SlicerNotInstalled,
    SlicerUnavailable,
    SliceTimeout,
    UnsupportedSlicerVersion,
    VersionMismatch,
)

SLICER_ERRORS = (
    SlicerNotInstalled,
    UnsupportedSlicerVersion,
    SlicerUnavailable,
    InvalidModel,
    InvalidProfile,
    SliceFailed,
    SliceTimeout,
    VersionMismatch,
)


def test_all_slicer_errors_subclass_slicer_error() -> None:
    for cls in SLICER_ERRORS:
        assert issubclass(cls, SlicerError)
        assert issubclass(cls, PrintEngineerError)


def test_errors_carry_stable_codes() -> None:
    expected = {
        SlicerNotInstalled: "slicer_not_installed",
        UnsupportedSlicerVersion: "unsupported_slicer_version",
        SlicerUnavailable: "slicer_unavailable",
        InvalidModel: "invalid_model",
        InvalidProfile: "invalid_profile",
        SliceFailed: "slice_failed",
        SliceTimeout: "slice_timeout",
        VersionMismatch: "version_mismatch",
    }
    for cls, code in expected.items():
        assert cls.code == code


def test_to_dict_includes_machine_readable_details() -> None:
    error = VersionMismatch(
        "project too new",
        details={"model_version": "2.7.1.62", "slicer_version": "2.3.2"},
    )
    payload = error.to_dict()
    assert payload["code"] == "version_mismatch"
    assert payload["message"] == "project too new"
    assert payload["details"]["model_version"] == "2.7.1.62"
    assert payload["details"]["slicer_version"] == "2.3.2"


def test_error_without_details_defaults_to_empty() -> None:
    error = InvalidModel("missing")
    assert error.to_dict()["details"] == {}


def test_exceptions_are_raiseable_without_arguments() -> None:
    for cls in SLICER_ERRORS:
        with pytest.raises(cls):
            raise cls()
