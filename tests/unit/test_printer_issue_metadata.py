"""Hermetic tests for the local printer issue metadata core."""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.request
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import print_engineer.adapters.printer.issue_metadata as issue_metadata_module
from print_engineer.adapters.printer.issue_metadata import (
    MAX_JSON_DEPTH,
    MAX_RESOURCE_BYTES,
    IssueMetadataError,
    IssueMetadataLoadFailure,
    IssueMetadataSet,
    derive_device_family,
    derive_hms_lookup_key,
    load_issue_metadata,
    resolve_issue_metadata,
)
from print_engineer.config import Settings
from print_engineer.core.types import PrinterIssue, PrinterIssueSource


def document(
    *, family: str = "030", locale: str = "en", entries: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "vendor": "bambu_lab",
        "vendor_dataset_version": "v1",
        "device_family": family,
        "locale": locale,
        "entries": entries
        if entries is not None
        else [{"source": "hms", "lookup_key": "0300123400020056", "message": "A message"}],
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_empty_set_and_known_unknown_resolution(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("printer: {}\n", encoding="utf-8")
    settings = Settings.load(config_path=config, root=tmp_path / "project-root")
    empty = load_issue_metadata(settings.printer.issue_metadata_paths)
    assert empty.ok and empty.accepted == IssueMetadataSet()
    path = tmp_path / "metadata.json"
    write_json(path, document())
    loaded = load_issue_metadata((path,))
    assert loaded.ok and loaded.accepted is not None
    issue = PrinterIssue(PrinterIssueSource.HMS, "0300123400020056")
    result = resolve_issue_metadata(issue, "030SERIAL", "en", False, loaded.accepted)
    assert result.resolved and result.issue == issue and result.metadata is not None
    assert result.metadata.message == "A message"
    assert (
        resolve_issue_metadata(
            PrinterIssue(PrinterIssueSource.HMS, "0102030405060708"),
            "030SERIAL",
            "en",
            False,
            loaded.accepted,
        ).metadata
        is None
    )


def test_hms_keys_and_family_are_exact() -> None:
    assert derive_device_family("20P123") == "20P"
    assert derive_device_family("20p123") == "20p"
    assert derive_hms_lookup_key("0300123400020056") == "0300123400020056"
    assert derive_hms_lookup_key("0300123400050056") == "0300123400000056"


@pytest.mark.parametrize(
    "bad",
    [
        b"{",
        b"\xef\xbb\xbf{}",
        b"\xff",
    ],
)
def test_invalid_documents_rejected(tmp_path: Path, bad: bytes) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(bad)
    assert not load_issue_metadata((path,)).ok


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("locale"),
        lambda d: d.update({"schema_version": "1"}),
        lambda d: d.update({"vendor": 1}),
        lambda d: d.update({"device_family": 30}),
        lambda d: d.update({"entries": {}}),
        lambda d: d.update({"extra": 1}),
        lambda d: d.update({"entries": []}),
        lambda d: d.update(
            {
                "entries": [
                    {"source": "hms", "lookup_key": "0300123400020056", "message": "x", "extra": 1}
                ]
            }
        ),
    ],
)
def test_schema_rejections(tmp_path: Path, mutate: object) -> None:
    value = document()
    mutate(value)  # type: ignore[operator]
    path = tmp_path / "bad.json"
    write_json(path, value)
    assert not load_issue_metadata((path,)).ok


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("vendor", True),
        ("vendor_dataset_version", True),
        ("device_family", True),
        ("locale", True),
        ("entries", True),
    ],
)
def test_boolean_values_are_not_accepted_for_schema_types(
    tmp_path: Path, field: str, value: object
) -> None:
    value_to_write = document()
    value_to_write[field] = value
    path = tmp_path / f"bool-{field}.json"
    write_json(path, value_to_write)
    assert not load_issue_metadata((path,)).ok


@pytest.mark.parametrize(
    "entry",
    [
        {"source": True, "lookup_key": "0300123400020056", "message": "x"},
        {"source": "hms", "lookup_key": True, "message": "x"},
        {"source": "hms", "lookup_key": "0300123400020056", "message": True},
        # A valid key for one source cannot be paired with the other source.
        {"source": "print_error", "lookup_key": "0300123400020056", "message": "x"},
        {"source": "hms", "lookup_key": "0012ABCD", "message": "x"},
    ],
)
def test_entry_field_types_and_source_key_combinations_are_strict(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    path = tmp_path / "bad-entry.json"
    write_json(path, document(entries=[entry]))
    assert not load_issue_metadata((path,)).ok


@pytest.mark.parametrize(
    "entry",
    [
        {"source": "hms", "lookup_key": "0300123400020056", "message": "x", "extra": 1},
        {"source": "hms", "lookup_key": "0300123400020056"},
        {"source": "hms", "lookup_key": "0300123400020056", "message": "x", "source2": "hms"},
    ],
)
def test_invalid_entry_field_combinations_reject_entire_resource(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    path = tmp_path / "invalid-combination.json"
    write_json(path, document(entries=[entry]))
    result = load_issue_metadata((path,))
    assert result.accepted is None
    assert isinstance(result.failure, IssueMetadataLoadFailure)
    assert result.failure.code == IssueMetadataError.__name__


def test_duplicate_entries_and_resources_are_atomic(tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_json(
        first,
        document(
            entries=[
                {"source": "hms", "lookup_key": "0300123400020056", "message": "a"},
                {"source": "hms", "lookup_key": "0300123400020056", "message": "b"},
            ]
        ),
    )
    assert not load_issue_metadata((first,)).ok
    write_json(first, document())
    write_json(second, document())
    assert not load_issue_metadata((first, second)).ok


def test_missing_nonregular_and_size_limits(tmp_path: Path) -> None:
    missing = load_issue_metadata((tmp_path / "missing.json",))
    assert missing.accepted is None
    assert isinstance(missing.failure, IssueMetadataLoadFailure)
    assert missing.failure.path == (tmp_path / "missing.json").resolve(strict=False)
    assert missing.failure.code == "FileNotFoundError"
    assert "missing.json" in missing.failure.message

    nonregular = load_issue_metadata((tmp_path,))
    assert nonregular.accepted is None
    assert isinstance(nonregular.failure, IssueMetadataLoadFailure)
    assert nonregular.failure.code == IssueMetadataError.__name__
    assert nonregular.failure.message == "configured path is not a regular file"
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_RESOURCE_BYTES + 1))
    assert not load_issue_metadata((path,)).ok


def test_oversized_resource_is_rejected_before_body_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (MAX_RESOURCE_BYTES + 1))

    def fail_if_read(self: Path) -> bytes:
        raise AssertionError("oversized resource body was read")

    monkeypatch.setattr(Path, "read_bytes", fail_if_read)
    result = load_issue_metadata((path,))
    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "IssueMetadataError"


def test_size_limit_boundary_is_accepted_when_content_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "boundary.json"
    raw = json.dumps(document(), separators=(",", ":")).encode()
    padded = raw + (b" " * (MAX_RESOURCE_BYTES - len(raw)))
    path.write_bytes(padded)
    result = load_issue_metadata((path,))
    assert result.ok
    assert result.accepted is not None
    assert result.accepted.resources[0].content_sha256 == hashlib.sha256(padded).hexdigest()


def test_json_parser_recursion_error_is_structured_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "deep.json"
    write_json(path, document())

    def recurse(*args: object, **kwargs: object) -> object:
        raise RecursionError("parser depth")

    monkeypatch.setattr("print_engineer.adapters.printer.issue_metadata.json.loads", recurse)
    result = load_issue_metadata((path,))
    assert not result.ok
    assert result.failure is not None
    assert result.failure.code == "IssueMetadataError"
    assert "parser depth" in result.failure.message


def test_application_json_depth_limit_is_enforced(tmp_path: Path) -> None:
    value: object = document()
    for _ in range(MAX_JSON_DEPTH):
        value = [value]
    path = tmp_path / "too-deep.json"
    write_json(path, value)
    assert not load_issue_metadata((path,)).ok


def test_duplicate_json_members_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-members.json"
    path.write_text(
        '{"schema_version":1,"vendor":"bambu_lab","vendor":"bambu_lab",'
        '"vendor_dataset_version":"v1","device_family":"030",'
        '"locale":"en","entries":[{"source":"hms",'
        '"lookup_key":"0300123400020056","message":"x"}]}',
        encoding="utf-8",
    )
    assert not load_issue_metadata((path,)).ok


def test_invalid_top_level_shape_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    write_json(path, [])
    assert not load_issue_metadata((path,)).ok


def test_locale_fallback_and_immutable_result(tmp_path: Path) -> None:
    en, de = tmp_path / "en.json", tmp_path / "de.json"
    write_json(en, document(locale="en"))
    write_json(
        de,
        document(
            locale="de",
            entries=[{"source": "print_error", "lookup_key": "0012ABCD", "message": "Fehler"}],
        ),
    )
    loaded = load_issue_metadata((de, en))
    assert loaded.accepted is not None
    issue = PrinterIssue(PrinterIssueSource.HMS, "0300123400020056")
    assert (
        resolve_issue_metadata(issue, "030SERIAL", "fr", True, loaded.accepted).metadata is not None
    )
    with pytest.raises(TypeError):
        loaded.accepted.resources[0].entries[("hms", "0300123400020056")] = "changed"  # type: ignore[index]


def test_unrelated_locale_does_not_fallback(tmp_path: Path) -> None:
    path = tmp_path / "de.json"
    write_json(path, document(locale="de"))
    loaded = load_issue_metadata((path,))
    assert loaded.accepted is not None
    result = resolve_issue_metadata(
        PrinterIssue(PrinterIssueSource.HMS, "0300123400020056"),
        "030SERIAL",
        "fr",
        True,
        loaded.accepted,
    )
    assert not result.resolved
    assert result.metadata is None


def test_resolved_and_unresolved_results_preserve_source_qualified_issue(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metadata.json"
    write_json(
        path,
        document(
            entries=[
                {"source": "hms", "lookup_key": "0300123400020056", "message": "hms"},
                {"source": "print_error", "lookup_key": "0012ABCD", "message": "error"},
            ]
        ),
    )
    loaded = load_issue_metadata((path,))
    assert loaded.accepted is not None

    known = PrinterIssue(PrinterIssueSource.HMS, "0300123400020056")
    resolved = resolve_issue_metadata(known, "030SERIAL", "en", False, loaded.accepted)
    assert resolved.resolved and resolved.issue is known

    unknown = PrinterIssue(PrinterIssueSource.HMS, "0102030405060708")
    unresolved = resolve_issue_metadata(unknown, "030SERIAL", "en", False, loaded.accepted)
    assert not unresolved.resolved and unresolved.issue is unknown

    print_error = PrinterIssue(PrinterIssueSource.PRINT_ERROR, "0012ABCD")
    error_result = resolve_issue_metadata(print_error, "030SERIAL", "en", False, loaded.accepted)
    assert error_result.resolved and error_result.issue is print_error
    assert resolve_issue_metadata(
        PrinterIssue(PrinterIssueSource.HMS, "0012ABCD"),
        "030SERIAL",
        "en",
        False,
        loaded.accepted,
    ).metadata is None


def test_resolution_result_and_nested_metadata_are_runtime_immutable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metadata.json"
    write_json(path, document())
    loaded = load_issue_metadata((path,))
    assert loaded.accepted is not None
    issue = PrinterIssue(PrinterIssueSource.HMS, "0300123400020056")
    result = resolve_issue_metadata(issue, "030SERIAL", "en", False, loaded.accepted)
    assert result.metadata is not None

    with pytest.raises(FrozenInstanceError):
        result.resolved = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.metadata.message = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.issue = PrinterIssue(PrinterIssueSource.HMS, "x")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.issue.code = "x"  # type: ignore[misc]

    resource = loaded.accepted.resources[0]
    with pytest.raises(TypeError):
        resource.entries["hms", "0300123400020056"] = "changed"  # type: ignore[index]
    again = resolve_issue_metadata(issue, "030SERIAL", "en", False, loaded.accepted)
    assert again.resolved and again.metadata is not None
    assert again.metadata.message == "A message"


def test_provenance_hash_source_and_nested_immutability(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    raw = json.dumps(document(), separators=(",", ":")).encode()
    path.write_bytes(raw)
    loaded = load_issue_metadata((path,))
    assert loaded.accepted is not None
    resource = loaded.accepted.resources[0]
    assert resource.provenance_origin == "user_supplied"
    assert resource.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert resource.source_reference == str(path.resolve())
    with pytest.raises(FrozenInstanceError):
        resource.entries = {}  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        loaded.accepted.resources += (resource,)  # type: ignore[misc]


def test_resolution_namespaces_locale_and_order_are_isolated(tmp_path: Path) -> None:
    en = tmp_path / "en.json"
    de = tmp_path / "de.json"
    write_json(
        en,
        document(
            entries=[
                {"source": "hms", "lookup_key": "0300123400020056", "message": "hms"},
                {"source": "print_error", "lookup_key": "0012ABCD", "message": "error"},
            ]
        ),
    )
    write_json(
        de,
        document(
            locale="de",
            entries=[{"source": "hms", "lookup_key": "0300123400020056", "message": "de"}],
        ),
    )
    first = load_issue_metadata((en, de)).accepted
    second = load_issue_metadata((de, en)).accepted
    assert first is not None and second is not None
    for accepted in (first, second):
        assert resolve_issue_metadata(
            PrinterIssue(PrinterIssueSource.HMS, "0300123400020056"),
            "030SERIAL", "en", False, accepted
        ).metadata.message == "hms"  # type: ignore[union-attr]
        assert resolve_issue_metadata(
            PrinterIssue(PrinterIssueSource.PRINT_ERROR, "0012ABCD"),
            "030SERIAL", "en", False, accepted
        ).metadata.message == "error"  # type: ignore[union-attr]
        assert resolve_issue_metadata(
            PrinterIssue(PrinterIssueSource.HMS, "0300123400020056"),
            "030SERIAL", "de", False, accepted
        ).metadata.message == "de"  # type: ignore[union-attr]
        assert resolve_issue_metadata(
            PrinterIssue(PrinterIssueSource.PRINT_ERROR, "0012ABCD"),
            "030SERIAL", "de", False, accepted
        ).metadata is None


def test_load_and_resolve_use_no_network_mqtt_or_printer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "metadata.json"
    write_json(path, document())

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("forbidden external or printer access")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(issue_metadata_module, "BambuPrinterAdapter", forbidden, raising=False)
    monkeypatch.setattr(issue_metadata_module, "mqtt", forbidden, raising=False)
    loaded = load_issue_metadata((path,))
    assert loaded.accepted is not None
    result = resolve_issue_metadata(
        PrinterIssue(PrinterIssueSource.HMS, "0300123400020056"),
        "030SERIAL",
        "en",
        False,
        loaded.accepted,
    )
    assert result.resolved
