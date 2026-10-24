"""One local MCP process, serialized calls, and artifact policies."""
import asyncio
import json
import os
import shutil
from uuid import uuid4
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .artifacts import Artifacts
from .mesh_tools import MeshTools

EXPOSED = {'execute', 'render_view', 'measure', 'validate', 'export',
           'session_state', 'last_error', 'version', 'health_check', 'mesh_holes'}


def text_of(result):
    return '\n'.join(b.get('text', '') for b in result.get('content', []) if b.get('type') == 'text')


def failed(result):
    text = text_of(result).strip()
    return result.get('isError', False) or text.startswith(('Error:', 'ERROR:', 'Traceback'))


def normalize_result(result):
    """Map MCP 2 Python models to the SDK tool handler's wire-format dict."""
    raw = result.model_dump(by_alias=True, exclude_none=True)
    content = []
    for block in raw.get('content', []):
        block = dict(block)
        if 'mime_type' in block:
            block['mimeType'] = block.pop('mime_type')
        content.append(block)
    payload = {'content': content, 'isError': raw.get('is_error', raw.get('isError', False))}
    structured = raw.get('structured_content', raw.get('structuredContent'))
    if structured is not None:
        payload['structuredContent'] = structured
    return payload


class CadBridge:
    def __init__(self, session, artifacts: Artifacts, timeout=180, units=None):
        self.session = session
        self.artifacts = artifacts
        self.timeout = timeout
        self.mesh = MeshTools(artifacts, units, timeout)
        self.lock = asyncio.Lock()
        self.broken = False
        self.schemas = {}

    async def discover(self):
        result = await self.session.list_tools()
        self.schemas = {t.name: t for t in result.tools}
        missing = (EXPOSED | {'import_cad_file'}) - self.schemas.keys()
        if missing:
            raise RuntimeError(f'CAD server is missing tools: {sorted(missing)}')

    async def _call(self, name, args):
        if self.broken:
            raise RuntimeError('CAD session timed out; restart Aida before further work')
        self.artifacts.log('tool_call', {'name': name, 'arguments': args})
        print(f'  [CAD] {name}', flush=True)
        try:
            result = await asyncio.wait_for(self.session.call_tool(name, args), self.timeout)
        except (TimeoutError, asyncio.TimeoutError):
            self.broken = True
            raise RuntimeError(f'{name} exceeded {self.timeout}s; restart the CAD session') from None
        payload = normalize_result(result)
        self.artifacts.log('tool_result', {'name': name, **payload})
        return payload

    async def call(self, name, args):
        async with self.lock:
            args = dict(args)
            if name == 'execute':
                self.artifacts.save_code(args['code'])
            if name == 'render_view':
                args['format'] = 'png'
                args['objects'] = args.get('objects') or 'reference_mesh'
                args['save_to'] = str(self.artifacts.output_path(f'view-{uuid4().hex[:8]}.png'))
            if name == 'export':
                validation = await self._call('validate', {'object_name': args.get('object_name', '')})
                raw = text_of(validation)
                if failed(validation) or '"passes_gate": true' not in raw.lower():
                    return {'isError': True, 'content': [{'type': 'text', 'text': f'Export blocked by validation: {raw}'}]}
                # Discover the actual installed export path field rather than assuming a version.
                fields = self.schemas['export'].input_schema.get('properties', {})
                for field in ('path', 'filename', 'file_path'):
                    if field in fields:
                        supplied = args.get(field)
                        if not supplied:
                            raise ValueError(f'Export requires {field}')
                        target = self.artifacts.output_path(supplied)
                        if target.name == 'reference.stl':
                            raise ValueError('The reference mesh cannot be overwritten')
                        args[field] = str(target)
                        break
            if name == 'render_view' and 'reference_mesh' in args['objects']:
                result = await self._render_reference(args)
            else:
                result = await self._call(name, args)
            if name == 'render_view' and not failed(result):
                try:
                    self.artifacts.image(args['save_to'])
                except (OSError, ValueError) as exc:
                    result['isError'] = True
                    result['content'].append({'type': 'text', 'text': f'PNG render failed: {exc}'})
            return result

    async def _render_reference(self, args):
        if args['objects'] != 'reference_mesh':
            raise ValueError('Render reference_mesh separately from generated CAD in this milestone')
        # The CAD server's own mesh render goes through a lossy STEP round-trip,
        # so reference views use the direct STL renderer (which also slices).
        spec = {'direction': args.get('direction') or 'iso',
                'azimuth': args.get('azimuth', 0), 'elevation': args.get('elevation', 0)}
        if args.get('clip_plane'):
            spec['slice_axis'] = args['clip_plane']
            if args.get('clip_at') is not None:
                spec['slice_at'] = args['clip_at']
        self.artifacts.log('tool_call', {'name': 'render_reference', 'arguments': args})
        result = await self.mesh.render(spec, args['save_to'])
        self.artifacts.log('tool_result', {'name': 'render_reference', **result})
        return result

    async def startup(self, mesh):
        await self.discover()
        for name in ('version', 'health_check'):
            result = await self.call(name, {})
            if failed(result):
                raise RuntimeError(f'{name} failed: {text_of(result)}')
            if name == 'health_check':
                report = json.loads(text_of(result))
                if not report.get('ok'):
                    raise RuntimeError(f'CAD rendering/export dependencies failed: {report}')
        # OpenCascade accepts some malformed STL files as empty shapes. Verify
        # real triangles and a usable preview before importing the reference.
        preview = await self.call('render_view', {'objects': 'reference_mesh', 'direction': 'iso'})
        if failed(preview):
            raise RuntimeError(f'STL preview failed: {text_of(preview)}')
        result = await self.call('import_cad_file', {'path': str(mesh), 'name': 'reference_mesh'})
        if failed(result):
            raise RuntimeError(f'STL import failed: {text_of(result)}')
        return result


@asynccontextmanager
async def open_cad(artifacts, units=None):
    executable = shutil.which('build123d-mcp')
    if not executable:
        raise RuntimeError('build123d-mcp is missing; run uv sync --locked')
    env = {k: v for k, v in os.environ.items() if not k.endswith(('API_KEY', 'TOKEN', 'SECRET'))}
    env.update(TMPDIR=str(artifacts.temp), TEMP=str(artifacts.temp), TMP=str(artifacts.temp))
    params = StdioServerParameters(command=executable, env=env, cwd=str(artifacts.root))
    with (artifacts.root / 'cad-stderr.log').open('w') as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=185) as session:
                await session.initialize()
                yield CadBridge(session, artifacts, units=units)
