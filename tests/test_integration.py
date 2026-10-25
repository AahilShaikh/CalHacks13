"""Local CAD test: set AIDA_INTEGRATION to 1, then run uv run pytest -m integration -s."""
import os
import re
import json
import math

import pytest

from aida.artifacts import Artifacts
from aida.cad import failed, open_cad, text_of

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('AIDA_INTEGRATION') != '1', reason='Set AIDA_INTEGRATION=1 for local CAD tests')]


async def test_cad_roundtrip(tmp_path):
    from build123d import Box, Cylinder, Pos, export_stl
    reference = tmp_path / 'asymmetric.stl'
    export_stl(Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10), str(reference))
    artifacts = Artifacts(tmp_path / 'sessions')
    mesh = artifacts.copy_mesh(reference)
    async with open_cad(artifacts) as cad:
        await cad.startup(mesh)
        for direction in ('front', 'side', 'top', 'iso'):
            result = await cad.call('render_view', {'objects': 'reference_mesh', 'direction': direction})
            assert not failed(result), text_of(result)
            matches = re.findall(r'\[SEND:\s*(.*?)\]', text_of(result))
            assert matches, text_of(result)
            assert artifacts.image(matches[0])['content'][0]['type'] == 'image'
        code = '''from build123d import *
width = 60
height = 40
thickness = 6
plate = Box(width, height, thickness)
for x in (-20, 20):
    plate -= Pos(x, 0, 0) * Cylinder(2.5, thickness + 2)
show(plate, "generated_cad")'''
        result = await cad.call('execute', {'code': code})
        assert not failed(result), text_of(result)
        for thickness in (6, 8):
            result = await cad.call('execute', {'code': code.replace('thickness = 6', f'thickness = {thickness}')})
            assert not failed(result), text_of(result)
            result = await cad.call('measure', {'object_name': 'generated_cad'})
            report = json.loads(text_of(result))
            assert report['bbox']['zsize'] == pytest.approx(thickness)
            assert report['volume'] == pytest.approx((60 * 40 - 2 * math.pi * 2.5**2) * thickness, abs=.01)
        rendered = await cad.call('render_view', {'objects': 'generated_cad', 'direction': 'iso'})
        assert not failed(rendered), text_of(rendered)
        props = cad.schemas['export'].input_schema['properties']
        path_field = next(k for k in ('path', 'filename', 'file_path') if k in props)
        for fmt in ('step', 'stl'):
            args = {path_field: f'plate.{fmt}', 'object_name': 'generated_cad'}
            if 'format' in props:
                args['format'] = fmt
            result = await cad.call('export', args)
            assert not failed(result), text_of(result)
            assert (artifacts.root / f'plate.{fmt}').stat().st_size > 0
        assert 'reference_mesh' in text_of(await cad.call('session_state', {}))


async def test_execute_mesh_snapshot(tmp_path):
    """Every successful execute should produce a GLB revision for the viewer."""
    from aida.api.cad_sessions import CadSession, snapshot_hook

    artifacts = Artifacts(tmp_path / 'sessions')
    session = CadSession(artifacts, 'build a plate', 'mm')
    code = '''from build123d import *
plate = Box(60, 40, 6)
for x in (-20, 20):
    plate -= Pos(x, 0, 0) * Cylinder(2.5, 8)
show(plate, "generated_cad")'''
    async with open_cad(artifacts) as cad:
        await cad.discover()
        cad.on_execute = snapshot_hook(cad, session)
        result = await cad.call('execute', {'code': code})
        assert not failed(result), text_of(result)
    assert len(session.meshes) == 1, [e for e in session.events if e['type'] in ('mesh', 'mesh_skipped')]
    mesh = session.meshes[0]
    assert mesh['generated'] and mesh['triangles'] > 0
    assert mesh['path'].read_bytes()[:4] == b'glTF'
    assert mesh['extents'][0] == pytest.approx(60, abs=0.1)
    assert mesh['step_path'].is_file()
    assert mesh['step_path'].read_text().startswith('ISO-10303-21;')
    assert [e['type'] for e in session.events].count('mesh') == 1


async def test_corrupt_stl_rejected(tmp_path):
    source = tmp_path / 'corrupt.stl'
    source.write_text('not an STL')
    artifacts = Artifacts(tmp_path / 'sessions')
    mesh = artifacts.copy_mesh(source)
    with pytest.raises(ExceptionGroup) as caught:
        async with open_cad(artifacts) as cad:
            await cad.startup(mesh)
    def messages(exc):
        if isinstance(exc, BaseExceptionGroup):
            return ' '.join(messages(e) for e in exc.exceptions)
        return str(exc)
    assert 'no renderable triangles' in messages(caught.value)


async def test_compare_generated_cad_with_reference(tmp_path):
    from build123d import Box, Cylinder, Pos, export_stl
    reference = tmp_path / 'plate.stl'
    export_stl(Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10), str(reference))
    artifacts = Artifacts(tmp_path / 'sessions')
    mesh = artifacts.copy_mesh(reference)
    async with open_cad(artifacts, 'mm') as cad:
        await cad.startup(mesh)
        code = '''from build123d import *
hole_x = 15
plate = Box(60, 40, 6) - Pos(hole_x, 5, 0) * Cylinder(4, 10)
show(plate, "generated_cad")'''
        for hole_x, verdict in ((15, 'within_threshold'), (17, 'deviates')):
            result = await cad.call('execute', {'code': code.replace('hole_x = 15', f'hole_x = {hole_x}')})
            assert not failed(result), text_of(result)
            report = await cad.mesh.compare({'object_name': 'generated_cad', 'samples': 4000})
            assert not report.get('isError'), report
            assert json.loads(report['content'][1]['text'])['verdict'] == verdict
        sliced = await cad.mesh.slice({'axis': 'z', 'at': 1, 'compare_to': 'generated_cad'})
        comparison = json.loads(sliced['content'][1]['text'])['comparison']
        hole = next(m for m in comparison['matched'] if m['role'] == 'hole')
        assert hole['centroid_offset'][0] == pytest.approx(2, abs=0.05)
        overlay = await cad.mesh.view({'compare_to': 'generated_cad'})
        assert not overlay.get('isError'), overlay
        missing = await cad.mesh.compare({'object_name': 'no_such_part'})
        assert missing['isError']


async def test_parametric_model_in_live_session(tmp_path):
    from build123d import Box, Cylinder, Pos, export_stl
    from aida.model import ModelError, ParametricModel
    reference = tmp_path / 'plate.stl'
    export_stl(Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10), str(reference))
    artifacts = Artifacts(tmp_path / 'sessions')
    mesh = artifacts.copy_mesh(reference)
    async with open_cad(artifacts, 'mm') as cad:
        await cad.startup(mesh, 'plate')
        model = ParametricModel(cad, artifacts, 'plate', reference=mesh)
        values = {'width': 60, 'depth': 40, 'thickness': 6, 'hole_x': 15, 'hole_y': 5, 'hole_d': 8}
        await model.set_params({name: {'value': value, 'source': 'measured', 'evidence': 'test'}
                                for name, value in values.items()})
        first = await model.add_feature('base plate', 'part = Box(P.width, P.depth, P.thickness)',
                                        evidence='test')
        assert first['deviation']['verdict'] == 'deviates'
        second = await model.add_feature(
            'hole', 'part -= Pos(P.hole_x, P.hole_y, 0) * Cylinder(P.hole_d / 2, P.thickness * 2)',
            evidence='test')
        assert second['trend'] == 'better' and second['deviation']['verdict'] == 'within_threshold'
        with pytest.raises(ModelError, match=r'F3 \(bad fillet\)'):
            await model.add_feature('bad fillet', 'part = part.fillet(P.width, part.edges())',
                                    evidence='test')
        worse = await model.set_params({'hole_x': 20})
        assert worse['trend'] == 'worse'
        await model.undo()
        report = json.loads(text_of(await cad.call('measure', {'object_name': 'part_plate'})))
        assert report['volume'] == pytest.approx((60 * 40 - math.pi * 16) * 6, rel=1e-3)
        assert 'within_threshold' in model.report('mm')


async def test_workspace_assembly_in_live_session(tmp_path):
    from build123d import Box, export_stl
    from aida.model_tools import model_tools
    from aida.workspace import Workspace, write_workspace
    reference = tmp_path / 'keycap.stl'
    export_stl(Box(18, 18, 8), str(reference))
    artifacts = Artifacts(tmp_path / 'sessions')
    mesh = artifacts.copy_mesh(reference)
    write_workspace(artifacts.root, [{'name': 'keycap', 'reference': mesh}])
    async with open_cad(artifacts, 'mm') as cad:
        await cad.startup(mesh, 'keycap')
        snapshots = []

        async def snapshot():
            snapshots.append(len(snapshots))
        cad.on_execute = snapshot
        workspace = Workspace.load(cad, artifacts)
        _, table = model_tools(cad, artifacts, workspace)
        tools = {name: handler for name, _, _, handler in table}

        def ok(response):
            assert not response.get('isError'), response['content'][0]['text']
            return json.loads(response['content'][0]['text'])

        def assumed(value):
            return {'value': value, 'source': 'assumed', 'evidence': 'test'}

        ok(await tools['model_set_params']({'params': {
            'width_u': assumed(1), 'pitch': assumed(19), 'gap': assumed(1), 'height': assumed(8)}}))
        cap = ok(await tools['model_add_feature']({'name': 'body', 'evidence': 'test',
                                                   'code': 'part = Box(P.width_u * P.pitch - P.gap, P.pitch - P.gap, P.height)'}))
        assert cap['deviation']['verdict'] == 'within_threshold'
        ok(await tools['workspace_add_part']({'name': 'plate', 'description': 'a plate under the keys'}))
        ok(await tools['model_set_params']({'params': {'length': assumed(80), 'width': assumed(20),
                                                       'thickness': assumed(1.5)}}))
        plate = ok(await tools['model_add_feature']({'name': 'plate', 'evidence': 'test',
                                                     'code': 'part = Box(P.length, P.width, P.thickness)'}))
        assert plate['trend'] == 'not checked (no reference mesh)'
        ok(await tools['assembly_set_params']({'params': {'widths': assumed([1, 1, 2]), 'pitch': assumed(19),
                                                          'cap_z': assumed(6)}}))
        before = len(snapshots)
        row = ok(await tools['assembly_add_feature']({'name': 'row', 'evidence': 'test', 'code': (
            'place("plate", Pos(0, 0, 0), name="plate")\n'
            'x = -P.pitch * 2\n'
            'for i, w in enumerate(P.widths):\n'
            '    place("keycap", Pos(x + w * P.pitch / 2, 0, P.cap_z), name=f"key{i}", width_u=w)\n'
            '    x += w * P.pitch')}))
        assert len(snapshots) == before + 1
        assert row['size']['instances'] == {'plate': 1, 'keycap': 3}
        assert len(row['size']['variants']) == 3
        key2 = next(i for i in workspace.assembly.layout['instances'] if i['id'] == 'key2')
        assert key2['matrix'][0][3] == pytest.approx(19) and key2['matrix'][2][3] == pytest.approx(6)
        fit = ok(await tools['assembly_check']({'a': 'key0', 'b': 'plate'}))
        assert fit['overlap_volume'] == pytest.approx(0) and fit['min_distance'] == pytest.approx(1.25)
        # Editing the keycap rebuilds the assembly in the same session.
        change = ok(await tools['model_set_params']({'part': 'keycap', 'params': {'height': 9}}))
        assert change['assembly'] == 'assembly rebuilt: 4 instances'
        measure = json.loads(text_of(await cad.call('measure', {'object_name': 'assembly'})))
        assert measure['bbox']['zmax'] == pytest.approx(10.5, abs=1e-3)
        exported = await cad.call('export', {'filename': 'row.step', 'format': 'step', 'object_name': 'assembly'})
        assert not failed(exported), text_of(exported)
