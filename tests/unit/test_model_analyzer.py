"""Tests for :class:`TrimeshModelAnalyzer` on STL inputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from tests.model_helpers import box_mesh, rotation_x, rotation_y, write_ascii_stl, write_binary_stl
from trimesh import Trimesh

from print_engineer.adapters.model.analyzer import TrimeshModelAnalyzer
from print_engineer.errors import InvalidModel


@pytest.fixture
def analyzer() -> TrimeshModelAnalyzer:
    return TrimeshModelAnalyzer()


class TestCube:
    def test_analytical_values(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
        result = analyzer.analyze(path, 45.0)

        assert result.valid is True
        assert result.format == "stl"
        assert result.dimensions_mm == pytest.approx((20, 20, 20))
        assert result.volume_mm3 == pytest.approx(8000.0)
        assert result.surface_area_mm2 == pytest.approx(2400.0)
        assert result.centroid_mm == pytest.approx((10, 10, 10))
        assert result.notes == ()

    def test_bounds(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
        bounds = analyzer.analyze(path, 45.0).bounds

        assert bounds is not None
        assert bounds.min_coords == pytest.approx((0, 0, 0))
        assert bounds.max_coords == pytest.approx((20, 20, 20))
        assert bounds.center == pytest.approx((10, 10, 10))
        assert bounds.extents_mm == pytest.approx((20, 20, 20))

    def test_topology(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
        topology = analyzer.analyze(path, 45.0).topology

        assert topology is not None
        assert topology.triangle_count == 12
        assert topology.vertex_count == 8
        assert topology.component_count == 1
        assert topology.degenerate_face_count == 0
        assert topology.non_manifold_edge_count == 0
        assert topology.boundary_edge_count == 0
        assert topology.euler_number == 2
        assert topology.watertight is True
        assert topology.winding_consistent is True
        assert topology.manifold is True
        assert topology.source_object_count is None
        assert topology.notes == ()

    def test_orientation(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "box.stl", box_mesh(40, 20, 10))
        orientation = analyzer.analyze(path, 45.0).orientation

        assert orientation is not None
        assert orientation.build_axis == "+Z"
        assert orientation.height_mm == pytest.approx(10.0)
        assert orientation.axis_aligned_extents_mm == pytest.approx((40, 20, 10))
        assert orientation.principal_extents_mm == pytest.approx((40, 20, 10))
        assert orientation.z_axis == pytest.approx((0, 0, 1))
        assert orientation.z_alignment == pytest.approx(1.0)
        assert orientation.notes == ()

    def test_overhang_flat_plate(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "plate.stl", box_mesh(40, 30, 2))
        overhang = analyzer.analyze(path, 45.0).overhang

        assert overhang is not None
        assert overhang.threshold_degrees == 45.0
        assert overhang.build_axis == "+Z"
        assert overhang.face_count == 2
        assert overhang.area_mm2 == pytest.approx(1200.0)
        assert overhang.area_percent == pytest.approx(1200.0 / 2680.0 * 100.0)

    def test_thin_wall_flat_plate(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "plate.stl", box_mesh(40, 30, 2))
        thin_wall = analyzer.analyze(path, 45.0).thin_wall

        assert thin_wall is not None
        assert thin_wall.supported is True
        assert thin_wall.min_mm == pytest.approx(2.0, abs=1e-6)
        assert thin_wall.median_mm == pytest.approx(2.0, abs=1e-6)
        assert thin_wall.sample_count > 0

    def test_overhang_threshold_edge(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
        at_89 = analyzer.analyze(path, 89.0).overhang
        at_90 = analyzer.analyze(path, 90.0).overhang

        assert at_89 is not None and at_89.area_mm2 == pytest.approx(400.0)
        assert at_90 is not None and at_90.area_mm2 == pytest.approx(0.0)

    def test_overhang_45_degrees_not_flagged(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        path = write_ascii_stl(tmp_path / "slope.stl", box_mesh(40, 30, 2, rotation_x(45)))
        overhang = analyzer.analyze(path, 45.0).overhang

        assert overhang is not None
        assert overhang.face_count == 0
        assert overhang.area_mm2 == pytest.approx(0.0)

    def test_overhang_steep_slope_flagged(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        path = write_ascii_stl(tmp_path / "slope.stl", box_mesh(40, 30, 2, rotation_x(30)))
        overhang = analyzer.analyze(path, 45.0).overhang

        assert overhang is not None
        assert overhang.face_count == 2
        assert overhang.area_mm2 == pytest.approx(1200.0)

    def test_rotated_box_orientation_invariant(
        self, analyzer: TrimeshModelAnalyzer, tmp_path: Path
    ) -> None:
        path = write_ascii_stl(tmp_path / "rotated.stl", box_mesh(40, 20, 10, rotation_y(30)))
        orientation = analyzer.analyze(path, 45.0).orientation

        assert orientation is not None
        assert orientation.principal_extents_mm == pytest.approx((40, 20, 10))
        assert orientation.height_mm == pytest.approx(40 * 0.5 + 10 * np.cos(np.radians(30)))
        assert orientation.z_alignment == pytest.approx(np.cos(np.radians(30)))
        assert orientation.z_axis == pytest.approx((0.5, 0.0, np.cos(np.radians(30))))


class TestMeshDefects:
    def test_open_box_not_watertight(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        mesh = box_mesh(20, 20, 20)
        keep = [index for index in range(len(mesh.faces)) if index not in (2, 3)]
        open_mesh = Trimesh(vertices=mesh.vertices, faces=mesh.faces[keep], process=False)
        path = write_ascii_stl(tmp_path / "open.stl", open_mesh)
        result = analyzer.analyze(path, 45.0)

        assert result.volume_mm3 is None
        assert result.topology is not None
        assert result.topology.watertight is False
        assert result.topology.boundary_edge_count == 4
        assert result.thin_wall is not None and result.thin_wall.supported is False
        assert any("watertight" in note for note in result.notes)

    def test_non_manifold_edge(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        verts = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, -1.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        faces = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]])
        path = write_ascii_stl(
            tmp_path / "nm.stl", Trimesh(vertices=verts, faces=faces, process=False)
        )
        result = analyzer.analyze(path, 45.0)

        assert result.topology is not None
        assert result.topology.non_manifold_edge_count == 1
        assert result.topology.boundary_edge_count == 6
        assert result.topology.component_count == 1
        assert result.topology.watertight is False

    def test_degenerate_face(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        cube = box_mesh(20, 20, 20)
        verts = np.vstack(
            [cube.vertices, [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]]]
        )
        extra_face = [[len(cube.vertices), len(cube.vertices) + 1, len(cube.vertices) + 2]]
        faces = np.vstack([cube.faces, extra_face])

        path = write_ascii_stl(
            tmp_path / "dg.stl", Trimesh(vertices=verts, faces=faces, process=False)
        )
        result = analyzer.analyze(path, 45.0)

        assert result.topology is not None
        assert result.topology.degenerate_face_count == 1
        assert result.topology.triangle_count == 13
        assert any("degenerate" in note for note in result.topology.notes)

    def test_multi_component(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        first = box_mesh(20, 20, 20)
        second_transform = np.array(
            [[1.0, 0, 0, 50], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        )
        second = box_mesh(20, 20, 20, second_transform)

        verts = np.vstack([first.vertices, second.vertices])
        faces = np.vstack([first.faces, second.faces + len(first.vertices)])
        path = write_ascii_stl(
            tmp_path / "mc.stl", Trimesh(vertices=verts, faces=faces, process=False)
        )
        result = analyzer.analyze(path, 45.0)

        assert result.topology is not None
        assert result.topology.component_count == 2
        assert result.topology.watertight is True
        assert result.volume_mm3 == pytest.approx(16000.0)
        assert result.dimensions_mm == pytest.approx((70, 20, 20))

    def test_inside_out_cube(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        mesh = box_mesh(20, 20, 20)
        inverted = Trimesh(vertices=mesh.vertices, faces=mesh.faces[:, ::-1], process=False)

        path = write_ascii_stl(tmp_path / "io.stl", inverted)
        result = analyzer.analyze(path, 45.0)

        assert result.topology is not None and result.topology.watertight is True
        assert result.volume_mm3 == pytest.approx(8000.0)
        assert any("inside-out" in note for note in result.notes)

    def test_binary_stl(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_binary_stl(tmp_path / "cube.stl", box_mesh(20, 20, 20))
        result = analyzer.analyze(path, 45.0)

        assert result.volume_mm3 == pytest.approx(8000.0)
        assert result.dimensions_mm == pytest.approx((20, 20, 20))


class TestInputValidation:
    def test_missing_file(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(tmp_path / "missing.stl", 45.0)
        assert excinfo.value.details["reason"] == "not_found"

    def test_directory_not_a_file(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        directory = tmp_path / "model.stl"
        directory.mkdir()
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(directory, 45.0)
        assert excinfo.value.details["reason"] == "not_a_file"

    def test_unsupported_suffix(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = tmp_path / "model.obj"
        path.write_text("o object", encoding="utf-8")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "unsupported_suffix"

    def test_empty_file(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = tmp_path / "empty.stl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "empty"

    def test_empty_geometry(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = tmp_path / "empty.stl"
        path.write_text("solid empty\nendsolid empty\n", encoding="utf-8")
        with pytest.raises(InvalidModel) as excinfo:
            analyzer.analyze(path, 45.0)
        assert excinfo.value.details["reason"] == "empty_geometry"

    def test_uppercase_suffix(self, analyzer: TrimeshModelAnalyzer, tmp_path: Path) -> None:
        path = write_ascii_stl(tmp_path / "MODEL.STL", box_mesh(20, 20, 20))
        result = analyzer.analyze(path, 45.0)
        assert result.format == "stl"
        assert result.volume_mm3 == pytest.approx(8000.0)
