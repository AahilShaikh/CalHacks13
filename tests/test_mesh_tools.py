import json

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
