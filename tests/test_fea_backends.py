"""CAD-conforming physics (Netgen/NGSolve) and the standalone MCP protocol."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from aida.analysis import require_step
from aida.analysis.settings import FeaSettings


def solve_step(step, output, **settings):
    output.mkdir()
    config = output / 'settings.json'
    config.write_text(FeaSettings(**settings).model_dump_json())
    completed = subprocess.run(
        [sys.executable, '-m', 'aida.analysis', '--step', str(step), '--settings', str(config), '--out', str(output)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ['STAGE', 'meshing', 'STAGE', 'solving', 'STAGE', 'post-processing']
    return json.loads((output / 'report.json').read_text())


@pytest.fixture
def bar_step(tmp_path):
    from build123d import Box, export_step

    path = tmp_path / 'bar.step'
    export_step(Box(40, 20, 10), str(path))
    return path


def test_require_step_explains_a_missing_snapshot(tmp_path):
    with pytest.raises(ValueError, match='no STEP snapshot'):
        require_step(None)
    with pytest.raises(ValueError, match='no STEP snapshot'):
        require_step(tmp_path / 'missing.step')


@pytest.mark.parametrize('face', ['x-', 'x+', 'y-', 'y+', 'z-', 'z+'])
def test_step_axial_bar_matches_exact_solution(bar_step, tmp_path, face):
    axis = 'xyz'.index(face[0])
    extents = np.array([40, 20, 10])
    report = solve_step(bar_step, tmp_path / 'result', resolution=10, poisson_ratio=0,
                        fixed_face=face, load_direction=face[0] + ('+' if face[1] == '-' else '-'))
    area = np.prod(np.delete(extents, axis))
    assert report['engine'] == 'ngsolve'
    assert report['max_von_mises_mpa'] == pytest.approx(100 / area, rel=1e-6)
    assert report['max_displacement_mm'] == pytest.approx(100 * extents[axis] / (area * 69000), rel=1e-6)
    np.testing.assert_allclose(report['reaction_force_n'], -np.array(report['force_vector_n']), atol=1e-6)
    assert report['relative_residual'] < 1e-6
    assert report['volume_error_fraction'] < 1e-6
    for name in ['stress', 'displacement']:
        mesh = trimesh.load(tmp_path / 'result' / f'{name}.glb', force='mesh')
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.visual.kind == 'vertex'
        assert mesh.volume > 0


def test_quadratic_cantilever_matches_beam_theory_and_scales_linearly(tmp_path):
    from build123d import Box, export_step

    step = tmp_path / 'beam.step'
    export_step(Box(60, 10, 10), str(step))
    coarse = solve_step(step, tmp_path / 'coarse', resolution=10)
    fine = solve_step(step, tmp_path / 'fine', resolution=22)
    doubled = solve_step(step, tmp_path / 'double', resolution=10, force_n=200)
    euler_bernoulli = 100 * 60**3 / (3 * 69000 * (10 * 10**3 / 12))
    # Quadratic elements are accurate even on a coarse mesh; shear adds ~1% over Euler-Bernoulli.
    for report in [coarse, fine]:
        assert report['max_displacement_mm'] == pytest.approx(euler_bernoulli, rel=.03)
    # Root bending stress M*c/I = 100*60*5/833.3 = 36 MPa; the clamp corner adds a local peak.
    assert 30 < coarse['max_von_mises_mpa'] < 60
    assert doubled['max_displacement_mm'] == pytest.approx(2 * coarse['max_displacement_mm'], rel=1e-6)
    assert doubled['max_von_mises_mpa'] == pytest.approx(2 * coarse['max_von_mises_mpa'], rel=1e-6)


def test_step_mesh_preserves_a_through_hole(tmp_path):
    from build123d import Box, Cylinder, export_step

    step = tmp_path / 'plate.step'
    export_step(Box(40, 20, 10) - Cylinder(3, 12), str(step))
    report = solve_step(step, tmp_path / 'result', resolution=10)
    surface = trimesh.load(tmp_path / 'result' / 'stress.glb', force='mesh')
    assert report['cad_volume_mm3'] == pytest.approx(8000 - np.pi * 3**2 * 10, rel=1e-6)
    assert report['volume_error_fraction'] < .01
    assert surface.euler_number == 0  # A single through hole, not a filled box.
    assert np.linalg.norm(surface.triangles_center[:, :2], axis=1).min() > 2.8


async def test_mcp_stdio_tools_and_workspace_boundary(tmp_path, bar_step):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    trimesh.creation.box([40, 10, 10]).export(tmp_path / 'bar.stl')
    params = StdioServerParameters(command=sys.executable, args=['-m', 'aida.analysis.mcp'],
                                   env={**os.environ, 'AIDA_FEA_ROOT': str(tmp_path)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {'fea_capabilities', 'run_fea'}
            info = await session.call_tool('fea_capabilities', {})
            assert not info.is_error
            assert (await session.call_tool('run_fea', {'cad_file': '../outside.step'})).is_error
            assert (await session.call_tool('run_fea', {'cad_file': 'bar.stl'})).is_error
            result = await session.call_tool('run_fea', {'cad_file': 'bar.step', 'settings': {'resolution': 10}})
            assert not result.is_error, result
            report = result.structured_content
            assert report['engine'] == 'ngsolve'
            assert report['max_von_mises_mpa'] > 0
            assert all(str(tmp_path) in path for path in report['files'].values())


def list_faces(step, output):
    completed = subprocess.run([sys.executable, '-m', 'aida.analysis', '--faces', '--step', str(step), '--out', str(output)],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads((output / 'faces.json').read_text())


def test_face_listing_describes_every_face_and_draws_them(bar_step, tmp_path):
    listing = list_faces(bar_step, tmp_path / 'faces')
    faces = {f['id']: f for f in listing['faces']}
    assert len(faces) == 6 and all(f['kind'] == 'flat' for f in faces.values())
    x_min = next(f for f in faces.values() if f['normal'] == [-1.0, 0.0, 0.0])
    assert x_min['area_mm2'] == pytest.approx(200) and x_min['center_mm'][0] == pytest.approx(-20)
    assert (tmp_path / 'faces' / 'faces.png').stat().st_size > 10_000


def test_chosen_faces_match_the_bounding_box_setup_and_checks_run(bar_step, tmp_path):
    faces = list_faces(bar_step, tmp_path / 'faces')['faces']
    x_min = next(f['id'] for f in faces if f['normal'] == [-1.0, 0.0, 0.0])
    x_max = next(f['id'] for f in faces if f['normal'] == [1.0, 0.0, 0.0])
    ends = solve_step(bar_step, tmp_path / 'ends', resolution=10, fixed_face='x-', load_direction='z-')
    chosen = solve_step(bar_step, tmp_path / 'chosen', resolution=10, support_faces=[x_min], load_faces=[x_max],
                        load_direction='z-')
    assert chosen['support_faces'] == ends['support_faces'] == [x_min]
    assert chosen['max_displacement_mm'] == pytest.approx(ends['max_displacement_mm'], rel=1e-9)
    # The cantilever's root corners are a rigid-support artifact; the reported peak away from them is lower.
    assert chosen['peak_at_support'] is True
    assert chosen['peak_away_from_supports_mpa'] < chosen['max_von_mises_mpa']
    assert (tmp_path / 'chosen' / 'setup.png').stat().st_size > 10_000


def test_mesh_check_reports_a_converged_axial_bar(bar_step, tmp_path):
    output = tmp_path / 'checked'
    output.mkdir()
    (output / 'settings.json').write_text(FeaSettings(resolution=10, poisson_ratio=0, fixed_face='x-', load_direction='x+',
                                                      check_mesh=True).model_dump_json())
    completed = subprocess.run([sys.executable, '-m', 'aida.analysis', '--step', str(bar_step), '--settings',
                                str(output / 'settings.json'), '--out', str(output)], capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split()[-2:] == ['checking', 'mesh']
    check = json.loads((output / 'report.json').read_text())['mesh_check']
    assert check['peak_away_change'] < 1e-4 and check['displacement_change'] < 1e-4


def test_face_choices_are_validated(bar_step, tmp_path):
    with pytest.raises(ValueError, match='both a support and a load'):
        FeaSettings(support_faces=[1], load_faces=[1, 2])
    output = tmp_path / 'bad'
    output.mkdir()
    (output / 'settings.json').write_text(FeaSettings(support_faces=[40], load_faces=[1]).model_dump_json())
    completed = subprocess.run([sys.executable, '-m', 'aida.analysis', '--step', str(bar_step), '--settings',
                                str(output / 'settings.json'), '--out', str(output)], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 1 and 'not faces of this part' in completed.stderr
