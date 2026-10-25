"""Explicit workspace commits and skipping viewer
revisions whose geometry did not change."""
import asyncio

from aida.api.cad_sessions import CadSession, snapshot_hook
from aida.api.commits import list_commits, restore_commit, save_commit
from aida.artifacts import Artifacts
from aida.model_tools import model_tools
from aida.workspace import Workspace, write_workspace
from test_cad_sessions import FakeArtifacts, FakeCad, fake_workspace
from test_workspace import LocalCad, assumed, make_keycap, result


async def test_unchanged_geometry_reuses_render_artifact(tmp_path):
    session = CadSession(FakeArtifacts(tmp_path), 'prompt', 'mm')
    cad = FakeCad(tmp_path, {'part_plate': {}})
    snapshot = snapshot_hook(cad, session, fake_workspace(cad, tmp_path))
    await snapshot()
    await snapshot()  # same box again: e.g. a new empty part, or parameters only
    assert len(session.meshes) == 1
    assert [e['reason'] for e in session.events if e['type'] == 'mesh_skipped'] == ['unchanged']


def live_session(tmp_path, plate_stl):
    artifacts = Artifacts(tmp_path / 'cad')
    write_workspace(artifacts.root, [{'name': 'plate', 'reference': artifacts.copy_mesh(plate_stl)}])
    cad = LocalCad(tmp_path)
    cad.lock = asyncio.Lock()
    session = CadSession(artifacts, '', 'mm')
    session.cad = cad
    session.workspace = Workspace.load(cad, artifacts)
    _, table = model_tools(cad, artifacts, session.workspace)
    glb = artifacts.root / 'mesh-000.glb'
    glb.write_bytes(b'exact-geometry')
    session.meshes = [{'revision': 0, 'path': glb, 'generated': True}]
    return session, {name: handler for name, _, _, handler in table}


async def test_commits_save_changes_and_restore(tmp_path, plate_stl):
    session, tools = live_session(tmp_path, plate_stl)
    assert list_commits(session.artifacts.root) == []  # prompts alone do not save commits

    await make_keycap(tools)
    first = save_commit(session, 'make a keycap')
    assert first['number'] == 1 and first['message'] == 'make a keycap'
    assert save_commit(session, 'no change') is None

    result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': assumed(12)}}))
    result(await tools['workspace_add_part']({'name': 'stem', 'description': 'a stem under the cap'}))
    second = save_commit(session, 'taller, add a stem')
    assert second['number'] == 2
    assert [v['number'] for v in list_commits(session.artifacts.root)] == [1, 2]
    published = [e for e in session.events if e['type'] == 'commit']
    assert [e['number'] for e in published] == [1, 2] and 'hash' not in published[0]

    executes = session.cad.executes
    notes = await restore_commit(session, 1)
    workspace = session.workspace
    assert notes == []
    assert sorted(workspace.parts) == ['keycap', 'plate']  # the stem came later
    assert workspace.parts['keycap'].state['params']['height']['value'] == 8
    assert session.cad.executes > executes  # rebuilt in the engine
    assert any('restored commit 1' in note for note in session.pending)
    assert any(e['type'] == 'commit_restored' and e['number'] == 1 for e in session.events)

    # Forward again: the later commit is still there.
    await restore_commit(session, 2)
    assert sorted(workspace.parts) == ['keycap', 'plate', 'stem']
    assert workspace.parts['keycap'].state['params']['height']['value'] == 12


async def test_analysis_navigation_and_counters_are_not_design_changes(tmp_path, plate_stl):
    import json
    from aida.api.commits import design_hash, publish_working_state
    session, tools = live_session(tmp_path, plate_stl)
    await make_keycap(tools)
    save_commit(session, 'Base shape')
    before = design_hash(session.artifacts.root)
    model = session.workspace.parts['keycap']
    model.state['revision'] += 1
    model.state['history'].append({'action': 'inspection'})
    model.state['deviation'] = {'p95': 1}
    path = model.folder / 'model.json'
    path.write_text(json.dumps(model.state), encoding='utf-8')
    session.workspace.active = 'plate'
    session.workspace.save()
    assert design_hash(session.artifacts.root) == before
    assert save_commit(session, 'Just inspect') is None
    publish_working_state(session)
    assert session.events[-1]['dirty'] is False


async def test_multiple_commits_steps_exact_geometry_and_restore_then_append(tmp_path, plate_stl):
    from aida.api.commits import current_commit
    session, tools = live_session(tmp_path, plate_stl)
    session.publish({'type': 'user_prompt', 'text': 'Build then improve'})
    session.publish({'type': 'tool_call', 'name': 'model_add_feature', 'args': {}})
    await make_keycap(tools)
    first = save_commit(session, 'Base')
    assert first['steps'][0]['text'] == 'Build then improve'
    assert (session.artifacts.root / 'commits/c001/mesh.glb').read_bytes() == b'exact-geometry'
    result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': assumed(12)}}))
    session.publish({'type': 'tool_call', 'name': 'model_set_params', 'args': {}})
    second = save_commit(session, 'Taller')
    assert [e['name'] for e in second['steps'] if e['type'] == 'tool_call'] == ['model_set_params']
    await restore_commit(session, 1)
    assert save_commit(session, 'Unchanged restored state') is None
    result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': assumed(10)}}))
    assert save_commit(session, 'Alternative height')['number'] == 3
    assert current_commit(session.artifacts.root) == 3
    assert len(list_commits(session.artifacts.root)) == 3


async def test_commit_snapshot_failure_is_atomic(tmp_path, plate_stl, monkeypatch):
    import pytest
    import aida.api.commits as commits
    session, tools = live_session(tmp_path, plate_stl)
    await make_keycap(tools)
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(commits.shutil, 'copy2', fail)
    with pytest.raises(OSError, match='disk full'):
        save_commit(session, 'Cannot save')
    assert list_commits(session.artifacts.root) == []
    assert not list((session.artifacts.root / 'commits').iterdir())
    assert not any(e['type'] == 'commit' for e in session.events)


async def test_commit_message_and_missing_geometry_validation(tmp_path, plate_stl):
    import pytest
    session, tools = live_session(tmp_path, plate_stl)
    with pytest.raises(ValueError, match='message'):
        save_commit(session, '  ')
    await make_keycap(tools)
    session.meshes.clear()
    with pytest.raises(ValueError, match='geometry'):
        save_commit(session, 'Missing mesh')


async def test_part_membership_can_be_committed_without_geometry(tmp_path, plate_stl):
    session, tools = live_session(tmp_path, plate_stl)
    first = save_commit(session, 'Import reference plate')
    assert first['geometry_id'] is None and first['geometry'] is None
    result(await tools['workspace_add_part']({'name': 'stem', 'description': 'a mounting stem'}))
    assert save_commit(session, 'Add mounting stem')['number'] == 2


async def test_head_write_failure_does_not_publish_commit(tmp_path, plate_stl, monkeypatch):
    import pytest
    import aida.api.commits as commits
    session, tools = live_session(tmp_path, plate_stl)
    await make_keycap(tools)
    def fail(*args):
        raise OSError('head write failed')
    monkeypatch.setattr(commits, '_write_head', fail)
    with pytest.raises(OSError, match='head write'):
        save_commit(session, 'Fails atomically')
    assert list_commits(session.artifacts.root) == []
    assert not any(e['type'] == 'commit' for e in session.events)


async def test_commit_tool_registration_noop_and_errors(tmp_path, plate_stl):
    import json
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost
    session, tools = live_session(tmp_path, plate_stl)
    tool = next(t for t in host_tools(SessionHost(session)) if t.name == 'commit_changes')
    assert (await tool.handler({'message': '  '}))['is_error']
    saved = await tool.handler({'message': 'Reference checkpoint'})
    assert json.loads(saved['content'][0]['text'])['committed'] is True
    noop = await tool.handler({'message': 'Unchanged'})
    assert json.loads(noop['content'][0]['text']) == {'committed': False, 'message': 'Nothing to commit.'}


async def test_reopen_and_commit_api_use_durable_snapshots(tmp_path, plate_stl, monkeypatch):
    from fastapi.testclient import TestClient
    from aida.api import server
    from aida.api.cad_sessions import CadSessionManager
    from aida.api.commits import current_commit
    session, tools = live_session(tmp_path, plate_stl)
    await make_keycap(tools)
    save_commit(session, 'Original shape')
    result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': assumed(12)}}))
    save_commit(session, 'Taller shape')
    from test_cad_sessions import stub_runner_factory
    manager = CadSessionManager(session.artifacts.root.parent, runner=stub_runner_factory([]))
    reopened = manager.reopen(session.id)
    assert current_commit(reopened.artifacts.root) == 2
    assert [e['message'] for e in reopened.events if e['type'] == 'commit'] == ['Original shape', 'Taller shape']
    reopened.cad, reopened.workspace = session.cad, session.workspace
    reopened.status, reopened.stage = 'running', 'idle'
    monkeypatch.setattr(server, 'cad_manager', manager)
    with TestClient(server.app) as client:
        base = f'/api/v1/workspaces/{session.id}/commits'
        commits = client.get(base).json()
        assert [c['number'] for c in commits] == [1, 2] and 'hash' not in commits[0]
        assert client.get(base + '/1/mesh.glb').content == b'exact-geometry'
        assert client.get(base + '/99/mesh.glb').status_code == 404
        assert client.post(base + '/99/restore').status_code == 404
        reopened.stage = 'agent_running'
        assert client.post(base + '/1/restore').status_code == 409
        reopened.stage = 'idle'
        assert client.post(base + '/1/restore').status_code == 200
        assert current_commit(session.artifacts.root) == 1
        assert len(client.get(base).json()) == 2


async def test_failed_geometry_capture_does_not_commit(tmp_path, plate_stl, monkeypatch):
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost
    session, tools = live_session(tmp_path, plate_stl)
    await make_keycap(tools)
    async def failed_export(*args, **kwargs):
        raise ValueError('CAD export failed')
    monkeypatch.setattr(session.cad, 'snapshot_stl', failed_export, raising=False)
    commit = next(t for t in host_tools(SessionHost(session)) if t.name == 'commit_changes')
    result = await commit.handler({'message': 'Cannot capture'})
    assert result['is_error'] and 'CAD export failed' in result['content'][0]['text']
    assert list_commits(session.artifacts.root) == []
