"""Saved workspaces: listing, reopening (events, meshes, rebuilt models), rename,
delete, thumbnails, idle shutdown and resuming the agent's conversation."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import aida.api.server as server
from aida.api.cad_sessions import (
    REOPEN_NOTE, CadSession, CadSessionManager, connect_agent, load_events, meshes_from,
)
from aida.api.workspace_meta import read_meta, update_meta
from aida.artifacts import Artifacts
from aida.workspace import Workspace, write_workspace
from test_cad_sessions import mesh_bytes, png_bytes, read_events, stub_runner_factory, wait_for_status
from test_workspace import LocalCad, make_keycap


@pytest.fixture()
def seen():
    return []


@pytest.fixture()
def client(tmp_path, monkeypatch, seen):
    manager = CadSessionManager(tmp_path / 'cad', runner=stub_runner_factory(seen))
    monkeypatch.setattr(server, 'cad_manager', manager)
    with TestClient(server.app) as test_client:
        yield test_client


def create(client, prompt='Build a plate', name=None, mesh=True):
    data = {'prompt': prompt}
    if name:
        data['name'] = name
    files = [('mesh', ('bracket.stl', mesh_bytes(), 'model/stl'))] if mesh else None
    response = client.post('/api/v1/cad-sessions', files=files, data=data)
    assert response.status_code == 202, response.text
    return response.json()['session_id']


def close(client, session_id):
    client.post(f'/api/v1/cad-sessions/{session_id}/close')
    wait_for_status(client, session_id)


def test_session_id_is_the_folder_and_references_name_the_file(client):
    session_id = create(client)
    session = server.cad_manager.get(session_id)
    assert session.artifacts.root.name == session_id
    assert read_meta(session.artifacts.root)['reference_files'] == {'bracket': 'bracket.stl'}
    events = read_events(client, session_id, stop_type='references')
    assert events[-1]['items'] == [{'kind': 'mesh', 'part': 'bracket', 'name': 'bracket.stl'}]


def test_list_hides_empty_workspaces_and_renames(client):
    named = create(client, prompt='', name='Gearbox', mesh=False)
    prompted = create(client, prompt='A hinge with two leaves', mesh=False)
    empty = create(client, prompt='', mesh=False)
    listing = {w['id']: w for w in client.get('/api/v1/workspaces').json()}
    assert named in listing and prompted in listing and empty not in listing
    assert listing[named]['name'] == 'Gearbox' and listing[named]['named']
    assert listing[prompted]['name'] == 'A hinge with two leaves' and not listing[prompted]['named']
    assert listing[named]['status'] == 'open' and listing[named]['thumbnail'] is None
    assert empty in {w['id'] for w in client.get('/api/v1/workspaces?include_empty=true').json()}

    renamed = client.patch(f'/api/v1/workspaces/{prompted}', json={'name': '  Hinge v2 '})
    assert renamed.status_code == 200 and renamed.json()['name'] == 'Hinge v2'
    assert client.patch(f'/api/v1/workspaces/{prompted}', json={'name': ''}).status_code == 422
    assert client.patch('/api/v1/workspaces/nope', json={'name': 'x'}).status_code == 404
    assert client.get('/api/v1/workspaces/..%2F..').status_code == 404


def test_reopen_replays_history_and_serves_old_meshes(client, seen):
    session_id = create(client, prompt='Build a plate')
    wait_for_status(client, session_id, wanted=('running',))
    read_events(client, session_id, stop_type='mesh', limit=50)
    client.post(f'/api/v1/cad-sessions/{session_id}/turns', json={'prompt': 'Make it thicker'})
    close(client, session_id)
    # A closed workspace takes no turns until it is reopened.
    assert client.post(f'/api/v1/cad-sessions/{session_id}/turns', json={'prompt': 'hi'}).status_code == 409

    opened = client.post(f'/api/v1/workspaces/{session_id}/open')
    assert opened.status_code == 202 and opened.json() == {
        'session_id': session_id, 'reopened': True, 'events_url': f'/api/v1/cad-sessions/{session_id}/events',
        'status_url': f'/api/v1/cad-sessions/{session_id}', 'units': None}
    wait_for_status(client, session_id, wanted=('running',))
    events = read_events(client, session_id, stop_type='status')
    types = [e['type'] for e in events]
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert 'session_done' not in types and types.count('status') == 1  # only the new run's
    prompts = [e['text'] for e in events if e['type'] == 'user_prompt']
    assert prompts == ['Build a plate', 'Make it thicker']
    assert any(e['type'] == 'notice' and e['text'] == 'Workspace reopened' for e in events)
    reference = next(e for e in events if e['type'] == 'mesh' and e['revision'] == 0)
    assert reference['url'] == f'/api/v1/cad-sessions/{session_id}/mesh.glb?rev=0'
    assert client.get(reference['url']).content[:4] == b'glTF'

    # Opening it again while it runs returns the same live session.
    again = client.post(f'/api/v1/workspaces/{session_id}/open').json()
    assert again['reopened'] is False
    client.post(f'/api/v1/cad-sessions/{session_id}/turns', json={'prompt': 'And a hole'})
    deadline = time.time() + 5
    while 'And a hole' not in seen and time.time() < deadline:
        time.sleep(0.02)
    assert 'And a hole' in seen


def test_load_events_rewrites_links_and_drops_lifecycle(tmp_path):
    folder = tmp_path / 'ws'
    folder.mkdir()
    lines = [
        {'seq': 1, 'type': 'stage', 'stage': 'preparing'},
        {'seq': 2, 'type': 'mesh', 'revision': 0, 'generated': False, 'url': '/api/v1/cad-sessions/abc123/mesh.glb?rev=0',
         'triangles': 12, 'extents': [1, 1, 1]},
        {'seq': 3, 'type': 'status', 'status': 'done'},
        {'seq': 4, 'type': 'mesh', 'revision': 1, 'generated': True, 'url': '/api/v1/cad-sessions/abc123/mesh.glb?rev=1',
         'triangles': 20, 'extents': [1, 1, 1], 'parts': ['plate']},
        {'seq': 5, 'type': 'heatmap', 'revision': 1, 'part': 'plate',
         'url': '/api/v1/cad-sessions/abc123/heatmap.glb?rev=1&part=plate'},
    ]
    (folder / 'ui-events.jsonl').write_text('\n'.join(json.dumps(e) for e in lines) + '\n{"cut', encoding='utf-8')
    events = load_events(folder, 'ws')
    assert [e['type'] for e in events] == ['mesh', 'mesh', 'heatmap'] and [e['seq'] for e in events] == [1, 2, 3]
    assert events[1]['url'] == '/api/v1/cad-sessions/ws/mesh.glb?rev=1'
    meshes = meshes_from(events, folder)
    assert [m['revision'] for m in meshes] == [0, 1] and meshes[1]['path'] == folder / 'mesh-001.glb'
    assert meshes[1]['heatmaps'] == {'plate': folder / 'heatmap-001-plate.glb'}


def test_delete_moves_to_trash(client):
    session_id = create(client, name='Scrap')
    folder = server.cad_manager.get(session_id).artifacts.root
    response = client.delete(f'/api/v1/workspaces/{session_id}')
    assert response.status_code == 200
    assert not folder.exists() and (folder.parent / '.trash' / session_id / 'workspace.json').is_file()
    assert client.get(f'/api/v1/workspaces/{session_id}').status_code == 404
    assert session_id not in {w['id'] for w in client.get('/api/v1/workspaces').json()}


def test_thumbnail_round_trip(client, tmp_path):
    session_id = create(client, name='Thumb')
    assert client.get(f'/api/v1/workspaces/{session_id}/thumbnail.png').status_code == 404
    url = f'/api/v1/workspaces/{session_id}/thumbnail.png'
    assert client.put(url, content=b'not a png').status_code == 400
    assert client.put(url, content=png_bytes(tmp_path)).status_code == 204
    assert client.get(url).content[:4] == b'\x89PNG'
    assert client.get(f'/api/v1/workspaces/{session_id}').json()['thumbnail'] == url


def test_photos_keep_their_names(client, tmp_path):
    session_id = create(client)
    response = client.post(f'/api/v1/cad-sessions/{session_id}/images',
                           files=[('images', ('IMG_0042.jpg', png_bytes(tmp_path), 'image/png'))])
    assert response.status_code == 202
    events = server.cad_manager.get(session_id).events
    photos = [i for e in events if e['type'] == 'references' for i in e['items'] if i['kind'] == 'photo']
    assert photos[-1]['name'] == 'IMG_0042.jpg'
    assert photos[-1]['url'] == f'/api/v1/cad-sessions/{session_id}/files/input-01.png'


async def test_idle_workspaces_close_only_when_unwatched(tmp_path):
    manager = CadSessionManager(tmp_path / 'cad', runner=stub_runner_factory([]))
    watched, idle, busy = (manager.create(prompt=p) for p in ('a', 'b', 'c'))
    for session in (watched, idle, busy):
        session.status, session.stage = 'running', 'idle'
    watched.viewers = 1
    busy.stage = 'agent_running'
    now = time.time() + 3600
    assert manager.reap_idle(now=now, idle_s=60) == [idle.id]
    assert idle.closing and idle.turns.get_nowait() is None
    assert manager.live(idle.id) is None and manager.live(watched.id) is watched
    assert manager.reap_idle(now=time.time(), idle_s=60) == []


async def test_saved_parts_reload_and_rebuild(tmp_path, plate_stl):
    artifacts = Artifacts(tmp_path / 'session')
    write_workspace(artifacts.root, [{'name': 'plate', 'reference': artifacts.copy_mesh(plate_stl)}])
    workspace = Workspace.load(LocalCad(tmp_path), artifacts)
    from aida.model_tools import model_tools
    _, table = model_tools(workspace.cad, artifacts, workspace)
    tools = {name: handler for name, _, _, handler in table}
    await make_keycap(tools)
    saved = workspace.parts['keycap'].state

    cad = LocalCad(tmp_path)
    reopened = Workspace.load(cad, Artifacts.open(artifacts.root))
    keycap = reopened.parts['keycap']
    assert keycap.state['features'] == saved['features'] and keycap.state['revision'] == saved['revision']
    assert reopened.has_saved_models() and not reopened.parts['plate'].state['features']
    assert await reopened.restore() == []
    assert keycap.object in cad.shown  # rebuilt in the new engine
    assert keycap.snapshot()['can_undo'] is False


class FakeClient:
    """Stands in for ClaudeSDKClient: records the options; can fail to connect."""
    attempts: list = []
    fail_resume = False

    def __init__(self, options):
        self.options = options

    async def connect(self):
        FakeClient.attempts.append(self.options.resume)
        if self.options.resume and FakeClient.fail_resume:
            raise RuntimeError('no such conversation')


class Options:
    resume = None


@pytest.mark.parametrize('transcript, fail, expect_attempts, expect_note', [
    (True, False, ['conv-1'], False),
    (True, True, ['conv-1', None], True),
    (False, False, [None], True),
])
async def test_reopened_agent_resumes_or_is_told(tmp_path, monkeypatch, transcript, fail, expect_attempts, expect_note):
    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, 'ClaudeSDKClient', FakeClient)
    FakeClient.attempts, FakeClient.fail_resume = [], fail
    artifacts = Artifacts(tmp_path / 'cad')
    update_meta(artifacts.root, agent_session='conv-1')
    if transcript:
        project = artifacts.root / 'claude-runtime' / 'projects' / 'x'
        project.mkdir(parents=True)
        (project / 'conv-1.jsonl').write_text('{}', encoding='utf-8')
    session = CadSession(artifacts, '', None)
    session.reopened = True

    class Saved:
        def has_saved_models(self):
            return True

    await connect_agent(session, Options(), Saved())
    assert FakeClient.attempts == expect_attempts
    assert (REOPEN_NOTE in session.pending) is expect_note
