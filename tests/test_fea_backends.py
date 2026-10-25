"""CAD-conforming physics, backend selection and the standalone MCP protocol."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from aida.analysis import analyze_model, capabilities, select_backend
from aida.analysis.settings import FeaSettings

HAS_NGSOLVE = capabilities()['ngsolve']
requires_ngsolve = pytest.mark.skipif(not HAS_NGSOLVE, reason='Install the optional fea extra')


def solve_step(step, output, **settings):
    output.mkdir()
    config = output / 'settings.json'
    config.write_text(FeaSettings(engine='ngsolve', **settings).model_dump_json())
    completed = subprocess.run(
        [sys.executable, '-m', 'aida.fea', '--step', str(step), '--settings', str(config), '--out', str(output)],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'},
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads((output / 'report.json').read_text())


@pytest.fixture
def bar_step(tmp_path):
    from build123d import Box, export_step

    path = tmp_path / 'bar.step'
    export_step(Box(40, 20, 10), str(path))
    return path


def test_backend_selection_and_fallback_are_explicit(tmp_path, monkeypatch):
    import aida.analysis as analysis

    mesh = tmp_path / 'bar.stl'
    trimesh.creation.box([40, 10, 10]).export(mesh)
    step = tmp_path / 'bar.step'
    step.write_text('placeholder')
    monkeypatch.setattr(analysis, 'capabilities', lambda: {'ngsolve': False})
    assert select_backend(FeaSettings(), mesh, step) == 'voxel'
    with pytest.raises(ValueError, match='not installed'):
        select_backend(FeaSettings(engine='ngsolve'), mesh, step)
    report = analyze_model(mesh, FeaSettings(resolution=10), tmp_path / 'result', step)
    assert report['engine'] == 'voxel'
    assert 'not installed' in report['warnings'][0]
    monkeypatch.setattr(analysis, 'capabilities', lambda: {'ngsolve': True})
    assert select_backend(FeaSettings(), mesh, step) == 'ngsolve'
    assert select_backend(FeaSettings(engine='voxel'), mesh, step) == 'voxel'
    with pytest.raises(ValueError, match='no STEP snapshot'):
        select_backend(FeaSettings(engine='ngsolve'), mesh, None)
    with pytest.raises(ValueError, match='mesh is required'):
        select_backend(FeaSettings(engine='voxel'), None, step)


@requires_ngsolve
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


@requires_ngsolve
def test_step_bending_refines_and_force_scales_linearly(tmp_path):
    from build123d import Box, export_step

    step = tmp_path / 'beam.step'
    export_step(Box(60, 10, 10), str(step))
    coarse = solve_step(step, tmp_path / 'coarse', resolution=10)
    fine = solve_step(step, tmp_path / 'fine', resolution=22)
    doubled = solve_step(step, tmp_path / 'double', resolution=10, force_n=200)
    expected = 100 * 60**3 / (3 * 69000 * (10 * 10**3 / 12))
    assert abs(fine['max_displacement_mm'] - expected) < abs(coarse['max_displacement_mm'] - expected)
    assert fine['max_displacement_mm'] == pytest.approx(expected, rel=.2)
    assert doubled['max_displacement_mm'] == pytest.approx(2 * coarse['max_displacement_mm'], rel=1e-6)
    assert doubled['max_von_mises_mpa'] == pytest.approx(2 * coarse['max_von_mises_mpa'], rel=1e-6)


@requires_ngsolve
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


async def test_mcp_stdio_tools_and_workspace_boundary(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    source = tmp_path / 'bar.stl'
    trimesh.creation.box([40, 10, 10]).export(source)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'aida.analysis.mcp'],
                                   env={**os.environ, 'AIDA_FEA_ROOT': str(tmp_path)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {'fea_capabilities', 'run_fea'}
            info = await session.call_tool('fea_capabilities', {})
            assert not info.is_error
            invalid = await session.call_tool('run_fea', {'cad_file': '../outside.step'})
            assert invalid.is_error
            result = await session.call_tool('run_fea', {'cad_file': 'bar.stl', 'settings': {'resolution': 10}})
            assert not result.is_error, result
            report = result.structured_content
            assert report['engine'] == 'voxel'
            assert report['max_von_mises_mpa'] > 0
            assert all(str(tmp_path) in path for path in report['files'].values())
