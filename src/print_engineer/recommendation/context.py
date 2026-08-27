"""Print-context resolution (Phase 3A.1).

A print context is *the actual* printer, nozzle, build plate, process, and
filament the user intends to use - resolved against the local slicer profile
store. Resolution is strict: an unresolvable printer or profile raises
``UnresolvedPrintContext`` and a name that matches several distinct printers
raises ``AmbiguousPrintContext``. There is no silent generic/default fallback;
``use_defaults`` must be explicitly requested before configured defaults apply
(and is then recorded in ``warnings``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, cast

from print_engineer.adapters.slicer.bambu import BambuStudioAdapter
from print_engineer.adapters.slicer.base import BaseSlicerAdapter
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.adapters.slicer.settings import build_digest
from print_engineer.config import Settings
from print_engineer.core.recommendation import (
    PrintContextIntent,
    ResolvedPrintContext,
    ResolvedPrinter,
)
from print_engineer.core.types import ProfileInfo, ProfileKind, SlicerKind
from print_engineer.errors import AmbiguousPrintContext, SlicerError, UnresolvedPrintContext

_ADAPTER_FACTORIES: dict[SlicerKind, type[BaseSlicerAdapter]] = {
    SlicerKind.ORCA_SLICER: OrcaSlicerAdapter,
    SlicerKind.BAMBU_STUDIO: BambuStudioAdapter,
}


class ProfileReader(Protocol):
    """Read-only slice of the slicer adapter used by the recommendation engine."""

    def list_profiles(self, profile_kind: ProfileKind) -> list[ProfileInfo]: ...

    def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None: ...


class AuthoritativeProfileReader(ProfileReader, Protocol):
    """Reader capability that materializes the exact discovered source."""

    def materialize_profile(self, profile: ProfileInfo) -> ProfileInfo: ...


@dataclass(frozen=True)
class ResolvedContextAuthority:
    """Internal source authority retained beside the public context DTO."""

    context: ResolvedPrintContext
    printer_profiles: tuple[ProfileInfo, ...] = ()
    process_profile: ProfileInfo | None = None


def parse_nozzle_values(value: object) -> list[float]:
    """Parse a nozzle spec into sorted unique diameters in mm.

    Handles ``"0.4"``, ``"0.4;0.2;0.6;0.8"``, ``["0.4"]``, and numbers.
    """
    raw: Any = value
    if isinstance(raw, str):
        raw = raw.split(";")
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out: list[float] = []
    for item in raw:
        if isinstance(item, bool):
            continue
        try:
            number = float(item)
        except (TypeError, ValueError):
            continue
        if number > 0:
            out.append(round(number, 4))
    return sorted(set(out))


def _read_json(profile: ProfileInfo) -> dict[str, Any]:
    if profile is None or profile.content is None:
        return {}
    try:
        data = json.loads(profile.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_float(value: object) -> float | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class PrintContextResolver:
    """Resolve a :class:`PrintContextIntent` against one slicer's store."""

    def __init__(self, settings: Settings, *, adapter: ProfileReader | None = None) -> None:
        self._settings = settings
        self._adapter = adapter

    def adapter(self, slicer_kind: str) -> ProfileReader:
        """Return the slicer adapter (profile-store access) for a kind."""
        self._validate_kind(slicer_kind)
        return self._get_adapter(slicer_kind)

    def resolve(self, intent: PrintContextIntent) -> ResolvedPrintContext:
        self._validate_kind(intent.slicer_kind)
        adapter = self._get_adapter(intent.slicer_kind)
        warnings: list[str] = []

        printer_spec = intent.printer
        if printer_spec is None and intent.use_defaults:
            printer_spec = self._settings.recommend.default_printer
            if printer_spec:
                warnings.append(f"used configured default printer {printer_spec!r}")
            else:
                raise UnresolvedPrintContext(
                    "use_defaults requested but no default printer is configured",
                    details={"field": "recommend.default_printer"},
                )

        printer = self._resolve_printer(adapter, intent.slicer_kind, printer_spec, warnings)
        nozzle = self._resolve_nozzle(intent, printer, warnings)
        build_plate = self._resolve_build_plate(intent, warnings)
        process = self._resolve_process(adapter, intent, printer)
        filament = self._resolve_filament(adapter, intent)

        context = ResolvedPrintContext(
            slicer_kind=intent.slicer_kind,
            printer=printer,
            process=process,
            filament=filament,
            nozzle_diameter_mm=nozzle,
            build_plate=build_plate,
            warnings=warnings,
        )
        return context

    def resolve_with_authority(self, intent: PrintContextIntent) -> ResolvedContextAuthority:
        """Resolve the public context and retain exact source profiles internally."""
        self._validate_kind(intent.slicer_kind)
        adapter = self._get_adapter(intent.slicer_kind)
        warnings: list[str] = []
        printer_spec = intent.printer
        if printer_spec is None and intent.use_defaults:
            printer_spec = self._settings.recommend.default_printer
            if printer_spec:
                warnings.append(f"used configured default printer {printer_spec!r}")
            else:
                raise UnresolvedPrintContext(
                    "use_defaults requested but no default printer is configured",
                    details={"field": "recommend.default_printer"},
                )

        raw_printers = self._select_printer_sources(adapter, intent.slicer_kind, printer_spec)
        printer_sources = [self._materialize_exact(adapter, profile) for profile in raw_printers]
        printer = self._printer_from_profiles(printer_sources, printer_spec or "")
        nozzle = self._resolve_nozzle(intent, printer, warnings, authoritative=True)
        if nozzle is not None and len(printer_sources) > 1:
            printer_sources = [
                profile
                for profile in printer_sources
                if any(
                    abs(nozzle - value) < 1e-6
                    for value in parse_nozzle_values(_read_json(profile).get("nozzle_diameter"))
                )
            ]
            printer = self._printer_from_profiles(printer_sources, printer_spec or "")
        build_plate = self._resolve_build_plate(intent, warnings)
        process_source = self._select_process_source(adapter, intent, printer)
        process = (
            build_digest(slicer_kind=intent.slicer_kind, process=process_source).process
            if process_source is not None
            else None
        )
        filament = self._resolve_filament(adapter, intent)
        context = ResolvedPrintContext(
            slicer_kind=intent.slicer_kind,
            printer=printer,
            process=process,
            filament=filament,
            nozzle_diameter_mm=nozzle,
            build_plate=build_plate,
            warnings=warnings,
        )
        return ResolvedContextAuthority(context, tuple(printer_sources), process_source)

    def _select_printer_sources(
        self, adapter: ProfileReader, slicer_kind: str, printer_spec: str | None
    ) -> list[ProfileInfo]:
        if printer_spec is None:
            raise UnresolvedPrintContext(
                "a printer is required for a setup recommendation",
                details={"slicer_kind": slicer_kind},
            )
        raw = adapter.list_profiles(ProfileKind.PRINTER)
        exact = [profile for profile in raw if profile.name == printer_spec]
        matched = exact or [
            profile for profile in raw if self._profile_model(profile) == printer_spec
        ]
        if not matched:
            matched = [
                profile for profile in raw
                if profile.name == printer_spec or profile.name.startswith(f"{printer_spec} ")
            ]
        if not matched:
            raise UnresolvedPrintContext(
                f"Unknown printer {printer_spec!r}",
                details={"printer": printer_spec, "slicer_kind": slicer_kind, "matches": []},
            )
        models = sorted({profile.printer_model or profile.name for profile in matched})
        if len(models) > 1:
            raise AmbiguousPrintContext(
                f"Printer {printer_spec!r} matches multiple printers",
                details={"printer": printer_spec, "matches": models},
            )
        return matched

    @staticmethod
    def _materialize_exact(adapter: ProfileReader, profile: ProfileInfo) -> ProfileInfo:
        materialize = getattr(adapter, "materialize_profile", None)
        if not callable(materialize):
            if profile.materialized:
                return profile
            raise SlicerError(
                "authoritative profile materialization is unavailable",
                details={"profile_kind": profile.kind.value},
            )
        return materialize(profile)

    def _select_process_source(
        self, adapter: ProfileReader, intent: PrintContextIntent, printer: ResolvedPrinter
    ) -> ProfileInfo | None:
        name = intent.process_profile or printer.default_print_profile
        if not name:
            return None
        profiles = [p for p in adapter.list_profiles(ProfileKind.PROCESS) if p.name == name]
        if not profiles:
            raise UnresolvedPrintContext(
                f"Unknown process profile {name!r}",
                details={"profile_kind": "process", "profile_name": name},
            )
        if len(profiles) > 1:
            raise AmbiguousPrintContext(
                f"Process profile {name!r} matches multiple sources",
                details={"profile_kind": "process", "profile_name": name},
            )
        return self._materialize_exact(adapter, profiles[0])

    def _validate_kind(self, slicer_kind: str) -> None:
        try:
            SlicerKind(slicer_kind)
        except ValueError as exc:
            raise SlicerError(
                f"Unknown slicer {slicer_kind!r}",
                details={"slicer_kind": slicer_kind},
            ) from exc

    def _get_adapter(self, slicer_kind: str) -> ProfileReader:
        if self._adapter is not None:
            return self._adapter
        kind = SlicerKind(slicer_kind)
        factory = _ADAPTER_FACTORIES.get(kind)
        if factory is None:
            raise SlicerError(
                f"Unknown slicer {slicer_kind!r}",
                details={"slicer_kind": slicer_kind},
            )
        kwargs: dict[str, object] = {
            "workdir": self._settings.storage.workspace_dir,
            "timeout_seconds": self._settings.slicer.timeout_seconds,
        }
        if kind == SlicerKind.ORCA_SLICER and self._settings.slicer.orca_install_path:
            kwargs["executable"] = self._settings.slicer.orca_install_path
        if kind == SlicerKind.BAMBU_STUDIO and self._settings.slicer.bambu_install_path:
            kwargs["executable"] = self._settings.slicer.bambu_install_path
        return cast(Any, factory)(**kwargs)

    def _resolve_printer(
        self,
        adapter: ProfileReader,
        slicer_kind: str,
        printer_spec: str | None,
        warnings: list[str],
    ) -> ResolvedPrinter | None:
        if printer_spec is None:
            return None
        exact = adapter.find_profile(ProfileKind.PRINTER, printer_spec)
        if exact is not None:
            return self._printer_from_profiles([exact], printer_spec)

        raw = adapter.list_profiles(ProfileKind.PRINTER)
        matched = [profile for profile in raw if profile.printer_model == printer_spec]
        if not matched:
            matched = [
                profile
                for profile in raw
                if profile.name == printer_spec or profile.name.startswith(f"{printer_spec} ")
            ]
        if not matched:
            raise UnresolvedPrintContext(
                f"Unknown printer {printer_spec!r}",
                details={"printer": printer_spec, "slicer_kind": slicer_kind, "matches": []},
            )
        models = sorted({self._profile_model(profile) for profile in matched})
        if len(models) > 1:
            raise AmbiguousPrintContext(
                f"Printer {printer_spec!r} matches multiple printers",
                details={"printer": printer_spec, "matches": models},
            )
        materialized: list[ProfileInfo] = []
        for profile in matched:
            resolved = adapter.find_profile(ProfileKind.PRINTER, profile.name)
            if resolved is not None:
                materialized.append(resolved)
        return self._printer_from_profiles(materialized, printer_spec)

    @staticmethod
    def _profile_model(profile: ProfileInfo) -> str:
        if profile.printer_model:
            return profile.printer_model
        data = _read_json(profile)
        model = data.get("printer_model")
        return model if isinstance(model, str) and model else profile.name

    def _printer_from_profiles(
        self, profiles: list[ProfileInfo], printer_spec: str
    ) -> ResolvedPrinter:
        nozzle_sets: list[list[float]] = []
        printable_height: float | None = None
        default_print_profile: str | None = None
        default_filament_profiles: list[str] = []
        printer_model: str | None = None
        printer_variant: str | None = None

        for profile in profiles:
            data = _read_json(profile)
            nozzle_sets.append(parse_nozzle_values(data.get("nozzle_diameter")))
            if profile.printer_model:
                printer_model = profile.printer_model
            if profile.printer_variant:
                printer_variant = profile.printer_variant
            height = _first_float(data.get("printable_height"))
            if height is not None:
                printable_height = height
            default_print = data.get("default_print_profile")
            if isinstance(default_print, str) and default_print.strip():
                default_print_profile = default_print.strip()
            default_filaments = _as_str_list(data.get("default_filament_profile"))
            if default_filaments:
                default_filament_profiles = default_filaments

        supported = sorted({nozzle for group in nozzle_sets for nozzle in group})
        model = printer_model or printer_spec
        current_nozzle = supported[0] if len(supported) == 1 else None
        return ResolvedPrinter(
            name=model,
            printer_model=model,
            printer_variant=printer_variant,
            nozzle_diameter_mm=current_nozzle,
            supported_nozzle_mm=supported,
            printable_height_mm=printable_height,
            default_print_profile=default_print_profile,
            default_filament_profiles=default_filament_profiles,
        )

    def _resolve_nozzle(
        self,
        intent: PrintContextIntent,
        printer: ResolvedPrinter | None,
        warnings: list[str],
        *,
        authoritative: bool = False,
    ) -> float | None:
        if intent.nozzle_diameter_mm is not None:
            nozzle = intent.nozzle_diameter_mm
            if (
                printer
                and printer.supported_nozzle_mm
                and not any(
                    abs(nozzle - supported) < 1e-6 for supported in printer.supported_nozzle_mm
                )
            ):
                raise UnresolvedPrintContext(
                    f"Nozzle {nozzle:g} mm is not supported by {printer.name}",
                    details={
                        "printer": printer.name,
                        "nozzle_diameter_mm": nozzle,
                        "supported_nozzle_mm": printer.supported_nozzle_mm,
                    },
                )
            return nozzle
        if printer is not None and printer.nozzle_diameter_mm is not None:
            return printer.nozzle_diameter_mm
        if intent.use_defaults and self._settings.recommend.default_nozzle_diameter is not None:
            default = self._settings.recommend.default_nozzle_diameter
            if (
                not authoritative
                or printer is None
                or any(abs(default - supported) < 1e-6 for supported in printer.supported_nozzle_mm)
            ):
                warnings.append(f"used configured default nozzle {default:g} mm")
                return default
        if not authoritative:
            return None
        if printer is not None and 0.4 in printer.supported_nozzle_mm:
            return 0.4
        if printer is not None and len(printer.supported_nozzle_mm) == 1:
            return printer.supported_nozzle_mm[0]
        return None

    def _resolve_build_plate(self, intent: PrintContextIntent, warnings: list[str]) -> str | None:
        if intent.build_plate:
            return intent.build_plate
        if intent.use_defaults and self._settings.recommend.default_build_plate:
            default_plate = self._settings.recommend.default_build_plate
            warnings.append(f"used configured default build plate {default_plate!r}")
            return default_plate
        return None

    def _resolve_process(
        self,
        adapter: ProfileReader,
        intent: PrintContextIntent,
        printer: ResolvedPrinter | None,
    ) -> Any | None:
        if intent.process_profile is not None:
            profile = adapter.find_profile(ProfileKind.PROCESS, intent.process_profile)
            if profile is None:
                raise UnresolvedPrintContext(
                    f"Unknown process profile {intent.process_profile!r}",
                    details={"profile_kind": "process", "profile_name": intent.process_profile},
                )
            return build_digest(slicer_kind=intent.slicer_kind, process=profile).process
        if printer is not None and printer.default_print_profile:
            profile = adapter.find_profile(ProfileKind.PROCESS, printer.default_print_profile)
            if profile is not None:
                return build_digest(slicer_kind=intent.slicer_kind, process=profile).process
        return None

    def _resolve_filament(self, adapter: ProfileReader, intent: PrintContextIntent) -> Any | None:
        if intent.filament_profile is None:
            return None
        profile = adapter.find_profile(ProfileKind.FILAMENT, intent.filament_profile)
        if profile is None:
            raise UnresolvedPrintContext(
                f"Unknown filament profile {intent.filament_profile!r}",
                details={"profile_kind": "filament", "profile_name": intent.filament_profile},
            )
        return build_digest(slicer_kind=intent.slicer_kind, filament=profile).filament
