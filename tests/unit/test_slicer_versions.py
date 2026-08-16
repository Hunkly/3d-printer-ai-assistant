"""Tests for slicer version detection helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_engineer.adapters.slicer.version import (
    detect_orca_version,
    parse_bambu_banner,
    parse_version,
    registry_display_version,
    scan_binary_for_version,
    version_gte,
)
from print_engineer.errors import SlicerError


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.3.2", (2, 3, 2, 0)),
        ("02.07.01.62", (2, 7, 1, 62)),
        ("2.6.0.51", (2, 6, 0, 51)),
        ("1.0", (1, 0, 0, 0)),
    ],
)
def test_parse_version(version: str, expected: tuple[int, int, int, int]) -> None:
    assert parse_version(version) == expected


def test_parse_version_rejects_garbage() -> None:
    assert parse_version("not-a-version") is None


def test_version_gte() -> None:
    assert version_gte("2.7.1.62", "2.7.1.62")
    assert version_gte("2.7.1.62", "2.3.2")
    assert not version_gte("2.6.0.51", "2.7.1.62")


def test_version_gte_raises_on_garbage() -> None:
    with pytest.raises(SlicerError):
        version_gte("nonsense", "2.3.2")


def test_parse_bambu_banner() -> None:
    banner = "BambuStudio-02.06.00.51:\nUsage: bambu-studio [ OPTIONS ] [ file... ]"
    assert parse_bambu_banner(banner) == "02.06.00.51"


def test_parse_bambu_banner_missing() -> None:
    assert parse_bambu_banner("No version here") is None


def test_scan_binary_for_version(tmp_path: Path) -> None:
    binary = tmp_path / "OrcaSlicer.dll"
    payload = b"\x00" * 64 + b"OrcaSlicer 2.3.2\x00" + b"G-code Viewer 2.3.2"
    binary.write_bytes(payload)
    assert scan_binary_for_version(binary) == "2.3.2"


def test_scan_binary_for_version_slash_separator(tmp_path: Path) -> None:
    binary = tmp_path / "OrcaSlicer.dll"
    binary.write_bytes(b"\x00OrcaSlicer/2.3.2\x00")
    assert scan_binary_for_version(binary) == "2.3.2"


def test_scan_binary_missing_returns_none(tmp_path: Path) -> None:
    assert scan_binary_for_version(tmp_path / "nope.dll") is None


def test_detect_orca_version_prefers_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "OrcaSlicer.dll"
    binary.write_bytes(b"\x00OrcaSlicer 9.9.9\x00")

    monkeypatch.setattr(
        "print_engineer.adapters.slicer.version.registry_display_version",
        lambda name: "2.3.2",
    )
    version, source = detect_orca_version(tmp_path)
    assert (version, source) == ("2.3.2", "registry")


def test_detect_orca_version_falls_back_to_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "OrcaSlicer.dll"
    binary.write_bytes(b"\x00OrcaSlicer 2.3.2\x00")

    monkeypatch.setattr(
        "print_engineer.adapters.slicer.version.registry_display_version",
        lambda name: None,
    )
    version, source = detect_orca_version(tmp_path)
    assert (version, source) == ("2.3.2", "binary")


def test_registry_display_version_missing_entry() -> None:
    # The registry is enumerated; a non-existent installer name yields None
    # without raising (also exercises the OSError-tolerant code paths).
    assert registry_display_version("definitely-not-an-installer-xyz") is None
