"""Live CAD sessions for the web viewer: streamed events and GLB mesh revisions.

One session owns one artifacts directory and one long-lived CAD + Claude run.
Every agent/CAD activity is appended to an in-memory event log that SSE clients
consume (with replay after reconnect). Every successful ``execute`` snapshots
the generated CAD: named objects (excluding ``reference_mesh``) are exported to
STL through the CAD server, converted to one GLB, and announced with a
revisioned binary URL. The viewer fetches that GLB and animates the transition
from the previous mesh while the export/convert step runs.

Routes are mounted by :mod:`aida.api.server`; the runner is a plain module
function so tests can substitute their own.
"""
import asyncio
import io
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..artifacts import Artifacts

DEFAULT_STORAGE = Path('artifacts/cad')
# Sample meshes offered by the UI's empty state ("Try the example part").
EXAMPLES_DIR = Path(__file__).resolve().parents[3] / 'examples'

MAX_MESH_BYTES = 64 * 1024 * 1024
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MESH_SUFFIXES = {'.stl', '.glb', '.obj', '.ply'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
UNITS = {'mm'}
SSE_HEARTBEAT_S = 15.0
# Tools whose results name a PNG the feed should show.
IMAGE_TOOLS = {'render_view', 'view_mesh', 'slice_mesh', 'compare_to_mesh'}

router = APIRouter()


# --------------------------------------------------------------------------
# Mesh helpers
# --------------------------------------------------------------------------

def load_mesh(data: bytes, name: str):
    """Load an uploaded mesh with trimesh; raises ValueError on garbage."""
    import trimesh

    if len(data) > MAX_MESH_BYTES:
        raise ValueError(f'Mesh exceeds {MAX_MESH_BYTES // (1024 * 1024)} MB')
    suffix = Path(name).suffix.lower()
    if suffix not in MESH_SUFFIXES:
        raise ValueError(f'Mesh must be one of {sorted(MESH_SUFFIXES)}')
    try:
        mesh = trimesh.load(io.BytesIO(data), file_type=suffix.lstrip('.'), force='mesh')
    except Exception as exc:
        raise ValueError(f'Mesh could not be read: {exc}') from exc
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ValueError('Mesh contains no triangles')
    return mesh


def mesh_stats(mesh) -> dict:
    bounds = mesh.bounds
    return {
        'triangles': int(len(mesh.faces)),
        'vertices': int(len(mesh.vertices)),
        'bbox': [round(float(v), 3) for v in (*bounds[0], *bounds[1])],
        'extents': [round(float(v), 3) for v in mesh.extents],
    }


def write_reference(mesh, artifacts: Artifacts) -> Path:
    """Write the uploaded mesh as reference.stl in session coordinates."""
    target = artifacts.root / 'reference.stl'
    mesh.export(str(target), file_type='stl')
    return target


def write_glb(mesh, path: Path) -> dict:
    mesh.export(str(path), file_type='glb')
    stats = mesh_stats(mesh)
    stats['bytes'] = path.stat().st_size
    return stats


def save_image_png(data: bytes, name: str, target: Path):
    """Store an uploaded photo as PNG (the agent's view_image only accepts PNG)."""
    from PIL import Image

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f'Image {name} exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB')
    if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f'Image {name} must be one of {sorted(IMAGE_SUFFIXES)}')
    try:
        img = Image.open(io.BytesIO(data))
        img.convert('RGB').save(target, format='PNG')
    except Exception as exc:
        raise ValueError(f'Image {name} could not be read: {exc}') from exc


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

class CadSession:
    """One live session: artifact directory, event log, and mesh revisions."""

    def __init__(self, artifacts: Artifacts, prompt: str, units: str | None):
        self.id = uuid.uuid4().hex[:12]
        self.artifacts = artifacts
        self.prompt = prompt
        self.units = units
        self.images: list[str] = []
        self.status = 'queued'      # queued | running | done | failed
        self.stage = 'preparing'    # fine-grained progress shown in the UI
        self.error: str | None = None
        self.events: list[dict] = []
        self.meshes: list[dict] = []    # one entry per revision: path + stats
        self.turns: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._waiters: set[asyncio.Future] = set()

    # -- URLs ---------------------------------------------------------------
    def heatmap_url(self, revision: int) -> str:
        return f'/api/v1/cad-sessions/{self.id}/heatmap.glb?rev={revision}'

    def mesh_url(self, revision: int | None = None) -> str:
        base = f'/api/v1/cad-sessions/{self.id}/mesh.glb'
        return base if revision is None else f'{base}?rev={revision}'

    @property
    def events_url(self) -> str:
        return f'/api/v1/cad-sessions/{self.id}/events'

    def to_dict(self) -> dict:
        return {
            'session_id': self.id,
            'status': self.status,
            'stage': self.stage,
            'error': self.error,
            'revisions': len(self.meshes),
            'latest_mesh': self.mesh_url(len(self.meshes) - 1) if self.meshes else None,
            'meshes': [{'revision': m['revision'], 'generated': m['generated'],
                        'triangles': m['triangles'], 'extents': m['extents']}
                       for m in self.meshes],
            'images': list(self.images),
            'events_url': self.events_url,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    # -- events -------------------------------------------------------------
    def record(self, event: dict) -> dict:
        """Append an event without waking streamers (used before clients can
        connect, possibly from a worker thread)."""
        stamped = {'seq': len(self.events) + 1, 'at': time.time(), **event}
        self.events.append(stamped)
        self.updated_at = stamped['at']
        return stamped

    def publish(self, event: dict) -> dict:
        """Append an event and wake every connected stream (loop thread only)."""
        stamped = self.record(event)
        for waiter in list(self._waiters):
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()
        return stamped

    def replay(self, after: int) -> list[dict]:
        return [e for e in self.events if e['seq'] > after]

    def set_stage(self, stage: str):
        self.stage = stage
        self.publish({'type': 'stage', 'stage': stage})

    def set_status(self, status: str, error: str | None = None):
        self.status = status
        self.error = error
        self.publish({'type': 'status', 'status': status, 'error': error})

    async def stream(self, after: int = 0):
        """Server-sent events: replay from ``after`` then live tail."""
        yield 'retry: 2000\n\n'
        cursor = after
        while True:
            pending = self.replay(cursor)
            if pending:
                for event in pending:
                    cursor = event['seq']
                    yield f'id: {cursor}\ndata: {json.dumps(event, default=str)}\n\n'
            elif await self._wait_for_more():
                # Timed out quiet: keep-alive comment so proxies and the
                # browser hold the stream open.
                yield ': ping\n\n'

    async def _wait_for_more(self) -> bool:
        """Wait for the next publish; True when it timed out instead."""
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.add(waiter)
        try:
            await asyncio.wait_for(waiter, SSE_HEARTBEAT_S)
            return False
        except TimeoutError:
            return True
        finally:
            self._waiters.discard(waiter)


class CadSessionManager:
    """Holds live sessions; the runner is injectable for tests."""

    def __init__(self, storage_dir=DEFAULT_STORAGE, runner=None):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._sessions: dict[str, CadSession] = {}
        self._mutex = threading.Lock()

    # -- lifecycle ----------------------------------------------------------
    def create(self, mesh_bytes: bytes, mesh_name: str,
               images: list[tuple[str, bytes]], prompt: str,
               units: str | None) -> CadSession:
        """Blocking preparation: validate uploads and build revision 0."""
        artifacts = Artifacts(self.storage_dir)
        session = CadSession(artifacts, prompt, units)
        session.record({'type': 'stage', 'stage': 'preparing'})

        upload = artifacts.root / f'upload{Path(mesh_name).suffix.lower()}'
        upload.write_bytes(mesh_bytes)
        mesh = load_mesh(mesh_bytes, mesh_name)
        write_reference(mesh, artifacts)

        image_names = []
        for index, (name, data) in enumerate(images, start=1):
            if len(image_names) >= MAX_IMAGES:
                raise ValueError(f'At most {MAX_IMAGES} reference images')
            image_path = artifacts.root / f'input-{index:02d}.png'
            save_image_png(data, name, image_path)
            image_names.append(image_path.name)
        session.images = image_names

        glb = artifacts.root / 'mesh-000.glb'
        stats = write_glb(mesh, glb)
        session.meshes.append({'revision': 0, 'path': glb, 'generated': False, **stats})
        session.record({'type': 'mesh', 'revision': 0, 'url': session.mesh_url(0),
                        'generated': False, **stats})

        artifacts.log('session', {'mesh': mesh_name, 'units': units,
                                  'images': image_names, 'prompt': prompt})
        with self._mutex:
            self._sessions[session.id] = session
        return session

    @property
    def uses_real_agent(self) -> bool:
        """False when a test or demo runner stands in for Claude."""
        return self._runner is None

    def start(self, session: CadSession):
        """Kick off the runner on the current event loop."""
        runner = self._runner or run_cad_session
        session.task = asyncio.create_task(runner(session))
        return session

    def get(self, session_id: str) -> CadSession | None:
        with self._mutex:
            return self._sessions.get(session_id)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def agent_event_bridge(session: CadSession):
    """Translate agent tool events into viewer events (code + render files)."""
    state = {'code': 0}

    async def bridge(event: dict):
        if event.get('type') == 'tool_call' and event.get('name') == 'execute':
            state['code'] += 1
            session.publish({'type': 'code', 'index': state['code'],
                             'code': (event.get('args') or {}).get('code', '')})
        elif event.get('type') == 'tool_result' and event.get('ok') \
                and event.get('name') in IMAGE_TOOLS:
            for match in re.findall(r'[\w./-]+\.png', event.get('text', '')):
                name = Path(match).name
                if (session.artifacts.root / name).is_file():
                    session.publish({'type': 'render',
                                     'url': f'/api/v1/cad-sessions/{session.id}/files/{name}'})
                    break
        session.publish(event)

    return bridge


def snapshot_hook(cad, session: CadSession):
    """After each execute: export generated CAD and publish the new GLB."""
    async def snapshot():
        revision = len(session.meshes)
        session.set_stage('exporting_mesh')
        try:
            names = await generated_object_names(cad)
            if not names:
                session.publish({'type': 'mesh_skipped',
                                 'reason': 'no generated CAD objects yet'})
                return
            parts = []
            for index, name in enumerate(names):
                part = session.artifacts.root / f'mesh-{revision:03d}-{index}.stl'
                try:
                    await cad.snapshot_stl(part, name)
                    parts.append(part)
                except Exception:
                    continue  # 2D sketches etc. cannot become a mesh
            if not parts:
                session.publish({'type': 'mesh_skipped',
                                 'reason': 'generated objects could not be meshed'})
                return
            glb = session.artifacts.root / f'mesh-{revision:03d}.glb'
            stats = await asyncio.to_thread(_stls_to_glb, parts, glb)
            session.meshes.append({'revision': revision, 'path': glb,
                                   'generated': True, **stats})
            session.publish({'type': 'mesh', 'revision': revision,
                             'url': session.mesh_url(revision), 'generated': True,
                             **stats})
            # After the mesh event, so the CAD preview never waits for the distances.
            await publish_heatmap(session, revision, parts)
        except Exception as exc:
            session.publish({'type': 'mesh_skipped', 'reason': str(exc)[:400]})
        finally:
            session.set_stage('agent_running')

    return snapshot


async def publish_heatmap(session: CadSession, revision: int, cad_parts: list[Path]):
    """Colour the reference by distance to this revision; best effort."""
    from .heatmap import build_heatmap

    reference = session.artifacts.root / 'reference.stl'
    glb = session.artifacts.root / f'heatmap-{revision:03d}.glb'
    try:
        scale = await asyncio.to_thread(build_heatmap, reference, cad_parts, glb)
    except Exception as exc:
        session.publish({'type': 'heatmap_skipped', 'revision': revision, 'reason': str(exc)[:400]})
        return
    for entry in session.meshes:
        if entry['revision'] == revision:
            entry['heatmap_path'] = glb
    session.publish({'type': 'heatmap', 'revision': revision, 'url': session.heatmap_url(revision),
                     **scale})


async def generated_object_names(cad) -> list[str]:
    """What the viewer shows as generated CAD: the parametric model's object
    once it exists (so scratch experiments stay out of the view), otherwise
    every named object except the imported reference mesh."""
    from ..model import OBJECT

    names = await cad.session_object_names()
    if OBJECT in names:
        return [OBJECT]
    return [name for name in names if name != 'reference_mesh']


def _stls_to_glb(stl_paths: list[Path], glb_path: Path) -> dict:
    import trimesh

    meshes = []
    for path in stl_paths:
        mesh = trimesh.load(str(path), force='mesh')
        if not mesh.is_empty and len(mesh.faces) > 0:
            meshes.append(mesh)
    if not meshes:
        raise ValueError('snapshot produced no triangles')
    merged = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    return write_glb(merged, glb_path)


async def run_cad_session(session: CadSession):
    """Drive one session: CAD engine + Claude client + queued turns."""
    from ..agent import options_for, run_turn
    from ..cad import open_cad

    bridge = agent_event_bridge(session)
    try:
        session.set_status('running')
        session.set_stage('cad_starting')
        async with open_cad(session.artifacts, session.units) as cad:
            await cad.startup(session.artifacts.root / 'reference.stl')
            cad.on_execute = snapshot_hook(cad, session)
            options = options_for(cad, session.artifacts, session.units, on_event=bridge)
            from claude_agent_sdk import ClaudeSDKClient

            async with ClaudeSDKClient(options=options) as client:
                while True:
                    prompt = await session.turns.get()
                    if prompt is None:
                        break
                    session.set_stage('agent_running')
                    await run_turn(client, prompt, session.artifacts, on_event=bridge)
                    if cad.broken:
                        raise RuntimeError('CAD session timed out; start a new session')
                    session.set_stage('idle')
        session.set_status('done')
        session.publish({'type': 'session_done', 'status': 'done'})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = explain_error(exc)
        session.set_status('failed', error=message)
        session.publish({'type': 'error', 'message': message})


def explain_error(exc: BaseException) -> str:
    """Flatten task-group failures to their real cause (mirrors the CLI)."""
    if isinstance(exc, BaseExceptionGroup):
        return '; '.join(explain_error(e) for e in exc.exceptions)
    return str(exc) or exc.__class__.__name__


def first_prompt(session: CadSession) -> str:
    """The user's prompt, plus pointers to any uploaded reference photos."""
    text = session.prompt
    if session.images:
        text += ('\n\nReference photos uploaded with this request: '
                 f"{', '.join(session.images)}. "
                 'Call view_image to inspect them before modeling.')
    return text


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

class TurnRequest(BaseModel):
    prompt: str = Field(min_length=1)


def _manager() -> CadSessionManager:
    from . import server
    return server.cad_manager


def _session_or_404(session_id: str) -> CadSession:
    session = _manager().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f'No such session: {session_id}')
    return session


@router.post('/api/v1/cad-sessions', status_code=202)
async def create_cad_session(
    mesh: UploadFile = File(...),
    prompt: str = Form(...),
    units: str | None = Form(None),
    images: list[UploadFile] | None = File(None),
):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail='prompt must not be empty')
    if _manager().uses_real_agent and not os.environ.get('ANTHROPIC_API_KEY'):
        # The agent runs with an isolated Claude config, so only the API key works.
        raise HTTPException(status_code=400, detail=(
            'ANTHROPIC_API_KEY is not set. Add it to .env in the repository root '
            '(copy .env.example), then restart `uv run aida-api`.'))
    if units is not None and units not in UNITS:
        raise HTTPException(status_code=400, detail=f'units must be one of {sorted(UNITS)}')
    mesh_bytes = await mesh.read()
    image_uploads = []
    for upload in images or []:
        image_uploads.append((upload.filename or 'image', await upload.read()))

    def prepare():
        return _manager().create(mesh_bytes, mesh.filename or 'mesh.stl',
                                 image_uploads, prompt.strip(), units)

    try:
        session = await asyncio.to_thread(prepare)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The initial turn starts the agent as soon as the session exists.
    session.turns.put_nowait(first_prompt(session))
    _manager().start(session)
    return {'session_id': session.id,
            'status_url': f'/api/v1/cad-sessions/{session.id}',
            'events_url': session.events_url,
            'mesh_url': session.mesh_url(0)}


@router.get('/api/v1/examples')
def list_examples():
    """Sample meshes shipped in ``examples/`` that the UI can load in one click."""
    if not EXAMPLES_DIR.is_dir():
        return []
    return [{'name': path.name, 'bytes': path.stat().st_size}
            for path in sorted(EXAMPLES_DIR.iterdir())
            if path.is_file() and path.suffix.lower() in MESH_SUFFIXES]


@router.get('/api/v1/examples/{name}')
def get_example(name: str):
    path = (EXAMPLES_DIR / name).resolve()
    if (not path.is_relative_to(EXAMPLES_DIR.resolve()) or not path.is_file()
            or path.suffix.lower() not in MESH_SUFFIXES):
        raise HTTPException(status_code=404, detail=f'No example {name!r}')
    return FileResponse(path, filename=path.name)


@router.get('/api/v1/cad-sessions/{session_id}')
def get_cad_session(session_id: str):
    return _session_or_404(session_id).to_dict()


@router.get('/api/v1/cad-sessions/{session_id}/events')
async def stream_cad_events(session_id: str, after: int = 0):
    session = _session_or_404(session_id)
    return StreamingResponse(session.stream(after), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


@router.get('/api/v1/cad-sessions/{session_id}/mesh.glb')
def get_cad_mesh(session_id: str, rev: int | None = None):
    session = _session_or_404(session_id)
    if not session.meshes:
        raise HTTPException(status_code=404, detail='No mesh yet')
    if rev is None:
        entry = session.meshes[-1]
    elif 0 <= rev < len(session.meshes):
        entry = session.meshes[rev]
    else:
        raise HTTPException(status_code=404, detail=f'No mesh revision {rev}')
    if not entry['path'].is_file():
        raise HTTPException(status_code=404, detail='Mesh missing on disk')
    headers = {'Cache-Control': ('public, max-age=31536000, immutable'
                                 if rev is not None else 'no-cache')}
    return FileResponse(entry['path'], media_type='model/gltf-binary',
                        headers=headers)


@router.get('/api/v1/cad-sessions/{session_id}/heatmap.glb')
def get_cad_heatmap(session_id: str, rev: int):
    session = _session_or_404(session_id)
    entry = next((m for m in session.meshes if m['revision'] == rev), None)
    path = entry.get('heatmap_path') if entry else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f'No heatmap for revision {rev}')
    return FileResponse(path, media_type='model/gltf-binary',
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})


@router.get('/api/v1/cad-sessions/{session_id}/files/{filename}')
def get_cad_file(session_id: str, filename: str):
    session = _session_or_404(session_id)
    path = (session.artifacts.root / filename).resolve()
    if not path.is_relative_to(session.artifacts.root) or not path.is_file():
        raise HTTPException(status_code=404, detail=f'No file {filename!r}')
    return FileResponse(path)


@router.post('/api/v1/cad-sessions/{session_id}/turns', status_code=202)
async def add_cad_turn(session_id: str, body: TurnRequest):
    session = _session_or_404(session_id)
    if session.status not in ('queued', 'running'):
        raise HTTPException(status_code=409,
                            detail=f'Session is {session.status}; start a new one')
    session.turns.put_nowait(body.prompt.strip())
    return {'queued': True, 'session_id': session.id}


@router.post('/api/v1/cad-sessions/{session_id}/close', status_code=202)
async def close_cad_session(session_id: str):
    session = _session_or_404(session_id)
    session.turns.put_nowait(None)
    return {'closing': True, 'session_id': session.id}
