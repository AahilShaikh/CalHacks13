import numpy as np
import pytest
from PIL import Image

from aida.mesh_compare import EXTRA, MISSING, CompareError, deviation
from aida.mesh_probe import load, polyline_distance, slice_and_compare
from aida.mesh_render import render


@pytest.fixture(scope='session')
def cad_variants(mesh_dir):
    """CAD candidates for the 60 x 40 x 6 plate with a d=8 hole at (15, 5)."""
    from build123d import Align, Box, Cylinder, Pos, export_stl
    shapes = {
        'thick_no_hole': Box(60, 40, 7),
        'short': Box(50, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10),
        'boss': Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10)
                + Pos(-20, -10, 3) * Cylinder(3, 4, align=(Align.CENTER, Align.CENTER, Align.MIN)),
        'shifted_hole': Box(60, 40, 6) - Pos(16, 5, 0) * Cylinder(4.5, 10),
    }
    paths = {}
    for name, shape in shapes.items():
        paths[name] = mesh_dir / f'cad_{name}.stl'
        export_stl(shape, str(paths[name]))
    return paths


def test_exact_cad_is_within_threshold(plate_stl):
    report = deviation(plate_stl, plate_stl, samples=5000)
    assert report['verdict'] == 'within_threshold'
    assert report['reference_to_cad']['max_abs'] < 1e-6
    assert report['regions'] == []
    assert report['volume']['cad_over_reference'] == pytest.approx(1)


def test_extra_material_found_at_missing_hole(plate_stl, cad_variants):
    report = deviation(plate_stl, cad_variants['thick_no_hole'], samples=10000)
    assert report['verdict'] == 'deviates'
    assert report['reference_to_cad']['share_cad_extra_material'] > 0.5
    assert report['reference_to_cad']['share_cad_missing_material'] == 0
    hole = [r for r in report['regions'] if np.linalg.norm(np.subtract(r['center'][:2], (15, 5))) < 1]
    assert hole and hole[0]['kind'] == EXTRA
    assert hole[0]['max_deviation'] == pytest.approx(3.5, abs=0.1)
    assert report['bbox']['delta_max'][2] == pytest.approx(0.5)


def test_missing_material_and_extra_boss(plate_stl, cad_variants):
    short = deviation(plate_stl, cad_variants['short'], samples=10000)
    ends = [r for r in short['regions'] if r['kind'] == MISSING]
    assert {round(r['center'][0] / 5) * 5 for r in ends} >= {-30, 30}
    boss = deviation(plate_stl, cad_variants['boss'], samples=10000)
    top = boss['regions'][0]
    assert top['kind'] == EXTRA
    assert top['center'][:2] == pytest.approx([-20, -10], abs=0.5)
    assert top['max_deviation'] == pytest.approx(4, abs=0.1)


def test_deviation_rejects_bad_options(plate_stl):
    with pytest.raises(CompareError, match='samples'):
        deviation(plate_stl, plate_stl, samples=10)


def test_heatmap_and_overlay_renders(plate_stl, cad_variants, tmp_path):
    heat = tmp_path / 'heat.png'
    meta = render(plate_stl, heat, {'direction': 'top', 'grid': False}, 'mm',
                  heatmap={'cad': cad_variants['thick_no_hole'], 'range': 2.0})
    assert meta['heatmap']['range'] == 2.0
    image = Image.open(heat).convert('RGB')
    # World (-10, -10) on the top face: the CAD top sits 0.5 above it, so it reads light blue.
    r, g, b = image.getpixel((round(400 - 10 / meta['units_per_pixel']),
                              round(300 + 10 / meta['units_per_pixel'])))
    assert b > r + 20
    overlay = tmp_path / 'overlay.png'
    meta = render(plate_stl, overlay, {'slice_axis': 'y', 'slice_at': 5, 'direction': 'iso'}, 'mm',
                  overlay=cad_variants['shifted_hole'])
    assert 'overlay' in meta
    colors = Image.open(overlay).convert('RGB').resize((200, 150)).getcolors(200 * 150)
    assert any(g > r + 40 and g > b + 40 for _, (r, g, b) in colors)  # green CAD


def test_polyline_distance():
    square = np.array([(0, 0), (2, 0), (2, 2), (0, 2)], float)
    points = np.array([(1, 1), (1, -1), (3, 3)], float)
    assert polyline_distance(points, square) == pytest.approx([1, 1, np.sqrt(2)])


def test_section_comparison(plate_stl, cad_variants):
    exact = slice_and_compare(load(plate_stl), load(plate_stl), axis='z', at=1)['comparison']
    assert exact['within_tolerance'] and exact['max_boundary_deviation'] < 1e-6
    shifted = slice_and_compare(load(plate_stl), load(cad_variants['shifted_hole']), axis='z', at=1)
    hole = next(m for m in shifted['comparison']['matched'] if m['role'] == 'hole')
    assert hole['max_boundary_deviation'] == pytest.approx(1.5, abs=0.05)
    assert hole['centroid_offset'] == pytest.approx([1, 0], abs=0.02)
    assert not shifted['comparison']['within_tolerance']
    missing = slice_and_compare(load(plate_stl), load(cad_variants['thick_no_hole']), axis='z', at=1)
    assert [m['role'] for m in missing['comparison']['missing_in_cad']] == ['hole']
    # A plane that misses the CAD reports every reference loop as missing.
    above = slice_and_compare(load(plate_stl), load(cad_variants['short']), axis='x', at=27)
    assert len(above['comparison']['missing_in_cad']) == 1
