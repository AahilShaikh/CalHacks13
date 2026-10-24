import json

import pytest

from aida.artifacts import Artifacts
from aida.cad import CadBridge
from aida.mesh_tools import MeshTools


def session(tmp_path, stl):
    artifacts = Artifacts(tmp_path / 'sessions')
    artifacts.copy_mesh(stl)
    return artifacts


async def test_view_returns_image_and_frame(tmp_path, plate_stl):
    tools = MeshTools(session(tmp_path, plate_stl), units='mm')
    result = await tools.view({'fit_box': [[10, 0, -3], [20, 10, 3]], 'direction': 'top'})
    assert not result.get('isError')
    image, meta, send = result['content']
    assert image['type'] == 'image'
    frame = json.loads(meta['text'])
    assert frame['focal_point'] == [15, 5, 0] and frame['units'] == 'mm'
    assert send['text'].startswith('[SEND:')


async def test_errors_are_clean_messages(tmp_path, plate_stl):
    tools = MeshTools(session(tmp_path, plate_stl))
    bad_view = await tools.view({'view_width': -1})
    assert bad_view['isError'] and bad_view['content'][0]['text'] == 'view_width must be positive'
    bad_slice = await tools.slice({'axis': 'z', 'at': 99})
    assert bad_slice['isError']
    assert bad_slice['content'][0]['text'].startswith('Slice at 99 is outside')
    assert 'Traceback' not in bad_slice['content'][0]['text']


async def test_slice_and_series(tmp_path, plate_stl):
    tools = MeshTools(session(tmp_path, plate_stl), units='mm')
    result = await tools.slice({'axis': 'z', 'at': 1})
    assert result['content'][0]['type'] == 'image'
    data = json.loads(result['content'][1]['text'])
    assert data['hole_count'] == 1 and 'build123d' in data
    series = json.loads((await tools.series({'axis': 'x', 'count': 6}))['content'][0]['text'])
    assert series['axis'] == 'x' and series['samples']


async def test_bridge_reference_render_supports_clip(tmp_path, plate_stl):
    class NoServer:
        async def call_tool(self, name, args):
            raise AssertionError('reference renders must not reach the CAD server')
    bridge = CadBridge(NoServer(), session(tmp_path, plate_stl), units='mm')
    result = await bridge.call('render_view', {'objects': 'reference_mesh', 'direction': 'front',
                                               'clip_plane': 'y', 'clip_at': 5})
    assert not result.get('isError')
    assert json.loads(result['content'][1]['text'])['slice']['label'] == 'y=5'


def _copying_tessellator(stl, artifacts):
    calls = []

    async def tessellate(name):
        calls.append(name)
        target = artifacts.temp / f'cad-{len(calls)}.stl'
        target.write_bytes(stl.read_bytes())
        return target
    return tessellate, calls


async def test_compare_view_and_slice_with_cad(tmp_path, plate_stl):
    artifacts = session(tmp_path, plate_stl)
    tessellate, calls = _copying_tessellator(plate_stl, artifacts)
    tools = MeshTools(artifacts, units='mm', tessellate=tessellate)
    report = await tools.compare({'object_name': 'generated_cad', 'samples': 2000,
                                  'view': {'direction': 'top'}})
    assert not report.get('isError'), report
    data = json.loads(report['content'][1]['text'])
    assert data['object_name'] == 'generated_cad' and data['verdict'] == 'within_threshold'
    assert data['heatmap']['view'] == 'top'
    view = await tools.view({'compare_to': 'generated_cad', 'direction': 'iso'})
    assert 'overlay' in json.loads(view['content'][1]['text'])
    sliced = await tools.slice({'axis': 'z', 'at': 1, 'compare_to': 'generated_cad'})
    assert json.loads(sliced['content'][1]['text'])['comparison']['within_tolerance']
    assert calls == ['generated_cad'] * 3
    assert not list(artifacts.temp.glob('cad-*.stl'))  # temporary tessellations are removed


async def test_compare_errors(tmp_path, plate_stl):
    artifacts = session(tmp_path, plate_stl)
    no_cad = await MeshTools(artifacts).compare({'object_name': 'part'})
    assert no_cad['isError'] and 'not available' in no_cad['content'][0]['text']
    tessellate, calls = _copying_tessellator(plate_stl, artifacts)
    tools = MeshTools(artifacts, tessellate=tessellate)
    blank = await tools.compare({'object_name': ' '})
    assert blank['isError'] and 'Name the generated CAD' in blank['content'][0]['text']
    bad_view = await tools.compare({'object_name': 'part', 'view': {'zoom': 3}})
    assert bad_view['isError'] and 'Unknown view options' in bad_view['content'][0]['text']
    assert calls == []


async def test_bridge_tessellates_through_ungated_export(tmp_path, plate_stl):
    from types import SimpleNamespace

    class ExportSession:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            from pathlib import Path
            Path(args['filename']).write_bytes(plate_stl.read_bytes())
            return SimpleNamespace(model_dump=lambda **_: {'content': [{'type': 'text', 'text': 'Exported'}]})

    bridge = CadBridge(ExportSession(), session(tmp_path, plate_stl))
    bridge.schemas['export'] = SimpleNamespace(input_schema={'properties': {'filename': {}, 'format': {}}})
    path = await bridge.tessellate('generated_cad')
    name, args = bridge.session.calls[-1]
    assert name == 'export' and args['format'] == 'stl' and args['object_name'] == 'generated_cad'
    assert path.parent == bridge.artifacts.temp and path.stat().st_size > 84
    with pytest.raises(ValueError, match='reference_mesh'):
        await bridge.tessellate('reference_mesh')
