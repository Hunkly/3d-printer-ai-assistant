"""Tests for :class:`TrimeshModelAnalyzer` on 3MF inputs."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.model_helpers import (
    box_mesh,
    write_3mf,
    write_3mf_external_part,
    write_3mf_raw,
)

from print_engineer.adapters.model.analyzer import TrimeshModelAnalyzer
from print_engineer.errors import InvalidModel


@pytest.fixture
def analyzer() -> TrimeshModelAnalyzer:
    return TrimeshModelAnalyzer()


def _cube_objects(size: float) -> dict[str, dict[str, object]]:
    mesh = box_mesh(size, size, size)
    return {
        "m": {
            "kind": "mesh",
            "vertices": mesh.vertices.tolist(),
            "triangles": mesh.faces.tolist(),
        }
    }


def _cube_mesh_xml(size: float = 10.0) -> str:
    mesh = box_mesh(size, size, size)
    vertices = "".join(
        f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in mesh.vertices
    )
    triangles = "".join(
        f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces
    )
    return f"<mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh>"


def _external_part_xml(*object_ids: str, size: float = 10.0) -> str:
    mesh = _cube_mesh_xml(size)
    objects = "".join(
        f'<object id="{object_id}" type="model">{mesh}</object>' for object_id in object_ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        f"<resources>{objects}</resources></model>"
    )


class TestValid3mf:
    def test_cube_millimeter(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(tmp_path / "cube.3mf", "millimeter", _cube_objects(10), [("m", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.format == "3mf"
        assert result.dimensions_mm == pytest.approx((10, 10, 10))
        assert result.volume_mm3 == pytest.approx(1000.0)
        assert result.surface_area_mm2 == pytest.approx(600.0)
        assert result.topology is not None
        assert result.topology.vertex_count == 8
        assert result.topology.triangle_count == 12
        assert result.topology.watertight is True
        assert result.topology.winding_consistent is True
        assert result.topology.source_object_count == 1

    def test_cube_inch_scales_to_mm(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(tmp_path / "cube.3mf", "inch", _cube_objects(1), [("m", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((25.4, 25.4, 25.4))
        assert result.volume_mm3 == pytest.approx(25.4**3)

    def test_cube_centimeter_scales_to_mm(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        path = write_3mf(tmp_path / "cube.3mf", "centimeter", _cube_objects(1), [("m", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 10))
        assert result.volume_mm3 == pytest.approx(1000.0)

    def test_components_with_transform(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        objects: dict[str, dict[str, object]] = {
            "m": _cube_objects(10)["m"],
            "c": {
                "kind": "components",
                # 3MF order: 3x3 linear part (row-major) then translation.
                "components": [("m", None), ("m", "1 0 0 0 1 0 0 0 1 0 0 12")],
            },
        }
        path = write_3mf(tmp_path / "components.3mf", "millimeter", objects, [("c", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 22))
        assert result.volume_mm3 == pytest.approx(2000.0)
        assert result.topology is not None
        assert result.topology.triangle_count == 24
        assert result.topology.component_count == 2
        assert result.topology.watertight is True
        assert result.topology.source_object_count == 1

    def test_two_build_items(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(
            tmp_path / "two.3mf",
            "millimeter",
            _cube_objects(10),
            [("m", None), ("m", "1 0 0 0 1 0 0 0 1 0 0 12")],
        )
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 22))
        assert result.volume_mm3 == pytest.approx(2000.0)
        assert result.topology is not None
        assert result.topology.source_object_count == 2
        assert result.topology.component_count == 2

    def test_component_with_non_uniform_scale(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        objects: dict[str, dict[str, object]] = {
            "m": _cube_objects(10)["m"],
            "c": {
                "kind": "components",
                # 3MF order: 3x3 linear part (row-major) then translation.
                "components": [("m", "1 0 0 0 1 0 0 0 0.5 0 0 0")],
            },
        }
        path = write_3mf(tmp_path / "scale.3mf", "millimeter", objects, [("c", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 5))
        assert result.volume_mm3 == pytest.approx(500.0)

    def test_component_rotation_swaps_extents(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        mesh = box_mesh(10, 20, 30)
        objects: dict[str, dict[str, object]] = {
            "m": {
                "kind": "mesh",
                "vertices": mesh.vertices.tolist(),
                "triangles": mesh.faces.tolist(),
            },
            "c": {
                "kind": "components",
                # 90 degrees about Z: x' = -y, y' = x.
                "components": [("m", "0 -1 0 1 0 0 0 0 1 0 0 0")],
            },
        }
        path = write_3mf(tmp_path / "rotate.3mf", "millimeter", objects, [("c", None)])
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((20, 10, 30))
        assert result.volume_mm3 == pytest.approx(6000.0)


class TestExternalObjectParts:
    def test_components_referencing_external_part(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        main_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="c" type="model"><components>'
            '<component p:path="/3D/Objects/part.model" objectid="1"/>'
            '<component p:path="/3D/Objects/part.model" objectid="2" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 12"/>'
            "</components></object></resources>"
            '<build><item objectid="c"/></build></model>'
        )
        path = write_3mf_external_part(
            tmp_path / "external.3mf",
            main_model,
            {"/3D/Objects/part.model": _external_part_xml("1", "2")},
        )
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 22))
        assert result.volume_mm3 == pytest.approx(2000.0)
        assert result.topology is not None
        assert result.topology.triangle_count == 24
        assert result.topology.component_count == 2

    def test_nested_external_parts(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        main_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="c" type="model"><components>'
            '<component p:path="/3D/Objects/group.model" objectid="g"/>'
            "</components></object></resources>"
            '<build><item objectid="c"/></build></model>'
        )
        group_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="g" type="model"><components>'
            '<component p:path="/3D/Objects/part.model" objectid="1"/>'
            "</components></object></resources></model>"
        )
        path = write_3mf_external_part(
            tmp_path / "nested.3mf",
            main_model,
            {
                "/3D/Objects/group.model": group_model,
                "/3D/Objects/part.model": _external_part_xml("1"),
            },
        )
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 10))
        assert result.volume_mm3 == pytest.approx(1000.0)
        assert result.topology is not None
        assert result.topology.source_object_count == 1

    def test_same_part_referenced_twice_loaded_once(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        main_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="c" type="model"><components>'
            '<component p:path="/3D/Objects/part.model" objectid="1"/>'
            '<component p:path="/3D/Objects/part.model" objectid="1" '
            'transform="1 0 0 0 1 0 0 0 1 0 0 12"/>'
            "</components></object></resources>"
            '<build><item objectid="c"/></build></model>'
        )
        path = write_3mf_external_part(
            tmp_path / "twice.3mf",
            main_model,
            {"/3D/Objects/part.model": _external_part_xml("1")},
        )
        result = analyzer.analyze(path, 45.0)

        assert result.dimensions_mm == pytest.approx((10, 10, 22))
        assert result.volume_mm3 == pytest.approx(2000.0)


class TestInvalid3mf:
    def test_not_a_zip(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = tmp_path / "bad.3mf"
        path.write_text("this is not a zip archive", encoding="utf-8")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "invalid_archive"

    def test_missing_required_entries(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        import zipfile

        path = tmp_path / "bad.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "invalid_archive"
        assert "missing" in excinfo.value.details

    def test_malformed_xml(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf_raw(tmp_path / "bad.3mf", "<model><broken")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"

    def test_unsupported_unit(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(tmp_path / "bad.3mf", "furlong", _cube_objects(1), [("m", None)])
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"

    def test_empty_build(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(tmp_path / "empty.3mf", "millimeter", _cube_objects(10), [])
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"

    def test_missing_object_reference(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_3mf(tmp_path / "ref.3mf", "millimeter", _cube_objects(10), [("nope", None)])
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"

    def test_component_cycle(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        objects: dict[str, dict[str, object]] = {
            "a": {"kind": "components", "components": [("b", None)]},
            "b": {"kind": "components", "components": [("a", None)]},
        }
        path = write_3mf(tmp_path / "cycle.3mf", "millimeter", objects, [("a", None)])
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"
        assert "cycle" in str(excinfo.value)

    def test_empty_geometry(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        objects: dict[str, dict[str, object]] = {
            "m": {"kind": "mesh", "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": []}
        }
        path = write_3mf(tmp_path / "empty.3mf", "millimeter", objects, [("m", None)])
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "empty_geometry"

    def test_missing_external_part(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        main_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources><object id="c" type="model"><components>'
            '<component p:path="/3D/Objects/part.model" objectid="1"/>'
            "</components></object></resources>"
            '<build><item objectid="c"/></build></model>'
        )
        path = write_3mf_raw(tmp_path / "missing.3mf", main_model)
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"
        assert "unreadable" in str(excinfo.value)

    def test_external_object_id_collision(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        main_model = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">'
            '<resources>'
            '<object id="1" type="model">'
            + _cube_mesh_xml(5.0)
            + "</object>"
            '<object id="c" type="model"><components>'
            '<component p:path="/3D/Objects/part.model" objectid="1"/>'
            "</components></object></resources>"
            '<build><item objectid="c"/></build></model>'
        )
        path = write_3mf_external_part(
            tmp_path / "collide.3mf",
            main_model,
            {"/3D/Objects/part.model": _external_part_xml("1")},
        )
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"
        assert "unique" in str(excinfo.value)

    def test_missing_vertex_attribute(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        model_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            '<resources><object id="m" type="model"><mesh><vertices>'
            '<vertex x="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>'
            "</vertices><triangles><triangle v1=\"0\" v2=\"1\" v3=\"2\"/></triangles>"
            "</mesh></object></resources>"
            '<build><item objectid="m"/></build></model>'
        )
        path = write_3mf_raw(tmp_path / "bad.3mf", model_xml)
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "parse_error"
