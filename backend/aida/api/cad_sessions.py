"""Live CAD sessions for the web viewer: streamed events and GLB mesh revisions.

One session owns one artifacts directory (a workspace of parts) and one
long-lived CAD + Claude run. Every agent/CAD activity is appended to an
in-memory event log that SSE clients consume (with replay after reconnect).
Every model change (and every scratch ``execute``) snapshots the generated CAD:
each part, or each assembly variant, is exported to STL through the CAD server
and written into one GLB scene whose nodes are named ``<part>|<instance>``, so
sixty keycaps share one keycap mesh. The viewer fetches that GLB and animates
the transition from the previous one while the export/convert step runs.

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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..artifacts import Artifacts

DEFAULT_STORAGE = Path('artifacts/cad')
# Sample meshes offered by the UI's empty state ("Try the example part").
EXAMPLES_DIR = Path(__file__).resolve().parents[3] / 'examples'

MAX_MESH_BYTES = 64 * 1024 * 1024
MAX_MESHES = 12
# Pieces of one uploaded mesh smaller than this share of its surface are scan debris.
DEBRIS_SHARE = 0.02
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MESH_SUFFIXES = {'.stl', '.glb', '.obj', '.ply'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
UNITS = {'mm'}
SSE_HEARTBEAT_S = 15.0
REPLAY_LOG = 'ui-events.jsonl'
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


def _group_overlapping(pieces):
    """Merge pieces whose bounding boxes overlap: a stem inside a keycap shell, or the
    inner wall of a hollow part, belongs to the same part. Separate parts sit apart."""
    import trimesh

    groups = [[p] for p in pieces]
    bounds = [p.bounds.copy() for p in pieces]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = bounds[i], bounds[j]
                if (a[0] <= b[1]).all() and (b[0] <= a[1]).all():
                    groups[i] += groups.pop(j)
                    bounds[i] = [a[0].clip(max=b[0]), a[1].clip(min=b[1])]
                    bounds.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [g[0] if len(g) == 1 else trimesh.util.concatenate(g) for g in groups]


def split_mesh(mesh, min_share=DEBRIS_SHARE):
    """Separate parts in one mesh: (pieces, dropped debris count).

    Pieces that touch, or whose bounding boxes overlap, stay one part. A mesh that
    falls apart into many pieces is more likely a broken scan than a set of parts,
    so it is kept whole."""
    pieces = mesh.split(only_watertight=False)
    if len(pieces) <= 1:
        return [mesh], 0
    total = sum(p.area for p in pieces) or 1.0
    kept = [p for p in pieces if p.area / total >= min_share]
    dropped = len(pieces) - len(kept)
    if not kept:
        return [mesh], 0
    kept = _group_overlapping(kept)
    if len(kept) > MAX_MESHES:
        return [mesh], 0
    kept.sort(key=lambda p: tuple(round(float(v), 3) for v in p.bounds[0]))
    return kept, dropped


def write_reference(mesh, artifacts: Artifacts, part: str | None = None) -> Path:
    """Write a reference mesh in session coordinates: parts/<part>/reference.stl."""
    if part is None:
        target = artifacts.root / 'reference.stl'
    else:
        folder = artifacts.root / 'parts' / part
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / 'reference.stl'
    mesh.export(str(target), file_type='stl')
    return target


def plan_parts(uploads: list[tuple[str, object]], taken=()) -> tuple[list[tuple[str, object]], list[str]]:
    """[(file name, mesh)] -> ([(part name, mesh)], notes). One file may hold several parts;
    names avoid ``taken`` (parts already in the workspace)."""
    from ..workspace import part_name_from

    parts, notes, taken = [], [], set(taken)
    for file_name, mesh in uploads:
        pieces, dropped = split_mesh(mesh) if len(uploads) == 1 else ([mesh], 0)
        base = part_name_from(file_name, taken)
        if dropped:
            notes.append(f'{file_name}: ignored {dropped} small fragment{"s" if dropped > 1 else ""} '
                         '(scan debris)')
        if len(pieces) > 1:
            notes.append(f'{file_name} holds {len(pieces)} separate pieces; each is its own part')
        for index, piece in enumerate(pieces, start=1):
            name = base if len(pieces) == 1 else part_name_from(f'{part_name_from(file_name)}_{index}', taken)
            taken.add(name)
            parts.append((name, piece))
    if len(taken) + len(parts) > MAX_MESHES:
        raise ValueError(f'At most {MAX_MESHES} parts per workspace')
    return parts, notes


def write_scene(items, glb_path: Path) -> dict:
    """Write a GLB scene: items are (geometry name, mesh or STL path, [(node name, 4x4 matrix)]).

    Each geometry is stored once however many nodes place it (glTF instancing)."""
    import numpy as np
    import trimesh

    scene = trimesh.Scene()
    triangles = 0
    for geometry, source, nodes in items:
        mesh = source if isinstance(source, trimesh.Trimesh) else trimesh.load(str(source), force='mesh')
        if mesh.is_empty or len(mesh.faces) == 0 or not nodes:
            continue
        scene.add_geometry(mesh, geom_name=geometry, node_name=nodes[0][0],
                           transform=np.asarray(nodes[0][1], dtype=float))
        for node, matrix in nodes[1:]:
            scene.graph.update(frame_to=node, frame_from=scene.graph.base_frame,
                               matrix=np.asarray(matrix, dtype=float), geometry=geometry)
        triangles += len(mesh.faces) * len(nodes)
    if not scene.geometry:
        raise ValueError('snapshot produced no triangles')
    scene.export(str(glb_path), file_type='glb')
    bounds = scene.bounds
    return {'triangles': int(triangles),
            'vertices': int(sum(len(g.vertices) for g in scene.geometry.values())),
            'bbox': [round(float(v), 3) for v in (*bounds[0], *bounds[1])],
            'extents': [round(float(v), 3) for v in scene.extents],
            'bytes': glb_path.stat().st_size}


def _matrix4(rows):
    """A 3x4 affine matrix (as the assembly reports it) padded to 4x4."""
    return [list(rows[0]), list(rows[1]), list(rows[2]), [0.0, 0.0, 0.0, 1.0]]


IDENTITY = _matrix4([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])


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


def ingest_meshes(session, meshes: list[tuple[str, bytes]], existing=()) -> dict:
    """Blocking: validate uploaded meshes, write each new part's reference, and write a
    scene of every reference (``existing`` (name, path) plus the new ones).

    Returns the new part specs, upload notes, and the scene for finish_ingest."""
    if not meshes:
        raise ValueError('Upload at least one mesh')
    if len(meshes) > MAX_MESHES:
        raise ValueError(f'At most {MAX_MESHES} meshes')
    root = session.artifacts.root
    first = len(list(root.glob('upload-*')))
    loaded = []
    for index, (mesh_name, data) in enumerate(meshes, start=first + 1):
        mesh = load_mesh(data, mesh_name)
        (root / f'upload-{index:02d}{Path(mesh_name).suffix.lower()}').write_bytes(data)
        loaded.append((mesh_name, mesh))
    existing = list(existing)
    parts, notes = plan_parts(loaded, taken=[name for name, _ in existing])
    specs = [{'name': name, 'reference': write_reference(mesh, session.artifacts, name)} for name, mesh in parts]
    items = [(name, path, [(f'{name}|reference', IDENTITY)]) for name, path in existing]
    items += [(name, mesh, [(f'{name}|reference', IDENTITY)]) for name, mesh in parts]
    glb = root / f'references-{uuid.uuid4().hex[:8]}.glb'
    stats = write_scene(items, glb)
    files = ', '.join(name for name, _ in meshes)
    names = [name for name, _ in parts]
    summary = (f'The user added {files}; ' + ('it is' if len(names) == 1 else 'they are')
               + f" now part{'s' if len(names) > 1 else ''} {', '.join(names)} (reference meshes "
               + ', '.join(f'ref_{n}' for n in names) + ').')
    return {'specs': specs, 'notes': notes, 'glb': glb, 'stats': stats, 'summary': summary,
            'parts': [name for name, _ in existing] + names}


def finish_ingest(session, added: dict) -> list[dict]:
    """Record the new parts on the session; return the events to publish (loop thread)."""
    new = [spec['name'] for spec in added['specs']]
    session.parts = list(dict.fromkeys(session.parts + new))
    session.notes += added['notes']
    session.pending.append(added['summary'] + ''.join(f' {note}.' for note in added['notes']))
    events = [{'type': 'notice', 'text': note} for note in added['notes']]
    events.append({'type': 'notice', 'text': f"Added part{'s' if len(new) > 1 else ''} {', '.join(new)}"})
    events.append(session.add_revision(added['glb'], False, added['stats'], parts=added['parts']))
    return events


def ingest_images(session, images: list[tuple[str, bytes]]):
    """Blocking: store photos as PNGs and tell the agent about them on its next turn."""
    root = session.artifacts.root
    if len(session.images) + len(images) > MAX_IMAGES:
        raise ValueError(f'At most {MAX_IMAGES} reference images')
    names = []
    for name, data in images:
        image_path = root / f'input-{len(session.images) + len(names) + 1:02d}.png'
        save_image_png(data, name, image_path)
        names.append(image_path.name)
    session.images += names
    session.pending.append(f"Reference photos uploaded: {', '.join(names)}. "
                           'Call view_image to inspect them before modeling.')
    return names


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
        self.parts: list[str] = []      # part names in the workspace at upload
        self.notes: list[str] = []      # what upload did with the meshes (splits, debris)
        self.workspace = None           # live Workspace while the runner is active
        # What happened since the agent's last turn (meshes added, photos): prefixed to the next prompt.
        self.pending: list[str] = []
        # Serialises adding parts with the runner loading the workspace.
        self.parts_lock = asyncio.Lock()
        self.turns: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None
        # Live handles while the runner is active: CAD bridge (downloads) and
        # Claude client (interrupt). None before start and after the session ends.
        self.cad = None
        self.client = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._waiters: set[asyncio.Future] = set()

    # -- URLs ---------------------------------------------------------------
    def add_revision(self, glb: Path, generated: bool, stats: dict, **extra) -> dict:
        """Number a GLB as the next revision and record it (loop thread, or before streaming)."""
        revision = len(self.meshes)
        target = glb.with_name(f'mesh-{revision:03d}.glb')
        if glb != target:
            glb.replace(target)
        entry = {'revision': revision, 'path': target, 'generated': generated, **extra, **stats}
        self.meshes.append(entry)
        return {'type': 'mesh', 'revision': revision, 'url': self.mesh_url(revision),
                'generated': generated, **extra, **stats}

    def heatmap_url(self, revision: int, part: str) -> str:
        return f'/api/v1/cad-sessions/{self.id}/heatmap.glb?rev={revision}&part={part}'

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
            'parts': list(self.parts),
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
        # Kept on disk too, so the session can be replayed later (aida.api.replays).
        try:
            with (self.artifacts.root / REPLAY_LOG).open('a', encoding='utf-8') as log:
                log.write(json.dumps(stamped, default=str) + '\n')
        except OSError:
            pass  # a replay log is a convenience; never break the live session for it
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
        self._warm = None

    # -- lifecycle ----------------------------------------------------------
    def create(self, meshes: list[tuple[str, bytes]] = (),
               images: list[tuple[str, bytes]] = (), prompt: str = '',
               units: str | None = None) -> CadSession:
        """Blocking preparation of a workspace session; meshes and photos are optional
        (a session can design from scratch, and both can be added later)."""
        from ..workspace import write_workspace

        artifacts = Artifacts(self.storage_dir)
        session = CadSession(artifacts, prompt, units)
        session.record({'type': 'stage', 'stage': 'preparing'})
        write_workspace(artifacts.root, [])
        if meshes:
            added = ingest_meshes(session, meshes)
            write_workspace(artifacts.root, added['specs'])
            for event in finish_ingest(session, added):
                session.record(event)
        if images:
            ingest_images(session, images)
        artifacts.log('session', {'meshes': [name for name, _ in meshes], 'parts': session.parts,
                                  'units': units, 'images': session.images, 'prompt': prompt})
        with self._mutex:
            self._sessions[session.id] = session
        return session

    # -- warm CAD engine -----------------------------------------------------
    # Starting a CAD server and running its render/export self-test takes ~10 s.
    # One is kept started and checked in the background, and the next session
    # adopts it, so the first prompt does not wait for it.

    def prewarm(self):
        """Start a warm CAD engine if none is running (needs a running event loop)."""
        if not self.uses_real_agent or os.environ.get('AIDA_PREWARM', '1') == '0':
            return
        if self._warm is not None and not self._warm['task'].done():
            return
        ready = asyncio.get_running_loop().create_future()
        release = asyncio.Event()
        task = asyncio.create_task(self._keep_warm(ready, release))
        self._warm = {'ready': ready, 'release': release, 'task': task}

    async def _keep_warm(self, ready, release):
        from ..cad import open_cad

        try:
            # Working directory = the folder holding every session folder, so the
            # session that adopts this engine can read its own reference mesh.
            async with open_cad(Artifacts(self.storage_dir / '_warm'),
                                cwd=self.storage_dir.resolve()) as bridge:
                await bridge.discover()
                await bridge.check_health()
                ready.set_result(bridge.session)
                await release.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)

    def take_warm(self):
        """The warm engine's MCP session and its release event, if one is ready."""
        warm = self._warm
        if warm is None or not warm['ready'].done() or warm['ready'].exception() is not None                 or warm['task'].done():
            return None
        self._warm = None
        return warm['ready'].result(), warm['release']

    async def shutdown(self):
        if self._warm is not None:
            self._warm['release'].set()
            self._warm['task'].cancel()
            self._warm = None

    @asynccontextmanager
    async def cad_for(self, session: 'CadSession'):
        """A CAD bridge for this session: the warm engine when ready, else a fresh one."""
        from ..cad import CadBridge, open_cad

        warm = self.take_warm()
        try:
            if warm is not None:
                client, release = warm
                try:
                    bridge = CadBridge(client, session.artifacts, units=session.units)
                    await bridge.discover()
                    yield bridge
                finally:
                    release.set()
            else:
                async with open_cad(session.artifacts, session.units) as bridge:
                    await bridge.discover()
                    await bridge.check_health()
                    yield bridge
        finally:
            self.prewarm()  # ready for the next session

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


def snapshot_hook(cad, session: CadSession, workspace=None):
    """Export the generated CAD and publish the new GLB scene (plus per-part heatmaps)."""
    async def snapshot():
        revision = len(session.meshes)
        session.set_stage('exporting_mesh')
        root = session.artifacts.root
        try:
            names = await generated_object_names(cad, workspace)
            if not names:
                session.publish({'type': 'mesh_skipped',
                                 'reason': 'no generated CAD objects yet'})
                return
            exported = {}

            async def export(object_name):
                if object_name not in exported:
                    target = root / f'mesh-{revision:03d}-{len(exported)}.stl'
                    try:
                        await cad.snapshot_stl(target, object_name)
                        exported[object_name] = target
                    except Exception:
                        exported[object_name] = None  # 2D sketches etc. cannot become a mesh
                return exported[object_name]

            items = []
            layout = workspace.assembly.layout if workspace is not None else None
            if names == ['assembly'] and layout and layout['instances']:
                for variant in layout['variants']:
                    stl = await export(variant['name'])
                    nodes = [(f"{i['part']}|{i['id']}", _matrix4(i['matrix']))
                             for i in layout['instances'] if i['variant'] == variant['name']]
                    if stl is not None:
                        items.append((variant['name'], stl, nodes))
            else:
                for name in names:
                    stl = await export(name)
                    part = name[len('part_'):] if name.startswith('part_') else name
                    if stl is not None:
                        items.append((name, stl, [(f'{part}|{part}', IDENTITY)]))
            if not items:
                session.publish({'type': 'mesh_skipped',
                                 'reason': 'generated objects could not be meshed'})
                return
            glb = root / f'mesh-{revision:03d}.glb'
            stats = await asyncio.to_thread(write_scene, items, glb)
            parts = sorted({node.split('|')[0] for _, _, nodes in items for node, _ in nodes})
            session.meshes.append({'revision': revision, 'path': glb,
                                   'generated': True, 'parts': parts, **stats})
            session.publish({'type': 'mesh', 'revision': revision,
                             'url': session.mesh_url(revision), 'generated': True,
                             'parts': parts, **stats})
            # After the mesh event, so the CAD preview never waits for the distances.
            for part, reference in (workspace.references() if workspace is not None else []):
                model = workspace.parts[part]
                if model.state['features']:
                    stl = await export(model.object)
                    if stl is not None:
                        await publish_heatmap(session, revision, part, reference, stl)
        except Exception as exc:
            session.publish({'type': 'mesh_skipped', 'reason': str(exc)[:400]})
        finally:
            session.set_stage('agent_running')

    return snapshot


async def publish_heatmap(session: CadSession, revision: int, part: str, reference: Path, cad_stl: Path):
    """Colour one part's reference by distance to its CAD in this revision; best effort."""
    from .heatmap import build_heatmap

    glb = session.artifacts.root / f'heatmap-{revision:03d}-{part}.glb'
    try:
        scale = await asyncio.to_thread(build_heatmap, reference, [cad_stl], glb)
    except Exception as exc:
        session.publish({'type': 'heatmap_skipped', 'revision': revision, 'part': part,
                         'reason': str(exc)[:400]})
        return
    for entry in session.meshes:
        if entry['revision'] == revision:
            entry.setdefault('heatmaps', {})[part] = glb
    session.publish({'type': 'heatmap', 'revision': revision, 'part': part,
                     'url': session.heatmap_url(revision, part), **scale})


async def generated_object_names(cad, workspace=None) -> list[str]:
    """What the viewer shows as generated CAD: the workspace's objects once a part
    is built (so scratch experiments stay out of the view), otherwise every named
    object except reference meshes and assembly internals."""
    from ..cad import is_reference

    if workspace is not None and workspace.objects():
        return workspace.objects()
    names = await cad.session_object_names()
    return [name for name in names if not is_reference(name) and not name.startswith('variant_')]


async def run_cad_session(session: CadSession):
    """Drive one session: CAD engine + Claude client + queued turns."""
    from ..agent import options_for, run_turn

    bridge = agent_event_bridge(session)
    try:
        session.set_status('running')
        session.set_stage('cad_starting')
        async with _manager().cad_for(session) as cad:
            from ..workspace import Workspace

            # Meshes uploaded while the engine started are in workspace.json; ones uploaded
            # after this are added to the live workspace (add_meshes_live).
            async with session.parts_lock:
                workspace = Workspace.load(cad, session.artifacts)
                for part, reference in workspace.references():
                    await cad.load_reference(reference, part)
                cad.on_execute = snapshot_hook(cad, session, workspace)
                session.cad, session.workspace = cad, workspace
            options = options_for(cad, session.artifacts, session.units, on_event=bridge,
                                  workspace=workspace)
            from claude_agent_sdk import ClaudeSDKClient

            async with ClaudeSDKClient(options=options) as client:
                session.client = client
                session.set_stage('idle')  # ready: the workspace waits for a prompt
                while True:
                    prompt = await session.turns.get()
                    if prompt is None:
                        break
                    prompt = compose_turn(session, prompt)
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
    finally:
        session.cad = session.client = None


def explain_error(exc: BaseException) -> str:
    """Flatten task-group failures to their real cause (mirrors the CLI)."""
    if isinstance(exc, BaseExceptionGroup):
        return '; '.join(explain_error(e) for e in exc.exceptions)
    return str(exc) or exc.__class__.__name__


def first_prompt(session: CadSession, prompt: str | None = None) -> str:
    """A prompt with what changed since the agent's last turn (meshes, photos) in front."""
    text = session.prompt if prompt is None else prompt
    if session.pending:
        text = 'Since your last turn: ' + ' '.join(session.pending) + '\n\n' + text
    return text


def compose_turn(session: CadSession, prompt: str) -> str:
    """first_prompt, then clear the pending notes (they were delivered)."""
    text = first_prompt(session, prompt)
    session.pending.clear()
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


def _check_units(units):
    if units is not None and units not in UNITS:
        raise HTTPException(status_code=400, detail=f'units must be one of {sorted(UNITS)}')


@router.post('/api/v1/cad-sessions', status_code=202)
async def create_cad_session(
    mesh: list[UploadFile] | None = File(None),
    prompt: str | None = Form(None),
    units: str | None = Form(None),
    images: list[UploadFile] | None = File(None),
):
    """Open a workspace session. The CAD engine and agent start now; meshes, photos
    and a first prompt are optional (they can all come later)."""
    if _manager().uses_real_agent and not os.environ.get('ANTHROPIC_API_KEY'):
        # The agent runs with an isolated Claude config, so only the API key works.
        raise HTTPException(status_code=400, detail=(
            'ANTHROPIC_API_KEY is not set. Add it to .env in the repository root '
            '(copy .env.example), then restart `uv run aida-api`.'))
    _check_units(units)
    prompt = (prompt or '').strip()
    mesh_uploads = [(upload.filename or 'mesh.stl', await upload.read()) for upload in mesh or []]
    image_uploads = [(upload.filename or 'image', await upload.read()) for upload in images or []]

    def prepare():
        return _manager().create(mesh_uploads, image_uploads, prompt, units)

    try:
        session = await asyncio.to_thread(prepare)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if prompt:
        session.record({'type': 'user_prompt', 'text': prompt})
        session.turns.put_nowait(prompt)
    _manager().start(session)
    return {'session_id': session.id,
            'status_url': f'/api/v1/cad-sessions/{session.id}',
            'events_url': session.events_url,
            'mesh_url': session.mesh_url(0) if session.meshes else None,
            'parts': session.parts}


def _open_session(session_id: str) -> CadSession:
    session = _session_or_404(session_id)
    if session.status not in ('queued', 'running'):
        raise HTTPException(status_code=409, detail=f'Session is {session.status}; start a new one')
    return session


@router.post('/api/v1/cad-sessions/{session_id}/meshes', status_code=202)
async def add_cad_meshes(session_id: str, mesh: list[UploadFile] = File(...), units: str | None = Form(None)):
    """Add meshes to a running workspace: each becomes a part with a reference mesh."""
    session = _open_session(session_id)
    _check_units(units)
    uploads = [(upload.filename or 'mesh.stl', await upload.read()) for upload in mesh]
    async with session.parts_lock:
        workspace = session.workspace
        if workspace is not None:
            existing = workspace.references()
        else:
            from ..workspace import read_workspace
            existing = [(p['name'], p['reference']) for p in read_workspace(session.artifacts.root)[0]
                        if p.get('reference')]
        try:
            added = await asyncio.to_thread(ingest_meshes, session, uploads, existing)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if workspace is not None:
            for spec in added['specs']:
                workspace.add_reference_part(spec['name'], spec['reference'])
                try:
                    await session.cad.load_reference(spec['reference'], spec['name'])
                except Exception as exc:  # the part still works through the mesh tools
                    session.publish({'type': 'notice', 'text': f"{spec['name']}: CAD import failed ({exc})"})
        else:
            from ..workspace import read_workspace, write_workspace
            parts, active = read_workspace(session.artifacts.root)
            write_workspace(session.artifacts.root, parts + added['specs'], active)
        if units:
            session.units = units
            if session.cad is not None:
                session.cad.mesh.units = units
        if not units:
            added['summary'] += ' Their units are unknown: do not claim physical dimensions.'
        for event in finish_ingest(session, added):
            session.publish(event)
    return {'parts': [spec['name'] for spec in added['specs']], 'notes': added['notes']}


@router.post('/api/v1/cad-sessions/{session_id}/images', status_code=202)
async def add_cad_images(session_id: str, images: list[UploadFile] = File(...)):
    """Reference photos for the agent's next turn."""
    session = _open_session(session_id)
    uploads = [(upload.filename or 'image', await upload.read()) for upload in images]
    try:
        names = await asyncio.to_thread(ingest_images, session, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'images': names}


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
def get_cad_heatmap(session_id: str, rev: int, part: str):
    session = _session_or_404(session_id)
    entry = next((m for m in session.meshes if m['revision'] == rev), None)
    path = (entry.get('heatmaps') or {}).get(part) if entry else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f'No heatmap of {part!r} for revision {rev}')
    return FileResponse(path, media_type='model/gltf-binary',
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})


@router.get('/api/v1/cad-sessions/{session_id}/files/{filename:path}')
def get_cad_file(session_id: str, filename: str):
    session = _session_or_404(session_id)
    path = (session.artifacts.root / filename).resolve()
    if not path.is_relative_to(session.artifacts.root) or not path.is_file():
        raise HTTPException(status_code=404, detail=f'No file {filename!r}')
    return FileResponse(path)


@router.post('/api/v1/cad-sessions/{session_id}/turns', status_code=202)
async def add_cad_turn(session_id: str, body: TurnRequest):
    session = _open_session(session_id)
    session.publish({'type': 'user_prompt', 'text': body.prompt.strip()})
    session.turns.put_nowait(body.prompt.strip())
    return {'queued': True, 'session_id': session.id}


@router.post('/api/v1/cad-sessions/{session_id}/interrupt', status_code=202)
async def interrupt_cad_turn(session_id: str):
    """Stop the agent's current turn; the session stays open for the next prompt."""
    session = _session_or_404(session_id)
    if session.client is None:
        raise HTTPException(status_code=409, detail='The agent is not running')
    await session.client.interrupt()
    session.publish({'type': 'interrupted'})
    return {'interrupted': True, 'session_id': session.id}


DOWNLOADS = {'step', 'stl', 'model.py'}


@router.get('/api/v1/cad-sessions/{session_id}/download/{kind}')
async def download_cad(session_id: str, kind: str):
    """The generated CAD: STEP (exported by the live CAD engine, validation-gated),
    STL (the latest revision's mesh), or model.py (every part's parametric build123d
    script, then the assembly's)."""
    from ..workspace import COMBINED_SCRIPT

    session = _session_or_404(session_id)
    root = session.artifacts.root
    if kind not in DOWNLOADS:
        raise HTTPException(status_code=404, detail=f'Download one of {sorted(DOWNLOADS)}')
    if kind == 'model.py':
        script = root / COMBINED_SCRIPT
        if not script.is_file():
            raise HTTPException(status_code=404, detail='No parametric model yet')
        return FileResponse(script, filename='aida_model.py', media_type='text/x-python')
    generated = [m for m in session.meshes if m.get('generated')]
    if not generated:
        raise HTTPException(status_code=404, detail='No generated CAD yet')
    revision = generated[-1]['revision']
    if kind == 'stl':
        target = root / f'aida-r{revision:03d}.stl'
        if not target.is_file():
            glb = generated[-1]['path']
            if not glb.is_file():
                raise HTTPException(status_code=404, detail='The latest revision has no mesh')
            await asyncio.to_thread(_scene_to_stl, glb, target)
        return FileResponse(target, filename=target.name, media_type='model/stl')
    target = root / f'aida-r{revision:03d}.step'
    if not target.is_file():
        if session.cad is None or session.cad.broken:
            raise HTTPException(status_code=409, detail='STEP export needs the CAD engine, which is not '
                                                        'running for this session. Download STL instead.')
        workspace = session.workspace
        objects = workspace.objects() if workspace is not None else []
        if len(objects) != 1:
            # Several loose parts: the active one (the assembly holds them all once it exists).
            objects = [workspace.part().object] if workspace is not None and workspace.parts else []
        if not objects:
            raise HTTPException(status_code=404, detail='No generated CAD yet')
        result = await session.cad.call('export', {'filename': target.name, 'format': 'step',
                                                   'object_name': objects[0]})
        if result.get('isError') or not target.is_file():
            from ..cad import text_of
            raise HTTPException(status_code=409, detail=f'STEP export failed: {text_of(result)[:600]}')
    return FileResponse(target, filename=target.name, media_type='application/step')


def _scene_to_stl(glb: Path, target: Path):
    """Every instance of a GLB scene, placed, as one STL."""
    import trimesh

    trimesh.load(str(glb), force='mesh').export(str(target), file_type='stl')


@router.post('/api/v1/cad-sessions/{session_id}/close', status_code=202)
async def close_cad_session(session_id: str):
    session = _session_or_404(session_id)
    session.turns.put_nowait(None)
    return {'closing': True, 'session_id': session.id}
