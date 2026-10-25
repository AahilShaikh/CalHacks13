"""Four-view scans attached to an existing live workspace; no separate agent."""
import asyncio
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
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
                workspace.add_reference_part(spec['name'], spec['reference'], 'Turntable scan; reference mesh in millimeters.')
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
