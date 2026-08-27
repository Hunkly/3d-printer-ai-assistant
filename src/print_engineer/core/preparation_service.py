"""Deterministic, local public preparation orchestration."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from print_engineer.adapters.slicer.base import SUPPORTED_INPUT_SUFFIXES
from print_engineer.adapters.slicer.execution import SliceExecutionFailure, SliceExecutor
from print_engineer.adapters.slicer.finalization import SliceFinalizer
from print_engineer.adapters.slicer.orca import OrcaSlicerAdapter
from print_engineer.adapters.slicer.profile import ProfileMaterializer, ProfileRepository
from print_engineer.adapters.slicer.realization import SetupRealizer
from print_engineer.config import Settings
from print_engineer.core.preparation import (
    FailureStage,
    ModelIdentity,
    NotReadyPreparationResult,
    PreparationAuthority,
    PreparationFailure,
    PreparationIdentity,
    PreparationResult,
    ProfileIdentity,
    SelectedSetup,
)
from print_engineer.core.recommendation import RecommendationGoal, SetupRequest
from print_engineer.core.types import ProfileInfo, ProfileKind, ProfileSource, SlicerKind
from print_engineer.errors import SlicerError
from print_engineer.recommendation.context import PrintContextResolver, ProfileReader
from print_engineer.recommendation.setup import AuthoritativeSetupSelection, SetupEngine


class _RepositoryProfileReader:
    def __init__(self, repository: ProfileRepository, materializer: ProfileMaterializer) -> None:
        self.repository = repository
        self.materializer = materializer

    def list_profiles(self, profile_kind: ProfileKind) -> list[ProfileInfo]:
        return self.repository.list_profiles(profile_kind)

    def find_profile(self, profile_kind: ProfileKind, name: str) -> ProfileInfo | None:
        source = self.repository.find(profile_kind, name)
        return None if source is None else self.materializer.materialize(source)

    def materialize_profile(self, profile: ProfileInfo) -> ProfileInfo:
        """Materialize this exact discovered source, never a same-name lookup."""
        if profile.materialized:
            return profile
        merged = self._merge_exact(profile)
        if profile.kind == ProfileKind.PRINTER:
            name = profile.name
        else:
            name = profile.name
        output: dict[str, object] = {"type": {ProfileKind.PRINTER: "machine", ProfileKind.PROCESS: "process", ProfileKind.FILAMENT: "filament"}[profile.kind], "name": name, "from": "system"}
        for item in merged:
            output.update({key: value for key, value in item.items() if key not in {"type", "name", "inherits", "from", "setting_id", "base_id", "instantiation", "version", "printer_settings_id"}})
        content = json.dumps(output, indent=2, ensure_ascii=False)
        return ProfileInfo(
            name=name,
            kind=profile.kind,
            path=None,
            content=content,
            source=ProfileSource.GENERATED,
            setting_id=profile.setting_id,
            printer_model=profile.printer_model,
            printer_variant=profile.printer_variant,
            compatible_printers=profile.compatible_printers,
            materialized=True,
        )

    def _merge_exact(self, profile: ProfileInfo, depth: int = 0) -> list[dict[str, object]]:
        if depth > 12:
            raise SlicerError("profile inheritance is too deep", details={"profile_kind": profile.kind.value})
        try:
            data = json.loads(profile.content or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SlicerError("profile content is invalid", details={"profile_kind": profile.kind.value}) from exc
        if not isinstance(data, dict):
            raise SlicerError("profile content is invalid", details={"profile_kind": profile.kind.value})
        parent_name = data.get("inherits")
        chain: list[dict[str, object]] = []
        if isinstance(parent_name, str) and parent_name:
            parent = next(
                (
                    candidate
                    for candidate in self.repository.list_profiles(profile.kind)
                    if candidate.name == parent_name
                    and (candidate.source == profile.source or candidate.source == ProfileSource.SYSTEM)
                ),
                None,
            )
            if parent is None:
                raise SlicerError("profile inheritance parent is unavailable", details={"profile_kind": profile.kind.value})
            chain.extend(self._merge_exact(parent, depth + 1))
        chain.append({str(key): value for key, value in data.items()})
        return chain


class _ExactRootRepository(ProfileRepository):
    """A view that pins one selected root while sharing parent authority."""

    def __init__(self, repository: ProfileRepository, root: ProfileInfo) -> None:
        # The base implementation is intentionally not used for discovery; all
        # discovery and non-root lookups remain delegated to the service-owned
        # repository below.
        super().__init__(repository.appdata_root)
        self._repository = repository
        self._root = root

    def list_profiles(self, kind: ProfileKind) -> list[ProfileInfo]:
        return self._repository.list_profiles(kind)

    def find(self, kind: ProfileKind, name: str) -> ProfileInfo | None:
        if kind is self._root.kind and name == self._root.name:
            return self._root
        return self._repository.find(kind, name)


class _ExactRootMaterializer(ProfileMaterializer):
    """Materialize each supplied source without re-resolving its root by name."""

    def __init__(self, repository: ProfileRepository) -> None:
        super().__init__(repository)
        self._authoritative_repository = repository

    def materialize(self, profile: ProfileInfo) -> ProfileInfo:
        if profile.materialized:
            return profile
        view = _ExactRootRepository(self._authoritative_repository, profile)
        return ProfileMaterializer(view).materialize(profile)


class PreparationService:
    """Request-stateless owner of the I5 recommendation-to-artifact pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        repository: ProfileRepository,
        materializer: ProfileMaterializer,
        reader: ProfileReader,
        setup_engine: SetupEngine,
        realizer: SetupRealizer,
        executor: SliceExecutor,
        finalizer: SliceFinalizer,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.materializer = materializer
        self.reader = reader
        self.setup_engine = setup_engine
        self.realizer = realizer
        self.executor = executor
        self.finalizer = finalizer

    @classmethod
    def from_settings(cls, settings: Settings) -> PreparationService:
        root = settings.slicer.orca_appdata_path
        profile_store_root = root or (
            Path.home() / "AppData" / "Roaming" / OrcaSlicerAdapter.appdata_dirname
        )
        repository = ProfileRepository(profile_store_root)
        materializer = _ExactRootMaterializer(repository)
        reader = _RepositoryProfileReader(repository, materializer)
        resolver = PrintContextResolver(settings, adapter=reader)
        setup_engine = SetupEngine(settings, llm=None, resolver=resolver)
        slicer = OrcaSlicerAdapter(
            executable=settings.slicer.orca_install_path,
            appdata=profile_store_root,
            workdir=settings.storage.workspace_dir,
            timeout_seconds=settings.recommend.slice_timeout_seconds,
        )
        return cls(
            settings,
            repository=repository,
            materializer=materializer,
            reader=reader,
            setup_engine=setup_engine,
            realizer=SetupRealizer(repository, materializer),
            executor=SliceExecutor(settings.storage.workspace_dir, slicer=slicer),
            finalizer=SliceFinalizer(),
        )

    def prepare(
        self,
        model: str,
        goal: RecommendationGoal | str,
        *,
        material: str | None = None,
        printer: str | None = None,
        build_plate: str | None = None,
        nozzle_diameter_mm: float | None = None,
    ) -> PreparationResult:
        resolved_model, model_failure = self._model(model)
        goal_value = RecommendationGoal(goal)
        if model_failure is not None:
            failure_model = resolved_model or ModelIdentity(
                Path(os.getcwd()) / "invalid-model"
            )
            identity = PreparationIdentity(failure_model, goal_value)
            return NotReadyPreparationResult(identity, model_failure)
        assert resolved_model is not None
        identity = PreparationIdentity(resolved_model, goal_value)
        request = SetupRequest(
            goal=goal_value,
            material=material,
            printer=printer,
            build_plate=build_plate,
            nozzle_diameter_mm=nozzle_diameter_mm,
            use_defaults=True,
            slicer_kind=SlicerKind.ORCA_SLICER.value,
            use_llm=False,
        )
        try:
            selected = self.setup_engine.recommend_authoritative(request)
            setup = self._selected_setup(selected, request)
        except (_DomainFailure, SlicerError, ValueError, KeyError, TypeError) as exc:
            return NotReadyPreparationResult(
                identity, self._failure(FailureStage.SETUP_SELECTION, exc)
            )
        authority = PreparationAuthority(
            PreparationIdentity(resolved_model, selected.recommendation.goal), setup
        )
        realization = self.realizer.realize(authority)
        if not realization.succeeded:
            failure = realization.failure
            assert failure is not None
            return NotReadyPreparationResult(
                identity, self._mapped_realization(failure), selected_setup=setup
            )
        execution = self.executor.execute(
            realization, timeout_seconds=self.settings.recommend.slice_timeout_seconds
        )
        if isinstance(execution, SliceExecutionFailure):
            return NotReadyPreparationResult(
                identity, self._mapped_slice(execution), selected_setup=setup
            )
        result = self.finalizer.finalize(execution)
        if isinstance(result, NotReadyPreparationResult):
            result = NotReadyPreparationResult(
                identity, self._mapped_finalization(result.failure), selected_setup=setup
            )
        return result

    def _selected_setup(self, selected: AuthoritativeSetupSelection, request: SetupRequest) -> Any:
        recommendation = selected.recommendation
        authority = selected.context_authority
        plate = authority.context.build_plate
        if not plate:
            raise _DomainFailure(
                "default_build_plate_missing",
                "A build plate is required.",
                {"field": "build_plate"},
            )
        if len(authority.printer_profiles) != 1:
            code = (
                "printer_profile_not_found"
                if not authority.printer_profiles
                else "printer_profile_ambiguous"
            )
            raise _DomainFailure(
                code, "The printer profile could not be selected.", {"profile_kind": "printer"}
            )
        printer = authority.printer_profiles[0]
        process = authority.process_profile
        candidate = selected.filament_candidate
        if process is None:
            raise _DomainFailure(
                "process_profile_not_found",
                "The process profile could not be selected.",
                {"profile_kind": "process"},
            )
        if candidate is None:
            raise _DomainFailure("no_compatible_setup", "No compatible setup is available.", {})
        filament = ProfileIdentity(
            name=candidate.profile_name,
            kind=ProfileKind.FILAMENT,
            setting_id=candidate.setting_id,
        )
        printer_identity = ProfileIdentity(
            name=printer.name,
            kind=ProfileKind.PRINTER,
            setting_id=printer.setting_id,
        )
        process_identity = ProfileIdentity(
            name=process.name,
            kind=ProfileKind.PROCESS,
            setting_id=process.setting_id,
        )
        self._require_unique_profile_authority(printer_identity, "printer")
        self._require_unique_profile_authority(process_identity, "process")
        self._require_unique_profile_authority(filament, "filament")
        nozzle = recommendation.nozzle.nozzle_diameter_mm if recommendation.nozzle else None
        if nozzle is None:
            raise _DomainFailure(
                "nozzle_not_authoritative", "The nozzle could not be selected.", {}
            )
        if authority.context.printer and authority.context.printer.supported_nozzle_mm:
            if not any(
                abs(nozzle - value) < 1e-6
                for value in authority.context.printer.supported_nozzle_mm
            ):
                raise _DomainFailure(
                    "nozzle_not_authoritative",
                    "The selected nozzle is unsupported.",
                    {"supported_values": authority.context.printer.supported_nozzle_mm},
                )
        return SelectedSetup(
            slicer=SlicerKind.ORCA_SLICER,
            printer=printer_identity,
            nozzle_diameter_mm=nozzle,
            build_plate=plate,
            material=candidate.material_type
            or (recommendation.material.material_type if recommendation.material else ""),
            filament_profile=filament,
            process_profile=process_identity,
            overrides=(),
        )

    def _require_unique_profile_authority(
        self, identity: ProfileIdentity, profile_kind: str
    ) -> None:
        matches = [
            candidate
            for candidate in self.repository.list_profiles(identity.kind)
            if (
                candidate.kind == identity.kind
                and candidate.name == identity.name
                and candidate.setting_id == identity.setting_id
            )
        ]
        if not matches:
            raise _DomainFailure(
                "profile_authority_missing",
                "The selected profile authority is unavailable.",
                {"profile_kind": profile_kind},
            )
        if len(matches) > 1:
            raise _DomainFailure(
                "profile_authority_ambiguous",
                "The selected profile authority is ambiguous.",
                {"profile_kind": profile_kind},
            )

    def _model(self, value: str) -> tuple[ModelIdentity | None, PreparationFailure | None]:
        if not isinstance(value, str) or not value.strip():
            return None, PreparationFailure(
                FailureStage.MODEL_INPUT, "model_blank", "The model path is blank."
            )
        lexical = Path(os.path.abspath(os.path.normpath(os.fspath(Path(value)))))
        try:
            current = lexical
            components: list[Path] = []
            while True:
                components.append(current)
                if current.parent == current:
                    break
                current = current.parent
            for component in reversed(components):
                if not component.exists():
                    continue
                info = os.lstat(component)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
                ):
                    return None, PreparationFailure(
                        FailureStage.MODEL_INPUT,
                        "model_symlink_or_reparse",
                        "The model path is unsafe.",
                    )
            resolved = lexical.resolve(strict=True)
            if not resolved.is_file():
                return None, PreparationFailure(
                    FailureStage.MODEL_INPUT, "model_not_file", "The model is not a regular file."
                )
            if resolved.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                return None, PreparationFailure(
                    FailureStage.MODEL_INPUT,
                    "model_unsupported_suffix",
                    "The model format is unsupported.",
                )
            try:
                digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except UnicodeError:
                return None, PreparationFailure(
                    FailureStage.MODEL_INPUT, "model_unreadable", "The model could not be read."
                )
            except OSError:
                return None, PreparationFailure(
                    FailureStage.MODEL_INPUT,
                    "model_hash_failed",
                    "The model digest could not be computed.",
                )
            return ModelIdentity(resolved, digest), None
        except FileNotFoundError:
            return None, PreparationFailure(
                FailureStage.MODEL_INPUT, "model_missing", "The model was not found."
            )
        except (OSError, RuntimeError):
            return None, PreparationFailure(
                FailureStage.MODEL_INPUT, "model_unreadable", "The model could not be read."
            )

    @staticmethod
    def _failure(stage: FailureStage, exc: Exception) -> PreparationFailure:
        if isinstance(exc, _DomainFailure):
            from print_engineer.core.preparation import EvidenceDetail

            return PreparationFailure(
                stage,
                exc.code,
                exc.message,
                tuple(EvidenceDetail(key, str(value)) for key, value in exc.details.items()),
            )
        code = getattr(exc, "code", "setup_selection_failed")
        return PreparationFailure(
            stage, str(code), "The requested preparation setup is unavailable."
        )

    @staticmethod
    def _mapped_realization(failure: PreparationFailure) -> PreparationFailure:
        messages = {
            "unsupported_slicer_version": "The requested slicer version is unsupported.",
            "printer_profile_missing": "The printer profile is unavailable.",
            "process_profile_missing": "The process profile is unavailable.",
            "filament_profile_missing": "The filament profile is unavailable.",
            "ambiguous_profile_resolution": "Profile resolution was ambiguous.",
            "wrong_profile_kind": "A selected profile has the wrong kind.",
            "profile_materialization_failed": "A selected profile could not be materialized.",
            "profile_content_invalid": "A selected profile is invalid.",
            "incompatible_profiles": "The selected profiles are incompatible.",
            "unsupported_nozzle": "The selected nozzle is unsupported by the printer profile.",
            "build_plate_not_representable": "The selected build plate cannot be represented.",
            "material_not_provable": "The filament material cannot be verified.",
            "material_profile_mismatch": "The selected material conflicts with the filament profile.",
            "unsupported_override": "A requested setup override is unsupported.",
            "invalid_effective_value": "The effective setup contains an invalid value.",
            "effective_settings_mismatch": "The effective setup does not match the selected setup.",
        }
        code = failure.code if failure.code in messages else "realization_failed"
        allowed = {"profile_kind", "supported_values", "field"}
        details = tuple(item for item in failure.details if item.key in allowed)
        return PreparationFailure(FailureStage.REALIZATION, code, messages.get(code, "The preparation realization failed."), details)

    @staticmethod
    def _mapped_slice(failure: SliceExecutionFailure) -> PreparationFailure:
        messages = {
            "invalid_source_model": "The source model is invalid.",
            "source_model_identity_mismatch": "The source model identity could not be verified.",
            "workspace_creation_failed": "The slice workspace could not be created.",
            "config_materialization_failed": "The realized slicer configuration could not be prepared.",
            "config_verification_failed": "The realized slicer configuration could not be verified.",
            "slicer_unavailable": "The slicer is unavailable.",
            "slicer_timeout": "The slicer timed out.",
            "slicer_process_failed": "The slicer process failed.",
            "slice_output_missing": "The slice output is missing.",
            "slice_output_invalid": "The slice output is invalid.",
            "slice_facts_invalid": "The slice facts are invalid.",
            "candidate_artifact_identity_failed": "The slice artifact identity could not be verified.",
        }
        code = failure.stage if failure.stage in messages else "slice_failed"
        return PreparationFailure(FailureStage.SLICING, code, messages.get(code, "The slicing operation failed."))

    @staticmethod
    def _mapped_finalization(failure: PreparationFailure) -> PreparationFailure:
        workspace = {"workspace_missing", "workspace_reparse_or_unsafe", "workspace_not_directory"}
        artifact = {"candidate_run_mismatch", "candidate_path_mismatch", "candidate_missing", "candidate_not_file", "candidate_empty", "candidate_size_mismatch", "candidate_hash_mismatch"}
        printer = {"printer_config_path_mismatch", "printer_config_missing", "printer_config_not_file", "printer_config_invalid", "printer_config_identity_mismatch"}
        process = {"process_config_path_mismatch", "process_config_missing", "process_config_not_file", "process_config_invalid", "process_config_identity_mismatch"}
        filament = {"filament_config_path_mismatch", "filament_config_missing", "filament_config_not_file", "filament_config_invalid", "filament_config_identity_mismatch"}
        if failure.code in workspace:
            message = "The slice workspace failed final verification."
        elif failure.code in artifact:
            message = "The slice artifact failed final verification."
        elif failure.code in printer:
            message = "The printer realized configuration failed final verification."
        elif failure.code in process:
            message = "The process realized configuration failed final verification."
        elif failure.code in filament:
            message = "The filament realized configuration failed final verification."
        elif failure.code == "unsupported_slicer_version":
            message = "The slicer identity failed final verification."
        else:
            return PreparationFailure(FailureStage.FINAL_VERIFICATION, "finalization_failed", "The prepared result failed final verification.")
        return PreparationFailure(FailureStage.FINAL_VERIFICATION, failure.code, message)


class _DomainFailure(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        self.code, self.message, self.details = code, message, details
