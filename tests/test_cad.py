import asyncio
from types import SimpleNamespace

import pytest

from aida.artifacts import Artifacts
from aida.cad import CadBridge


class Result:
    def __init__(self, text):
        self.text = text

    def model_dump(self, **kwargs):
        return {'content': [{'type': 'text', 'text': self.text}]}


class FakeSession:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.calls = []

    async def call_tool(self, name, args):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.calls.append((name, args))
        try:
            await asyncio.sleep(.01)
            return Result('FAIL' if name == 'validate' else 'OK')
        finally:
            self.active -= 1


async def test_calls_serialized_and_export_gated(tmp_path):
    session = FakeSession()
    bridge = CadBridge(session, Artifacts(tmp_path))
    await asyncio.gather(bridge.call('measure', {}), bridge.call('measure', {}))
    assert session.maximum == 1
    result = await bridge.call('export', {'path': 'bad.step'})
    assert result['isError']
    assert not any(name == 'export' for name, _ in session.calls)


async def test_timeout_stops_reuse(tmp_path):
    bridge = CadBridge(FakeSession(), Artifacts(tmp_path), timeout=.001)
    with pytest.raises(RuntimeError, match='exceeded'):
        await bridge.call('execute', {'code': 'result = Box(1, 2, 3)'})
    with pytest.raises(RuntimeError, match='restart'):
        await bridge.call('measure', {})
    assert list(bridge.artifacts.root.glob('code-*.py'))


def test_mcp_v2_result_preserves_images_and_errors():
    from aida.cad import normalize_result
    class NativeResult:
        def model_dump(self, **kwargs):
            return {'content': [{'type': 'image', 'mime_type': 'image/png', 'data': 'abc'}],
                    'is_error': True, 'structured_content': {'reason': 'failure'}}
    converted = normalize_result(NativeResult())
    assert converted['isError'] is True
    assert converted['content'][0]['mimeType'] == 'image/png'
    assert converted['structuredContent'] == {'reason': 'failure'}


async def test_render_without_image_is_error(tmp_path):
    bridge = CadBridge(FakeSession(), Artifacts(tmp_path))
    result = await bridge.call('render_view', {'objects': 'generated_cad'})
    assert result['isError']
    assert 'PNG render failed' in result['content'][-1]['text']


async def test_export_path_and_reference_protection(tmp_path):
    class ValidSession(FakeSession):
        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return Result('Validity gate: PASS\n{"passes_gate": true}')
    bridge = CadBridge(ValidSession(), Artifacts(tmp_path))
    bridge.schemas['export'] = SimpleNamespace(input_schema={'properties': {'filename': {}}})
    with pytest.raises(ValueError, match='reference'):
        await bridge.call('export', {'filename': 'reference.stl'})
    with pytest.raises(ValueError, match='inside'):
        await bridge.call('export', {'filename': '../escape.step'})
    await bridge.call('export', {'filename': 'part.step'})
    assert bridge.session.calls[-1] == ('export', {'filename': str(bridge.artifacts.root / 'part.step')})


async def test_render_view_defaults_to_the_model_and_uses_our_renderer(tmp_path):
    bridge = CadBridge(FakeSession(), Artifacts(tmp_path))
    bridge.default_objects = lambda: ['assembly']
    seen = {}

    async def tessellate(name):
        seen['object'] = name
        return tmp_path / 'assembly.stl'

    async def render_file(stl, spec, save_to, style='scan', sheet=False):
        seen.update(stl=stl, style=style, direction=spec['direction'])
        from PIL import Image
        image = Image.new('RGB', (8, 8), 'gray')
        image.putpixel((0, 0), (0, 0, 0))  # not blank, so the PNG check passes
        image.save(save_to)
        return {'content': [{'type': 'text', 'text': 'ok'}]}

    bridge._tessellate = tessellate
    bridge.mesh.render_file = render_file
    result = await bridge.call('render_view', {'direction': 'front'})
    assert not result.get('isError'), result
    assert seen == {'object': 'assembly', 'stl': tmp_path / 'assembly.stl', 'style': 'cad', 'direction': 'front'}
    assert not any(name == 'render_view' for name, _ in bridge.session.calls)  # not the engine's renderer
