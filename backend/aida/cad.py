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


REFERENCE_ALIAS = 'reference_mesh'


def reference_object(part=None):
    """CAD session name of a part's imported reference mesh."""
    return f'ref_{part}' if part else REFERENCE_ALIAS


def is_reference(name):
    return name == REFERENCE_ALIAS or name.startswith('ref_')


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
        self.mesh = MeshTools(artifacts, units, timeout, tessellate=self.tessellate)
        self.lock = asyncio.Lock()
        self.broken = False
        self.schemas = {}
        # Optional async callback invoked after each successful execute; used by
        # the web session runner to snapshot generated CAD for the live viewer.
        self.on_execute = None
        # While above zero, execute does not snapshot (a model tool snapshots once
        # when it finishes, after the part and the assembly using it are rebuilt).
        self.paused_snapshots = 0

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
                args['objects'] = args.get('objects') or REFERENCE_ALIAS
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
            if name == 'render_view' and any(is_reference(o.strip()) for o in str(args['objects']).split(',')):
                result = await self._render_reference(args)
            else:
                result = await self._call(name, args)
            if name == 'render_view' and not failed(result):
                try:
                    self.artifacts.image(args['save_to'])
                except (OSError, ValueError) as exc:
                    result['isError'] = True
                    result['content'].append({'type': 'text', 'text': f'PNG render failed: {exc}'})
            if name == 'execute' and not failed(result) and self.on_execute is not None \
                    and not self.paused_snapshots:
                try:
                    await self.on_execute()
                except Exception as exc:
                    # A missed visualization snapshot must never break the
                    # agent's execution flow.
                    self.artifacts.log('snapshot_error', {'error': str(exc)})
            return result

    async def tessellate(self, object_name):
        """Write a named CAD object to a temporary STL for comparison; return its path."""
        if is_reference(object_name):
            raise ValueError('Compare a generated CAD object, not a reference mesh itself')
        target = self.artifacts.temp / f'compare-{uuid4().hex[:8]}.stl'
        fields = self.schemas['export'].input_schema.get('properties', {})
        field = next((f for f in ('path', 'filename', 'file_path') if f in fields), None)
        if field is None:
            raise RuntimeError('CAD export tool has no path argument')
        args = {field: str(target), 'object_name': object_name}
        if 'format' in fields:
            args['format'] = 'stl'
        async with self.lock:
            # Not the gated export(): a comparison needs the shape even when it is not yet valid.
            result = await self._call('export', args)
        if failed(result) or not target.is_file() or target.stat().st_size <= 84:
            target.unlink(missing_ok=True)
            raise ValueError(f'Could not tessellate {object_name}: {text_of(result)[:1500]}')
        return target

    async def snapshot_stl(self, target, object_name='*'):
        """Best-effort STL snapshot of one named object (or ``*`` for all),
        without the export validation gate: intermediate design states are
        expected to fail validation while the agent iterates, but the viewer
        still wants them."""
        fields = self.schemas['export'].input_schema.get('properties', {})
        if 'format' not in fields:
            raise RuntimeError('Installed export schema cannot select the STL format')
        args = {'format': 'stl', 'object_name': object_name}
        for field in ('filename', 'path', 'file_path'):
            if field in fields:
                args[field] = str(target)
                break
        else:
            raise RuntimeError('Installed export schema has no output path field')
        result = await self._call('export', args)
        if failed(result):
            raise RuntimeError(text_of(result))
        return target

    async def session_object_names(self):
        """Named objects registered in the CAD session. Uses the unlocked call
        path so it is safe from ``on_execute`` hooks running inside ``call``."""
        result = await self._call('session_state', {})
        if failed(result):
            return []
        try:
            return list(json.loads(text_of(result)).get('objects', {}))
        except Exception:
            return []

    async def _render_reference(self, args):
        target = args['objects'].strip()
        if not is_reference(target):
            raise ValueError('Render a reference mesh on its own, not together with generated CAD')
        part = None if target == REFERENCE_ALIAS else target[len('ref_'):]
        # The CAD server's own mesh render goes through a lossy STEP round-trip,
        # so reference views use the direct STL renderer (which also slices).
        spec = {'direction': args.get('direction') or 'iso',
                'azimuth': args.get('azimuth', 0), 'elevation': args.get('elevation', 0)}
        if args.get('clip_plane'):
            spec['slice_axis'] = args['clip_plane']
            if args.get('clip_at') is not None:
                spec['slice_at'] = args['clip_at']
        self.artifacts.log('tool_call', {'name': 'render_reference', 'arguments': args})
        result = await self.mesh.render(spec, args['save_to'], part=part)
        self.artifacts.log('tool_result', {'name': 'render_reference', **result})
        return result

    async def startup(self, mesh, part=None):
        await self.discover()
        await self.check_health()
        return await self.load_reference(mesh, part)

    async def check_health(self):
        """Version and render/export self-test (the slow part of startup, ~10 s)."""
        for name in ('version', 'health_check'):
            result = await self.call(name, {})
            if failed(result):
                raise RuntimeError(f'{name} failed: {text_of(result)}')
            if name == 'health_check':
                report = json.loads(text_of(result))
                if not report.get('ok'):
                    raise RuntimeError(f'CAD rendering/export dependencies failed: {report}')

    async def load_reference(self, mesh, part=None):
        """Import a reference mesh as ref_<part> (or reference_mesh without a part)."""
        # OpenCascade accepts some malformed STL files as empty shapes. Verify
        # real triangles and a usable preview before importing the reference.
        name = reference_object(part)
        if part and part not in self.mesh.references:
            self.mesh.references[part] = mesh
        preview = await self.call('render_view', {'objects': name, 'direction': 'iso'})
        if failed(preview):
            raise RuntimeError(f'STL preview of {name} failed: {text_of(preview)}')
        result = await self.call('import_cad_file', {'path': str(mesh), 'name': name})
        if failed(result):
            raise RuntimeError(f'STL import failed: {text_of(result)}')
        return result


@asynccontextmanager
async def open_cad(artifacts, units=None, cwd=None):
    """Start a CAD server. It may read and write only under its working directory
    (default: this session's folder) and its temp folder; a pre-started engine
    shared by later sessions passes their common parent folder as ``cwd``."""
    executable = shutil.which('build123d-mcp')
    if not executable:
        raise RuntimeError('build123d-mcp is missing; run uv sync --locked')
    env = {k: v for k, v in os.environ.items() if not k.endswith(('API_KEY', 'TOKEN', 'SECRET'))}
    env.update(TMPDIR=str(artifacts.temp), TEMP=str(artifacts.temp), TMP=str(artifacts.temp))
    params = StdioServerParameters(command=executable, env=env, cwd=str(cwd or artifacts.root))
    with (artifacts.root / 'cad-stderr.log').open('w') as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=185) as session:
                await session.initialize()
                yield CadBridge(session, artifacts, units=units)
