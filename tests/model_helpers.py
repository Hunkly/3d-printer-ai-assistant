"""Shared helpers for model analysis tests."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
import trimesh

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" '
    'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)


def box_mesh(
    size_x: float,
    size_y: float,
    size_z: float,
    transform: np.ndarray | None = None,
) -> trimesh.Trimesh:
    """A well-wound box spanning [0..size] on each axis, optionally transformed."""
    mesh = trimesh.creation.box(extents=[size_x, size_y, size_z])
    translate = np.eye(4)
    translate[:3, 3] = [size_x / 2.0, size_y / 2.0, size_z / 2.0]
    mesh.apply_transform(translate)
    if transform is not None:
        mesh.apply_transform(transform)
    return mesh


def rotation_x(angle_degrees: float) -> np.ndarray:
    angle = np.radians(angle_degrees)
    c, s = np.cos(angle), np.sin(angle)
    matrix = np.eye(4)
    matrix[1, 1], matrix[1, 2] = c, -s
    matrix[2, 1], matrix[2, 2] = s, c
    return matrix


def rotation_y(angle_degrees: float) -> np.ndarray:
    angle = np.radians(angle_degrees)
    c, s = np.cos(angle), np.sin(angle)
    matrix = np.eye(4)
    matrix[0, 0], matrix[0, 2] = c, s
    matrix[2, 0], matrix[2, 2] = -s, c
    return matrix


def write_ascii_stl(path: Path, mesh: trimesh.Trimesh) -> Path:
    """Write *mesh* as an ASCII STL preserving vertex/face order and winding."""
    lines = ["solid model"]
    for a, b, c in mesh.faces:
        lines.append("facet normal 0 0 0")
        lines.append("  outer loop")
        for index in (a, b, c):
            x, y, z = mesh.vertices[index]
            lines.append(f"    vertex {float(x)} {float(y)} {float(z)}")
        lines.append("  endloop")
        lines.append("endfacet")
    lines.append("endsolid model")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_binary_stl(path: Path, mesh: trimesh.Trimesh) -> Path:
    """Write *mesh* as a binary STL preserving vertex/face order and winding."""
    with path.open("wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(mesh.faces)))
        for a, b, c in mesh.faces:
            fh.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for index in (a, b, c):
                fh.write(struct.pack("<3f", *(float(value) for value in mesh.vertices[index])))
            fh.write(struct.pack("<H", 0))
    return path


def write_cube_stl(path: Path, size: float = 20.0) -> Path:
    """Write a well-wound cube STL (analytical: volume ``size**3``)."""
    return write_ascii_stl(path, box_mesh(size, size, size))


def write_3mf(
    path: Path,
    unit: str,
    objects: dict[str, dict[str, Any]],
    build: list[tuple[str, str | None]],
) -> Path:
    """Write a valid 3MF archive with the given *objects* and *build* items.

    Each object is ``{"kind": "mesh", "vertices": [...], "triangles": [...]}`` or
    ``{"kind": "components", "components": [(object_id, transform_or_None), ...]}``.
    Build items are ``(object_id, transform_or_None)`` tuples.
    """
    namespace = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ET.register_namespace("", namespace)
    model = ET.Element(f"{{{namespace}}}model", {"unit": unit, "xml:lang": "en-US"})
    resources = ET.SubElement(model, f"{{{namespace}}}resources")
    for object_id, obj in objects.items():
        element = ET.SubElement(
            resources,
            f"{{{namespace}}}object",
            {"id": object_id, "type": "model"},
        )
        if obj["kind"] == "mesh":
            mesh = ET.SubElement(element, f"{{{namespace}}}mesh")
            vertices = ET.SubElement(mesh, f"{{{namespace}}}vertices")
            for x, y, z in obj["vertices"]:
                ET.SubElement(
                    vertices,
                    f"{{{namespace}}}vertex",
                    {"x": str(x), "y": str(y), "z": str(z)},
                )
            triangles = ET.SubElement(mesh, f"{{{namespace}}}triangles")
            for v1, v2, v3 in obj["triangles"]:
                ET.SubElement(
                    triangles,
                    f"{{{namespace}}}triangle",
                    {"v1": str(v1), "v2": str(v2), "v3": str(v3)},
                )
        else:
            components = ET.SubElement(element, f"{{{namespace}}}components")
            for child_id, transform in obj["components"]:
                component_attrs: dict[str, str] = {"objectid": child_id}
                if transform is not None:
                    component_attrs["transform"] = transform
                ET.SubElement(components, f"{{{namespace}}}component", component_attrs)
    build_element = ET.SubElement(model, f"{{{namespace}}}build")
    for object_id, transform in build:
        item_attrs: dict[str, str] = {"objectid": object_id}
        if transform is not None:
            item_attrs["transform"] = transform
        ET.SubElement(build_element, f"{{{namespace}}}item", item_attrs)
    model_xml = ET.tostring(model, encoding="utf-8", xml_declaration=True)
    write_3mf_raw(path, model_xml)
    return path


def write_3mf_raw(path: Path, model_xml: str | bytes) -> Path:
    """Write a 3MF archive wrapping the exact model XML string given."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("3D/3dmodel.model", model_xml)
    return path


def write_3mf_external_part(
    path: Path, main_model_xml: str | bytes, parts: dict[str, str | bytes]
) -> Path:
    """Write a 3MF archive with the given main model XML plus external object parts.

    *parts* maps archive entry paths (e.g. ``/3D/Objects/object_6.model``) to
    model XML. Models Bambu Studio / OrcaSlicer projects whose mesh geometry
    lives in external parts referenced via ``p:path`` on a ``<component>``.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("3D/3dmodel.model", main_model_xml)
        for entry, content in parts.items():
            archive.writestr(entry.lstrip("/"), content)
    return path
