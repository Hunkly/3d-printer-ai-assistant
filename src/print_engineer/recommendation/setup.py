"""Setup recommendation engine (Phase 3A.1).

Produces a four-layer setup recommendation - material, filament, nozzle,
process - from the resolved print context and the locally-installed filament
candidates.

Deterministic ranking is authoritative and computed first. An optional local
LLM may add a narrative ``summary``, but only if it is grounded in verbatim
profile facts; otherwise the engine falls back to the deterministic result.
This engine never invokes a slicer, never applies settings, and never touches
a printer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from print_engineer.core.recommendation import (
    FilamentCandidate,
    FilamentRecommendation,
    MaterialRecommendation,
    NozzleRecommendation,
    ProcessRecommendation,
    RecommendationGoal,
    RecommendationMode,
    ResolvedPrintContext,
    SetupLLMNarrative,
    SetupRecommendation,
    SetupRequest,
)
from print_engineer.errors import (
    LLMError,
    LLMInvalidResponse,
    LLMUnavailable,
    UnresolvedPrintContext,
)
from print_engineer.recommendation.context import PrintContextResolver, ResolvedContextAuthority
from print_engineer.recommendation.filament import FilamentMatrixBuilder
from print_engineer.recommendation.prompt import (
    build_setup_prompt,
    setup_grounding_lines,
)

_GOAL_HINTS: dict[RecommendationGoal, str] = {
    RecommendationGoal.SURFACE_QUALITY: (
        "prefer a finer layer height (rungs 0.12/0.16 mm), at least two walls, and an "
        "outer wall speed at or below 120 mm/s"
    ),
    RecommendationGoal.STRENGTH: (
        "prefer at least three walls, 25% gyroid/crosshatch infill, and a layer height "
        "that keeps any thin walls printable"
    ),
    RecommendationGoal.PRINT_TIME: "prefer a coarser layer height and infill at or below 15%",
    RecommendationGoal.FILAMENT_USAGE: "prefer infill at or below 10% and two walls",
    RecommendationGoal.BALANCED: "keep the printer's default process as a starting point",
}

_EXTERNAL_GOALS = frozenset({RecommendationGoal.STRENGTH, RecommendationGoal.SURFACE_QUALITY})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_grounded(narrative: SetupLLMNarrative, lines: list[str]) -> bool:
    """Require at least one verbatim (normalized) atomic fact line in the text."""
    narrative_text = _normalize(narrative.rationale + " " + narrative.summary)
    for line in lines:
        normalized = _normalize(line)
        if len(normalized) >= 12 and normalized in narrative_text:
            return True
    return False


class SetupEngine:
    """Orchestrates context resolution, candidate ranking, and narrative."""

    def __init__(
        self,
        settings: Any,
        *,
        llm: Any | None = None,
        resolver: PrintContextResolver | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._resolver = resolver or PrintContextResolver(settings)

    def recommend(self, request: SetupRequest) -> SetupRecommendation:
        resolved = self._resolver.resolve(request)
        return self._recommend_resolved(request, resolved)

    def _recommend_resolved(
        self, request: SetupRequest, resolved: ResolvedPrintContext
    ) -> SetupRecommendation:
        if resolved.printer is None:
            raise UnresolvedPrintContext(
                "a printer is required for a setup recommendation; no default was requested",
                details={"slicer_kind": request.slicer_kind},
            )

        adapter = self._resolver.adapter(request.slicer_kind)
        matrix = FilamentMatrixBuilder(self._settings, adapter).build(
            resolved,
            goal=request.goal,
            vendor=request.vendor,
            material=request.material,
        )

        warnings: list[str] = list(resolved.warnings) + list(matrix.warnings)
        material = self._material_layer(matrix.candidates, request.goal)
        filament = self._filament_layer(matrix.candidates)
        nozzle = self._nozzle_layer(resolved)
        process = self._process_layer(resolved, request)

        if nozzle is None:
            warnings.append("no nozzle information available to recommend a nozzle")
        if process is None:
            warnings.append(
                "no process profile available; the printer's default_print_profile is unset "
                "and none was requested"
            )

        mode = RecommendationMode.DETERMINISTIC
        summary = self._default_summary(request.goal, matrix.candidates)
        if self._llm is not None and request.use_llm:
            summary, mode, warnings = self._llm_narrative(
                self._llm, resolved, matrix.candidates, request.goal, warnings
            )

        return SetupRecommendation(
            goal=request.goal,
            context=resolved,
            matrix=matrix,
            material=material,
            filament=filament,
            nozzle=nozzle,
            process=process,
            mode=mode,
            summary=summary,
            warnings=warnings,
        )

    def recommend_authoritative(self, request: SetupRequest) -> AuthoritativeSetupSelection:
        """Return the deterministic recommendation with its exact source profiles."""
        authority = self._resolver.resolve_with_authority(request)
        recommendation = self._recommend_resolved(request, authority.context)
        candidate = (
            recommendation.matrix.candidates[0] if recommendation.matrix.candidates else None
        )
        return AuthoritativeSetupSelection(recommendation, authority, candidate)

    def _llm_narrative(
        self,
        llm: Any,
        resolved: ResolvedPrintContext,
        candidates: list[FilamentCandidate],
        goal: RecommendationGoal,
        warnings: list[str],
    ) -> tuple[str, RecommendationMode, list[str]]:
        prompt = build_setup_prompt(resolved, candidates, goal.value)
        grounding_lines = setup_grounding_lines(resolved, candidates)
        try:
            payload = llm.complete_json(prompt, timeout_seconds=60.0)
            narrative = SetupLLMNarrative.model_validate(payload)
            if not _is_grounded(narrative, grounding_lines):
                raise LLMInvalidResponse("setup narrative is not grounded in profile facts")
        except (LLMError, ValidationError) as exc:
            if isinstance(exc, LLMUnavailable) and not (
                self._settings.recommend.allow_deterministic_fallback
            ):
                raise
            code = getattr(exc, "code", "llm_error")
            warnings.append(f"LLM reasoning unavailable ({code}); using deterministic result")
            return (
                self._default_summary(goal, candidates),
                RecommendationMode.DETERMINISTIC,
                warnings,
            )

        warnings.extend(narrative.warnings)
        return narrative.summary, RecommendationMode.LLM, warnings

    def _default_summary(
        self, goal: RecommendationGoal, candidates: list[FilamentCandidate]
    ) -> str:
        if not candidates:
            return f"No compatible filament candidates for goal {goal.value}."
        top = candidates[0]
        parts = [
            f"Deterministic ranking for {goal.value}: best candidate is "
            f"{top.profile_name} (score {top.score:.1f})."
        ]
        if top.vendor:
            parts.append(f"Vendor is {top.vendor} (verified={top.vendor_verified}).")
        if top.data_warnings:
            parts.append("Candidate has data warnings: " + "; ".join(top.data_warnings) + ".")
        return " ".join(parts)

    def _material_layer(
        self, candidates: list[FilamentCandidate], goal: RecommendationGoal
    ) -> MaterialRecommendation | None:
        if not candidates:
            return None
        top = candidates[0]
        alternatives: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            material = candidate.material_type
            if material and material != top.material_type and material not in seen:
                seen.add(material)
                alternatives.append(material)

        external = goal in _EXTERNAL_GOALS
        rationale = (
            f"Highest-ranked material for goal {goal.value} is {top.material_type} "
            f"({top.profile_name}). "
        )
        if external:
            rationale += (
                "Local profiles carry no numeric strength or surface-quality data, so "
                "material suitability must be verified externally before final use."
            )
        else:
            rationale += "Ranking is based on numeric profile values (speed, density, cost)."
        return MaterialRecommendation(
            material_type=top.material_type or "unknown",
            rationale=rationale,
            requires_external_evidence=external,
            alternatives=alternatives,
        )

    def _filament_layer(self, candidates: list[FilamentCandidate]) -> FilamentRecommendation | None:
        if not candidates:
            return None
        top = candidates[0]
        evidence = ", ".join(f"{field}={value}" for field, value in top.goal_scores.items())
        rationale = (
            f"Ranked first for the goal with score {top.score:.1f}"
            + (f" ({evidence})" if evidence else "")
            + "."
        )
        if not top.vendor_verified:
            rationale += " Vendor is not verified; treat brand-specific claims with care."
        return FilamentRecommendation(
            profile_name=top.profile_name,
            vendor=top.vendor,
            vendor_verified=top.vendor_verified,
            score=top.score,
            rationale=rationale,
            requires_external_evidence=top.requires_external_evidence,
            alternatives=candidates[1:],
        )

    def _nozzle_layer(self, resolved: ResolvedPrintContext) -> NozzleRecommendation | None:
        printer = resolved.printer
        if printer is None:
            return None
        supported = printer.supported_nozzle_mm
        chosen = resolved.nozzle_diameter_mm
        if chosen is None and printer.nozzle_diameter_mm is not None:
            chosen = printer.nozzle_diameter_mm
        if chosen is None:
            if 0.4 in supported:
                chosen = 0.4
            elif len(supported) == 1:
                chosen = supported[0]
        if chosen is None:
            return None
        alternatives = [nozzle for nozzle in supported if abs(nozzle - chosen) > 1e-6]
        if resolved.nozzle_diameter_mm is not None:
            rationale = f"nozzle {chosen:g} mm selected by the user"
        elif printer.nozzle_diameter_mm is not None:
            rationale = f"nozzle {chosen:g} mm from the resolved printer profile"
        elif chosen == 0.4:
            rationale = "no nozzle specified; using the standard 0.4 mm nozzle"
        else:
            rationale = f"no nozzle specified; using {chosen:g} mm from the printer's supported set"
        return NozzleRecommendation(
            nozzle_diameter_mm=chosen,
            supported=supported,
            rationale=rationale,
            alternatives=alternatives,
        )

    def _process_layer(
        self, resolved: ResolvedPrintContext, request: SetupRequest
    ) -> ProcessRecommendation | None:
        process = resolved.process
        if process is not None:
            name = process.name
            source = "user_selected" if request.process_profile is not None else "printer_default"
            key_settings: dict[str, float | str | bool | None] = {
                key: value
                for key, value in process.model_dump().items()
                if key != "name" and value is not None
            }
        elif resolved.printer is not None and resolved.printer.default_print_profile:
            name = resolved.printer.default_print_profile
            source = "printer_default"
            key_settings = {}
        else:
            return None
        rationale = "process profile " + (
            "selected by the user"
            if source == "user_selected"
            else "taken from the printer default"
        )
        return ProcessRecommendation(
            process_profile=name,
            source=source,
            rationale=rationale,
            key_settings=key_settings,
            goal_hint=_GOAL_HINTS.get(request.goal),
        )


@dataclass(frozen=True)
class AuthoritativeSetupSelection:
    recommendation: SetupRecommendation
    context_authority: ResolvedContextAuthority
    filament_candidate: FilamentCandidate | None
