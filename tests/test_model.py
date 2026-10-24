import json
import math
import traceback

import numpy as np
import pytest

from aida.artifacts import Artifacts
from aida.mesh_compare import deviation
from aida.model import ModelError, ParametricModel, generate_script, trend


class LocalCad:
    """Runs generated scripts with build123d in-process, like the CAD server's execute."""

    def __init__(self, reference, tmp_path):
        self.namespace = {}
        self.shown = {}
        self.last_line = None
        self.calls = []
        self.mesh = self
        self.units = 'mm'
        self.reference = reference
        self.tmp_path = tmp_path

    async def call(self, name, args):
        self.calls.append(name)
        if name == 'execute':
            return self._execute(args['code'])
        if name == 'last_error':
            return _text(json.dumps({'line': self.last_line}))
        if name == 'measure':
            part = self.shown[args['object_name']]
            box = part.bounding_box()
            return _text(json.dumps({'volume': part.volume, 'bbox': {
                'xsize': box.size.X, 'ysize': box.size.Y, 'zsize': box.size.Z}}))
        raise AssertionError(name)

    def _execute(self, code):
        self.namespace['show'] = lambda shape, name: self.shown.__setitem__(name, shape)
        try:
            exec(compile(code, '<mcp>', 'exec'), self.namespace)
        except SyntaxError as exc:
            self.last_line = exc.lineno
            return _text(f'Error: SyntaxError: {exc}')
        except Exception as exc:
            frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == '<mcp>']
            self.last_line = frames[-1].lineno if frames else None
            return _text(f'Error: {type(exc).__name__}: {exc}')
        self.last_line = None
        return _text('ok')

    async def stats(self, object_name, samples):
        from build123d import export_stl
        path = self.tmp_path / 'cad.stl'
        export_stl(self.shown[object_name], str(path))
        return deviation(self.reference, path, samples=samples)


def _text(value):
    return {'content': [{'type': 'text', 'text': value}]}


BASE = 'part = Box(P.width, P.depth, P.thickness)'
HOLE = 'part -= Pos(P.hole_x, P.hole_y, 0) * Cylinder(P.hole_d / 2, P.thickness * 2)'


@pytest.fixture
def model(tmp_path, plate_stl):
    artifacts = Artifacts(tmp_path / 'session')
    artifacts.copy_mesh(plate_stl)
    return ParametricModel(LocalCad(plate_stl, tmp_path), artifacts)


async def build_plate(model, hole_x=15):
    await model.set_params({'width': {'value': 60, 'source': 'measured', 'evidence': 'slice z=1 L0',
                                      'measured': 60.002},
                            'depth': 40, 'thickness': 6}, verify=False)
    await model.add_feature('base plate', BASE, evidence='slice z=1 L0 rectangle')
    await model.set_params({'hole_x': hole_x, 'hole_y': 5, 'hole_d': 8}, verify=False)
    return await model.add_feature('hole', HOLE, evidence='slice z=1 L1 circle')


async def test_build_verify_and_persist(model):
    result = await build_plate(model)
    assert result['trend'] == 'better'
    assert result['deviation']['verdict'] == 'within_threshold'
    assert result['size']['volume'] == pytest.approx(60 * 40 * 6 - math.pi * 16 * 6, rel=1e-3)
    assert result['features'] == ['F1 base plate', 'F2 hole']
    script = (model.artifacts.root / 'model.py').read_text()
    assert 'class P:' in script and 'def build():' in script and 'width = 60  # measured' in script
    saved = json.loads((model.artifacts.root / 'model.json').read_text())
    assert saved['params']['width']['measured'] == 60.002
    assert len(list((model.artifacts.root / 'model-history').glob('*.py'))) == model.state['revision']


async def test_failed_build_rolls_back_and_names_feature(model):
    await build_plate(model)
    before = json.dumps(model.state)
    with pytest.raises(ModelError, match=r'in F3 \(broken\).*NameError'):
        await model.add_feature('broken', 'part -= Pos(0, 0, 0) * Cylinder(no_such_value, 10)')
    assert json.dumps(model.state) == before
    with pytest.raises(ModelError, match=r'in F1 \(base plate\)'):
        await model.set_params({'thickness': -1})  # Box rejects a negative size
    assert model.state['params']['thickness']['value'] == 6


@pytest.mark.parametrize('code, message', [
    ('show(part, "x")', 'show'), ('import os', 'import'), ('return part', 'return'),
    ('part -= Box(1, 1, P.missing)', 'undefined parameters'), ('part = (', 'syntax error'),
])
async def test_feature_code_checks(model, code, message):
    await build_plate(model)
    calls = len(model.cad.calls)
    with pytest.raises(ModelError, match=message):
        await model.add_feature('bad', code)
    assert len(model.cad.calls) == calls  # rejected before reaching the CAD session


async def test_first_feature_must_create_part(model):
    await model.set_params({'width': 60}, verify=False)
    with pytest.raises(ModelError, match='first feature must assign part'):
        await model.add_feature('helper', 'size = P.width * 2')


async def test_worse_change_then_undo(model):
    await build_plate(model)
    good = model.state['deviation']
    worse = await model.set_params({'hole_x': 22})
    assert worse['trend'] == 'worse' and 'model_undo' in worse['hint']
    restored = await model.undo()
    assert model.state['params']['hole_x']['value'] == 15
    assert restored['deviation'] == good
    assert model.cad.shown['generated_cad'].volume == pytest.approx(60 * 40 * 6 - math.pi * 16 * 6, rel=1e-3)
    assert model.state['history'][-1]['trend'] == 'restored'


async def test_param_and_feature_edits(model):
    await build_plate(model)
    with pytest.raises(ModelError, match='still used'):
        await model.set_params(remove=['hole_d'])
    with pytest.raises(ModelError, match='source'):
        await model.set_params({'hole_d': {'value': 8, 'source': 'guessed'}})
    meta_only = await model.set_params({'hole_d': {'evidence': 'mesh_holes'}}, verify=False)
    assert meta_only['trend'] == 'not checked' and model.state['params']['hole_d']['value'] == 8
    removed = await model.edit_feature('F2', remove=True)
    assert removed['features'] == ['F1 base plate'] and removed['deviation']['verdict'] == 'deviates'
    await model.set_params(remove=['hole_d'], verify=False)
    with pytest.raises(ModelError, match='only feature'):
        await model.edit_feature('F1', remove=True)


async def test_placement_and_report(model):
    await build_plate(model)
    with pytest.raises(ModelError, match='expression'):
        await model.set_params(placement='Location((0, 0, 0)) =')
    shifted = await model.set_params(placement='Location((0, 0, 1))')
    assert shifted['trend'] == 'worse'
    await model.undo()
    text = model.report('mm')
    assert '**Verdict: within_threshold**' in text
    assert '| width | 60 | measured | 60 | slice z=1 L0 |' in text
    assert 'F2. **hole** uses hole_d, hole_x, hole_y, thickness.' in text
    assert (model.artifacts.root / 'report.md').read_text() == text


def test_script_spans_and_trend():
    state = {'params': {'a': {'value': 1, 'source': 'assumed', 'evidence': ''}}, 'placement': None, 'features': [
        {'id': 'F1', 'name': 'one', 'code': 'part = Box(1, 1, 1)\nx = 2', 'evidence': ''},
        {'id': 'F4', 'name': 'two', 'code': 'part -= Box(1, 1, 1)', 'evidence': 'e'}]}
    script, spans = generate_script(state)
    lines = script.splitlines()
    assert [lines[i - 1].strip() for i in range(spans['F1'][0], spans['F1'][1] + 1)] == ['part = Box(1, 1, 1)', 'x = 2']
    assert lines[spans['F4'][0] - 1].strip() == 'part -= Box(1, 1, 1)'
    base = {'share_within': 0.5, 'p95': 2.0, 'threshold': 0.4}
    assert trend(None, base) == 'first'
    assert trend(base, {**base, 'share_within': 0.9}) == 'better'
    assert trend(base, {**base, 'p95': 3.0}) == 'worse'
    assert trend(base, {**base, 'share_within': 0.9, 'p95': 3.0}) == 'mixed'
    assert trend(base, dict(base)) == 'same'
