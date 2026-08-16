"""Recommendation engine orchestrator (Phase 3A).

Pipeline:

    analyze model  ->  resolve current slicer settings  ->  (optional) slice
    ->  build RecommendationInput  ->  deterministic rules
    ->  LLM reasoning (if enabled)  ->  strict validation  ->  merge  ->  output

Read-only by construction: the engine exposes no methods that write profiles,
modify the model, or control the printer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from print_engineer.adapters.model.analyzer import TrimeshModelAnalyzer
from print_engineer.adapters.slicer.registry import SlicerRegistry
from print_engineer.adapters.slicer.settings import build_digest
from print_engineer.config import Settings
from print_engineer.core.interfaces.llm import LLMClient
from print_engineer.core.interfaces.model_analyzer import ModelAnalyzer
from print_engineer.core.interfaces.recommender import Recommender
from print_engineer.core.recommendation import (
    RECOMMENDABLE_SETTINGS,
    LLMRecommendationSet,
    ModelFacts,
    Recommendation,
    RecommendationInput,
    RecommendationMode,
    RecommendationRequest,
    RecommendationSet,
    RecommendationSource,
    SliceStatistics,
    category_for_setting,
)
from print_engineer.core.types import (
    ModelAnalysis,
    ProfileInfo,
    ProfileKind,
    SliceJob,
    SlicerKind,
)
from print_engineer.errors import (
    InvalidProfile,
    LLMError,
    LLMInvalidResponse,
    SlicerError,
)
from print_engineer.recommendation.prompt import build_facts_text, build_prompt
from print_engineer.recommendation.rules import RuleConfig, evaluate

log = logging.getLogger("print_engineer.recommend")


def _raise_missing_profile(profile_kind: ProfileKind, name: str) -> None:
    raise InvalidProfile(
        f"Unknown {profile_kind.value} profile {name!r}",
        details={"profile_kind": profile_kind.value, "profile_name": name},
    )


@dataclass(frozen=True)
class _ProfileTriple:
    process: ProfileInfo | None
    filament: ProfileInfo | None
    printer: ProfileInfo | None
    slicer_kind: SlicerKind


@dataclass(frozen=True)
class _PrinterDefaults:
    process: str | None
    filament: str | None


class RecommendationEngine(Recommender):
    """Concrete recommendation engine wired to the analyzer and slicer registry."""

    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        analyzer: ModelAnalyzer | None = None,
        registry: SlicerRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._analyzer = analyzer if analyzer is not None else TrimeshModelAnalyzer()
        self._registry = registry or SlicerRegistry(
            settings=settings,
            workdir=settings.storage.workspace_dir,
            timeout_seconds=settings.slicer.timeout_seconds,
        )

    def recommend(self, request: RecommendationRequest) -> RecommendationSet:
        analysis = self._analyzer.analyze(
            request.model_path, request.overhang_threshold_degrees
        )
        facts = _facts_from_analysis(analysis)
        triple, resolve_warnings = self._resolve_slicer(request)
        stats = self._obtain_stats(request, triple)
        input_ = RecommendationInput(
            goal=request.goal,
            model=facts,
            slicer=build_digest(
                slicer_kind=request.slicer_kind,
                process=triple.process,
                filament=triple.filament,
                printer=triple.printer,
            )
            if triple.slicer_kind is not None
            else None,
            slice_stats=stats,
            max_time_minutes=request.max_time_minutes,
            max_filament_g=request.max_filament_g,
        )
        rule_config = RuleConfig(
            overhang_percent_threshold=self._settings.recommend.overhang_percent_threshold,
            thin_wall_min_ratio=self._settings.recommend.thin_wall_min_ratio,
        )
        candidates, rule_warnings = evaluate(input_, rule_config)
        warnings = [*resolve_warnings, *rule_warnings]

        if self._llm is None:
            return self._deterministic_set(input_, candidates, warnings)

        try:
            llm_set = self._call_llm(input_, candidates)
        except LLMError as exc:
            if self._settings.recommend.allow_deterministic_fallback:
                log.warning("LLM reasoning unavailable (%s); using deterministic rules", exc.code)
                warnings.append(
                    f"LLM reasoning unavailable ({exc.code}); results are deterministic "
                    "rule-based recommendations only, no LLM reasoning was used"
                )
                return self._deterministic_set(input_, candidates, warnings)
            raise
        return self._merge(input_, candidates, llm_set, warnings)

    def _resolve_slicer(self, request: RecommendationRequest) -> tuple[_ProfileTriple, list[str]]:
        warnings: list[str] = []
        requested = any(
            (request.process_profile, request.filament_profile, request.printer_profile)
        )
        try:
            kind = SlicerKind(request.slicer_kind)
        except ValueError as exc:
            raise SlicerError(
                f"Unknown slicer {request.slicer_kind!r} "
                f"(expected one of {[k.value for k in SlicerKind]})",
                details={"slicer_kind": request.slicer_kind},
            ) from exc

        try:
            adapter = self._registry.get(kind)
        except SlicerError:
            if requested:
                raise
            warnings.append(f"{request.slicer_kind} not installed; slicer settings unavailable")
            return _ProfileTriple(None, None, None, kind), warnings

        printer: ProfileInfo | None = None
        if request.printer_profile is not None:
            printer = adapter.find_profile(ProfileKind.PRINTER, request.printer_profile)
            if printer is None:
                _raise_missing_profile(ProfileKind.PRINTER, request.printer_profile)

        defaults = _printer_defaults(printer)

        process: ProfileInfo | None = None
        process_name = request.process_profile
        if process_name is None:
            process_name = defaults.process
        if process_name is not None:
            process = adapter.find_profile(ProfileKind.PROCESS, process_name)
            if process is None and request.process_profile is not None:
                _raise_missing_profile(ProfileKind.PROCESS, process_name)

        filament: ProfileInfo | None = None
        filament_name = request.filament_profile
        if filament_name is None:
            filament_name = defaults.filament
        if filament_name is not None:
            filament = adapter.find_profile(ProfileKind.FILAMENT, filament_name)
            if filament is None and request.filament_profile is not None:
                _raise_missing_profile(ProfileKind.FILAMENT, filament_name)

        if process is None and filament is None and printer is None:
            warnings.append("no slicer profile selected; slicer settings unavailable")

        return _ProfileTriple(process, filament, printer, kind), warnings

    def _obtain_stats(
        self, request: RecommendationRequest, triple: _ProfileTriple
    ) -> SliceStatistics | None:
        if not request.slice_on_demand:
            return None
        if triple.process is None or triple.filament is None or triple.printer is None:
            raise InvalidProfile(
                "slicing on demand requires process, filament, and printer profiles",
                details={"reason": "incomplete_profiles"},
            )
        adapter = self._registry.get(triple.slicer_kind)
        job = SliceJob(
            model_path=request.model_path,
            profile=triple.process,
            filament=triple.filament,
            printer=triple.printer,
            kind=triple.slicer_kind,
            timeout_seconds=self._settings.recommend.slice_timeout_seconds,
        )
        result = adapter.slice(job)
        return SliceStatistics(
            available=True,
            estimated_time_minutes=result.estimated_time_minutes,
            layer_count=result.layer_count,
            filament_used_mm=result.filament_used_mm,
            filament_used_cm3=result.filament_used_cm3,
            filament_weight_g=result.filament_weight_g,
        )

    def _call_llm(
        self, input_: RecommendationInput, candidates: list[Recommendation]
    ) -> LLMRecommendationSet:
        assert self._llm is not None
        prompt = build_prompt(input_, candidates)
        raw = self._llm.complete_json(prompt)
        try:
            llm_set = LLMRecommendationSet.model_validate(raw)
        except ValidationError as exc:
            raise LLMInvalidResponse(
                "LLM output did not match the recommendation schema",
                details={"validation_errors": str(exc)[:500]},
            ) from exc
        _validate_llm_set(llm_set, input_)
        return llm_set

    def _merge(
        self,
        input_: RecommendationInput,
        candidates: list[Recommendation],
        llm_set: LLMRecommendationSet,
        warnings: list[str],
    ) -> RecommendationSet:
        by_key = {(c.setting, c.change.value): c for c in candidates}
        final: list[Recommendation] = []
        seen: set[tuple[str, str]] = set()
        for llm_rec in llm_set.recommendations:
            key = (llm_rec.setting, llm_rec.change.value)
            deterministic = by_key.get(key)
            if deterministic is not None:
                final.append(deterministic)
            else:
                category = category_for_setting(llm_rec.setting)
                assert category is not None  # allowlist membership checked during validation
                final.append(
                    Recommendation(
                        setting=llm_rec.setting,
                        category=category,
                        change=llm_rec.change,
                        current_value=llm_rec.current_value,
                        recommended_value=llm_rec.recommended_value,
                        reason=llm_rec.reason,
                        expected_benefit=llm_rec.expected_benefit,
                        tradeoff=llm_rec.tradeoff,
                        confidence=llm_rec.confidence,
                        evidence=llm_rec.evidence,
                        source=RecommendationSource.LLM,
                    )
                )
            seen.add(key)
        for candidate in candidates:
            key = (candidate.setting, candidate.change.value)
            if key not in seen:
                final.append(candidate)
                seen.add(key)
        return RecommendationSet(
            goal=input_.goal,
            recommendations=final,
            summary=llm_set.summary,
            warnings=[*warnings, *llm_set.warnings],
            mode=RecommendationMode.LLM,
        )

    def _deterministic_set(
        self,
        input_: RecommendationInput,
        candidates: list[Recommendation],
        warnings: list[str],
    ) -> RecommendationSet:
        return RecommendationSet(
            goal=input_.goal,
            recommendations=list(candidates),
            summary=(
                "Rule-based recommendations derived from measured model geometry, current "
                "slicer settings, and available slice statistics. No LLM reasoning was used."
            ),
            warnings=list(warnings),
            mode=RecommendationMode.DETERMINISTIC,
        )


def _printer_defaults(printer: ProfileInfo | None) -> _PrinterDefaults:
    """Read ``default_print_profile`` / ``default_filament_profile`` from a machine."""
    if printer is None or printer.content is None:
        return _PrinterDefaults(None, None)
    try:
        data = json.loads(printer.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _PrinterDefaults(None, None)
    if not isinstance(data, dict):
        return _PrinterDefaults(None, None)

    def _name(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) and value else None

    return _PrinterDefaults(
        process=_name("default_print_profile"),
        filament=_name("default_filament_profile"),
    )


def _facts_from_analysis(analysis: ModelAnalysis) -> ModelFacts:
    topology = analysis.topology
    orientation = analysis.orientation
    overhang = analysis.overhang
    thin_wall = analysis.thin_wall
    return ModelFacts(
        dimensions_mm=analysis.dimensions_mm,
        volume_mm3=analysis.volume_mm3,
        surface_area_mm2=analysis.surface_area_mm2,
        centroid_mm=analysis.centroid_mm,
        watertight=topology.watertight if topology is not None else None,
        component_count=topology.component_count if topology is not None else None,
        overhang_area_percent=overhang.area_percent if overhang is not None else None,
        overhang_face_count=overhang.face_count if overhang is not None else None,
        thin_wall_min_mm=thin_wall.min_mm if thin_wall is not None else None,
        thin_wall_median_mm=thin_wall.median_mm if thin_wall is not None else None,
        thin_wall_supported=thin_wall.supported if thin_wall is not None else None,
        z_alignment=orientation.z_alignment if orientation is not None else None,
        height_mm=orientation.height_mm if orientation is not None else None,
    )


def _validate_llm_set(llm_set: LLMRecommendationSet, input_: RecommendationInput) -> None:
    """Reject LLM output that invents settings, values, evidence, or goals."""
    if llm_set.goal != input_.goal:
        raise LLMInvalidResponse(
            f"LLM returned goal {llm_set.goal.value!r} instead of {input_.goal.value!r}",
            details={"expected_goal": input_.goal.value, "got_goal": llm_set.goal.value},
        )
    facts_text = build_facts_text(input_).lower()
    for rec in llm_set.recommendations:
        if rec.setting not in RECOMMENDABLE_SETTINGS:
            raise LLMInvalidResponse(
                f"LLM recommended disallowed setting {rec.setting!r}",
                details={"setting": rec.setting},
            )
        known = _known_current_value(input_, rec.setting)
        if known is None and rec.current_value is not None:
            raise LLMInvalidResponse(
                f"LLM fabricated a current value for {rec.setting!r}, which is not "
                "present in the measured facts",
                details={
                    "setting": rec.setting,
                    "current_value": rec.current_value,
                    "recommended_value": rec.recommended_value,
                },
            )
        for evidence in rec.evidence:
            if evidence.strip().lower() not in facts_text:
                raise LLMInvalidResponse(
                    "LLM cited evidence that is not in the measured facts",
                    details={"setting": rec.setting, "evidence": evidence},
                )


_SETTING_PROCESS_FIELD: dict[str, str] = {
    "layer_height_mm": "layer_height_mm",
    "wall_loops": "wall_loops",
    "sparse_infill_percent": "sparse_infill_percent",
    "sparse_infill_pattern": "sparse_infill_pattern",
    "support_enablement": "enable_support",
    "support_type": "support_type",
    "support_threshold_angle_deg": "support_threshold_angle_deg",
    "outer_wall_speed_mms": "outer_wall_speed_mms",
}


def _known_current_value(input_: RecommendationInput, setting: str) -> object | None:
    """Return the measured current value for *setting*, or None when unavailable."""
    if input_.slicer is None or input_.slicer.process is None:
        return None
    field = _SETTING_PROCESS_FIELD.get(setting)
    if field is None:
        return None
    return getattr(input_.slicer.process, field)
