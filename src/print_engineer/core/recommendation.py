"""Recommendation domain types (Phase 3A).

The recommendation engine is read-only: it never modifies slicer profiles, the
input model, or the printer. Everything in this module is a value object; the
engine exposes no write/apply methods.

``RecommendationSet`` is the validated output contract. ``LLMRecommendationSet``
is the intermediate schema the local LLM is asked to fill in; it is validated
strictly before being merged with the deterministic rule results.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class RecommendationGoal(StrEnum):
    SURFACE_QUALITY = "surface_quality"
    STRENGTH = "strength"
    PRINT_TIME = "print_time"
    FILAMENT_USAGE = "filament_usage"
    BALANCED = "balanced"


class RecommendationCategory(StrEnum):
    LAYER_HEIGHT = "layer_height"
    WALLS = "walls"
    INFILL = "infill"
    SUPPORTS = "supports"
    SPEED = "speed"


class ChangeDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    ENABLE = "enable"
    DISABLE = "disable"
    SET = "set"


class RecommendationSource(StrEnum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


class RecommendationMode(StrEnum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


# Settings the recommendation engine may suggest. Anything else (temperatures,
# flow, calibration, hardware tuning) is deliberately excluded.
RECOMMENDABLE_SETTINGS: frozenset[str] = frozenset(
    {
        "layer_height_mm",
        "wall_loops",
        "sparse_infill_percent",
        "sparse_infill_pattern",
        "support_enablement",
        "support_type",
        "support_threshold_angle_deg",
        "outer_wall_speed_mms",
    }
)

_SETTING_CATEGORY: dict[str, RecommendationCategory] = {
    "layer_height_mm": RecommendationCategory.LAYER_HEIGHT,
    "wall_loops": RecommendationCategory.WALLS,
    "sparse_infill_percent": RecommendationCategory.INFILL,
    "sparse_infill_pattern": RecommendationCategory.INFILL,
    "support_enablement": RecommendationCategory.SUPPORTS,
    "support_type": RecommendationCategory.SUPPORTS,
    "support_threshold_angle_deg": RecommendationCategory.SUPPORTS,
    "outer_wall_speed_mms": RecommendationCategory.SPEED,
}


def category_for_setting(setting: str) -> RecommendationCategory | None:
    """Return the category for a recommendation *setting*, if recommendable."""
    return _SETTING_CATEGORY.get(setting)


class PrinterSettings(BaseModel):
    name: str
    nozzle_diameter_mm: float | None = None
    printable_height_mm: float | None = None
    printer_model: str | None = None
    printer_variant: str | None = None
    max_acceleration_mm_s2: float | None = None


class ProcessSettings(BaseModel):
    name: str
    layer_height_mm: float | None = None
    initial_layer_height_mm: float | None = None
    line_width_mm: float | None = None
    wall_loops: int | None = None
    wall_generator: str | None = None
    sparse_infill_percent: float | None = None
    sparse_infill_pattern: str | None = None
    top_shell_layers: int | None = None
    top_shell_thickness_mm: float | None = None
    bottom_shell_layers: int | None = None
    bottom_shell_thickness_mm: float | None = None
    enable_support: bool | None = None
    support_type: str | None = None
    support_threshold_angle_deg: float | None = None
    support_on_build_plate_only: bool | None = None
    outer_wall_speed_mms: float | None = None
    inner_wall_speed_mms: float | None = None
    top_surface_speed_mms: float | None = None
    sparse_infill_speed_mms: float | None = None
    initial_layer_speed_mms: float | None = None
    detect_thin_wall: bool | None = None
    spiral_mode: bool | None = None
    adaptive_layer_height: bool | None = None
    brim_width_mm: float | None = None


class FilamentSettings(BaseModel):
    name: str
    material_type: str | None = None
    density_g_cm3: float | None = None
    max_volumetric_speed: float | None = None
    flow_ratio: float | None = None
    vendor: str | None = None


class SlicerSettingsDigest(BaseModel):
    """Typed, unit-labeled subset of the current slicer configuration."""

    slicer_kind: str
    process: ProcessSettings | None = None
    filament: FilamentSettings | None = None
    printer: PrinterSettings | None = None
    unavailable: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SliceStatistics(BaseModel):
    """Measured statistics from a slice (time, filament, layers)."""

    available: bool = False
    estimated_time_minutes: float | None = None
    layer_count: int | None = None
    filament_used_mm: float | None = None
    filament_used_cm3: float | None = None
    filament_weight_g: float | None = None


class ModelFacts(BaseModel):
    """Condensed, measured model facts extracted from ``ModelAnalysis``."""

    dimensions_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    volume_mm3: float | None = None
    surface_area_mm2: float = 0.0
    centroid_mm: tuple[float, float, float] | None = None
    watertight: bool | None = None
    component_count: int | None = None
    overhang_area_percent: float | None = None
    overhang_face_count: int | None = None
    thin_wall_min_mm: float | None = None
    thin_wall_median_mm: float | None = None
    thin_wall_supported: bool | None = None
    z_alignment: float | None = None
    height_mm: float | None = None
    notes: list[str] = Field(default_factory=list)


class RecommendationInput(BaseModel):
    """Everything the engine knows before recommending."""

    goal: RecommendationGoal
    model: ModelFacts
    slicer: SlicerSettingsDigest | None = None
    slice_stats: SliceStatistics | None = None
    max_time_minutes: float | None = None
    max_filament_g: float | None = None


class RecommendationRequest(BaseModel):
    """What a caller (CLI / MCP) asks the engine to do."""

    model_path: Path
    slicer_kind: str = "orca_slicer"
    process_profile: str | None = None
    filament_profile: str | None = None
    printer_profile: str | None = None
    goal: RecommendationGoal = RecommendationGoal.BALANCED
    overhang_threshold_degrees: float = Field(default=45.0, gt=0.0, le=90.0)
    max_time_minutes: float | None = Field(default=None, ge=0.0)
    max_filament_g: float | None = Field(default=None, ge=0.0)
    slice_on_demand: bool = False


class Recommendation(BaseModel):
    """A single validated recommendation with full explainability."""

    setting: str
    category: RecommendationCategory
    change: ChangeDirection
    current_value: float | str | None = None
    recommended_value: float | str | None = None
    reason: str
    expected_benefit: str = ""
    tradeoff: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    source: RecommendationSource = RecommendationSource.DETERMINISTIC


class RecommendationSet(BaseModel):
    """Validated output of the recommendation engine."""

    goal: RecommendationGoal
    recommendations: list[Recommendation]
    summary: str
    warnings: list[str] = Field(default_factory=list)
    mode: RecommendationMode = RecommendationMode.DETERMINISTIC


class LLMRecommendation(BaseModel):
    """Recommendation shape the LLM is allowed to produce."""

    setting: str
    change: ChangeDirection
    current_value: float | str | None = None
    recommended_value: float | str | None = None
    reason: str
    expected_benefit: str = ""
    tradeoff: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class LLMRecommendationSet(BaseModel):
    """Structured output expected from the LLM, validated strictly."""

    goal: RecommendationGoal
    summary: str
    recommendations: list[LLMRecommendation]
    warnings: list[str] = Field(default_factory=list)


class PrintContextIntent(BaseModel):
    """What a caller knows about the printing environment before resolution.

    ``printer`` accepts either an exact machine-profile name (e.g.
    ``"Bambu Lab A1 0.4 nozzle"``) or a printer model name (e.g.
    ``"Bambu Lab A1"``). Resolution never falls back to a generic default:
    a printer that cannot be found raises ``UnresolvedPrintContext`` unless
    ``use_defaults`` is explicitly set.
    """

    slicer_kind: str = "orca_slicer"
    printer: str | None = None
    nozzle_diameter_mm: float | None = Field(default=None, ge=0.05, le=2.0)
    build_plate: str | None = None
    process_profile: str | None = None
    filament_profile: str | None = None
    use_defaults: bool = False


class SetupRequest(PrintContextIntent):
    """Request for a four-layer setup recommendation (Phase 3A.1).

    Adds the goal plus optional vendor/material filters to the print-context
    intent. Resolution semantics are identical to ``PrintContextIntent``: no
    silent generic fallback.
    """

    goal: RecommendationGoal = RecommendationGoal.BALANCED
    vendor: str | None = None
    material: str | None = None
    use_llm: bool = True


class ResolvedPrinter(BaseModel):
    """A printer resolved against the local slicer machine-profile store."""

    name: str
    printer_model: str | None = None
    printer_variant: str | None = None
    nozzle_diameter_mm: float | None = None
    supported_nozzle_mm: list[float] = Field(default_factory=list)
    printable_height_mm: float | None = None
    default_print_profile: str | None = None
    default_filament_profiles: list[str] = Field(default_factory=list)


class ResolvedPrintContext(BaseModel):
    """The actual printing context, or the parts of it that are known.

    ``printer`` is None when the caller did not select a printer and
    ``use_defaults`` is off (never a silent generic default). Missing
    ``nozzle_diameter_mm`` is a recommendation dimension, not an error.
    """

    slicer_kind: str
    printer: ResolvedPrinter | None = None
    process: ProcessSettings | None = None
    filament: FilamentSettings | None = None
    nozzle_diameter_mm: float | None = None
    build_plate: str | None = None
    warnings: list[str] = Field(default_factory=list)


class FilamentCandidate(BaseModel):
    """A locally-installed filament profile with vendor/consistency metadata.

    ``vendor_verified`` is False whenever the vendor could not be established
    from the profile's own document (e.g. it was inherited from another
    vendor's profile). ``data_warnings`` surface internally inconsistent
    values. ``requires_external_evidence`` marks claims that rely on
    real-world knowledge rather than numbers present in the profile store.
    """

    profile_name: str
    setting_id: str | None = None
    vendor: str | None = None
    vendor_verified: bool = False
    material_type: str | None = None
    density_g_cm3: float | None = None
    max_volumetric_speed: float | None = None
    flow_ratio: float | None = None
    cost_per_kg: float | None = None
    required_nozzle_hrc: str | None = None
    diameter_mm: float | None = None
    shrinkage: float | None = None
    soluble: bool | None = None
    nozzle_temperature_c: float | None = None
    nozzle_temperature_range_low_c: float | None = None
    nozzle_temperature_range_high_c: float | None = None
    nozzle_temperature_initial_layer_c: float | None = None
    hot_plate_temperature_c: float | None = None
    textured_plate_temperature_c: float | None = None
    cool_plate_temperature_c: float | None = None
    fan_max_speed: float | None = None
    fan_min_speed: float | None = None
    fan_cooling_layer_time: float | None = None
    close_fan_the_first_x_layers: float | None = None
    overhang_fan_speed: float | None = None
    compatible_printers: list[str] = Field(default_factory=list)
    data_warnings: list[str] = Field(default_factory=list)
    goal_scores: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    requires_external_evidence: bool = False
    notes: list[str] = Field(default_factory=list)


class RejectedFilamentCandidate(BaseModel):
    """Why a locally-installed filament profile was excluded."""

    profile_name: str
    vendor: str | None = None
    material_type: str | None = None
    reason_code: str
    reason: str


class FilamentCandidateMatrix(BaseModel):
    """Enumerated filament candidates with deterministic ranking.

    ``candidates`` are ranked (best first) for ``goal``. ``rejected`` explains
    every exclusion (incompatible printer/nozzle/plate, filters, data errors).
    No slicing is ever performed to build this matrix.
    """

    slicer_kind: str
    printer: ResolvedPrinter | None = None
    goal: RecommendationGoal = RecommendationGoal.BALANCED
    nozzle_diameter_mm: float | None = None
    build_plate: str | None = None
    candidates: list[FilamentCandidate] = Field(default_factory=list)
    rejected: list[RejectedFilamentCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MaterialRecommendation(BaseModel):
    """Layer 1: which material family to print with."""

    material_type: str
    rationale: str
    requires_external_evidence: bool = False
    alternatives: list[str] = Field(default_factory=list)


class FilamentRecommendation(BaseModel):
    """Layer 2: the specific local filament profile to use."""

    profile_name: str
    vendor: str | None = None
    vendor_verified: bool = False
    score: float = 0.0
    rationale: str
    requires_external_evidence: bool = False
    alternatives: list[FilamentCandidate] = Field(default_factory=list)


class NozzleRecommendation(BaseModel):
    """Layer 3: which nozzle diameter to use."""

    nozzle_diameter_mm: float
    supported: list[float] = Field(default_factory=list)
    rationale: str = ""
    alternatives: list[float] = Field(default_factory=list)


class ProcessRecommendation(BaseModel):
    """Layer 4: which process (print) profile to use."""

    process_profile: str
    source: str
    rationale: str
    key_settings: dict[str, float | str | bool | None] = Field(default_factory=dict)
    goal_hint: str | None = None


class SetupLLMNarrative(BaseModel):
    """Optional LLM narrative merged into a SetupRecommendation."""

    summary: str
    rationale: str
    warnings: list[str] = Field(default_factory=list)


class SetupRecommendation(BaseModel):
    """Four-layer setup recommendation (material, filament, nozzle, process).

    Deterministic ranking is authoritative; the LLM narrative (when enabled)
    is validated against verbatim candidate facts and dropped on failure.
    """

    goal: RecommendationGoal
    context: ResolvedPrintContext
    matrix: FilamentCandidateMatrix
    material: MaterialRecommendation | None = None
    filament: FilamentRecommendation | None = None
    nozzle: NozzleRecommendation | None = None
    process: ProcessRecommendation | None = None
    mode: RecommendationMode = RecommendationMode.DETERMINISTIC
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
