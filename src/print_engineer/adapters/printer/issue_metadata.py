"""Strict, local-only Bambu printer issue metadata loading and lookup."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from print_engineer.core.types import PrinterIssue, PrinterIssueSource

MAX_RESOURCE_BYTES = 5 * 1024 * 1024
MAX_JSON_DEPTH = 8
_FAMILY = re.compile(r"^[0-9A-Za-z]{3}$")
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_HEX16 = re.compile(r"^[0-9A-F]{16}$")
_HEX8 = re.compile(r"^[0-9A-F]{8}$")


class IssueMetadataError(ValueError):
    """Internal validation failure for a local metadata candidate."""


@dataclass(frozen=True)
class IssueMetadataResource:
    vendor: str
    vendor_dataset_version: str
    device_family: str
    locale: str
    entries: Mapping[tuple[str, str], str]
    schema_version: int
    provenance_origin: str
    content_sha256: str
    source_reference: str


@dataclass(frozen=True)
class IssueMetadataSet:
    resources: tuple[IssueMetadataResource, ...] = ()
    _index: Mapping[tuple[str, str], IssueMetadataResource] | None = None

    def __post_init__(self) -> None:
        if self._index is None:
            object.__setattr__(
                self,
                "_index",
                MappingProxyType(
                    {
                        (resource.device_family, resource.locale): resource
                        for resource in self.resources
                    }
                ),
            )

    def resource(self, family: str, locale: str) -> IssueMetadataResource | None:
        return self._index.get((family, locale)) if self._index is not None else None


@dataclass(frozen=True)
class IssueMetadataLoadFailure:
    path: Path | None
    code: str
    message: str


@dataclass(frozen=True)
class IssueMetadataLoadResult:
    accepted: IssueMetadataSet | None = None
    failure: IssueMetadataLoadFailure | None = None

    @property
    def ok(self) -> bool:
        return self.accepted is not None and self.failure is None


class ResolutionReason(StrEnum):
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class ResolvedIssueMetadata:
    message: str
    locale: str
    vendor: str
    vendor_dataset_version: str
    resource_schema_version: int
    provenance_origin: str
    content_sha256: str


@dataclass(frozen=True)
class IssueMetadataResolution:
    issue: PrinterIssue
    resolved: bool
    metadata: ResolvedIssueMetadata | None = None
    reason: ResolutionReason = ResolutionReason.NO_MATCH


def _reject_constant(value: str) -> Any:
    raise IssueMetadataError(f"invalid JSON constant: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IssueMetadataError("duplicate JSON object member")
        result[key] = value
    return result


def _depth(value: Any, level: int = 1) -> None:
    if level > MAX_JSON_DEPTH:
        raise IssueMetadataError("JSON nesting depth exceeds limit")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def _text(value: Any, name: str, maximum: int, *, controls: bool = False) -> str:
    if not isinstance(value, str) or len(value) < 1 or len(value) > maximum:
        raise IssueMetadataError(f"{name} must be a string of length 1..{maximum}")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise IssueMetadataError(f"{name} contains a non-scalar value")
    if "\x00" in value or (
        controls and any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        raise IssueMetadataError(f"{name} contains prohibited control characters")
    if not value.strip():
        raise IssueMetadataError(f"{name} must contain non-whitespace text")
    return value


def _validate_entry(entry: Any, seen: set[tuple[str, str]]) -> tuple[tuple[str, str], str]:
    if not isinstance(entry, dict) or set(entry) != {"source", "lookup_key", "message"}:
        raise IssueMetadataError("entry must have exactly source, lookup_key, and message")
    source = entry["source"]
    key = entry["lookup_key"]
    message = _text(entry["message"], "message", 4096)
    if source not in ("hms", "print_error") or not isinstance(key, str):
        raise IssueMetadataError("invalid entry source or lookup key")
    if source == "hms":
        if (
            not _HEX16.fullmatch(key)
            or key[8:10] != "00"
            or key[10:12] not in {"00", "01", "02", "03", "04"}
        ):
            raise IssueMetadataError("invalid HMS lookup key")
    elif not _HEX8.fullmatch(key) or int(key, 16) == 0 or int(key, 16) > 0x7FFFFFFF:
        raise IssueMetadataError("invalid print-error lookup key")
    pair = (source, key)
    if pair in seen:
        raise IssueMetadataError("duplicate entry lookup key")
    seen.add(pair)
    return pair, message


def _parse_resource(path: Path, raw: bytes) -> IssueMetadataResource:
    if len(raw) > MAX_RESOURCE_BYTES:
        raise IssueMetadataError("resource exceeds 5 MiB limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise IssueMetadataError("UTF-8 BOM is not permitted")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IssueMetadataError(f"invalid UTF-8 or JSON: {exc}") from exc
    _depth(document)
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "vendor",
        "vendor_dataset_version",
        "device_family",
        "locale",
        "entries",
    }:
        raise IssueMetadataError("invalid top-level fields")
    if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
        raise IssueMetadataError("unsupported schema version")
    if document["vendor"] != "bambu_lab":
        raise IssueMetadataError("invalid vendor")
    version = _text(
        document["vendor_dataset_version"], "vendor_dataset_version", 128, controls=True
    )
    family = document["device_family"]
    locale = document["locale"]
    if not isinstance(family, str) or not _FAMILY.fullmatch(family):
        raise IssueMetadataError("invalid device family")
    if not isinstance(locale, str) or not _LOCALE.fullmatch(locale):
        raise IssueMetadataError("invalid locale")
    entries = document["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10000:
        raise IssueMetadataError("entries must contain 1..10000 items")
    lookup: dict[tuple[str, str], str] = {}
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        pair, message = _validate_entry(entry, seen)
        lookup[pair] = message
    return IssueMetadataResource(
        "bambu_lab",
        version,
        family,
        locale,
        MappingProxyType(lookup),
        1,
        "user_supplied",
        hashlib.sha256(raw).hexdigest(),
        str(path),
    )


def load_issue_metadata(paths: tuple[Path, ...] = ()) -> IssueMetadataLoadResult:
    """Read and validate all configured local resources as one atomic set."""
    if not paths:
        return IssueMetadataLoadResult(accepted=IssueMetadataSet())
    resources: list[IssueMetadataResource] = []
    selected: set[tuple[str, str]] = set()
    path: Path | None = None
    try:
        for configured in paths:
            if not configured.is_absolute():
                raise IssueMetadataError("configured path must be absolute")
            path = configured.resolve(strict=False)
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise IssueMetadataError("configured path is not a regular file")
            if info.st_size > MAX_RESOURCE_BYTES:
                raise IssueMetadataError("resource exceeds 5 MiB limit")
            raw = path.read_bytes()
            resource = _parse_resource(path, raw)
            selection = (resource.device_family, resource.locale)
            if selection in selected:
                raise IssueMetadataError("duplicate resource selection key")
            selected.add(selection)
            resources.append(resource)
    except (OSError, IssueMetadataError) as exc:
        return IssueMetadataLoadResult(
            failure=IssueMetadataLoadFailure(path, type(exc).__name__, str(exc))
        )
    resources.sort(key=lambda item: (item.device_family, item.locale, item.content_sha256))
    return IssueMetadataLoadResult(accepted=IssueMetadataSet(tuple(resources)))


def derive_device_family(serial: str | None) -> str | None:
    if not isinstance(serial, str) or len(serial) < 3:
        return None
    family = serial[:3]
    return family if _FAMILY.fullmatch(family) else None


def derive_hms_lookup_key(code: str) -> str | None:
    if not isinstance(code, str) or not _HEX16.fullmatch(code):
        return None
    attr, raw_code = int(code[:8], 16), int(code[8:], 16)
    level = raw_code >> 16
    level = level if level < 5 else 0
    return (
        f"{(attr >> 24) & 0xFF:02X}{(attr >> 16) & 0xFF:02X}"
        f"{(attr >> 8) & 0xFF:02X}{attr & 0xFF:02X}00{level:02X}"
        f"{raw_code & 0xFFFF:04X}"
    )


def derive_lookup_key(issue: PrinterIssue) -> str | None:
    if issue.source == PrinterIssueSource.HMS:
        return derive_hms_lookup_key(issue.code)
    if (
        issue.source == PrinterIssueSource.PRINT_ERROR
        and isinstance(issue.code, str)
        and _HEX8.fullmatch(issue.code)
    ):
        value = int(issue.code, 16)
        return issue.code if 0 < value <= 0x7FFFFFFF else None
    return None


def resolve_issue_metadata(
    issue: PrinterIssue,
    serial: str | None,
    requested_locale: str,
    allow_english_fallback: bool,
    accepted: IssueMetadataSet,
) -> IssueMetadataResolution:
    family = derive_device_family(serial)
    if (
        family is None
        or not isinstance(requested_locale, str)
        or not _LOCALE.fullmatch(requested_locale)
    ):
        return IssueMetadataResolution(issue, False)
    key = derive_lookup_key(issue)
    if key is None:
        return IssueMetadataResolution(issue, False)
    resource = accepted.resource(family, requested_locale)
    if resource is None and allow_english_fallback and requested_locale != "en":
        resource = accepted.resource(family, "en")
    if resource is None:
        return IssueMetadataResolution(issue, False)
    message = resource.entries.get((issue.source.value, key))
    if message is None:
        return IssueMetadataResolution(issue, False)
    return IssueMetadataResolution(
        issue,
        True,
        ResolvedIssueMetadata(
            message,
            resource.locale,
            resource.vendor,
            resource.vendor_dataset_version,
            resource.schema_version,
            resource.provenance_origin,
            resource.content_sha256,
        ),
    )
