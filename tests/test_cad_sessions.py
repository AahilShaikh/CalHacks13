"""Live CAD session API tests: lifecycle, SSE stream, mesh revisions, turns.

The agent runner is substituted with a stub so these tests are hermetic and
fast; real CAD/agent runs are covered by the integration and live tests.
"""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

import aida.api.server as server
from aida.api.cad_sessions import (
    CadSessionManager, agent_event_bridge, first_prompt, snapshot_hook,
)


def mesh_bytes():
    import trimesh
    return trimesh.creation.box((60, 40, 6)).export(file_type='stl')


def png_bytes(tmp_path):
    from PIL import Image
    path = tmp_path / 'photo.png'
    Image.new('RGB', (16, 16), 'white').save(path)
    return path.read_bytes()


def stub_runner_factory(seen):
    """A runner that mimics the real one's event pattern without Claude/CAD."""
    async def runner(session):
        session.set_status('running')
        while True:
            prompt = await session.turns.get()
            if prompt is None:
                break
            seen.append(prompt)
            session.set_stage('agent_running')
            session.publish({'type': 'agent_text', 'text': f'echo: {prompt[:20]}'})
            session.publish({'type': 'code', 'index': len(seen), 'code': 'show(part)'})
            revision = len(session.meshes)
            glb = session.artifacts.root / f'mesh-stub-{revision}.glb'
            glb.write_bytes(b'glTF-stub')
            session.meshes.append({'revision': revision, 'path': glb,
                                   'generated': True, 'triangles': 12,
                                   'vertices': 8, 'bbox': [0] * 6, 'extents': [1, 1, 1]})
            session.publish({'type': 'mesh', 'revision': revision,
                             'url': session.mesh_url(revision), 'generated': True,
                             'triangles': 12, 'vertices': 8,
                             'bbox': [0] * 6, 'extents': [1, 1, 1]})
            session.set_stage('idle')
        session.set_status('done')
        session.publish({'type': 'session_done', 'status': 'done'})
    return runner


@pytest.fixture()
def seen():
    return []


@pytest.fixture()
def client(tmp_path, monkeypatch, seen):
    manager = CadSessionManager(tmp_path / 'cad', runner=stub_runner_factory(seen))
    monkeypatch.setattr(server, 'cad_manager', manager)
    # Context-manager form keeps one portal (event loop) alive for all
    # requests, so the background session runner survives between them.
    with TestClient(server.app) as test_client:
        yield test_client


def start_session(client, tmp_path, prompt='Build a plate', **fields):
    response = client.post(
        '/api/v1/cad-sessions',
        files=[('mesh', ('box.stl', mesh_bytes(), 'model/stl'))],
        data={'prompt': prompt, **fields})
    assert response.status_code == 202, response.text
    return response.json()


def wait_for_status(client, session_id, wanted=('done', 'failed'), deadline=5.0):
    end = time.time() + deadline
    data = {}
    while time.time() < end:
        data = client.get(f'/api/v1/cad-sessions/{session_id}').json()
        if data['status'] in wanted:
            return data
        time.sleep(0.02)
    raise AssertionError(f'session {session_id} did not reach {wanted}: {data}')


def read_events(client, session_id, stop_type='session_done', limit=200):
    events = []
    with client.stream('GET', f'/api/v1/cad-sessions/{session_id}/events') as response:
        assert response.headers['content-type'].startswith('text/event-stream')
        for line in response.iter_lines():
            if line.startswith('data: '):
                events.append(json.loads(line[len('data: '):]))
                if events[-1]['type'] == stop_type or len(events) >= limit:
                    break
    return events


def test_session_lifecycle_events_and_mesh(client, tmp_path):
    created = start_session(client, tmp_path, units='mm')
    session_id = created['session_id']
    assert created['mesh_url'].endswith('mesh.glb?rev=0')

    client.post(f'/api/v1/cad-sessions/{session_id}/close')
    data = wait_for_status(client, session_id)
    assert data['status'] == 'done'
    assert data['revisions'] == 2        # revision 0 from upload + one stub revision

    events = read_events(client, session_id)
    types = [e['type'] for e in events]
    assert 'mesh' in types and 'code' in types and 'agent_text' in types
    assert types.index('mesh') < types.index('code')   # reference mesh first
    assert [e['seq'] for e in events] == sorted(e['seq'] for e in events)
    first_mesh = next(e for e in events if e['type'] == 'mesh')
    assert first_mesh['revision'] == 0 and not first_mesh['generated']
    assert first_mesh['triangles'] > 0

    # Binary GLB fetch: explicit revision is cacheable, latest is not.
    response = client.get(f'/api/v1/cad-sessions/{session_id}/mesh.glb?rev=1')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'model/gltf-binary'
    assert response.content == b'glTF-stub'
    assert 'immutable' in response.headers['cache-control']
    latest = client.get(f'/api/v1/cad-sessions/{session_id}/mesh.glb')
    assert latest.content == b'glTF-stub'
    assert 'no-cache' in latest.headers['cache-control']
    assert client.get(f'/api/v1/cad-sessions/{session_id}/mesh.glb?rev=9').status_code == 404

    # One mesh, one part, and session artifacts are downloadable.
    assert data['parts'] == ['box'] and first_mesh['parts'] == ['box']
    assert client.get(f'/api/v1/cad-sessions/{session_id}/files/parts/box/reference.stl').status_code == 200
    assert client.get(f'/api/v1/cad-sessions/{session_id}/files/workspace.json').status_code == 200
    assert client.get(f'/api/v1/cad-sessions/{session_id}/files/..%2Fsecret').status_code == 404


def test_turn_queue_reaches_runner(client, seen, tmp_path):
    created = start_session(client, tmp_path, prompt='First')
    session_id = created['session_id']
    assert client.post(f'/api/v1/cad-sessions/{session_id}/turns',
                       json={'prompt': 'Second'}).status_code == 202
    assert client.post(f'/api/v1/cad-sessions/{session_id}/close').status_code == 202

    data = wait_for_status(client, session_id)
    assert data['status'] == 'done'
    assert len(seen) == 2
    assert seen[0].startswith('First')          # composed with image list if any
    assert seen[1] == 'Second'
    # Sessions are closed: further turns are refused.
    assert client.post(f'/api/v1/cad-sessions/{session_id}/turns',
                       json={'prompt': 'More'}).status_code == 409


def test_images_join_the_agent_prompt(client, tmp_path):
    response = client.post(
        '/api/v1/cad-sessions',
        files=[('mesh', ('box.stl', mesh_bytes(), 'model/stl')),
               ('images', ('photo.png', png_bytes(tmp_path), 'image/png'))],
        data={'prompt': 'Match this photo'})
    assert response.status_code == 202
    session_id = response.json()['session_id']
    data = client.get(f'/api/v1/cad-sessions/{session_id}').json()
    assert data['images'] == ['input-01.png']
    session = server.cad_manager.get(session_id)
    assert 'input-01.png' in first_prompt(session)
    assert 'view_image' in first_prompt(session)


def two_boxes_bytes(speck=False):
    import trimesh
    meshes = [trimesh.creation.box((20, 20, 8)),
              trimesh.creation.box((20, 20, 8)).apply_translation((40, 0, 0))]
    if speck:
        meshes.append(trimesh.creation.box((0.5, 0.5, 0.5)).apply_translation((0, 60, 0)))
    return trimesh.util.concatenate(meshes).export(file_type='stl')


def test_several_meshes_become_parts(client, tmp_path):
    import trimesh
    response = client.post(
        '/api/v1/cad-sessions',
        files=[('mesh', ('Keycap.stl', mesh_bytes(), 'model/stl')),
               ('mesh', ('plate.stl', mesh_bytes(), 'model/stl'))],
        data={'prompt': 'Assemble these'})
    assert response.status_code == 202, response.text
    assert response.json()['parts'] == ['keycap', 'plate']
    session = server.cad_manager.get(response.json()['session_id'])
    workspace = json.loads((session.artifacts.root / 'workspace.json').read_text())
    assert [p['name'] for p in workspace['parts']] == ['keycap', 'plate']
    assert workspace['parts'][1]['reference'].replace('\\', '/') == 'parts/plate/reference.stl'
    assert 'keycap, plate' in first_prompt(session)
    scene = trimesh.load(str(session.meshes[0]['path']))
    assert sorted(scene.graph.nodes_geometry) == ['keycap|reference', 'plate|reference']


def test_one_mesh_with_separate_pieces_is_split(client, tmp_path):
    response = client.post('/api/v1/cad-sessions',
                           files=[('mesh', ('pair.stl', two_boxes_bytes(speck=True), 'model/stl'))],
                           data={'prompt': 'Two parts'})
    assert response.status_code == 202, response.text
    assert response.json()['parts'] == ['pair_1', 'pair_2']
    session = server.cad_manager.get(response.json()['session_id'])
    prompt = first_prompt(session)
    assert 'ignored 1 small fragment' in prompt and '2 separate pieces' in prompt
    events = read_events(client, session.id, stop_type='mesh')
    assert any(e['type'] == 'notice' for e in events)


def test_workspace_opens_empty_and_takes_meshes_later(client, seen, tmp_path):
    import trimesh
    from aida.api.cad_sessions import compose_turn

    created = client.post('/api/v1/cad-sessions', data={'units': 'mm'})
    assert created.status_code == 202, created.text
    session_id = created.json()['session_id']
    assert created.json()['parts'] == [] and created.json()['mesh_url'] is None
    session = server.cad_manager.get(session_id)
    assert json.loads((session.artifacts.root / 'workspace.json').read_text())['parts'] == []
    assert session.meshes == [] and seen == []  # no prompt yet: the agent waits

    added = client.post(f'/api/v1/cad-sessions/{session_id}/meshes',
                        files=[('mesh', ('Keycap.stl', mesh_bytes(), 'model/stl'))], data={'units': 'mm'})
    assert added.status_code == 202, added.text
    assert added.json()['parts'] == ['keycap']
    again = client.post(f'/api/v1/cad-sessions/{session_id}/meshes',
                        files=[('mesh', ('keycap.stl', two_boxes_bytes(), 'model/stl'))])
    assert again.json()['parts'] == ['keycap_1', 'keycap_2']   # split, named around the first
    parts = json.loads((session.artifacts.root / 'workspace.json').read_text())['parts']
    assert [p['name'] for p in parts] == ['keycap', 'keycap_1', 'keycap_2']
    # Each upload publishes a reference revision holding every reference so far.
    scene = trimesh.load(str(session.meshes[-1]['path']))
    assert len(scene.graph.nodes_geometry) == 3 and not session.meshes[-1]['generated']

    photo = client.post(f'/api/v1/cad-sessions/{session_id}/images',
                        files=[('images', ('photo.png', png_bytes(tmp_path), 'image/png'))])
    assert photo.json()['images'] == ['input-01.png']

    # The agent hears about all of it on its next turn, once.
    turn = compose_turn(session, 'Make a keyboard')
    assert turn.endswith('Make a keyboard') and 'keycap_1, keycap_2' in turn
    assert 'unknown' in turn and 'input-01.png' in turn
    assert compose_turn(session, 'Next') == 'Next'

    notices = [e['text'] for e in session.events if e['type'] == 'notice']
    assert 'Added part keycap' in notices and 'Added parts keycap_1, keycap_2' in notices


async def test_meshes_join_a_running_workspace(tmp_path):
    import trimesh
    from aida.api.cad_sessions import CadSession, finish_ingest, ingest_meshes

    class Mesh:
        references = {}

    class LiveCad:
        mesh = Mesh()
        loaded = []

        async def load_reference(self, path, part):
            self.loaded.append(part)

    cad = LiveCad()
    workspace = fake_workspace(cad, tmp_path, ('plate',), built=(), reference=True)
    session = CadSession(FakeArtifacts(tmp_path), '', 'mm')
    added = ingest_meshes(session, [('cap.stl', mesh_bytes())], workspace.references())
    for spec in added['specs']:
        workspace.add_reference_part(spec['name'], spec['reference'])
    events = finish_ingest(session, added)
    assert sorted(workspace.parts) == ['cap', 'plate'] and cad.mesh.references['cap'].is_file()
    assert workspace.active == 'plate'           # the agent's part stays active
    mesh = events[-1]
    assert mesh['type'] == 'mesh' and mesh['parts'] == ['plate', 'cap'] and mesh['revision'] == 0
    assert sorted(trimesh.load(str(session.meshes[0]['path'])).graph.nodes_geometry) == \
        ['cap|reference', 'plate|reference']


def test_invalid_uploads_rejected(client, tmp_path):
    response = client.post('/api/v1/cad-sessions',
                           files=[('mesh', ('bad.stl', b'not an stl', 'model/stl'))],
                           data={'prompt': 'x'})
    assert response.status_code == 400
    assert 'Mesh' in response.json()['detail']

    response = client.post('/api/v1/cad-sessions',
                           files=[('mesh', ('box.stl', mesh_bytes(), 'model/stl'))],
                           data={'prompt': 'x', 'units': 'm'})
    assert response.status_code == 400

    assert client.post('/api/v1/cad-sessions/nope/meshes',
                       files=[('mesh', ('box.stl', mesh_bytes(), 'model/stl'))]).status_code == 404

    assert client.get('/api/v1/cad-sessions/nope').status_code == 404
    assert client.get('/api/v1/cad-sessions/nope/events').status_code == 404


class FakeCad:
    """Just enough of CadBridge for snapshot_hook."""

    def __init__(self, root, objects):
        self.root = root
        self.objects = objects
        self.exports = []

    async def session_object_names(self):
        return list(self.objects)

    async def snapshot_stl(self, target, object_name='*'):
        import trimesh
        self.exports.append(object_name)
        trimesh.creation.box((10, 10, 10)).export(str(target), file_type='stl')
        return target


class FakeArtifacts:
    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)


def fake_workspace(cad, root, parts=('plate',), built=('plate',), reference=False):
    """A real Workspace whose parts look built, without running any CAD."""
    import trimesh
    from aida.workspace import Workspace

    specs = []
    for name in parts:
        ref = None
        if reference:
            ref = root / f'{name}-reference.stl'
            trimesh.creation.box((10, 10, 10)).export(str(ref))
        specs.append({'name': name, 'reference': ref})
    workspace = Workspace(cad, FakeArtifacts(root), specs)
    for name in built:
        workspace.parts[name].state['features'] = [{'id': 'F1', 'name': 'body'}]
    return workspace


async def test_snapshot_hook_meshes_generated_objects_only(tmp_path):
    import trimesh
    from aida.api.cad_sessions import CadSession

    session = CadSession(FakeArtifacts(tmp_path), 'prompt', 'mm')
    cad = FakeCad(tmp_path, {'ref_plate': {}, 'part_plate': {}, 'part_spacer': {}, 'scratch': {}})
    await snapshot_hook(cad, session, fake_workspace(cad, tmp_path, ('plate', 'spacer'), ('plate', 'spacer')))()

    assert cad.exports == ['part_plate', 'part_spacer']   # never the reference or scratch
    assert len(session.meshes) == 1
    mesh = session.meshes[0]
    assert mesh['generated'] and mesh['triangles'] == 24 and mesh['parts'] == ['plate', 'spacer']
    assert sorted(trimesh.load(str(mesh['path'])).graph.nodes_geometry) == ['plate|plate', 'spacer|spacer']
    events = [e['type'] for e in session.events]
    assert events.count('mesh') == 1
    assert 'stage' in events


async def test_snapshot_hook_instances_assembly_variants(tmp_path):
    import trimesh
    from aida.api.cad_sessions import CadSession

    session = CadSession(FakeArtifacts(tmp_path), 'prompt', 'mm')
    cad = FakeCad(tmp_path, {'part_keycap': {}, 'variant_0': {}, 'variant_1': {}, 'assembly': {}})
    workspace = fake_workspace(cad, tmp_path, ('keycap',), ('keycap',))
    shift = lambda x: [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0]]  # noqa: E731
    workspace.assembly.layout = {
        'variants': [{'name': 'variant_0', 'part': 'keycap', 'overrides': {}},
                     {'name': 'variant_1', 'part': 'keycap', 'overrides': {'width_u': 2}}],
        'instances': [{'id': f'key{i}', 'part': 'keycap', 'variant': 'variant_0', 'matrix': shift(19 * i)}
                      for i in range(5)] + [{'id': 'wide', 'part': 'keycap', 'variant': 'variant_1',
                                             'matrix': shift(200)}]}
    await snapshot_hook(cad, session, workspace)()

    assert cad.exports == ['variant_0', 'variant_1']      # each variant meshed once
    scene = trimesh.load(str(session.meshes[0]['path']))
    assert len(scene.geometry) == 2 and len(scene.graph.nodes_geometry) == 6
    assert scene.graph['keycap|key4'][0][0][3] == 76
    assert session.meshes[0]['triangles'] == 6 * 12


async def test_snapshot_hook_skips_when_no_generated_objects(tmp_path):
    from aida.api.cad_sessions import CadSession

    session = CadSession(FakeArtifacts(tmp_path), 'prompt', None)
    cad = FakeCad(tmp_path, {'ref_plate': {}})
    await snapshot_hook(cad, session, fake_workspace(cad, tmp_path, built=()))()

    assert session.meshes == []
    assert any(e['type'] == 'mesh_skipped' for e in session.events)


async def test_agent_event_bridge_emits_code_and_render(tmp_path):
    from pathlib import Path

    from aida.api.cad_sessions import CadSession

    session = CadSession(FakeArtifacts(tmp_path), 'prompt', None)
    (session.artifacts.root / 'view-abc.png').write_bytes(b'png')
    bridge = agent_event_bridge(session)

    await bridge({'type': 'tool_call', 'name': 'execute', 'args': {'code': 'show(part)'}})
    await bridge({'type': 'tool_result', 'name': 'render_view', 'ok': True,
                  'text': '[SEND: /tmp/view-abc.png]'})
    await bridge({'type': 'tool_result', 'name': 'view_mesh', 'ok': True,
                  'text': '[SEND: /tmp/view-abc.png]'})
    await bridge({'type': 'agent_text', 'text': 'done'})

    types = [e['type'] for e in session.events]
    assert types == ['code', 'tool_call', 'render', 'tool_result', 'render',
                     'tool_result', 'agent_text']
    code = next(e for e in session.events if e['type'] == 'code')
    assert code['code'] == 'show(part)'
    render = next(e for e in session.events if e['type'] == 'render')
    assert render['url'].endswith('/files/view-abc.png')
    assert Path(session.artifacts.root / 'view-abc.png').is_file()


def test_viewer_shows_only_the_model_object_once_it_exists(tmp_path):
    import asyncio

    from aida.api.cad_sessions import generated_object_names

    class Names:
        def __init__(self, names):
            self.names = names

        async def session_object_names(self):
            return self.names

    cad = Names(['ref_plate', 'reference_mesh', 'scratch', 'variant_0'])
    # Before any part is built, everything but references and assembly internals is shown.
    assert asyncio.run(generated_object_names(cad, fake_workspace(cad, tmp_path, built=()))) == ['scratch']
    # Once a part is built, scratch experiments stay out of the viewer.
    assert asyncio.run(generated_object_names(cad, fake_workspace(cad, tmp_path))) == ['part_plate']


def test_examples_are_listed_and_served(client):
    listing = client.get('/api/v1/examples')
    assert listing.status_code == 200
    names = [entry['name'] for entry in listing.json()]
    assert 'asymmetric.stl' in names
    assert 'make_fixture.py' not in names  # only meshes are offered

    mesh = client.get('/api/v1/examples/asymmetric.stl')
    assert mesh.status_code == 200 and len(mesh.content) > 84

    assert client.get('/api/v1/examples/make_fixture.py').status_code == 404
    assert client.get('/api/v1/examples/..%2Fpyproject.toml').status_code == 404


def _heat_colours(path):
    import trimesh
    mesh = trimesh.load(str(path), force='mesh')
    return mesh.vertices, mesh.visual.vertex_colors[:, :3].astype(int)


def test_heatmap_colours_missing_red_and_extra_blue(tmp_path):
    import trimesh
    from aida.api.heatmap import build_heatmap

    reference = tmp_path / 'reference.stl'
    trimesh.creation.box((60, 40, 6)).export(str(reference))

    short = tmp_path / 'short.stl'  # CAD 10 mm too short in x: missing material at the ends
    trimesh.creation.box((50, 40, 6)).export(str(short))
    scale = build_heatmap(reference, [short], tmp_path / 'short.glb')
    assert scale['threshold'] > 0 and scale['range'] >= 4 * scale['threshold']
    vertices, colours = _heat_colours(tmp_path / 'short.glb')
    ends = abs(vertices[:, 0]) > 29.9
    assert (colours[ends, 0] > colours[ends, 2]).all()      # red at the uncovered ends

    long = tmp_path / 'long.stl'  # CAD 10 mm too long: the reference ends sit inside it
    trimesh.creation.box((70, 40, 6)).export(str(long))
    build_heatmap(reference, [long], tmp_path / 'long.glb')
    vertices, colours = _heat_colours(tmp_path / 'long.glb')
    # Inside of the end faces (their rims touch the CAD's side faces, distance 0, white).
    ends = (abs(vertices[:, 0]) > 29.9) & (abs(vertices[:, 1]) < 19) & (abs(vertices[:, 2]) < 2.5)
    assert ends.any() and (colours[ends, 2] > colours[ends, 0]).all()  # blue: CAD has extra material


async def test_snapshot_hook_publishes_heatmap_after_mesh(tmp_path):
    from aida.api.cad_sessions import CadSession

    session = CadSession(FakeArtifacts(tmp_path), 'prompt', 'mm')
    cad = FakeCad(tmp_path, {'ref_plate': {}, 'part_plate': {}, 'part_plate_2': {}})
    workspace = fake_workspace(cad, tmp_path, ('plate', 'plate_2'), ('plate', 'plate_2'), reference=True)
    await snapshot_hook(cad, session, workspace)()

    types = [e['type'] for e in session.events]
    assert types.index('mesh') < types.index('heatmap')
    heats = [e for e in session.events if e['type'] == 'heatmap']
    assert [h['part'] for h in heats] == ['plate', 'plate_2']   # one per part with a reference
    assert heats[0]['revision'] == 0 and heats[0]['url'].endswith('/heatmap.glb?rev=0&part=plate')
    assert session.meshes[0]['heatmaps']['plate'].is_file()
    assert cad.exports == ['part_plate', 'part_plate_2']        # reused for the heatmaps


def test_heatmap_route_404_without_heatmap(client, tmp_path):
    session_id = start_session(client, tmp_path)['session_id']
    assert client.get(f'/api/v1/cad-sessions/{session_id}/heatmap.glb?rev=0&part=box').status_code == 404


def test_real_agent_needs_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(server, 'cad_manager', CadSessionManager(tmp_path / 'cad'))  # real runner
    with TestClient(server.app) as test_client:
        response = test_client.post('/api/v1/cad-sessions',
                                    files=[('mesh', ('box.stl', mesh_bytes(), 'model/stl'))],
                                    data={'prompt': 'Build a plate'})
    assert response.status_code == 400
    assert 'ANTHROPIC_API_KEY' in response.json()['detail']


def test_env_loads_from_repo_root_when_started_elsewhere(tmp_path, monkeypatch):
    from aida import config

    root = tmp_path / 'repo'
    root.mkdir()
    (root / '.env').write_text('AIDA_TEST_ROOT_ENV=from-root\n')
    elsewhere = tmp_path / 'frontend'
    elsewhere.mkdir()
    monkeypatch.setattr(config, 'REPO_ROOT', root)
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv('AIDA_TEST_ROOT_ENV', raising=False)
    config.load_local_env()
    import os
    assert os.environ['AIDA_TEST_ROOT_ENV'] == 'from-root'
    monkeypatch.delenv('AIDA_TEST_ROOT_ENV')


def test_interrupt_needs_a_running_agent(client, tmp_path):
    session_id = start_session(client, tmp_path)['session_id']
    assert client.post(f'/api/v1/cad-sessions/{session_id}/interrupt').status_code == 409

    calls = []

    class Client:
        async def interrupt(self):
            calls.append('interrupt')

    server.cad_manager.get(session_id).client = Client()
    response = client.post(f'/api/v1/cad-sessions/{session_id}/interrupt')
    assert response.status_code == 202 and calls == ['interrupt']
    session = server.cad_manager.get(session_id)
    assert any(e['type'] == 'interrupted' for e in session.events)


def test_downloads(client, tmp_path):
    import trimesh

    session_id = start_session(client, tmp_path)['session_id']
    client.post(f'/api/v1/cad-sessions/{session_id}/close')
    wait_for_status(client, session_id)
    session = server.cad_manager.get(session_id)
    del session.meshes[1:]  # the stub runner's placeholder revision
    url = f'/api/v1/cad-sessions/{session_id}/download'
    assert client.get(f'{url}/model.py').status_code == 404
    assert client.get(f'{url}/stl').status_code == 404
    assert client.get(f'{url}/zip').status_code == 404

    (session.artifacts.root / 'workspace_model.py').write_text("DEFAULTS_box = {'width': 60}\n")
    script = client.get(f'{url}/model.py')
    assert script.status_code == 200 and "'width': 60" in script.text

    # A generated revision: its GLB scene, every instance placed, becomes one STL.
    from aida.api.cad_sessions import IDENTITY, write_scene
    revision = len(session.meshes)
    glb = session.artifacts.root / f'mesh-{revision:03d}.glb'
    moved = [row[:] for row in IDENTITY]
    moved[0][3] = 30
    write_scene([('v0', trimesh.creation.box((10, 10, 10)), [('box|a', IDENTITY), ('box|b', moved)])], glb)
    session.meshes.append({'revision': revision, 'generated': True, 'path': glb})
    stl = client.get(f'{url}/stl')
    assert stl.status_code == 200
    placed = trimesh.load(io.BytesIO(stl.content), file_type='stl')
    assert len(placed.faces) == 24 and placed.extents[0] == pytest.approx(40)

    # STEP needs the live CAD engine.
    assert client.get(f'{url}/step').status_code == 409

    class Cad:
        broken = False

        async def call(self, name, args):
            assert name == 'export' and args['format'] == 'step'
            assert args['object_name'] == 'part_box'
            (session.artifacts.root / args['filename']).write_text('ISO-10303-21;')
            return {'content': [{'type': 'text', 'text': 'ok'}]}

    from aida.workspace import Workspace
    session.cad = Cad()
    session.workspace = Workspace.load(session.cad, session.artifacts)
    session.workspace.parts['box'].state['features'] = [{'id': 'F1', 'name': 'body'}]
    step = client.get(f'{url}/step')
    assert step.status_code == 200 and step.text.startswith('ISO-10303-21')


def test_no_warm_engine_with_a_test_runner(tmp_path):
    manager = CadSessionManager(tmp_path, runner=lambda session: None)
    manager.prewarm()  # no running loop needed: test runners never start a CAD engine
    assert manager.take_warm() is None


def test_finished_session_can_be_replayed(client, tmp_path):
    started = start_session(client, tmp_path, prompt='Build a plate')
    session_id = started['session_id']
    wait_for_status(client, session_id, wanted=('running', 'done'))
    client.post(f'/api/v1/cad-sessions/{session_id}/close')
    wait_for_status(client, session_id)
    folder = server.cad_manager.get(session_id).artifacts.root.name

    listing = client.get('/api/v1/replays/index.json').json()
    entry = next(r for r in listing if r['id'] == folder)
    assert entry['prompt'] == 'Build a plate' and entry['events'] > 3

    events = client.get(f'/api/v1/replays/{folder}/events.json').json()
    assert events[0]['type'] in ('stage', 'user_prompt')
    assert any(e['type'] == 'user_prompt' and e['text'] == 'Build a plate' for e in events)
    reference = next(e for e in events if e['type'] == 'mesh' and e['revision'] == 0)
    assert reference['url'] == f'/api/v1/replays/{folder}/files/mesh-000.glb'
    glb = client.get(reference['url'])
    assert glb.status_code == 200 and glb.content[:4] == b'glTF'

    assert client.get('/api/v1/replays/nope/events.json').status_code == 404
    assert client.get(f'/api/v1/replays/{folder}/files/..%2F..%2Fpyproject.toml').status_code == 404
    assert client.get('/api/v1/replays/..%2F..%2F/events.json').status_code == 404
