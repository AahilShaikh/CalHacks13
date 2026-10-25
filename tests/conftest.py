"""Shared STL fixtures with known dimensions, built once per test session."""
import pytest


@pytest.fixture(autouse=True)
def simulated_camera_inventory(monkeypatch):
    """Unit/integration tests never enumerate or open the host's real cameras."""
    monkeypatch.setenv('AIDA_TURNTABLE_CAMERA_ID', 'test-usb-camera')
    monkeypatch.setattr('aida.hardware.cameras.list_cameras', lambda: [{
        'index': 0, 'device_id': 'test-usb-camera', 'name': 'Test USB camera', 'is_usb': True}])


def _export(shape, path):
    from build123d import export_stl
    export_stl(shape, str(path), tolerance=0.01, angular_tolerance=0.1)
    return path


@pytest.fixture(scope='session')
def mesh_dir(tmp_path_factory):
    return tmp_path_factory.mktemp('meshes')


@pytest.fixture(scope='session')
def plate_stl(mesh_dir):
    """60 x 40 x 6 plate centred on the origin with a d=8 hole at (15, 5)."""
    from build123d import Box, Cylinder, Pos
    return _export(Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10), mesh_dir / 'plate.stl')


@pytest.fixture(scope='session')
def rounded_plate_stl(mesh_dir):
    """80 x 50 r8 plate, 5 thick on Z=0, with a 30 x 8 slot at (-20, 0) and d=6 hole at (25, 10)."""
    from build123d import Cylinder, Pos, RectangleRounded, SlotOverall, extrude
    plate = extrude(RectangleRounded(80, 50, 8), 5)
    plate -= Pos(-20, 0, 0) * extrude(SlotOverall(30, 8), 10, both=True)
    plate -= Pos(25, 10, 0) * Cylinder(3, 20)
    return _export(plate, mesh_dir / 'rounded_plate.stl')


@pytest.fixture(scope='session')
def bracket_stl(mesh_dir):
    """L bracket: 40 long, 5 thick base, 30 tall 5 thick wall, r4 inner fillet, 20 deep in Y."""
    from build123d import (Axis, BuildLine, BuildPart, BuildSketch, Plane, Polyline,
                           extrude, fillet, make_face)
    with BuildPart() as part:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Polyline((0, 0), (40, 0), (40, 5), (5, 5), (5, 30), (0, 30), close=True)
            make_face()
        extrude(amount=-20)
        inner = [e for e in part.edges().filter_by(Axis.Y)
                 if abs(e.center().X - 5) < 0.1 and abs(e.center().Z - 5) < 0.1]
        fillet(inner, 4)
    return _export(part.part, mesh_dir / 'bracket.stl')
