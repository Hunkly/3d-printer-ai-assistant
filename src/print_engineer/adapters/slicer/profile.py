"""Profile discovery and materialization for Bambu Studio / OrcaSlicer.

Both slicers keep the same on-disk layout under ``%APPDATA%``:

    <AppData>/<Slicer>/system/BBL/{machine,process,filament}/*.json
    <AppData>/<Slicer>/user/<uid>/{machine,process,filament}/*.json
    <AppData>/<Slicer>/user/<uid>/filament/base/*.json

System profiles are full documents; user profiles are *deltas* that override a
parent named by ``inherits`` and typically omit ``type``/``setting_id``/etc.
The CLI rejects ``from: User`` presets, so user deltas are materialized into
self-contained documents with ``type`` and ``from: system`` before slicing.
"""

from __future__ import annotations

import json
from pathlib import Path

from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource
from print_engineer.errors import InvalidProfile

META_KEYS = frozenset(
    {
        "type",
        "name",
        "inherits",
        "from",
        "setting_id",
        "base_id",
        "instantiation",
        "version",
        "printer_settings_id",
    }
)

MAX_INHERIT_DEPTH = 12

_STORE_DIRNAMES = {
    ProfileKind.PROCESS: "process",
    ProfileKind.FILAMENT: "filament",
    ProfileKind.PRINTER: "machine",
}

_CLI_TYPE = {
    ProfileKind.PROCESS: "process",
    ProfileKind.FILAMENT: "filament",
    ProfileKind.PRINTER: "machine",
}


def _store_dirname(kind: ProfileKind) -> str:
    return _STORE_DIRNAMES[kind]


def _normalize_compatible(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return tuple(part for part in value.split(";") if part.strip())
    return ()


def _read_json_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidProfile(
            f"Could not read profile {path}: {exc}",
            details={"profile_kind": _kind_from_path(path), "profile_name": path.stem},
        ) from exc


def _kind_from_path(path: Path) -> str:
    return path.parent.name or "unknown"


def _parse_profile(path: Path, source: ProfileSource, kind: ProfileKind) -> ProfileInfo | None:
    """Parse a single profile JSON without ever raising.

    Returns ``None`` for malformed/irrelevant files so discovery never crashes
    the application.
    """
    text = _read_json_text(path)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    compatible = _normalize_compatible(data.get("compatible_printers"))
    printer_model = data.get("printer_model")
    printer_variant = data.get("printer_variant")
    inherits = data.get("inherits")
    setting_id = data.get("setting_id")

    info = ProfileInfo(
        name=name,
        kind=kind,
        path=path,
        content=text,
        source=source,
        setting_id=setting_id if isinstance(setting_id, str) and setting_id else None,
        printer_model=printer_model if isinstance(printer_model, str) else None,
        printer_variant=printer_variant if isinstance(printer_variant, str) else None,
        compatible_printers=compatible,
        inherits=inherits if isinstance(inherits, str) and inherits else None,
    )
    return info


class ProfileRepository:
    """Discover and look up profiles for one slicer's ``%APPDATA%`` store."""

    def __init__(self, appdata_root: Path) -> None:
        self.appdata_root = appdata_root

    def system_dir(self, kind: ProfileKind) -> Path:
        return self.appdata_root / "system" / "BBL" / _store_dirname(kind)

    def user_dirs(self, kind: ProfileKind) -> list[Path]:
        base = _store_dirname(kind)
        dirs: list[Path] = []
        user_root = self.appdata_root / "user"
        if not user_root.is_dir():
            return dirs
        for user_id in sorted(p.name for p in user_root.iterdir() if p.is_dir()):
            store = user_root / user_id / base
            if store.is_dir():
                dirs.append(store)
            nested = store / "base"
            if nested.is_dir():
                dirs.append(nested)
        return dirs

    def list_profiles(self, kind: ProfileKind) -> list[ProfileInfo]:
        """List all discovered profiles (system first, then user)."""
        profiles: list[ProfileInfo] = []
        for directory, source in self._scan_dirs(kind):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                profile = _parse_profile(path, source, kind)
                if profile is not None:
                    profiles.append(profile)
        return profiles

    def find(self, kind: ProfileKind, name: str) -> ProfileInfo | None:
        """Find a profile by name (user stores shadow the system store)."""
        for directory, source in reversed(self._scan_dirs(kind)):
            candidate = directory / f"{name}.json"
            if candidate.is_file():
                return _parse_profile(candidate, source, kind)
        return None

    def _scan_dirs(self, kind: ProfileKind) -> list[tuple[Path, ProfileSource]]:
        entries: list[tuple[Path, ProfileSource]] = [
            (self.system_dir(kind), ProfileSource.SYSTEM)
        ]
        for directory in self.user_dirs(kind):
            entries.append((directory, ProfileSource.USER))
        return entries


class ProfileMaterializer:
    """Resolve inheritance chains and fix up profiles for CLI consumption."""

    def __init__(self, repository: ProfileRepository) -> None:
        self._repo = repository

    def materialize(self, profile: ProfileInfo) -> ProfileInfo:
        """Produce a self-contained profile suitable for ``--load-settings``.

        User deltas are resolved against their inheritance chain. Machine
        profiles are renamed to their base identity so ``compatible_printers``
        references (which name the base machine) keep matching.
        """
        if profile.materialized:
            return profile

        merged = self._resolve_merged(profile)
        if profile.kind == ProfileKind.PRINTER:
            identity = self._machine_identity(profile.kind, profile.name)
            name = identity
        else:
            name = profile.name

        out: dict[str, object] = {
            "type": _CLI_TYPE[profile.kind],
            "name": name,
            "from": "system",
        }
        out.update(merged)

        printer_model = merged.get("printer_model")
        printer_variant = merged.get("printer_variant")
        materialized = ProfileInfo(
            name=name,
            kind=profile.kind,
            path=None,
            content=json.dumps(out, indent=2, ensure_ascii=False),
            source=ProfileSource.GENERATED,
            setting_id=profile.setting_id,
            printer_model=printer_model if isinstance(printer_model, str) else None,
            printer_variant=printer_variant if isinstance(printer_variant, str) else None,
            compatible_printers=_normalize_compatible(merged.get("compatible_printers")),
            inherits=None,
            materialized=True,
        )
        return materialized

    def _resolve_chain(self, kind: ProfileKind, name: str, depth: int = 0) -> list[dict]:
        if depth > MAX_INHERIT_DEPTH:
            raise InvalidProfile(
                f"Profile inheritance chain too deep for {name!r}",
                details={"profile_kind": kind.value, "profile_name": name},
            )
        parent = self._repo.find(kind, name)
        if parent is None:
            return []
        try:
            data = json.loads(parent.content or "{}")
        except json.JSONDecodeError as exc:
            raise InvalidProfile(
                f"Malformed profile {name!r}",
                details={"profile_kind": kind.value, "profile_name": name},
            ) from exc
        if not isinstance(data, dict):
            raise InvalidProfile(
                f"Profile {name!r} is not a JSON object",
                details={"profile_kind": kind.value, "profile_name": name},
            )
        chain: list[dict] = []
        inherits = data.get("inherits")
        if isinstance(inherits, str) and inherits:
            chain.extend(self._resolve_chain(kind, inherits, depth + 1))
        chain.append(data)
        return chain

    def _resolve_merged(self, profile: ProfileInfo) -> dict[str, object]:
        chain = self._resolve_chain(profile.kind, profile.name)
        if not chain:
            raise InvalidProfile(
                f"Profile {profile.name!r} could not be resolved",
                details={
                    "profile_kind": profile.kind.value,
                    "profile_name": profile.name,
                },
            )
        merged: dict[str, object] = {}
        for data in chain:
            for key, value in data.items():
                if key not in META_KEYS:
                    merged[key] = value
        return merged

    def _machine_identity(self, kind: ProfileKind, name: str) -> str:
        """Return the base machine name a delta profile inherits from."""
        chain = self._resolve_chain(kind, name)
        for data in reversed(chain):
            if data.get("printer_model"):
                candidate = data.get("name")
                if isinstance(candidate, str) and candidate:
                    return candidate
        for data in reversed(chain):
            candidate = data.get("name")
            if isinstance(candidate, str) and candidate:
                return candidate
        return name
