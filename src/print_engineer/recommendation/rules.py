"""Deterministic rule-based recommendations (Phase 3A).

Rules convert measured facts (model geometry + current slicer settings + slice
statistics) into structured :class:`Recommendation` candidates without any LLM
involvement. Every value a rule recommends is computed from a known current
value against a small fixed ladder/step table.

Precedence: geometry/safety rules > hard constraints (max time / max filament) >
goal-based rules. When two phases would change the same setting in opposite
directions, the higher-precedence candidate wins and a warning is recorded.

Deterministic recommendations carry ``source="deterministic"`` and
``confidence=None`` (no statistical basis is claimed).
"""

from __future__ import annotations

from dataclasses import dataclass

from print_engineer.core.recommendation import (
    ChangeDirection,
    ModelFacts,
    ProcessSettings,
    Recommendation,
    RecommendationCategory,
    RecommendationGoal,
    RecommendationInput,
    RecommendationSource,
)

_DEFAULT_RUNGS = (0.08, 0.12, 0.16, 0.20, 0.24, 0.28)


@dataclass(frozen=True)
class RuleConfig:
    overhang_percent_threshold: float = 10.0
    thin_wall_min_ratio: float = 2.5
    quality_outer_wall_speed_mms: float = 120.0
    strength_wall_loops: int = 3
    strength_infill_percent: float = 25.0
    time_infill_percent: float = 15.0
    filament_infill_percent: float = 10.0
    layer_height_rungs: tuple[float, ...] = _DEFAULT_RUNGS


def _next_lower(current: float, rungs: tuple[float, ...]) -> float | None:
    below = [rung for rung in rungs if rung < current - 1e-9]
    return below[-1] if below else None


def _next_higher(current: float, rungs: tuple[float, ...]) -> float | None:
    above = [rung for rung in rungs if rung > current + 1e-9]
    return above[0] if above else None


def _make_rec(
    setting: str,
    category: RecommendationCategory,
    change: ChangeDirection,
    current_value: float | str | None,
    recommended_value: float | str | None,
    reason: str,
    expected_benefit: str,
    tradeoff: str,
    evidence: list[str],
) -> Recommendation:
    return Recommendation(
        setting=setting,
        category=category,
        change=change,
        current_value=current_value,
        recommended_value=recommended_value,
        reason=reason,
        expected_benefit=expected_benefit,
        tradeoff=tradeoff,
        confidence=None,
        evidence=evidence,
        source=RecommendationSource.DETERMINISTIC,
    )


class _Builder:
    """Collects recommendations with phase precedence and conflict tracking."""

    def __init__(self) -> None:
        self.recommendations: list[Recommendation] = []
        self.conflicts: list[str] = []
        self._by_setting_change: dict[tuple[str, str], Recommendation] = {}
        self._by_setting: dict[str, Recommendation] = {}

    def add(self, rec: Recommendation) -> None:
        key = (rec.setting, rec.change.value)
        if key in self._by_setting_change:
            return
        existing = self._by_setting.get(rec.setting)
        if existing is not None and existing.change.value != rec.change.value:
            self.conflicts.append(rec.setting)
            return
        self._by_setting_change[key] = rec
        self._by_setting[rec.setting] = rec
        self.recommendations.append(rec)


def _geometry_warnings(model: ModelFacts) -> list[str]:
    warnings: list[str] = []
    if model.watertight is False:
        warnings.append("model is not watertight; volume and thin-wall estimates are unavailable")
    if model.thin_wall_supported is False:
        warnings.append("thin-wall estimate unavailable (mesh is not watertight)")
    if model.component_count is not None and model.component_count > 1:
        warnings.append(
            f"model has {model.component_count} separate components; verify plate adhesion "
            "and supports per component"
        )
    if model.overhang_area_percent is None:
        warnings.append("overhang analysis unavailable; support recommendations limited")
    return warnings


def _thin_wall_rule(
    builder: _Builder,
    model: ModelFacts,
    process: ProcessSettings | None,
    config: RuleConfig,
) -> None:
    if not model.thin_wall_supported or model.thin_wall_min_mm is None:
        return
    if process is None or process.layer_height_mm is None:
        return
    min_wall = model.thin_wall_min_mm
    layer_height = process.layer_height_mm
    if min_wall >= config.thin_wall_min_ratio * layer_height:
        return
    lower = _next_lower(layer_height, config.layer_height_rungs)
    if lower is None:
        return
    builder.add(
        _make_rec(
            setting="layer_height_mm",
            category=RecommendationCategory.LAYER_HEIGHT,
            change=ChangeDirection.DECREASE,
            current_value=layer_height,
            recommended_value=lower,
            reason=(
                f"the model has thin walls (min {min_wall:.2f} mm) below "
                f"{config.thin_wall_min_ratio:.1f}x the current layer height "
                f"({layer_height:.2f} mm), which risks gaps between perimeters"
            ),
            expected_benefit="finer layers improve perimeter continuity in thin walls",
            tradeoff="print time increases with more layers",
            evidence=[
                f"Minimum wall thickness = {min_wall:.2f} mm",
                f"Current layer height = {layer_height:.2f} mm",
            ],
        )
    )


def _overhang_rule(
    builder: _Builder,
    model: ModelFacts,
    process: ProcessSettings | None,
    config: RuleConfig,
) -> None:
    percent = model.overhang_area_percent
    if percent is None or percent < config.overhang_percent_threshold:
        return
    if process is None:
        return
    if process.enable_support is None:
        return
    if process.enable_support:
        return
    face_count = model.overhang_face_count
    evidence = [f"Overhang area = {percent:.1f}% of surface area"]
    if face_count is not None:
        evidence.append(f"Overhang faces = {face_count}")
    builder.add(
        _make_rec(
            setting="support_enablement",
            category=RecommendationCategory.SUPPORTS,
            change=ChangeDirection.ENABLE,
            current_value="off",
            recommended_value="on",
            reason=(
                f"about {percent:.0f}% of the surface overhangs beyond the "
                f"{config.overhang_percent_threshold:.0f}% threshold and supports are disabled"
            ),
            expected_benefit="supports prevent downward faces from sagging or failing",
            tradeoff="more material and print time, plus support removal afterward",
            evidence=evidence,
        )
    )


def _constraint_rules(
    builder: _Builder,
    input_: RecommendationInput,
    process: ProcessSettings | None,
    config: RuleConfig,
) -> None:
    stats = input_.slice_stats

    def measured_time() -> float | None:
        if stats is not None and stats.available:
            return stats.estimated_time_minutes
        return None

    def measured_weight() -> float | None:
        if stats is not None and stats.available:
            return stats.filament_weight_g
        return None

    if input_.max_time_minutes is not None:
        estimated = measured_time()
        if estimated is not None and estimated > input_.max_time_minutes:
            if process is not None and process.layer_height_mm is not None:
                higher = _next_higher(process.layer_height_mm, config.layer_height_rungs)
                if higher is not None:
                    builder.add(
                        _make_rec(
                            setting="layer_height_mm",
                            category=RecommendationCategory.LAYER_HEIGHT,
                            change=ChangeDirection.INCREASE,
                            current_value=process.layer_height_mm,
                            recommended_value=higher,
                            reason=(
                                f"estimated print time ({estimated:.1f} min) exceeds the "
                                f"requested maximum ({input_.max_time_minutes:.1f} min)"
                            ),
                            expected_benefit="fewer layers reduce print time",
                            tradeoff="coarser surface finish",
                            evidence=[
                                f"Measured print time = {estimated:.1f} min",
                                "Requested maximum print time = "
                                f"{input_.max_time_minutes:.1f} min",
                            ],
                        )
                    )
            if process is not None and process.wall_loops is not None and process.wall_loops > 2:
                builder.add(
                    _make_rec(
                        setting="wall_loops",
                        category=RecommendationCategory.WALLS,
                        change=ChangeDirection.DECREASE,
                        current_value=process.wall_loops,
                        recommended_value=2,
                        reason="reducing wall count cuts per-outer-loop travel and material",
                        expected_benefit="shorter print time",
                        tradeoff="slightly weaker outer shell",
                        evidence=[
                            f"Wall count = {process.wall_loops}",
                            f"Measured print time = {estimated:.1f} min",
                        ],
                    )
                )
            if (
                process is not None
                and process.sparse_infill_percent is not None
                and process.sparse_infill_percent > config.time_infill_percent
            ):
                builder.add(
                    _make_rec(
                        setting="sparse_infill_percent",
                        category=RecommendationCategory.INFILL,
                        change=ChangeDirection.DECREASE,
                        current_value=process.sparse_infill_percent,
                        recommended_value=config.time_infill_percent,
                        reason=(
                            f"lower infill from {process.sparse_infill_percent:.0f}% to "
                            f"{config.time_infill_percent:.0f}% shortens the print"
                        ),
                        expected_benefit="shorter print time and less material",
                        tradeoff="weaker internal structure",
                        evidence=[
                            f"Infill density = {process.sparse_infill_percent:.0f}%",
                            f"Measured print time = {estimated:.1f} min",
                        ],
                    )
                )

    if input_.max_filament_g is not None:
        weight = measured_weight()
        if weight is not None and weight > input_.max_filament_g:
            if (
                process is not None
                and process.sparse_infill_percent is not None
                and process.sparse_infill_percent > config.filament_infill_percent
            ):
                builder.add(
                    _make_rec(
                        setting="sparse_infill_percent",
                        category=RecommendationCategory.INFILL,
                        change=ChangeDirection.DECREASE,
                        current_value=process.sparse_infill_percent,
                        recommended_value=config.filament_infill_percent,
                        reason=(
                            f"measured filament weight ({weight:.1f} g) exceeds the "
                            f"requested maximum ({input_.max_filament_g:.1f} g)"
                        ),
                        expected_benefit="less filament consumed",
                        tradeoff="weaker internal structure",
                        evidence=[
                            f"Measured filament weight = {weight:.1f} g",
                            f"Infill density = {process.sparse_infill_percent:.0f}%",
                        ],
                    )
                )
            if process is not None and process.wall_loops is not None and process.wall_loops > 2:
                builder.add(
                    _make_rec(
                        setting="wall_loops",
                        category=RecommendationCategory.WALLS,
                        change=ChangeDirection.DECREASE,
                        current_value=process.wall_loops,
                        recommended_value=2,
                        reason="fewer walls reduce filament usage",
                        expected_benefit="less filament consumed",
                        tradeoff="slightly weaker outer shell",
                        evidence=[
                            f"Wall count = {process.wall_loops}",
                            f"Measured filament weight = {weight:.1f} g",
                        ],
                    )
                )


def _goal_rules(
    builder: _Builder,
    input_: RecommendationInput,
    process: ProcessSettings | None,
    config: RuleConfig,
) -> None:
    if process is None:
        return
    layer_height = process.layer_height_mm
    walls = process.wall_loops
    infill = process.sparse_infill_percent
    goal = input_.goal

    if goal == RecommendationGoal.SURFACE_QUALITY:
        if layer_height is not None:
            lower = _next_lower(layer_height, config.layer_height_rungs)
            if lower is not None:
                builder.add(
                    _make_rec(
                        setting="layer_height_mm",
                        category=RecommendationCategory.LAYER_HEIGHT,
                        change=ChangeDirection.DECREASE,
                        current_value=layer_height,
                        recommended_value=lower,
                        reason="the surface-quality goal favors a finer layer height",
                        expected_benefit="smoother surfaces and finer details",
                        tradeoff="print time increases",
                        evidence=[f"Current layer height = {layer_height:.2f} mm"],
                    )
                )
        if walls is not None and walls < 2:
            builder.add(
                _make_rec(
                    setting="wall_loops",
                    category=RecommendationCategory.WALLS,
                    change=ChangeDirection.INCREASE,
                    current_value=walls,
                    recommended_value=2,
                    reason="at least two walls prevent surface defects on thin shells",
                    expected_benefit="cleaner outer surfaces",
                    tradeoff="marginally more material",
                    evidence=[f"Wall count = {walls}"],
                )
            )
        speed = process.outer_wall_speed_mms
        if speed is not None and speed > config.quality_outer_wall_speed_mms:
            builder.add(
                _make_rec(
                    setting="outer_wall_speed_mms",
                    category=RecommendationCategory.SPEED,
                    change=ChangeDirection.DECREASE,
                    current_value=speed,
                    recommended_value=config.quality_outer_wall_speed_mms,
                    reason=(
                        f"outer wall speed above {config.quality_outer_wall_speed_mms:.0f} "
                        "mm/s degrades surface finish"
                    ),
                    expected_benefit="smoother exterior walls",
                    tradeoff="slightly longer print time",
                    evidence=[f"Current outer wall speed = {speed:.0f} mm/s"],
                )
            )

    elif goal == RecommendationGoal.STRENGTH:
        if walls is not None and walls < config.strength_wall_loops:
            builder.add(
                _make_rec(
                    setting="wall_loops",
                    category=RecommendationCategory.WALLS,
                    change=ChangeDirection.INCREASE,
                    current_value=walls,
                    recommended_value=config.strength_wall_loops,
                    reason=f"the strength goal favors at least {config.strength_wall_loops} walls",
                    expected_benefit="stiffer, more impact-resistant shell",
                    tradeoff="more material and time",
                    evidence=[f"Wall count = {walls}"],
                )
            )
        if infill is not None and infill < config.strength_infill_percent:
            builder.add(
                _make_rec(
                    setting="sparse_infill_percent",
                    category=RecommendationCategory.INFILL,
                    change=ChangeDirection.INCREASE,
                    current_value=infill,
                    recommended_value=config.strength_infill_percent,
                    reason=f"the strength goal favors {config.strength_infill_percent:.0f}% infill",
                    expected_benefit="stronger internal structure",
                    tradeoff="more material and time",
                    evidence=[f"Infill density = {infill:.0f}%"],
                )
            )
        pattern = process.sparse_infill_pattern
        if pattern is not None and pattern not in ("gyroid", "crosshatch"):
            builder.add(
                _make_rec(
                    setting="sparse_infill_pattern",
                    category=RecommendationCategory.INFILL,
                    change=ChangeDirection.SET,
                    current_value=pattern,
                    recommended_value="gyroid",
                    reason="gyroid infill distributes load in all directions for the strength goal",
                    expected_benefit="stronger, more isotropic internal structure",
                    tradeoff="slightly slower infill printing",
                    evidence=[f"Infill pattern = {pattern}"],
                )
            )

    elif goal == RecommendationGoal.PRINT_TIME:
        if layer_height is not None:
            higher = _next_higher(layer_height, config.layer_height_rungs)
            if higher is not None:
                builder.add(
                    _make_rec(
                        setting="layer_height_mm",
                        category=RecommendationCategory.LAYER_HEIGHT,
                        change=ChangeDirection.INCREASE,
                        current_value=layer_height,
                        recommended_value=higher,
                        reason="the print-time goal favors a coarser layer height",
                        expected_benefit="fewer layers reduce print time",
                        tradeoff="coarser surface finish",
                        evidence=[f"Current layer height = {layer_height:.2f} mm"],
                    )
                )
        if infill is not None and infill > config.time_infill_percent:
            builder.add(
                _make_rec(
                    setting="sparse_infill_percent",
                    category=RecommendationCategory.INFILL,
                    change=ChangeDirection.DECREASE,
                    current_value=infill,
                    recommended_value=config.time_infill_percent,
                    reason="lower infill prints faster",
                    expected_benefit="shorter print time",
                    tradeoff="weaker internal structure",
                    evidence=[f"Infill density = {infill:.0f}%"],
                )
            )
        if walls is not None and walls > 2:
            builder.add(
                _make_rec(
                    setting="wall_loops",
                    category=RecommendationCategory.WALLS,
                    change=ChangeDirection.DECREASE,
                    current_value=walls,
                    recommended_value=2,
                    reason="fewer walls reduce per-layer travel",
                    expected_benefit="shorter print time",
                    tradeoff="slightly weaker shell",
                    evidence=[f"Wall count = {walls}"],
                )
            )

    elif goal == RecommendationGoal.FILAMENT_USAGE:
        if infill is not None and infill > config.filament_infill_percent:
            builder.add(
                _make_rec(
                    setting="sparse_infill_percent",
                    category=RecommendationCategory.INFILL,
                    change=ChangeDirection.DECREASE,
                    current_value=infill,
                    recommended_value=config.filament_infill_percent,
                    reason="lower infill uses less filament",
                    expected_benefit="less filament consumed",
                    tradeoff="weaker internal structure",
                    evidence=[f"Infill density = {infill:.0f}%"],
                )
            )
        if walls is not None and walls > 2:
            builder.add(
                _make_rec(
                    setting="wall_loops",
                    category=RecommendationCategory.WALLS,
                    change=ChangeDirection.DECREASE,
                    current_value=walls,
                    recommended_value=2,
                    reason="fewer walls use less filament",
                    expected_benefit="less filament consumed",
                    tradeoff="slightly weaker shell",
                    evidence=[f"Wall count = {walls}"],
                )
            )


def evaluate(
    input_: RecommendationInput, config: RuleConfig | None = None
) -> tuple[list[Recommendation], list[str]]:
    """Run deterministic rules and return (recommendations, warnings)."""
    config = config or RuleConfig()
    warnings = _geometry_warnings(input_.model)
    process = input_.slicer.process if input_.slicer is not None else None
    if input_.slicer is None or process is None:
        warnings.append("no current process settings available; goal-based recommendations skipped")

    if input_.max_time_minutes is not None and (
        input_.slice_stats is None
        or not input_.slice_stats.available
        or input_.slice_stats.estimated_time_minutes is None
    ):
        warnings.append("max-time constraint requested but measured print time is unavailable")
    if input_.max_filament_g is not None and (
        input_.slice_stats is None
        or not input_.slice_stats.available
        or input_.slice_stats.filament_weight_g is None
    ):
        warnings.append(
            "max-filament constraint requested but measured filament weight is unavailable"
        )

    builder = _Builder()
    _thin_wall_rule(builder, input_.model, process, config)
    _overhang_rule(builder, input_.model, process, config)
    _constraint_rules(builder, input_, process, config)
    _goal_rules(builder, input_, process, config)

    warnings.extend(
        f"conflicting recommendations for {setting}; higher-priority rule applied"
        for setting in builder.conflicts
    )
    return builder.recommendations, warnings
