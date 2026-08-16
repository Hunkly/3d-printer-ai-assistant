"""Deterministic geometry analysis for STL and 3MF models.

The analyzer reads a model file, computes mesh geometry metrics with numpy and
trimesh, and returns a structured :class:`ModelAnalysis`. It never modifies,
repairs, or rotates the model and never calls a slicer or printer.

Units are canonicalized to millimeters. STL has no unit metadata and is assumed
to be in millimeters. 3MF declares a ``unit`` attribute which is honored.
"""

from __future__ import annotations

import math
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

from print_engineer.core.interfaces.model_analyzer import ModelAnalyzer
from print_engineer.core.types import (
    ModelAnalysis,
    ModelBounds,
    ModelOrientation,
    ModelTopology,
    OverhangReport,
    ThinWallReport,
)
from print_engineer.errors import InvalidModel

SUPPORTED_SUFFIXES = frozenset({".stl", ".3mf"})

_BUILD_AXIS = "+Z"
_RAY_EPS = 1e-12
_AREA_EPS_FACTOR = 1e-9
_MIN_AREA_EPS = 1e-12
_PARITY_OFFSET = np.array([1e-7, 1e-7, 1e-7])

_3MF_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_3MF_PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_3MF_PATH_ATTR = f"{{{_3MF_PRODUCTION_NS}}}path"
_3MF_REQUIRED_ENTRIES = frozenset({"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"})
_3MF_UNITS_TO_MM = {
    "micron": 1e-3,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}

_OVERHANG_METHOD = "downward_face_angle_from_vertical"
_OVERHANG_NOTE = (
    "Geometric heuristic: only downward-facing faces (normal.z < 0) are considered; "
    "a face is flagged when its surface deviates from the vertical by more than the "
    "threshold. This is not a prediction of slicer support-material generation."
)
_THIN_WALL_METHOD = "axis_pair_ray_cast"
_THIN_WALL_NOTE = (
    "Local thickness estimated at interior grid samples by ray casting along the three "
    "axis pairs (first surface hit in +/- each axis, summed and minimized over axes). "
    "A heuristic approximation, not exact medial-axis thickness."
)
_THIN_WALL_UNSUPPORTED_NOTE = (
    "Thin-wall analysis requires a watertight mesh; the interior sampling is not "
    "well-defined otherwise."
)


class TrimeshModelAnalyzer(ModelAnalyzer):
    """Analysis backed by trimesh (STL) plus a stdlib 3MF reader."""

    def analyze(
        self, path: Path, overhang_threshold_degrees: float = 45.0
    ) -> ModelAnalysis:
        _validate_model(path)
        if path.suffix.lower() == ".3mf":
            mesh, object_count = _load_3mf(path)
            fmt = "3mf"
        else:
            mesh = _load_stl(path)
            object_count = None
            fmt = "stl"
        mesh.merge_vertices(merge_tex=False, merge_norm=False)
        return _analyze_mesh(
            path=path,
            mesh=mesh,
            fmt=fmt,
            object_count=object_count,
            overhang_threshold_degrees=overhang_threshold_degrees,
        )


def _validate_model(path: Path) -> None:
    if not path.exists():
        _raise_invalid(path, "not_found", f"Model file does not exist: {path}")
    if not path.is_file():
        _raise_invalid(path, "not_a_file", f"Model path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        _raise_invalid(
            path,
            "unsupported_suffix",
            f"Unsupported model type {path.suffix!r}",
            supported=sorted(SUPPORTED_SUFFIXES),
        )
    try:
        if path.stat().st_size == 0:
            _raise_invalid(path, "empty", f"Model file is empty: {path}")
    except OSError as exc:
        _raise_invalid(path, "unreadable", f"Model file is not readable: {exc}")


def _raise_invalid(path: Path, reason: str, message: str, **extra: Any) -> NoReturn:
    details: dict[str, Any] = {"model_path": str(path), "reason": reason}
    details.update(extra)
    raise InvalidModel(message, details=details)


def _vec3(values: np.ndarray | tuple[float, ...]) -> tuple[float, float, float]:
    return (float(values[0]), float(values[1]), float(values[2]))


def _required_attr(elem: ET.Element, name: str, path: Path) -> str:
    value = elem.get(name)
    if value is None:
        _raise_invalid(
            path,
            "parse_error",
            f"3MF element <{_local_name(elem.tag)}> is missing required "
            f"attribute {name!r}",
        )
    return value


def _load_stl(path: Path) -> trimesh.Trimesh:
    try:
        loaded = trimesh.load(str(path), file_type="stl", force="mesh", process=False)
    except Exception as exc:  # noqa: BLE001 - map any loader failure
        _raise_invalid(
            path, "parse_error", f"Could not parse STL model: {exc}", error=str(exc)
        )
    if not isinstance(loaded, trimesh.Trimesh):
        _raise_invalid(path, "parse_error", "STL file did not load as a mesh")
    if loaded.is_empty:
        _raise_invalid(path, "empty_geometry", "STL file contains no triangle geometry")
    return loaded


def _load_3mf(path: Path) -> tuple[trimesh.Trimesh, int]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = sorted(_3MF_REQUIRED_ENTRIES - names)
            if missing:
                _raise_invalid(
                    path,
                    "invalid_archive",
                    f"3MF archive is missing required entries: {', '.join(missing)}",
                    missing=missing,
                )
            try:
                model_text = archive.read("3D/3dmodel.model")
            except (KeyError, OSError):
                _raise_invalid(path, "invalid_archive", "3MF model entry is unreadable")

            def load_part(part_path: str) -> ET.Element:
                try:
                    data = archive.read(part_path.lstrip("/"))
                except (KeyError, OSError):
                    _raise_invalid(
                        path,
                        "parse_error",
                        f"3MF external object part {part_path!r} is unreadable",
                        part=part_path,
                    )
                try:
                    return ET.fromstring(data)
                except ET.ParseError as exc:
                    _raise_invalid(
                        path,
                        "parse_error",
                        f"3MF external object part {part_path!r} is malformed: {exc}",
                        part=part_path,
                    )

            try:
                root = ET.fromstring(model_text)
            except ET.ParseError as exc:
                _raise_invalid(path, "parse_error", f"3MF model XML is malformed: {exc}")

            unit = root.get("unit", "millimeter")
            scale = _3MF_UNITS_TO_MM.get(unit)
            if scale is None:
                _raise_invalid(
                    path, "parse_error", f"3MF uses unsupported unit {unit!r}", unit=unit
                )

            objects = _parse_3mf_objects(path, root, load_part)
            build_items = _children(_find_child(root, "build"), "item")
            object_count = len(build_items)
            if object_count == 0:
                _raise_invalid(path, "parse_error", "3MF build section is empty (no items)")

            verts: list[np.ndarray] = []
            faces: list[np.ndarray] = []
            visited: set[str] = set()
            vertex_offset = 0

            def resolve(obj_id: str | None, matrix: np.ndarray | None) -> None:
                nonlocal vertex_offset
                if obj_id is None or obj_id not in objects:
                    _raise_invalid(
                        path, "parse_error", f"3MF build references missing object {obj_id!r}"
                    )
                if obj_id in visited:
                    _raise_invalid(path, "parse_error", "3MF component graph contains a cycle")
                visited.add(obj_id)
                obj = objects[obj_id]
                if obj["kind"] == "mesh":
                    v, f = _apply_3mf_transform(obj["verts"], obj["faces"], matrix)
                    verts.append(v * scale)
                    faces.append(f + vertex_offset)
                    vertex_offset += len(v)
                else:
                    for component in obj["components"]:
                        child_id = component.get("objectid")
                        child_transform = _parse_3mf_transform(
                            component.get("transform"), path, f"component of object {obj_id}"
                        )
                        resolve(child_id, _compose_3mf_transforms(matrix, child_transform))
                visited.remove(obj_id)

            for item in build_items:
                resolve(
                    item.get("objectid"),
                    _parse_3mf_transform(item.get("transform"), path, "build item"),
                )

            if not faces or sum(len(f) for f in faces) == 0:
                _raise_invalid(path, "empty_geometry", "3MF file contains no triangle geometry")
    except zipfile.BadZipFile:
        _raise_invalid(path, "invalid_archive", "File is not a valid 3MF archive (not a zip)")

    vertices = np.vstack(verts) if len(verts) > 1 else verts[0]
    face_array = np.vstack(faces) if len(faces) > 1 else faces[0]
    mesh = trimesh.Trimesh(vertices=vertices, faces=face_array, process=False)
    return mesh, object_count


def _parse_3mf_objects(
    path: Path,
    root: ET.Element,
    load_part: Callable[[str], ET.Element] | None = None,
) -> dict[str, dict[str, Any]]:
    """Parse 3MF ``<resources>`` objects, resolving external parts.

    Bambu Studio / OrcaSlicer projects often store mesh geometry in external
    object parts (``<component p:path="/3D/Objects/...">``). Objects from those
    parts are loaded recursively and merged into the flat id -> object map so the
    build graph can resolve them. Object ids must be unique across the whole
    model, otherwise resolution would be ambiguous.
    """
    return _collect_3mf_objects(path, root, load_part, set())


def _collect_3mf_objects(
    path: Path,
    root: ET.Element,
    load_part: Callable[[str], ET.Element] | None,
    loaded_parts: set[str],
) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    resources = _find_child(root, "resources")
    if resources is None:
        return objects
    for obj in _children(resources, "object"):
        obj_id = obj.get("id")
        if obj_id is None:
            continue
        mesh_el = _find_child(obj, "mesh")
        if mesh_el is None:
            components_el = _find_child(obj, "components")
            components = (
                _children(components_el, "component") if components_el is not None else []
            )
            objects[obj_id] = {"kind": "components", "components": components}
            if load_part is not None:
                for component in components:
                    part_path = component.get(_3MF_PATH_ATTR)
                    if part_path is None or part_path in loaded_parts:
                        continue
                    loaded_parts.add(part_path)
                    part_objects = _collect_3mf_objects(
                        path, load_part(part_path), load_part, loaded_parts
                    )
                    for part_obj_id, part_obj in part_objects.items():
                        if part_obj_id in objects:
                            _raise_invalid(
                                path,
                                "parse_error",
                                f"3MF object id {part_obj_id!r} is defined in both the "
                                f"main model and external part {part_path!r}; object ids "
                                "must be unique",
                                part=part_path,
                            )
                        objects[part_obj_id] = part_obj
            continue
        vertex_el = _find_child(mesh_el, "vertices")
        triangle_el = _find_child(mesh_el, "triangles")
        verts = np.array(
            [
                [float(_required_attr(v, "x", path)), float(_required_attr(v, "y", path)),
                 float(_required_attr(v, "z", path))]
                for v in _children(vertex_el, "vertex")
            ],
            dtype=float,
        )
        triangles = np.array(
            [
                [int(_required_attr(t, "v1", path)), int(_required_attr(t, "v2", path)),
                 int(_required_attr(t, "v3", path))]
                for t in _children(triangle_el, "triangle")
            ],
            dtype=int,
        )
        objects[obj_id] = {"kind": "mesh", "verts": verts, "faces": triangles}
    return objects


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(elem: ET.Element | None, name: str) -> list[ET.Element]:
    if elem is None:
        return []
    return [child for child in elem if _local_name(child.tag) == name]


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in _children(elem, name):
        return child
    return None


def _parse_3mf_transform(
    transform: str | None, path: Path, context: str
) -> np.ndarray | None:
    if transform is None:
        return None
    values = [float(value) for value in transform.split()]
    if len(values) != 12:
        _raise_invalid(
            path,
            "parse_error",
            f"3MF transform attribute of {context} must contain 12 numbers",
        )
    # 3MF serializes the 3x3 linear part in row-major order, followed by the
    # translation: m00 m01 m02 m10 m11 m12 m20 m21 m22 tx ty tz.
    linear = np.array(values[:9], dtype=float).reshape(3, 3)
    translation = np.array(values[9:], dtype=float)
    return np.hstack([linear, translation.reshape(3, 1)])


def _compose_3mf_transforms(
    parent: np.ndarray | None, child: np.ndarray | None
) -> np.ndarray | None:
    if parent is None:
        return child
    if child is None:
        return parent
    parent_aug = np.vstack([parent, [0.0, 0.0, 0.0, 1.0]])
    child_aug = np.vstack([child, [0.0, 0.0, 0.0, 1.0]])
    return (parent_aug @ child_aug)[:3]


def _apply_3mf_transform(
    verts: np.ndarray, faces: np.ndarray, matrix: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray]:
    if matrix is None:
        return verts, faces
    transformed = verts @ matrix[:, :3].T + matrix[:, 3]
    return transformed, faces


def _analyze_mesh(
    *,
    path: Path,
    mesh: trimesh.Trimesh,
    fmt: str,
    object_count: int | None,
    overhang_threshold_degrees: float,
) -> ModelAnalysis:
    extents = _vec3(np.asarray(mesh.extents))
    bounds_min_arr = np.asarray(mesh.bounds[0])
    bounds_max_arr = np.asarray(mesh.bounds[1])
    bounds_min = _vec3(bounds_min_arr)
    bounds_max = _vec3(bounds_max_arr)
    bounds_center = _vec3((bounds_min_arr + bounds_max_arr) / 2.0)
    total_area = float(mesh.area)
    watertight = bool(mesh.is_watertight)
    winding_consistent = (
        bool(mesh.is_winding_consistent) if not mesh.is_empty else None
    )

    signed_volume: float | None
    volume: float | None
    notes: list[str] = []
    if watertight:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            signed_volume = float(mesh.volume)
        if signed_volume < 0:
            volume = -signed_volume
            notes.append(
                "signed volume was negative; the mesh is likely wound inside-out, "
                "volume reported as magnitude"
            )
        else:
            volume = signed_volume
    else:
        signed_volume = None
        volume = None
        notes.append("volume omitted: mesh is not watertight")

    if watertight and signed_volume is not None and math.isfinite(signed_volume):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            centroid = _vec3(np.asarray(mesh.center_mass))
    else:
        total_area_checked = float(mesh.area_faces.sum())
        if total_area_checked > 0:
            centroid = _vec3(
                np.average(mesh.triangles_center, axis=0, weights=mesh.area_faces)
            )
        else:
            centroid = _vec3(np.asarray(mesh.bounds).mean(axis=0))
    topology = _build_topology(mesh, watertight, winding_consistent, object_count)
    orientation = _build_orientation(mesh, extents)
    overhang = _build_overhang(mesh, overhang_threshold_degrees, total_area)
    thin_wall = _build_thin_wall(mesh, watertight)

    return ModelAnalysis(
        path=path,
        format=fmt,
        valid=True,
        dimensions_mm=extents,
        volume_mm3=volume,
        surface_area_mm2=total_area,
        centroid_mm=centroid,
        bounds=ModelBounds(
            min_coords=bounds_min,
            max_coords=bounds_max,
            center=bounds_center,
            extents_mm=extents,
        ),
        topology=topology,
        orientation=orientation,
        overhang=overhang,
        thin_wall=thin_wall,
        notes=tuple(notes),
    )


def _build_topology(
    mesh: trimesh.Trimesh,
    watertight: bool,
    winding_consistent: bool | None,
    object_count: int | None,
) -> ModelTopology:
    edge_counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
    non_manifold_edges = int((edge_counts > 2).sum())
    boundary_edges = int((edge_counts == 1).sum())
    component_count = _count_face_components(mesh)
    max_extent = float(np.max(mesh.extents))
    area_eps = max(_MIN_AREA_EPS, _AREA_EPS_FACTOR * max_extent * max_extent)
    degenerate = int((mesh.area_faces <= area_eps).sum())
    notes: list[str] = []
    if degenerate:
        notes.append("degenerate (zero-area) faces present")
    if non_manifold_edges:
        notes.append("non-manifold edges present")
    if boundary_edges:
        notes.append("open (boundary) edges present")
    return ModelTopology(
        triangle_count=int(len(mesh.faces)),
        vertex_count=int(len(mesh.vertices)),
        component_count=component_count,
        degenerate_face_count=degenerate,
        non_manifold_edge_count=non_manifold_edges,
        boundary_edge_count=boundary_edges,
        euler_number=int(mesh.euler_number),
        watertight=watertight,
        winding_consistent=winding_consistent,
        manifold=non_manifold_edges == 0,
        source_object_count=object_count,
        notes=tuple(notes),
    )


def _count_face_components(mesh: trimesh.Trimesh) -> int:
    """Count connected components of the face graph via shared edges."""
    face_count = len(mesh.faces)
    if face_count == 0:
        return 0
    face_edges = mesh.edges_unique_inverse.reshape(face_count, 3)
    flat_edges = face_edges.reshape(-1)
    flat_faces = np.repeat(np.arange(face_count), 3)
    order = np.argsort(flat_edges, kind="stable")
    sorted_edges = flat_edges[order]
    sorted_faces = flat_faces[order]
    change = np.r_[True, sorted_edges[1:] != sorted_edges[:-1]]
    groups = np.split(sorted_faces, np.flatnonzero(change)[1:])

    parent = np.arange(face_count)

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for group in groups:
        if len(group) > 1:
            first = int(group[0])
            for face in group[1:]:
                union(first, int(face))
    return len({find(int(index)) for index in range(face_count)})


def _build_orientation(
    mesh: trimesh.Trimesh, axis_aligned_extents: tuple[float, float, float]
) -> ModelOrientation:
    mean, axes = _principal_axes(mesh)
    projected = (mesh.vertices - mean[None, :]) @ axes
    lengths = [float(np.ptp(projected[:, axis])) for axis in range(3)]
    order = sorted(range(3), key=lambda axis: lengths[axis], reverse=True)
    principal_extents = (
        float(lengths[order[0]]),
        float(lengths[order[1]]),
        float(lengths[order[2]]),
    )
    principal_axes = tuple(_vec3(axes[:, axis]) for axis in order)

    alignments = [abs(axes[2, axis]) for axis in range(3)]
    best = int(np.argmax(alignments))
    z_axis = np.array(axes[:, best])
    if z_axis[2] < 0:
        z_axis = -z_axis
    z_alignment = float(alignments[best])

    notes: list[str] = []
    spread = max(alignments) - min(alignments)
    if spread < 0.05:
        notes.append(
            "principal axes are ambiguous for this (near-)symmetric mesh; "
            "reported values are deterministic but not unique"
        )
    return ModelOrientation(
        build_axis=_BUILD_AXIS,
        height_mm=axis_aligned_extents[2],
        axis_aligned_extents_mm=axis_aligned_extents,
        principal_extents_mm=principal_extents,
        principal_axes=principal_axes,
        z_axis=_vec3(z_axis),
        z_alignment=z_alignment,
        notes=tuple(notes),
    )


def _principal_axes(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted PCA of triangle centroids (columns of the result)."""
    centroids = mesh.triangles_center
    areas = mesh.area_faces
    total = float(areas.sum())
    if total <= 0:
        return np.zeros(3), np.eye(3)
    mean = (centroids * areas[:, None]).sum(axis=0) / total
    centered = centroids - mean[None, :]
    weighted = centered * areas[:, None]
    covariance = (weighted.T @ centered) / total
    eigvals, eigvecs = np.linalg.eigh(covariance)
    order = np.argsort(eigvals)[::-1]
    return mean, eigvecs[:, order]


def _build_overhang(
    mesh: trimesh.Trimesh, threshold_degrees: float, total_area: float
) -> OverhangReport:
    normals = mesh.face_normals
    areas = mesh.area_faces
    down = normals[:, 2] < -_RAY_EPS
    if not bool(down.any()):
        return OverhangReport(
            threshold_degrees=threshold_degrees,
            build_axis=_BUILD_AXIS,
            face_count=0,
            area_mm2=0.0,
            area_percent=0.0,
            method=_OVERHANG_METHOD,
            note=_OVERHANG_NOTE,
        )
    angles = 90.0 - np.degrees(np.arccos(np.clip(-normals[down, 2], 0.0, 1.0)))
    flagged = angles > threshold_degrees
    area = float(areas[down][flagged].sum())
    percent = area / total_area * 100.0 if total_area > 0 else 0.0
    return OverhangReport(
        threshold_degrees=threshold_degrees,
        build_axis=_BUILD_AXIS,
        face_count=int(flagged.sum()),
        area_mm2=area,
        area_percent=percent,
        method=_OVERHANG_METHOD,
        note=_OVERHANG_NOTE,
    )


def _build_thin_wall(mesh: trimesh.Trimesh, watertight: bool) -> ThinWallReport:
    if not watertight:
        return ThinWallReport(
            supported=False,
            min_mm=None,
            median_mm=None,
            sample_count=0,
            method=_THIN_WALL_METHOD,
            note=_THIN_WALL_UNSUPPORTED_NOTE,
        )
    if mesh.is_empty:
        return ThinWallReport(
            supported=True,
            min_mm=None,
            median_mm=None,
            sample_count=0,
            method=_THIN_WALL_METHOD,
            note="mesh contains no triangle geometry",
        )

    grid = _interior_grid_points(mesh)
    if len(grid) == 0:
        return ThinWallReport(
            supported=True,
            min_mm=None,
            median_mm=None,
            sample_count=0,
            method=_THIN_WALL_METHOD,
            note="no interior grid samples found",
        )

    triangles = mesh.triangles
    tri_min = triangles.min(axis=1)
    tri_max = triangles.max(axis=1)

    thicknesses: list[float] = []
    for point in grid:
        value = _point_thickness(point, tri_min, tri_max, triangles)
        if math.isfinite(value):
            thicknesses.append(value)
    if not thicknesses:
        return ThinWallReport(
            supported=True,
            min_mm=None,
            median_mm=None,
            sample_count=len(grid),
            method=_THIN_WALL_METHOD,
            note="no valid interior thickness samples found",
        )
    return ThinWallReport(
        supported=True,
        min_mm=float(np.min(thicknesses)),
        median_mm=float(np.median(thicknesses)),
        sample_count=len(thicknesses),
        method=_THIN_WALL_METHOD,
        note=_THIN_WALL_NOTE,
    )


def _interior_grid_points(mesh: trimesh.Trimesh) -> np.ndarray:
    max_extent = float(np.max(mesh.extents))
    steps = int(min(10, max(3, int(math.ceil(max_extent / 4.0)) + 1)))
    coordinates = [
        np.linspace(mesh.bounds[0, axis], mesh.bounds[1, axis], steps) for axis in range(3)
    ]
    xs, ys, zs = np.meshgrid(*coordinates, indexing="ij")
    grid = np.stack([xs.ravel(), ys.ravel(), zs.ravel()], axis=1)
    offset_grid = grid + _PARITY_OFFSET
    inside = _ray_count(
        offset_grid, np.array([1.0, 0.0, 0.0]), mesh.triangles
    ) % 2 == 1
    return offset_grid[inside]


def _point_thickness(
    origin: np.ndarray,
    tri_min: np.ndarray,
    tri_max: np.ndarray,
    triangles: np.ndarray,
) -> float:
    axis_sums: list[float] = []
    for axis in range(3):
        direction = np.zeros(3)
        direction[axis] = 1.0
        plus = _nearest_hit(origin, direction, tri_min, tri_max, triangles)
        minus = _nearest_hit(origin, -direction, tri_min, tri_max, triangles)
        if math.isfinite(plus) and math.isfinite(minus):
            axis_sums.append(plus + minus)
    return float(min(axis_sums)) if axis_sums else math.inf


def _ray_count(origins: np.ndarray, direction: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Number of ray/triangle intersections per origin (Moller-Trumbore)."""
    counts = np.zeros(len(origins), dtype=int)
    tri_min = triangles.min(axis=1)
    tri_max = triangles.max(axis=1)
    for index, origin in enumerate(origins):
        mask = _ray_prefilter(origin, direction, tri_min, tri_max)
        if not bool(mask.any()):
            continue
        values = _moller_trumbore(origin, direction, triangles[mask])
        counts[index] = int(np.isfinite(values).sum())
    return counts


def _nearest_hit(
    origin: np.ndarray,
    direction: np.ndarray,
    tri_min: np.ndarray,
    tri_max: np.ndarray,
    triangles: np.ndarray,
) -> float:
    mask = _ray_prefilter(origin, direction, tri_min, tri_max)
    if not bool(mask.any()):
        return math.inf
    values = _moller_trumbore(origin, direction, triangles[mask])
    valid = values[(values > _RAY_EPS) & np.isfinite(values)]
    return float(np.min(valid)) if len(valid) else math.inf


def _ray_prefilter(
    origin: np.ndarray,
    direction: np.ndarray,
    tri_min: np.ndarray,
    tri_max: np.ndarray,
) -> np.ndarray:
    mask = np.ones(len(tri_min), dtype=bool)
    for axis in range(3):
        if direction[axis] > 0:
            mask &= origin[axis] <= tri_max[:, axis] + _RAY_EPS
        elif direction[axis] < 0:
            mask &= origin[axis] >= tri_min[:, axis] - _RAY_EPS
        else:
            mask &= (tri_min[:, axis] <= origin[axis] + _RAY_EPS) & (
                tri_max[:, axis] >= origin[axis] - _RAY_EPS
            )
    return mask


def _moller_trumbore(
    origin: np.ndarray, direction: np.ndarray, triangles: np.ndarray
) -> np.ndarray:
    """Ray/triangle intersection parameter ``t`` for each triangle (inf if none)."""
    count = len(triangles)
    direction_repeated = np.repeat(direction[None, :], count, axis=0)
    edge1 = triangles[:, 1] - triangles[:, 0]
    edge2 = triangles[:, 2] - triangles[:, 0]
    tvec = origin[None, :] - triangles[:, 0]
    pvec = np.cross(direction_repeated, edge2)
    det = np.einsum("ij,ij->i", edge1, pvec)
    u = np.einsum("ij,ij->i", tvec, pvec)
    qvec = np.cross(tvec, edge1)
    v = np.einsum("ij,ij->i", direction_repeated, qvec)
    t = np.einsum("ij,ij->i", edge2, qvec)
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse = 1.0 / det
        u = u * inverse
        v = v * inverse
        t = t * inverse
        valid = (
            (np.abs(det) > _RAY_EPS)
            & (t >= 0.0)
            & (u >= 0.0)
            & (v >= 0.0)
            & (u + v <= 1.0)
        )
    return np.where(valid, t, np.inf)


def model_analysis_to_dict(analysis: ModelAnalysis) -> dict[str, Any]:
    """Serialize a :class:`ModelAnalysis` into a JSON-friendly mapping."""
    bounds = analysis.bounds
    topology = analysis.topology
    orientation = analysis.orientation
    overhang = analysis.overhang
    thin_wall = analysis.thin_wall
    return {
        "path": str(analysis.path),
        "format": analysis.format,
        "valid": analysis.valid,
        "dimensions_mm": list(analysis.dimensions_mm),
        "volume_mm3": analysis.volume_mm3,
        "surface_area_mm2": analysis.surface_area_mm2,
        "centroid_mm": list(analysis.centroid_mm) if analysis.centroid_mm else None,
        "bounds": {
            "min_coords": list(bounds.min_coords),
            "max_coords": list(bounds.max_coords),
            "center": list(bounds.center),
            "extents_mm": list(bounds.extents_mm),
        }
        if bounds
        else None,
        "topology": {
            "triangle_count": topology.triangle_count,
            "vertex_count": topology.vertex_count,
            "component_count": topology.component_count,
            "degenerate_face_count": topology.degenerate_face_count,
            "non_manifold_edge_count": topology.non_manifold_edge_count,
            "boundary_edge_count": topology.boundary_edge_count,
            "euler_number": topology.euler_number,
            "watertight": topology.watertight,
            "winding_consistent": topology.winding_consistent,
            "manifold": topology.manifold,
            "source_object_count": topology.source_object_count,
            "notes": list(topology.notes),
        }
        if topology
        else None,
        "orientation": {
            "build_axis": orientation.build_axis,
            "height_mm": orientation.height_mm,
            "axis_aligned_extents_mm": list(orientation.axis_aligned_extents_mm),
            "principal_extents_mm": list(orientation.principal_extents_mm),
            "principal_axes": [list(axis) for axis in orientation.principal_axes],
            "z_axis": list(orientation.z_axis) if orientation.z_axis else None,
            "z_alignment": orientation.z_alignment,
            "notes": list(orientation.notes),
        }
        if orientation
        else None,
        "overhang": {
            "threshold_degrees": overhang.threshold_degrees,
            "build_axis": overhang.build_axis,
            "face_count": overhang.face_count,
            "area_mm2": overhang.area_mm2,
            "area_percent": overhang.area_percent,
            "method": overhang.method,
            "note": overhang.note,
        }
        if overhang
        else None,
        "thin_wall": {
            "supported": thin_wall.supported,
            "min_mm": thin_wall.min_mm,
            "median_mm": thin_wall.median_mm,
            "sample_count": thin_wall.sample_count,
            "method": thin_wall.method,
            "note": thin_wall.note,
        }
        if thin_wall
        else None,
        "notes": list(analysis.notes),
    }
