from types import SimpleNamespace

from aida.agent import MESH_TOOLS, make_tools, options_for
from aida.artifacts import Artifacts
from aida.cad import EXPOSED


class FakeMesh:
    async def view(self, args):
        return {'content': [{'type': 'text', 'text': 'view_mesh'}]}

    async def slice(self, args):
        return {'content': [{'type': 'text', 'text': 'slice_mesh'}]}

    async def series(self, args):
        return {'isError': True, 'content': [{'type': 'text', 'text': 'slice_series'}]}

    async def compare(self, args):
        return {'content': [{'type': 'text', 'text': 'compare_to_mesh'}]}


class FakeCad:
    schemas = {name: SimpleNamespace(description=name, input_schema={
        'type': 'object', 'properties': {}}) for name in EXPOSED}
    mesh = FakeMesh()

    async def call(self, name, args):
        return {'content': [{'type': 'text', 'text': name}]}


def test_runtime_restricted(tmp_path):
    opts = options_for(FakeCad(), Artifacts(tmp_path))
    assert opts.tools == []
    assert opts.setting_sources == []
    assert opts.strict_mcp_config
    assert opts.permission_mode == 'dontAsk'
    assert opts.max_turns == 30
    assert set(opts.mcp_servers) == {'aida'}
    assert set(opts.allowed_tools) == {f'mcp__aida__{n}' for n in EXPOSED | MESH_TOOLS | {'view_image'}}


async def test_dynamic_handlers_keep_correct_tool_names(tmp_path):
    tools = make_tools(FakeCad(), Artifacts(tmp_path))
    for item in tools:
        if item.name != 'view_image':
            result = await item.handler({})
            assert result['content'][0]['text'] == item.name


async def test_mesh_tool_error_flag(tmp_path):
    tools = {t.name: t for t in make_tools(FakeCad(), Artifacts(tmp_path))}
    assert MESH_TOOLS <= set(tools)
    assert (await tools['slice_series'].handler({'axis': 'z'}))['is_error'] is True
    assert (await tools['view_mesh'].handler({}))['is_error'] is False


async def test_sdk_error_flag(tmp_path):
    class FailingCad(FakeCad):
        async def call(self, name, args):
            return {'isError': True, 'content': [{'type': 'text', 'text': 'CAD failure'}]}
    tools = make_tools(FailingCad(), Artifacts(tmp_path))
    execute = next(t for t in tools if t.name == 'execute')
    assert (await execute.handler({}))['is_error'] is True
    viewer = next(t for t in tools if t.name == 'view_image')
    assert (await viewer.handler({'path': '/outside.png'}))['is_error'] is True
