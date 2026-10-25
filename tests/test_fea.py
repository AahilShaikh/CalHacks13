"""Revision-bound FEA API jobs and their session events (no CAD engine or API key)."""
import asyncio
import time

import pytest
import trimesh
from fastapi.testclient import TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    import aida.api.server as server
    from aida.api.cad_sessions import CadSessionManager
    from build123d import Box, export_step

    monkeypatch.setenv('AIDA_PREWARM', '0')
    manager = CadSessionManager(tmp_path / 'cad')
    monkeypatch.setattr(server, 'cad_manager', manager)
    mesh = trimesh.creation.box([40, 10, 10])
    session = manager.create([('box.stl', mesh.export(file_type='stl'))], [], 'test', 'mm')
    path = session.artifacts.root / 'mesh-001.glb'
    mesh.export(path)
    step = session.artifacts.root / 'cad-001.step'
    export_step(Box(40, 10, 10), str(step))
    session.meshes.append({'revision': 1, 'path': path, 'generated': True, 'step_path': step})
    with TestClient(server.app) as client:
        yield client, session


def wait_for(client, status_url):
    for _ in range(300):
        job = client.get(status_url).json()
        if job['status'] in {'succeeded', 'failed'}:
            return job
        time.sleep(.1)
    raise AssertionError('FEA did not finish')


def test_api_runs_on_immutable_revision_publishes_events_and_serves_results(api):
    from build123d import Box, export_step

    client, session = api
    base = f'/api/v1/cad-sessions/{session.id}/fea'
    created = client.post(base, json={'revision': 1, 'resolution': 10, 'load_direction': 'x+', 'poisson_ratio': 0})
    assert created.status_code == 202, created.text
    job = created.json()
    assert not {'directory', 'task', 'step_path', 'done'} & set(job)
    # A later revision has a different shape; rev 1 must keep its own STEP.
    later_step = session.artifacts.root / 'cad-002.step'
    export_step(Box(80, 20, 20), str(later_step))
    session.meshes.append({'revision': 2, 'path': session.meshes[1]['path'],
                           'generated': True, 'step_path': later_step})
    job = wait_for(client, job['status_url'])
    assert job['status'] == 'succeeded', job
    result = job['result']
    assert result['engine'] == 'ngsolve' and result['revision'] == 1
    assert result['cad_volume_mm3'] == pytest.approx(4000)
    assert result['max_von_mises_mpa'] == pytest.approx(1)
    for key in ['stress_url', 'displacement_url', 'report_url']:
        response = client.get(result[key])
        assert response.status_code == 200
        assert 'immutable' in response.headers['cache-control']
    assert client.get(result['report_url']).json()['revision'] == 1
    assert client.get(f"{job['status_url']}/files/settings.json").status_code == 404
    assert client.get(f'{base}/unknown').status_code == 404

    events = [e for e in session.events if e['type'].startswith('fea_')]
    assert [e['type'] for e in events] == ['fea_started', 'fea_stage', 'fea_stage', 'fea_stage', 'fea_done']
    assert [e['stage'] for e in events[1:4]] == ['meshing', 'solving', 'post-processing']
    assert events[-1]['result']['stress_url'] == result['stress_url']

    # After a reopen the job is gone from memory, but its files still serve from disk.
    session.fea_jobs.clear()
    assert client.get(result['stress_url']).status_code == 200


def test_api_validates_units_revision_settings_step_and_concurrency(api, monkeypatch):
    import aida.api.fea as api_module

    client, session = api
    base = f'/api/v1/cad-sessions/{session.id}/fea'
    assert client.post(base, json={'revision': -1}).status_code == 422
    assert client.post(base, json={'revision': 0}).status_code == 404  # valid number, no such CAD
    assert client.post(base, json={'revision': 9}).status_code == 404
    for extra in [{'force_n': 0}, {'poisson_ratio': .5}, {'resolution': 1000}, {'force_n': 'NaN'},
                  {'fixed_face': 'oops'}, {'engine': 'voxel'}]:
        assert client.post(base, json={'revision': 1, **extra}).status_code == 422
    session.units = None
    assert client.post(base, json={'revision': 1}).status_code == 409
    session.units = 'mm'
    step = session.meshes[1].pop('step_path')
    response = client.post(base, json={'revision': 1})
    assert response.status_code == 409 and 'STEP' in response.json()['detail']
    session.meshes[1]['step_path'] = step

    async def blocked(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(api_module, 'run_analysis', blocked)
    assert client.post(base, json={'revision': 1}).status_code == 202
    assert client.post(base, json={'revision': 1}).status_code == 409
    assert client.post('/api/v1/cad-sessions/missing/fea', json={'revision': 1}).status_code == 404


async def test_timed_out_worker_is_killed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import aida.api.fea as api_module

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def read(self):
            await asyncio.Event().wait()

    class Process:
        returncode = None
        killed = False
        stdout = Stream()
        stderr = Stream()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(api_module, 'FEA_TIMEOUT_S', .1)
    monkeypatch.setattr(api_module, '_manager', lambda: SimpleNamespace(fea_slots=asyncio.Semaphore(1)))
    events = []
    job = {'directory': tmp_path, 'revision': 1, 'analysis_id': 'test', 'step_path': tmp_path / 'cad.step',
           'done': asyncio.Event()}
    await api_module.run_analysis(SimpleNamespace(id='session', publish=events.append), job)
    assert job['status'] == 'failed'
    assert 'two-minute' in job['error']
    assert process.killed
    assert job['done'].is_set()
    assert events[-1]['type'] == 'fea_failed'


@pytest.fixture
def live_session(tmp_path, monkeypatch):
    """A session with one generated revision and its STEP, without the HTTP client."""
    import aida.api.server as server
    from aida.api.cad_sessions import CadSessionManager
    from build123d import Box, export_step

    monkeypatch.setenv('AIDA_PREWARM', '0')
    manager = CadSessionManager(tmp_path / 'cad')
    monkeypatch.setattr(server, 'cad_manager', manager)
    mesh = trimesh.creation.box([60, 10, 10])
    session = manager.create([('box.stl', mesh.export(file_type='stl'))], [], 'test', 'mm')
    path = session.artifacts.root / 'mesh-001.glb'
    mesh.export(path)
    step = session.artifacts.root / 'cad-001.step'
    export_step(Box(60, 10, 10), str(step))
    session.meshes.append({'revision': 1, 'path': path, 'generated': True, 'step_path': step})
    return session


async def test_agent_run_fea_tool_tests_the_latest_revision_and_summarises(live_session):
    import json
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost

    events = []
    run_fea = {t.name: t for t in host_tools(SessionHost(live_session), on_event=events.append)}['run_fea']
    result = await run_fea.handler({'youngs_modulus_mpa': 3500, 'poisson_ratio': .35, 'yield_strength_mpa': 50,
                                    'force_n': 20, 'fixed_face': 'x-', 'load_direction': 'z-', 'resolution': 10})
    assert not result.get('is_error'), result
    summary = json.loads(result['content'][0]['text'])
    assert summary['design'] == 'Live geometry'
    assert summary['material']['youngs_modulus_mpa'] == 3500
    # Root bending stress for 20 N on a 60 mm, 10x10 mm cantilever is about 7.2 MPa.
    assert 6 < summary['peak_von_mises_mpa'] < 12
    assert summary['safety_factor_vs_yield'] == pytest.approx(50 / summary['peak_von_mises_mpa'], rel=1e-2)
    assert events[-1] == {'type': 'tool_result', 'name': 'run_fea', 'ok': True, 'text': result['content'][0]['text']}
    done = [e for e in live_session.events if e['type'] == 'fea_done']
    assert done and done[0]['source'] == 'agent'

    bad = await run_fea.handler({'youngs_modulus_mpa': 3500, 'poisson_ratio': .35, 'yield_strength_mpa': 50,
                                 'force_n': 20, 'fixed_face': 'x-', 'load_direction': 'z-', 'revision': 7})
    assert bad['is_error'] and 'geometry 7' in bad['content'][0]['text']


async def test_agent_ask_user_publishes_a_clean_question(live_session):
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost

    ask_user = {t.name: t for t in host_tools(SessionHost(live_session))}['ask_user']
    result = await ask_user.handler({'questions': [
        {'id': 'material', 'prompt': 'What will you print it in?', 'kind': 'choice',
         'options': [{'label': 'PLA', 'detail': 'E 3500 MPa'}, {'label': 'PETG'}, {'nope': 1}]},
        {'id': 'load', 'prompt': 'How much weight should it hold?', 'kind': 'number', 'unit': 'N'},
        {'prompt': 'Anything else?', 'kind': 'choice'},
    ]})
    assert not result.get('is_error')
    assert 'End your turn' in result['content'][0]['text']
    question = live_session.events[-1]
    assert question['type'] == 'question' and len(question['id']) == 12
    material, load, other = question['questions']
    assert [o['label'] for o in material['options']] == ['PLA', 'PETG']
    assert material['allow_other'] is True
    assert load['unit'] == 'N' and load['kind'] == 'number'
    assert other['id'] == 'q3' and other['kind'] == 'text'  # a choice with no options becomes text
    assert (await ask_user.handler({'questions': []}))['is_error']


async def test_agent_fea_faces_tool_returns_the_listing_and_a_picture(live_session):
    import json
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost

    tools = {t.name: t for t in host_tools(SessionHost(live_session))}
    fea_faces, run_fea = tools['fea_faces'], tools['run_fea']
    result = await fea_faces.handler({})
    assert not result.get('is_error'), result
    text, image = result['content']
    assert len(json.loads(text['text'])['faces']) == 6 and image['type'] == 'image'
    # The setup picture comes back with every run.
    ran = await run_fea.handler({'youngs_modulus_mpa': 3500, 'poisson_ratio': .35, 'yield_strength_mpa': 50,
                                 'force_n': 20, 'support_faces': [0], 'load_faces': [1], 'load_direction': 'z-',
                                 'resolution': 10})
    assert not ran.get('is_error'), ran
    summary = json.loads(ran['content'][0]['text'])
    assert summary['held_faces'] == [0] and summary['mesh_check'] is not None
    assert ran['content'][1]['type'] == 'image'
