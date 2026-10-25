"""Four-view scans attached to an existing live workspace; no separate agent."""
import asyncio
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..scan.acquisition import check_cancelled, run_capture_scan
from ..scan.camera import CameraCalibration
from ..turntable import HardwareSettings, service
from .workspace_meta import META_FILE, update_meta

router = APIRouter(prefix='/api/v1/acquisitions', tags=['turntable scanning'])


class AcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1)
    workflow: Literal['fast', 'full'] = 'full'


def require_live_workspace(session):
    if session is None or session.closing or session.status not in ('queued', 'running'):
        raise HTTPException(409, 'Open the workspace before scanning.')
    if session.workspace is None or session.cad is None:
        raise HTTPException(409, 'Wait for the workspace CAD engine to be ready.')
    if session.workspace.parts and session.units != 'mm':
        raise HTTPException(409, 'This workspace has unknown units. Use a millimeter workspace for metric scans.')


@dataclass
class AcquisitionJob:
    id: str
    directory: Path
    session_id: str
    status: str = 'queued'
    stage: str = 'queued'
    error: str | None = None
    cad_specs: dict | None = None
    parts: list[str] = field(default_factory=list)
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    def to_dict(self):
        return {'job_id': self.id, 'session_id': self.session_id, 'status': self.status,
                'stage': self.stage, 'error': self.error, 'parts': self.parts, 'cad_specs': self.cad_specs,
                'viewer_url': f'/w/{self.session_id}',
                'files': {p.name: f'/api/v1/cad-sessions/{self.session_id}/files/scans/{self.id}/{p.name}'
                          for p in self.directory.glob('*') if p.is_file()}}


async def attach_scan(session, job, result):
    """Publish a reference only after every import succeeds. Retain raw scan evidence on failure."""
    from .cad_sessions import ingest_meshes, finish_ingest
    from .commits import publish_working_state

    async with session.parts_lock:
        require_live_workspace(session)
        check_cancelled(job.cancelled)
        workspace = session.workspace
        previous_parts, previous_active = dict(workspace.parts), workspace.active
        previous_refs = dict(session.cad.mesh.references)
        root = session.artifacts.root
        old_meta = None
        metadata_changed = False
        old_units = session.units
        old_pending, old_parts, old_notes, old_meshes = list(session.pending), list(session.parts), list(session.notes), list(session.meshes)
        try:
            added = await asyncio.to_thread(ingest_meshes, session,
                [('scanned_object.stl', result.metric_stl.read_bytes())], workspace.references(), tuple(workspace.parts))
            check_cancelled(job.cancelled)
            for spec in added['specs']:
                await session.cad.load_reference(spec['reference'], spec['name'])
                check_cancelled(job.cancelled)
            # Register photos separately from manually uploaded input images and their limit.
            photos = {f'scans/{job.id}/{name}.png': f'Scan {job.id[:8]} · {name}'
                      for name in ('front', 'left', 'back', 'right', 'blueprints')
                      if (job.directory / f'{name}.png').is_file()}
            for spec in added['specs']:
                description = 'Turntable scan; reference mesh in millimeters.'
                warning = result.cad_specs.get('registration', {}).get('warning')
                workspace.add_reference_part(spec['name'], spec['reference'], description + (' ' + warning if warning else ''))
            workspace.active = added['specs'][0]['name']
            workspace.save()
            session.units = 'mm'
            session.cad.mesh.units = 'mm'
            added['summary'] = ('A turntable scan added reference parts ' + ', '.join(s['name'] for s in added['specs'])
                                + '. The mesh is ALREADY in millimeters; do not scale it again. ' + result.agent_prompt)
            old_meta = (root / META_FILE).read_bytes() if (root / META_FILE).exists() else None
            update_meta(root, units='mm', photo_files=photos)
            metadata_changed = True
            events = finish_ingest(session, added)
        except BaseException:
            workspace.parts, workspace.active = previous_parts, previous_active
            session.cad.mesh.references = previous_refs
            session.units = old_units
            session.cad.mesh.units = old_units or ''
            session.pending, session.parts, session.notes, session.meshes = old_pending, old_parts, old_notes, old_meshes
            workspace.save()
            if metadata_changed:
                if old_meta is None:
                    (root / META_FILE).unlink(missing_ok=True)
                else:
                    (root / META_FILE).write_bytes(old_meta)
            raise
        for event in events:
            session.publish(event)
        warning = result.cad_specs.get('registration', {}).get('warning')
        if warning:
            session.publish({'type': 'notice', 'text': warning})
        session.publish({'type': 'workspace_units', 'units': 'mm'})
        session.publish({'type': 'workspace_state', **workspace.snapshot()})
        publish_working_state(session)
        return [s['name'] for s in added['specs']]


class AcquisitionManager:
    def __init__(self, runner=run_capture_scan, controller=None):
        self.jobs = {}
        self.runner = runner
        self.controller = controller or service

    def submit(self, request, cad_manager):
        session = cad_manager.live(request.session_id)
        if session is None:
            raise HTTPException(409, 'Open the workspace before scanning.')
        if session.stage != 'idle':
            raise HTTPException(409, 'Aida is working. Wait for it to finish, or ask it to scan in chat.')
        return self.submit_session(session, request.workflow)

    def submit_session(self, session, workflow='full'):
        require_live_workspace(session)
        if any(job.status in ('queued', 'running', 'cancelling') for job in self.jobs.values()):
            raise HTTPException(409, 'Another turntable scan is active.')
        self.controller.ensure_zero_ready()
        settings = HardwareSettings.from_env()
        calibration = CameraCalibration.load(settings.storage / 'camera.json')
        if calibration.camera_id != settings.camera_id:
            raise HTTPException(409, 'Selected camera differs from its calibration. Recalibrate at /setup.html.')
        if not (settings.storage / 'zero.json').is_file():
            raise HTTPException(409, 'Startup zero is not ready; retry initialization at /setup.html.')
        identity = uuid.uuid4().hex
        directory = session.artifacts.root / 'scans' / identity
        directory.mkdir(parents=True)
        job = AcquisitionJob(identity, directory, session.id)
        self.jobs[identity] = job
        job.task = asyncio.create_task(self._run(job, session, workflow))
        return job

    def cancel_for_session(self, session_id):
        for job in self.jobs.values():
            if job.session_id == session_id and job.status in ('queued', 'running', 'cancelling'):
                job.cancelled.set()
                job.status = 'cancelling'

    async def _run(self, job, session, workflow):
        job.status = 'running'
        previous_stage = session.stage
        loop = asyncio.get_running_loop()
        def progress(stage):
            if not job.done.is_set() and not job.cancelled.is_set():
                job.stage = stage
                session.set_stage('scan_' + stage)
        try:
            result = await asyncio.to_thread(self.runner, job.directory, controller=self.controller,
                workflow=workflow, progress=lambda stage: loop.call_soon_threadsafe(progress, stage),
                cancelled=job.cancelled)
            check_cancelled(job.cancelled)
            job.cad_specs = result.cad_specs
            progress('attaching_mesh')
            job.parts = await attach_scan(session, job, result)
            job.status, job.stage = 'succeeded', 'mesh_attached'
            if result.blueprint.is_file():
                session.publish({'type': 'render', 'url': job.to_dict()['files'][result.blueprint.name]})
        except asyncio.CancelledError:
            job.cancelled.set()
            job.status, job.stage = 'cancelled', 'cancelled'
        except Exception as exc:
            job.error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc) or type(exc).__name__
            job.status = 'cancelled' if job.cancelled.is_set() else 'failed'
            job.stage = job.status
        finally:
            try:
                (job.directory / 'workflow.json').write_text(json.dumps(job.to_dict(), indent=2), encoding='utf-8')
            except OSError:
                pass  # failure diagnostics must not prevent the waiting tool from finishing
            if session.stage.startswith('scan_'):
                session.set_stage(previous_stage)
            if job.status != 'succeeded':
                session.publish({'type': 'notice', 'text': f'Scan {job.status}: {job.error or job.status}'})
            job.done.set()


manager = AcquisitionManager()


def get_job(job_id):
    job = manager.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, 'No such acquisition.')
    return job


@router.post('', status_code=202)
async def create(request: AcquisitionRequest):
    from .server import cad_manager
    job = manager.submit(request, cad_manager)
    return {'job_id': job.id, 'status_url': f'/api/v1/acquisitions/{job.id}'}


SCAN_VIEWS = ('front', 'left', 'back', 'right', 'blueprints')


def _save_import(directory, mesh_name, mesh_bytes, photos, dimensions):
    """Blocking: write a completed scan's mesh, photos and fit evidence into a job folder."""
    import io
    import trimesh
    from PIL import Image, UnidentifiedImageError
    from ..scan import blueprints
    from ..scan.acquisition import scan_agent_prompt
    from ..scan.pipeline import ScanResult
    from ..scan.scaling import export_stl
    from .cad_sessions import MESH_SUFFIXES

    suffix = Path(mesh_name).suffix.lower()
    if suffix not in MESH_SUFFIXES:
        raise ValueError(f'{mesh_name}: the scan mesh must be one of {", ".join(sorted(MESH_SUFFIXES))}.')
    source = directory / f'imported_mesh{suffix}'
    source.write_bytes(mesh_bytes)
    stl = directory / 'metric_model.stl'
    try:
        export_stl(source, stl)
        extents = trimesh.load(str(stl), force='mesh').extents
    except Exception as exc:
        raise ValueError(f'Cannot read the scan mesh {mesh_name}: {exc}') from exc

    # Photos named after a view keep it; others fill the remaining turntable views in order.
    used, queue = set(), []
    for name, data in photos:
        stem = Path(name).stem.lower()
        view = stem if stem in SCAN_VIEWS and stem not in used else None
        used.add(view)
        queue.append((view, name, data))
    free = [view for view in SCAN_VIEWS[:4] if view not in used]
    for view, name, data in queue:
        if view is None:
            if not free:
                raise ValueError('Name the photos front, left, back, right and blueprints (one of each).')
            view = free.pop(0)
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.convert('RGB').save(directory / f'{view}.png')
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError(f'{name} is not a readable image.') from exc

    if dimensions is not None:
        try:
            specs = json.loads(dimensions)
        except ValueError as exc:
            raise ValueError('dimensions.json is not valid JSON.') from exc
        if not isinstance(specs, dict) or 'bounding_box_mm' not in specs:
            raise ValueError('dimensions.json must be the dimensions file from a turntable scan.')
        (directory / 'dimensions.json').write_text(json.dumps(specs, indent=2)+'\n')
        prompt = scan_agent_prompt(specs)
    else:
        specs = {}
        width, depth, height = (round(float(v), 2) for v in extents)
        prompt = ('Build a parametric CAD model of this turntable-scanned object using build123d. '
                  f'Mesh bounding box: {width}mm (W) x {depth}mm (D) x {height}mm (H). '
                  'The reference mesh is ALREADY in millimeters; do not scale it. Inspect the scan photos '
                  '(front/left/back/right) and orthographic blueprints before modeling. '
                  'Treat internal details as estimates.')
    blueprint = directory / 'blueprints.png'
    if not blueprint.is_file():
        try:
            blueprints.render_mesh_projections(stl, blueprint)
        except Exception:
            pass  # the scan photos still show every view
    (directory / 'agent_prompt.txt').write_text(prompt+'\n')
    return ScanResult(directory, stl, stl, directory / 'dimensions.json', blueprint, specs, prompt)


@router.post('/import')
async def import_scan(session_id: str = Form(...), mesh: UploadFile = File(...),
                      photos: list[UploadFile] | None = File(None),
                      dimensions: UploadFile | None = File(None)):
    """Attach a completed turntable scan (millimeter mesh, its photos and optional dimensions.json)
    exactly as a live scan attaches: mm units, scan photos and the scan summary for the agent."""
    from .server import cad_manager
    session = cad_manager.live(session_id)
    if session is None:
        raise HTTPException(409, 'Open the workspace before importing a scan.')
    require_live_workspace(session)
    if session.stage != 'idle':
        raise HTTPException(409, 'Aida is working. Wait for it to finish, then import the scan.')
    if any(job.status in ('queued', 'running', 'cancelling') for job in manager.jobs.values()):
        raise HTTPException(409, 'Another turntable scan is active.')
    photos = photos or []
    if len(photos) > len(SCAN_VIEWS):
        raise HTTPException(400, f'At most {len(SCAN_VIEWS)} scan photos (four views and blueprints).')
    mesh_name = mesh.filename or 'scan.stl'
    mesh_bytes = await mesh.read()
    photo_data = [(photo.filename or 'photo', await photo.read()) for photo in photos]
    dimensions_bytes = await dimensions.read() if dimensions is not None else None

    identity = uuid.uuid4().hex
    directory = session.artifacts.root / 'scans' / identity
    directory.mkdir(parents=True)
    job = AcquisitionJob(identity, directory, session.id, status='running', stage='importing')
    manager.jobs[identity] = job
    try:
        result = await asyncio.to_thread(_save_import, directory, mesh_name, mesh_bytes,
                                         photo_data, dimensions_bytes)
        job.cad_specs = result.cad_specs
        job.parts = await attach_scan(session, job, result)
        job.status, job.stage = 'succeeded', 'mesh_attached'
        if result.blueprint.is_file():
            session.publish({'type': 'render', 'url': job.to_dict()['files'][result.blueprint.name]})
    except Exception as exc:
        job.status = job.stage = 'failed'
        job.error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc) or type(exc).__name__
        session.publish({'type': 'notice', 'text': f'Scan import failed: {job.error}'})
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400 if isinstance(exc, ValueError) else 500, job.error) from exc
    finally:
        try:
            (directory / 'workflow.json').write_text(json.dumps(job.to_dict(), indent=2), encoding='utf-8')
        except OSError:
            pass
        job.done.set()
    return job.to_dict()


@router.get('/{job_id}')
def status(job_id: str):
    return get_job(job_id).to_dict()


@router.post('/{job_id}/cancel')
def cancel(job_id: str):
    job = get_job(job_id)
    if job.status in ('queued', 'running', 'cancelling'):
        job.cancelled.set()
        job.status = 'cancelling'
    return job.to_dict()


@router.get('/{job_id}/files/{filename}')
def download(job_id: str, filename: str):
    job = get_job(job_id)
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.(png|jpg|json|jsonl|glb|stl|txt)', filename):
        raise HTTPException(404, 'No such artifact.')
    path = job.directory / filename
    if not path.is_file():
        raise HTTPException(404, 'No such artifact.')
    return FileResponse(path, filename=filename)
