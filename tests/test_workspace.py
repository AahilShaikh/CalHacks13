"""Workspaces: several parts, an assembly of their variants, and the agent tools."""
import contextlib
import io
import json
import traceback

import pytest

from aida.artifacts import Artifacts
from aida.mesh_compare import deviation
from aida.model import ModelError
from aida.model_tools import model_tools
from aida.workspace import COMBINED_SCRIPT, Workspace, part_name_from, read_workspace, write_workspace


class LocalCad:
    """In-process stand-in for the CAD server: one persistent namespace, show(), printed output."""

    def __init__(self, tmp_path):
        self.namespace = {}
        self.shown = {}
        self.last_line = None
        self.executes = 0
        self.snapshots = 0
        self.paused_snapshots = 0
        self.mesh = self
        self.units = 'mm'
        self.references = {}
        self.active_part = lambda: None
        self.tmp_path = tmp_path
        self.on_execute = self._snapshot

    async def _snapshot(self):
        self.snapshots += 1

    async def call(self, name, args):
        if name == 'execute':
            self.executes += 1
            result = self._execute(args['code'])
            if not result.get('isError') and not self.paused_snapshots:
                await self.on_execute()
            return result
        if name == 'last_error':
            return _text(json.dumps({'line': self.last_line}))
        if name == 'measure':
            part = self.shown[args['object_name']]
            return _text(json.dumps({'volume': part.volume}))
        raise AssertionError(name)

    def _execute(self, code):
        self.namespace['show'] = lambda shape, name: self.shown.__setitem__(name, shape)
        before = dict(self.namespace)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                exec(compile(code, '<mcp>', 'exec'), self.namespace)
        except Exception as exc:
            self.namespace.clear()
            self.namespace.update(before)  # the server rolls its namespace back too
            frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == '<mcp>']
            self.last_line = frames[-1].lineno if frames else None
            return {'isError': True, 'content': [{'type': 'text', 'text': f'Error: {type(exc).__name__}: {exc}'}]}
        self.last_line = None
        return _text(out.getvalue() or 'ok')

    async def stats(self, object_name, samples, reference=None):
        from build123d import export_stl
        path = self.tmp_path / 'cad.stl'
        export_stl(self.shown[object_name], str(path))
        return deviation(reference, path, samples=samples)


def _text(value):
    return {'content': [{'type': 'text', 'text': value}]}


def assumed(value):
    return {'value': value, 'source': 'assumed', 'evidence': 'test'}


@pytest.fixture
def workspace(tmp_path, plate_stl):
    artifacts = Artifacts(tmp_path / 'session')
    write_workspace(artifacts.root, [{'name': 'plate', 'reference': artifacts.copy_mesh(plate_stl)}])
    return Workspace.load(LocalCad(tmp_path), artifacts)


@pytest.fixture
def tools(workspace):
    _, table = model_tools(workspace.cad, workspace.artifacts, workspace)
    return {name: handler for name, _, _, handler in table}


def result(response):
    assert not response.get('isError'), response['content'][0]['text']
    return json.loads(response['content'][0]['text'])


def error(response):
    assert response.get('isError'), response
    return response['content'][0]['text']


async def make_keycap(tools):
    result(await tools['workspace_add_part']({'name': 'keycap', 'description': 'a square keycap'}))
    result(await tools['model_set_params']({'params': {
        'width_u': assumed(1), 'pitch': assumed(19), 'height': assumed(8)}}))
    return result(await tools['model_add_feature']({
        'name': 'body', 'code': 'part = Box(P.width_u * P.pitch - 1, P.pitch - 1, P.height)',
        'evidence': 'test'}))


async def lay_out_row(tools, widths=(1, 1, 2, 1)):
    result(await tools['assembly_set_params']({'params': {
        'widths': assumed(list(widths)), 'pitch': assumed(19)}}))
    return result(await tools['assembly_add_feature']({'name': 'row', 'evidence': 'test', 'code': (
        'x = 0\n'
        'for i, w in enumerate(P.widths):\n'
        '    place("keycap", Pos(x + w * P.pitch / 2, 0, 0), name=f"key{i}", width_u=w)\n'
        '    x += w * P.pitch')}))


def test_workspace_file_and_names(tmp_path):
    assert part_name_from('Keycap Scan (2).STL') == 'keycap_scan_2'
    assert part_name_from('3d.stl') == 'part_3d'
    assert part_name_from('plate.stl', taken={'plate'}) == 'plate_2'
    assert part_name_from('assembly.stl') == 'assembly_2'
    (tmp_path / 'parts' / 'a').mkdir(parents=True)
    write_workspace(tmp_path, [{'name': 'a', 'reference': tmp_path / 'parts' / 'a' / 'reference.stl'},
                               {'name': 'b', 'reference': None}])
    parts, active = read_workspace(tmp_path)
    assert active == 'a' and parts[0]['reference'] == tmp_path / 'parts' / 'a' / 'reference.stl'
    assert parts[1]['reference'] is None


async def test_parts_build_independently(workspace, tools):
    assert workspace.active == 'plate' and workspace.cad.references['plate'].is_file()
    result(await tools['model_set_params']({'params': {
        'width': assumed(60), 'depth': assumed(40), 'thickness': assumed(6)}, 'verify': False}))
    plate = result(await tools['model_add_feature']({
        'name': 'base', 'code': 'part = Box(P.width, P.depth, P.thickness)', 'evidence': 'test'}))
    assert plate['part'] == 'plate' and plate['deviation']['verdict'] == 'deviates'  # no hole yet

    keycap = await make_keycap(tools)
    assert keycap['part'] == 'keycap' and workspace.active == 'keycap'
    assert keycap['deviation'] is None and keycap['trend'] == 'not checked (no reference mesh)'
    shown = workspace.cad.shown
    assert {'part_plate', 'part_keycap'} <= set(shown)
    assert shown['part_plate'].volume == pytest.approx(60 * 40 * 6)
    assert sorted(workspace.cad.namespace['PARTS']) == ['keycap', 'plate']

    # The plate is still addressable by name while keycap is active.
    status = result(await tools['model_status']({'part': 'plate'}))
    assert status['part'] == 'plate' and status['has_reference'] is True
    assert 'unknown' in error(await tools['model_status']({'part': 'unknown'}))
    assert 'already exists' in error(await tools['workspace_add_part']({'name': 'plate', 'description': 'x'}))
    assert workspace.objects() == ['part_plate', 'part_keycap']


async def test_assembly_variants_and_rebuild(workspace, tools):
    await make_keycap(tools)
    snapshots = workspace.cad.snapshots
    row = await lay_out_row(tools)
    assert row['size']['instances'] == {'keycap': 4}
    assert sorted(v['overrides']['width_u'] for v in row['size']['variants']) == [1, 2]
    assert workspace.cad.snapshots == snapshots + 2  # one per tool call, not per build
    assembly = workspace.cad.shown['assembly']
    assert sorted(c.label for c in assembly.children) == ['key0', 'key1', 'key2', 'key3']
    wide = next(c for c in assembly.children if c.label == 'key2')
    assert wide.bounding_box().size.X == pytest.approx(2 * 19 - 1)
    layout = workspace.assembly.layout
    key2 = next(i for i in layout['instances'] if i['id'] == 'key2')
    assert key2['matrix'][0][3] == pytest.approx(2 * 19 + 19)  # x of the 2u key's centre
    assert workspace.objects() == ['assembly']
    assert workspace.snapshot()['assembly']['instance_count'] == 4

    # Changing the keycap rebuilds the assembly with it.
    change = result(await tools['model_set_params']({'part': 'keycap', 'params': {'height': 10}}))
    assert change['assembly'] == 'assembly rebuilt: 4 instances'
    assert workspace.cad.shown['assembly'].bounding_box().size.Z == pytest.approx(10)

    # The combined script rebuilds everything from scratch.
    script = (workspace.artifacts.root / COMBINED_SCRIPT).read_text()
    shown = {}
    exec(compile(script, COMBINED_SCRIPT, 'exec'), {'show': lambda s, n: shown.__setitem__(n, s)})
    assert len(shown['assembly'].children) == 4


async def test_assembly_errors_roll_back(workspace, tools):
    await make_keycap(tools)
    await lay_out_row(tools)
    before = json.dumps(workspace.assembly.state)
    message = error(await tools['assembly_add_feature']({
        'name': 'bad', 'evidence': 'test', 'code': 'place("nope", Pos(0, P.pitch, 0))'}))
    assert "no part 'nope'; parts are ['keycap']" in message
    message = error(await tools['assembly_add_feature']({
        'name': 'bad', 'evidence': 'test', 'code': 'place("keycap", Pos(0, P.pitch, 0), colour=P.pitch)'}))
    assert 'keycap has no parameters' in message
    assert 'hard-codes' in error(await tools['assembly_add_feature']({
        'name': 'bad', 'evidence': 'test', 'code': 'place("keycap", Pos(0, 25, 0))'}))
    assert json.dumps(workspace.assembly.state) == before
    assert len(workspace.cad.shown['assembly'].children) == 4

    with pytest.raises(ModelError, match='placement'):
        await workspace.assembly.set_params(placement='Location((0, 0, 1))')
    undo = result(await tools['assembly_undo']({}))
    assert undo['features'] == []


async def test_assembly_check_reports_overlap(workspace, tools):
    await make_keycap(tools)
    await lay_out_row(tools, widths=(1, 1))
    apart = result(await tools['assembly_check']({'a': 'key0', 'b': 'key1'}))
    assert apart['overlap_volume'] == pytest.approx(0) and apart['min_distance'] == pytest.approx(1)
    result(await tools['assembly_set_params']({'params': {'pitch': 9}}))  # keys now overlap
    tight = result(await tools['assembly_check']({'a': 'key0', 'b': 'key1'}))
    assert tight['overlap_volume'] > 0
    assert 'No instance' in error(await tools['assembly_check']({'a': 'key0', 'b': 'key9'}))
