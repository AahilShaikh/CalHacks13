"""Versions (one per prompt that changed the design, restorable) and skipping viewer
revisions whose geometry did not change."""
import asyncio

from aida.api.cad_sessions import CadSession, snapshot_hook
from aida.api.versions import list_versions, restore_version, save_version
from aida.artifacts import Artifacts
from aida.model_tools import model_tools
from aida.workspace import Workspace, write_workspace
from test_cad_sessions import FakeArtifacts, FakeCad, fake_workspace
from test_workspace import LocalCad, assumed, make_keycap, result


async def test_unchanged_geometry_is_not_a_new_revision(tmp_path):
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
    return session, {name: handler for name, _, _, handler in table}


async def test_versions_save_changes_and_restore(tmp_path, plate_stl):
    session, tools = live_session(tmp_path, plate_stl)
    assert save_version(session, 'nothing yet') is None  # no models yet

    await make_keycap(tools)
    first = save_version(session, 'make a keycap')
    assert first['number'] == 1 and first['prompt'] == 'make a keycap'
    assert save_version(session, 'no change') is None

    result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': assumed(12)}}))
    result(await tools['workspace_add_part']({'name': 'stem', 'description': 'a stem under the cap'}))
    second = save_version(session, 'taller, add a stem')
    assert second['number'] == 2
    assert [v['number'] for v in list_versions(session.artifacts.root)] == [1, 2]
    published = [e for e in session.events if e['type'] == 'version']
    assert [e['number'] for e in published] == [1, 2] and 'hash' not in published[0]

    executes = session.cad.executes
    notes = await restore_version(session, 1)
    workspace = session.workspace
    assert notes == []
    assert sorted(workspace.parts) == ['keycap', 'plate']  # the stem came later
    assert workspace.parts['keycap'].state['params']['height']['value'] == 8
    assert session.cad.executes > executes  # rebuilt in the engine
    assert any('restored version 1' in note for note in session.pending)
    assert session.events[-1] == {**session.events[-1], 'type': 'version_restored', 'number': 1}

    # Forward again: the later version is still there.
    await restore_version(session, 2)
    assert sorted(workspace.parts) == ['keycap', 'plate', 'stem']
    assert workspace.parts['keycap'].state['params']['height']['value'] == 12
