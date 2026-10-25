"""Physics checks, solid meshing, and revision-bound FEA API jobs (no CAD/API key)."""
import asyncio
import time

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from aida.fea import FeaSettings, analyze, elasticity, strain_matrix, voxel_mesh


def test_hexahedron_affine_strain_patch():
    from aida.fea import CORNERS

    size = np.array([2., 3., 5.])
    # Exact affine displacement field, including shear and a rigid translation.
    gradient = np.array([[.01, .02, 0], [0, -.03, .04], [.05, 0, .06]])
    u = ((CORNERS * size) @ gradient.T + [5, 6, 7]).ravel()
    expected = [.01, -.03, .06, .02, .04, .05]
    for point in [np.zeros(3), np.array([.4, -.7, .1])]:
        np.testing.assert_allclose(strain_matrix(size, point) @ u, expected, atol=1e-14)
    assert np.linalg.eigvalsh(elasticity(69000, .33)).min() > 0


@pytest.mark.parametrize('face', ['x-', 'x+', 'y-', 'y+', 'z-', 'z+'])
def test_axial_bar_matches_analytical_solution_and_balances_force(tmp_path, face):
    mesh = trimesh.creation.box([40, 20, 10])
    path = tmp_path / 'bar.stl'
    mesh.export(path)
    axis = 'xyz'.index(face[0])
    settings = FeaSettings(resolution=10, poisson_ratio=0, fixed_face=face,
                           load_direction=face[0] + ('+' if face[1] == '-' else '-'))
    report = analyze(path, settings, tmp_path / 'results')
    area = np.prod(np.delete(mesh.extents, axis))
    expected_stress = settings.force_n / area
    expected_displacement = settings.force_n * mesh.extents[axis] / (area * settings.youngs_modulus_mpa)
    assert report['max_von_mises_mpa'] == pytest.approx(expected_stress, rel=1e-5)
    assert report['max_displacement_mm'] == pytest.approx(expected_displacement, rel=1e-5)
    np.testing.assert_allclose(report['reaction_force_n'], -np.array(report['force_vector_n']), atol=1e-5)
    assert report['relative_residual'] < 1e-6
    for name in ['stress', 'displacement']:
        surface = trimesh.load(tmp_path / 'results' / f'{name}.glb', force='mesh')
        assert surface.is_watertight
        assert surface.visual.kind == 'vertex'


def test_cantilever_refines_toward_beam_deflection(tmp_path):
    mesh = trimesh.creation.box([60, 10, 10])
    path = tmp_path / 'beam.stl'
    mesh.export(path)
    coarse = analyze(path, FeaSettings(resolution=10), tmp_path / 'coarse')
    fine = analyze(path, FeaSettings(resolution=22), tmp_path / 'fine')
    beam_deflection = 100 * 60**3 / (3 * 69000 * (10 * 10**3 / 12))
    assert abs(fine['max_displacement_mm'] - beam_deflection) < abs(coarse['max_displacement_mm'] - beam_deflection)
    assert fine['max_displacement_mm'] == pytest.approx(beam_deflection, rel=.12)
    doubled = analyze(path, FeaSettings(resolution=10, force_n=200), tmp_path / 'double')
    assert doubled['max_displacement_mm'] == pytest.approx(2 * coarse['max_displacement_mm'], rel=1e-5)
    assert doubled['max_von_mises_mpa'] == pytest.approx(2 * coarse['max_von_mises_mpa'], rel=1e-5)


def test_grid_preserves_resolved_through_hole():
    mesh = trimesh.creation.annulus(r_min=4, r_max=10, height=10)
    nodes, elements, _, _ = voxel_mesh(mesh, 22)
    centers = nodes[elements].mean(axis=1)
    assert np.linalg.norm(centers[:, :2], axis=1).min() > 3.9


def test_reject_open_and_disconnected_meshes(tmp_path):
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    with pytest.raises(ValueError, match='closed'):
        voxel_mesh(mesh, 10)
    first = trimesh.creation.box()
    second = trimesh.creation.box()
    second.apply_translation([3, 0, 0])
    with pytest.raises(ValueError, match='one connected'):
        voxel_mesh(trimesh.util.concatenate([first, second]), 10)


@pytest.fixture
def api(tmp_path, monkeypatch):
    import aida.api.server as server
    from aida.api.cad_sessions import CadSessionManager

    monkeypatch.setenv('AIDA_PREWARM', '0')
    manager = CadSessionManager(tmp_path / 'cad')
    monkeypatch.setattr(server, 'cad_manager', manager)
    mesh = trimesh.creation.box([40, 10, 10])
    session = manager.create([('box.stl', mesh.export(file_type='stl'))], [], 'test', 'mm')
    path = session.artifacts.root / 'mesh-001.glb'
    mesh.export(path)
    session.meshes.append({'revision': 1, 'path': path, 'generated': True})
    with TestClient(server.app) as client:
        yield client, session


def test_api_runs_on_immutable_generated_revision_and_serves_results(api):
    client, session = api
    base = f'/api/v1/cad-sessions/{session.id}/fea'
    created = client.post(base, json={'revision': 1, 'resolution': 10})
    assert created.status_code == 202, created.text
    job = created.json()
    assert 'directory' not in job and 'task' not in job
    assert 'step_path' not in job
    status_url = job['status_url']
    # Later CAD revisions must not relabel the running test.
    session.meshes.append({'revision': 2, 'path': session.meshes[1]['path'], 'generated': True})
    for _ in range(150):
        job = client.get(status_url).json()
        if job['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(.1)
    assert job['status'] == 'succeeded', job
    assert job['result']['revision'] == 1
    assert job['result']['max_von_mises_mpa'] > 0
    for key in ['stress_url', 'displacement_url', 'report_url']:
        response = client.get(job['result'][key])
        assert response.status_code == 200
        assert 'immutable' in response.headers['cache-control']
    assert client.get(job['result']['report_url']).json()['revision'] == 1
    assert client.get(f'{status_url}/files/settings.json').status_code == 404
    assert client.get(f'{base}/unknown').status_code == 404


def test_api_validates_units_revision_settings_and_concurrency(api, monkeypatch):
    import aida.api.fea as api_module

    client, session = api
    base = f'/api/v1/cad-sessions/{session.id}/fea'
    assert client.post(base, json={'revision': 0}).status_code == 422
    assert client.post(base, json={'revision': 9}).status_code == 404
    for extra in [{'force_n': 0}, {'poisson_ratio': .5}, {'resolution': 1000}, {'force_n': 'NaN'}, {'fixed_face': 'oops'}]:
        assert client.post(base, json={'revision': 1, **extra}).status_code == 422
    session.units = None
    assert client.post(base, json={'revision': 1}).status_code == 409
    session.units = 'mm'

    async def blocked(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(api_module, 'run_analysis', blocked)
    assert client.post(base, json={'revision': 1}).status_code == 202
    assert client.post(base, json={'revision': 1}).status_code == 409
    assert client.post('/api/v1/cad-sessions/missing/fea', json={'revision': 1}).status_code == 404


def test_api_uses_the_selected_revisions_step_snapshot(api):
    from aida.analysis import capabilities

    if not capabilities()['ngsolve']:
        pytest.skip('Install the optional fea extra')
    from build123d import Box, export_step

    client, session = api
    step = session.artifacts.root / 'cad-001.step'
    export_step(Box(40, 10, 10), str(step))
    session.meshes[1]['step_path'] = step
    base = f'/api/v1/cad-sessions/{session.id}/fea'
    created = client.post(base, json={'revision': 1, 'resolution': 10, 'load_direction': 'x+', 'poisson_ratio': 0})
    assert created.status_code == 202, created.text
    job = created.json()
    assert job['engine'] == 'ngsolve'
    assert 'step_path' not in job
    # A later viewer revision has a different shape; rev 1 must retain its STEP.
    later_step = session.artifacts.root / 'cad-002.step'
    export_step(Box(80, 20, 20), str(later_step))
    session.meshes.append({'revision': 2, 'path': session.meshes[1]['path'],
                           'generated': True, 'step_path': later_step})
    for _ in range(150):
        job = client.get(job['status_url']).json()
        if job['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(.1)
    assert job['status'] == 'succeeded', job
    assert job['result']['engine'] == 'ngsolve'
    assert job['result']['revision'] == 1
    assert job['result']['cad_volume_mm3'] == pytest.approx(4000)
    assert job['result']['max_von_mises_mpa'] == pytest.approx(1)
    assert client.get(job['result']['stress_url']).status_code == 200


async def test_timed_out_worker_is_killed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import aida.api.fea as api_module

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            raise TimeoutError

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(api_module, '_manager', lambda: SimpleNamespace(fea_slots=asyncio.Semaphore(1)))
    job = {'directory': tmp_path, 'revision': 1, 'analysis_id': 'test'}
    await api_module.run_analysis(SimpleNamespace(id='session'), job, tmp_path / 'mesh.glb')
    assert job['status'] == 'failed'
    assert 'two-minute' in job['error']
    assert process.killed
