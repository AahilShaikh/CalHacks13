"""Background capture-to-CAD workflows; each individual hardware move blocks."""
import asyncio
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..scan.acquisition import run_capture_scan
from ..scan.camera import CameraCalibration
from ..turntable import HardwareSettings, service
from .cad_sessions import first_prompt

router = APIRouter(prefix='/api/v1/acquisitions', tags=['capture to CAD'])


class AcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(default='Scan this object and create a parametric CAD model.', min_length=1, max_length=10000)
    policy: Literal['agent', 'four_views'] = 'agent'
    start_cad: bool = True


@dataclass
class AcquisitionJob:
    id: str
    directory: Path
    status: str = 'queued'
    stage: str = 'queued'
    error: str | None = None
    cad_session_id: str | None = None
    cad_specs: dict | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None

    def to_dict(self):
        return {'job_id': self.id, 'status': self.status, 'stage': self.stage, 'error': self.error,
                'cad_session_id': self.cad_session_id, 'cad_specs': self.cad_specs,
                'viewer_url': f'/?session={self.cad_session_id}' if self.cad_session_id else None,
                'files': {p.name: f'/api/v1/acquisitions/{self.id}/files/{p.name}'
                          for p in self.directory.iterdir() if p.is_file()}}


class AcquisitionManager:
    def __init__(self, runner=run_capture_scan):
        self.jobs = {}
        self.runner = runner

    def submit(self, request, cad_manager):
        if any(job.status in ('queued', 'running', 'cancelling') for job in self.jobs.values()):
            raise HTTPException(409, 'Another capture-to-CAD workflow is active.')
        settings = HardwareSettings.from_env()
        calibration = CameraCalibration.load(settings.storage / 'camera.json')
        if calibration.camera_id != settings.camera_id:
            raise HTTPException(409, 'Selected camera differs from its calibration.')
        if not (settings.storage / 'zero.json').is_file():
            raise HTTPException(409, 'Set the mounted turntable zero before starting a scan.')
        if not all((settings.storage / name).is_file() for name in ('empty_table.png', 'empty_table.json')):
            raise HTTPException(409, 'Save an empty-table reference when setting zero at /setup.html.')
        identity = uuid.uuid4().hex
        directory = Path(os.environ.get('AIDA_ACQUISITION_STORAGE', 'artifacts/acquisitions')) / identity
        directory.mkdir(parents=True, exist_ok=True)
        job = AcquisitionJob(identity, directory)
        self.jobs[identity] = job
        job.task = asyncio.create_task(self._run(job, request, cad_manager))
        return job

    async def _run(self, job, request, cad_manager):
        job.status = 'running'
        try:
            result = await asyncio.to_thread(
                self.runner, job.directory, request.prompt, controller=service, policy=request.policy,
                progress=lambda stage: setattr(job, 'stage', stage), cancelled=job.cancelled)
            if job.cancelled.is_set():
                job.status, job.stage = 'cancelled', 'cancelled'
                return
            job.cad_specs = result.cad_specs
            if request.start_cad:
                job.stage = 'preparing_cad'
                def prepare():
                    images = [(path.name, path.read_bytes()) for path in job.directory.glob('*.png')
                              if path.stem in ('front', 'left', 'back', 'right', 'blueprints')]
                    session = cad_manager.create([('metric_model.stl', result.metric_stl.read_bytes())],
                                                 images, request.prompt+'\n\n'+result.agent_prompt, 'mm')
                    for name in ('dimensions.json', 'camera.json', 'agent_prompt.txt'):
                        source = job.directory / name
                        if source.exists():
                            (session.artifacts.root / name).write_bytes(source.read_bytes())
                    return session
                session = await asyncio.to_thread(prepare)
                if job.cancelled.is_set():
                    session.set_status('done')
                    job.status, job.stage = 'cancelled', 'cancelled'
                    return
                session.turns.put_nowait(first_prompt(session))
                cad_manager.start(session)
                job.cad_session_id = session.id
            job.status = 'succeeded'
            job.stage = 'cad_started' if request.start_cad else 'metric_mesh_ready'
            (job.directory / 'workflow.json').write_text(json.dumps(job.to_dict(), indent=2)+'\n')
        except Exception as exc:
            job.error = str(exc) or type(exc).__name__
            job.status = 'cancelled' if job.cancelled.is_set() else 'failed'
            job.stage = job.status


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
