"""Shared data types used across the project."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class PrinterState(StrEnum):
    OFFLINE = "offline"
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    ERROR = "error"
    UNKNOWN = "unknown"


class PrintOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SlicerKind(StrEnum):
    BAMBU_STUDIO = "bambu_studio"
    ORCA_SLICER = "orca_slicer"


class ProfileKind(StrEnum):
    PROCESS = "process"
    FILAMENT = "filament"
    PRINTER = "printer"


class ProfileSource(StrEnum):
    SYSTEM = "system"
    USER = "user"
    GENERATED = "generated"


@dataclass(frozen=True)
class AMSInfo:
    is_connected: bool = False
    slots: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PrinterStatus:
    state: PrinterState = PrinterState.UNKNOWN
    is_connected: bool = False
    bed_temp: float | None = None
    nozzle_temp: float | None = None
    target_bed_temp: float | None = None
    target_nozzle_temp: float | None = None
    progress: float | None = None
    ams: AMSInfo | None = None
    current_layer: int | None = None
    total_layers: int | None = None
    remaining_time_minutes: int | None = None


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    image: bytes
    source: str


@dataclass(frozen=True)
class TemperatureSetpoint:
    component: str
    target_celsius: float


@dataclass(frozen=True)
class ProfileInfo:
    """A slicer process/filament/printer profile.

    ``content`` holds the raw JSON document. For discovered (non-materialized)
    profiles it is the on-disk JSON; materialized profiles carry a fully
    resolved, self-contained JSON with ``type``/``from``/``name`` fixed up.
    """

    name: str
    kind: ProfileKind
    path: Path | None = None
    content: str | None = None
    source: ProfileSource = ProfileSource.SYSTEM
    setting_id: str | None = None
    printer_model: str | None = None
    printer_variant: str | None = None
    compatible_printers: tuple[str, ...] = ()
    inherits: str | None = None
    materialized: bool = False


@dataclass(frozen=True)
class SlicerInfo:
    """Detected slicer installation and capabilities."""

    kind: SlicerKind
    name: str
    executable: Path
    version: str | None = None
    version_source: str | None = None
    slicing_supported: bool = True
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelValidation:
    """Outcome of validating a model against a slicer."""

    path: Path
    is_valid: bool
    message: str = ""
    size: tuple[float, float, float] | None = None
    volume_mm3: float | None = None
    facets: int | None = None
    is_manifold: bool | None = None
    parts: int | None = None


@dataclass(frozen=True)
class SliceJob:
    model_path: Path
    profile: ProfileInfo
    filament: ProfileInfo
    printer: ProfileInfo | None = None
    output_dir: Path | None = None
    kind: SlicerKind = SlicerKind.ORCA_SLICER
    timeout_seconds: float | None = None
    export_name: str | None = None


@dataclass(frozen=True)
class SliceResult:
    """Outcome of a slice operation with parsed statistics."""

    job: SliceJob
    sliced_at: datetime
    output_3mf: Path | None = None
    gcode_path: Path | None = None
    estimated_time_minutes: float | None = None
    layer_count: int | None = None
    max_z_height: float | None = None
    filament_used_mm: float | None = None
    filament_used_cm3: float | None = None
    filament_density: float | None = None
    filament_weight_g: float | None = None
    return_code: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModelBounds:
    """Axis-aligned bounding box of a model, in millimeters."""

    min_coords: tuple[float, float, float]
    max_coords: tuple[float, float, float]
    center: tuple[float, float, float]
    extents_mm: tuple[float, float, float]


@dataclass(frozen=True)
class ModelTopology:
    """Mesh topology and quality metrics."""

    triangle_count: int
    vertex_count: int
    component_count: int
    degenerate_face_count: int
    non_manifold_edge_count: int
    boundary_edge_count: int
    euler_number: int | None
    watertight: bool
    winding_consistent: bool | None
    manifold: bool | None
    source_object_count: int | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelOrientation:
    """Orientation / principal-dimension analysis (read-only, no rotation)."""

    build_axis: str
    height_mm: float
    axis_aligned_extents_mm: tuple[float, float, float]
    principal_extents_mm: tuple[float, float, float]
    principal_axes: tuple[tuple[float, float, float], ...]
    z_axis: tuple[float, float, float] | None
    z_alignment: float | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverhangReport:
    """Geometric overhang heuristic relative to a fixed build direction."""

    threshold_degrees: float
    build_axis: str
    face_count: int
    area_mm2: float
    area_percent: float
    method: str
    note: str


@dataclass(frozen=True)
class ThinWallReport:
    """Thin-wall estimate via axis-pair ray casting.

    ``supported`` is False (with ``None`` measurements) when the mesh is not
    watertight, because the interior sampling is only well-defined then.
    """

    supported: bool
    min_mm: float | None
    median_mm: float | None
    sample_count: int
    method: str
    note: str


@dataclass(frozen=True)
class ModelAnalysis:
    """Structured result of a deterministic geometry analysis."""

    path: Path
    format: str
    valid: bool = True
    dimensions_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    volume_mm3: float | None = None
    surface_area_mm2: float = 0.0
    centroid_mm: tuple[float, float, float] | None = None
    bounds: ModelBounds | None = None
    topology: ModelTopology | None = None
    orientation: ModelOrientation | None = None
    overhang: OverhangReport | None = None
    thin_wall: ThinWallReport | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SettingsFingerprint:
    slicer_kind: SlicerKind
    profile_name: str
    filament_name: str
    material: str | None = None
    layer_height: float | None = None
    infill_percent: float | None = None


@dataclass(frozen=True)
class PrintRecord:
    record_id: str
    model_path: Path
    started_at: datetime
    finished_at: datetime
    outcome: PrintOutcome
    fingerprint: SettingsFingerprint
    notes: str = ""


@dataclass(frozen=True)
class Recommendation:
    fingerprint: SettingsFingerprint
    score: float
    reasons: list[str] = field(default_factory=list)
