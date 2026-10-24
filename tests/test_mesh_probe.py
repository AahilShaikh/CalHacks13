import math

import numpy as np
import pytest

from aida.mesh_probe import (ProbeError, chain_segments, draw_section, fit_circle, fit_loop, load,
                             min_area_rect, public, signed_area, slice_mesh, slice_series)


def run_snippet(code):
    namespace = {}
    exec('from build123d import *\n' + code, namespace)
    return namespace['section']


def test_chain_segments_dedupes_and_closes():
    square = [((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))]
    segments = np.array(square + [((1, 0), (0, 0))])  # repeated edge from a coplanar facet
    chains = chain_segments(segments, 1e-6)
    assert len(chains) == 1
    points, closed = chains[0]
    assert closed and len(points) == 4
    assert abs(signed_area(points)) == pytest.approx(1)


def test_primitive_fits_on_synthetic_points():
    t = np.linspace(0, 2 * math.pi, 40, endpoint=False)
    circle = np.column_stack([3 + 2 * np.cos(t), -1 + 2 * np.sin(t)])
    center, radius, residual = fit_circle(circle)
    assert center == pytest.approx([3, -1]) and radius == pytest.approx(2) and residual < 1e-9
    angle = math.radians(30)
    corners = np.array([(-10, -5), (10, -5), (10, 5), (-10, 5)])
    rotation = np.array([[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]])
    rect = corners @ rotation + (4, 2)
    center, width, height, degrees = min_area_rect(rect)
    assert (width, height, degrees) == pytest.approx((20, 10, 30))
    assert center == pytest.approx([4, 2])
    assert fit_loop(rect, 0.01)['type'] == 'rectangle'


def test_plate_slice_finds_rectangle_and_hole(plate_stl):
    result = slice_mesh(load(plate_stl), axis='z', at=0.5)
    assert (result['outer_count'], result['hole_count']) == (1, 1)
    outer, hole = result['loops']
    assert outer['fit']['type'] == 'rectangle'
    assert (outer['fit']['width'], outer['fit']['height']) == pytest.approx((60, 40), abs=1e-3)
    assert hole['role'] == 'hole' and hole['parent'] == 'L0'
    assert hole['fit']['type'] == 'circle'
    assert hole['fit']['center'] == pytest.approx([15, 5], abs=0.01)
    assert hole['fit']['diameter'] == pytest.approx(8, abs=0.02)
    assert result['plane']['build123d_plane'] == 'Plane.XY.offset(0.5)'
    section = run_snippet(result['build123d'])
    assert section.area == pytest.approx(result['material_area'], rel=1e-3)
    assert section.center().Z == pytest.approx(0.5)


def test_rounded_plate_slot_and_hole(rounded_plate_stl):
    result = slice_mesh(load(rounded_plate_stl), axis='z', at=2.5)
    fits = {loop['fit']['type']: loop['fit'] for loop in result['loops']}
    assert set(fits) == {'rounded_rectangle', 'slot', 'circle'}
    outer = fits['rounded_rectangle']
    assert (outer['width'], outer['height'], outer['corner_radius']) == pytest.approx((80, 50, 8), abs=0.1)
    assert (fits['slot']['length'], fits['slot']['width']) == pytest.approx((30, 8), abs=0.05)
    assert fits['slot']['center'] == pytest.approx([-20, 0], abs=0.05)
    assert fits['circle']['diameter'] == pytest.approx(6, abs=0.02)
    assert run_snippet(result['build123d']).area == pytest.approx(result['material_area'], rel=2e-3)


def test_bracket_profile_has_fillet_arc(bracket_stl, tmp_path):
    result = slice_mesh(load(bracket_stl), axis='y', at=10)
    assert result['plane']['coordinates'] == ['X', 'Z']
    assert result['plane']['build123d_plane'] == 'Plane.XZ.offset(-10)'
    fit = result['loops'][0]['fit']
    assert fit['type'] == 'profile'
    arcs = [s for s in fit['segments'] if s['type'] == 'arc']
    assert len(arcs) == 1 and arcs[0]['radius'] == pytest.approx(4, abs=0.15)
    assert fit['max_deviation'] <= result['tolerance']
    section = run_snippet(result['build123d'])
    assert section.area == pytest.approx(result['material_area'], rel=5e-3)
    assert section.center().Y == pytest.approx(10)
    draw_section(result, tmp_path / 'section.png', 'mm')
    assert (tmp_path / 'section.png').stat().st_size > 0
    assert '_points' not in public(result)


def test_oblique_slice(plate_stl):
    result = slice_mesh(load(plate_stl), normal=[1, 1, 0], at=-25)
    fit = result['loops'][0]['fit']
    assert fit['type'] == 'rectangle'
    # The plane x + y = -25*sqrt(2) cuts the corner (-30, -20) off the plate: its chord runs
    # from (-30, 30 - 25*sqrt(2)) to (20 - 25*sqrt(2), -20), length 50*sqrt(2) - 50.
    assert sorted([fit['width'], fit['height']]) == pytest.approx([6, 50 * math.sqrt(2) - 50], abs=1e-3)
    section = run_snippet(result['build123d'])
    assert section.area == pytest.approx(result['material_area'], rel=1e-3)
    assert section.center().X + section.center().Y == pytest.approx(-25 * math.sqrt(2), abs=1e-3)


def test_slice_errors(plate_stl):
    mesh = load(plate_stl)
    with pytest.raises(ProbeError, match='outside'):
        slice_mesh(mesh, axis='z', at=10)
    with pytest.raises(ProbeError, match='exactly one'):
        slice_mesh(mesh, axis='z', normal=[0, 0, 1])


def test_series_finds_bracket_step(bracket_stl):
    result = slice_series(load(bracket_stl), axis='z', count=8)
    assert len(result['transitions']) == 1
    low, high = result['transitions'][0]['between']
    assert low <= 5 <= high and high - low <= result['tolerance']
    first, last = result['constant_segments'][0], result['constant_segments'][-1]
    assert first['profile']['material_area'] == pytest.approx(800, rel=1e-3)
    assert last['profile']['material_area'] == pytest.approx(100, rel=1e-2)


def test_frame_of_aligned_plate(plate_stl):
    from aida.mesh_probe import mesh_frame
    frame = mesh_frame(load(plate_stl))
    assert frame['aligned_with_world'] and frame['max_tilt_deg'] < 0.01
    assert sorted(frame['oriented_box']['extents']) == pytest.approx([6, 40, 60], abs=1e-3)
    planes = {level['plane'] for group in frame['planar_groups'] for level in group['levels']}
    assert {'Z=3', 'Z=-3', 'X=30', 'X=-30', 'Y=20', 'Y=-20'} <= planes
    assert 'placement' not in frame


def test_frame_of_tilted_plate_gives_placement(mesh_dir):
    from build123d import Box, Cylinder, Location, Pos, export_stl
    from aida.mesh_probe import mesh_frame
    path = mesh_dir / 'tilted.stl'
    export_stl(Location((5, -3, 10), (12, -7, 25)) * (Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10)), str(path))
    frame = mesh_frame(load(path))
    assert not frame['aligned_with_world'] and frame['max_tilt_deg'] > 10
    assert frame['oriented_box']['extents'] == pytest.approx([60, 40, 6], abs=1e-3)
    placed = eval(frame['placement'], {'Location': Location})
    assert placed.position.X == pytest.approx(5, abs=1e-3) and placed.position.Z == pytest.approx(10, abs=1e-3)
    assert [placed.orientation.X, placed.orientation.Y, placed.orientation.Z] == pytest.approx([12, -7, 25], abs=1e-2)


def test_euler_roundtrip_matches_build123d():
    from build123d import Location, Vector
    from aida.mesh_probe import euler_xyz
    angles = (-40, 25, 130)
    loc = Location((0, 0, 0), angles)
    columns = []
    for axis in np.eye(3):
        p = (loc * Location(Vector(*axis))).position
        columns.append([p.X, p.Y, p.Z])
    assert euler_xyz(np.array(columns).T) == pytest.approx(angles, abs=1e-6)


def test_sparse_rectangle_is_not_a_circle():
    # A rectangle traced with a few extra points near its corners: all vertices sit close to one
    # circle, so the edges must be checked too.
    loop = np.array([(0, 0), (0.04, 0), (5, 0), (5, 19.86), (5, 20), (0.04, 20), (0, 20), (0, 19.9)])
    fit = fit_loop(loop, 0.135)
    assert fit['type'] == 'rectangle'
    assert (fit['width'], fit['height']) == pytest.approx((5, 20)) or \
        (fit['width'], fit['height']) == pytest.approx((20, 5))
