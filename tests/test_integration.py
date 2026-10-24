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
