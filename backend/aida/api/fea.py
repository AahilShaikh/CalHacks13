"""Revision-bound structural analysis jobs and their immutable result artifacts.

Every run, whether started from the FEA panel or by the agent, is told to the UI
as session events (``fea_started``, ``fea_stage``, ``fea_done``/``fea_failed``),
so it shows live and replays when the workspace is reopened.
"""
import asyncio
import json
import re
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from ..analysis import capabilities, require_step
from ..analysis.settings import FeaSettings
from ..analysis.worker import FEA_TIMEOUT_S, run_worker
from .cad_sessions import _manager, _session_or_404

router = APIRouter()
RESULT_FILES = {'stress.glb', 'displacement.glb', 'report.json', 'setup.png'}
ANALYSIS_ID = re.compile(r'^[0-9a-f]{12}$')


class FeaRequest(FeaSettings):
    revision: int = Field(ge=0)  # a workspace without scans numbers its first CAD revision 0


class FeaError(Exception):
    """A request that cannot start: ``status`` is the HTTP code, the message is for people."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def files_base(session, analysis_id: str) -> str:
    return f'/api/v1/cad-sessions/{session.id}/fea/{analysis_id}/files'


def public_job(job):
    return {key: value for key, value in job.items() if key not in {'directory', 'task', 'step_path', 'done'}}


async def run_analysis(session, job):
    """Keep native meshing/solving outside the API, and kill overdue workers."""
    publish = getattr(session, 'publish', lambda event: None)

    def stage(name):
        job['stage'] = name
        publish({'type': 'fea_stage', 'analysis_id': job['analysis_id'], 'stage': name})

    try:
        async with _manager().fea_slots:
            job['status'] = 'running'
            await run_worker(job['step_path'], job['directory'] / 'settings.json', job['directory'],
                             timeout=FEA_TIMEOUT_S, on_stage=stage)
            report_path = job['directory'] / 'report.json'
            result = json.loads(report_path.read_text())
            result['revision'] = job['revision']
            result['analysis_id'] = job['analysis_id']
            report_path.write_text(json.dumps(result, allow_nan=False, indent=2), encoding='utf-8')
            base = files_base(session, job['analysis_id'])
            job['result'] = {**result, 'stress_url': f'{base}/stress.glb', 'setup_url': f'{base}/setup.png',
                             'displacement_url': f'{base}/displacement.glb', 'report_url': f'{base}/report.json'}
            job['status'] = 'succeeded'
    except TimeoutError:
        job.update(status='failed', error='FEA exceeded the two-minute limit. Try a coarser resolution.')
    except asyncio.CancelledError:
        job.update(status='failed', error='FEA stopped because the server shut down.')
        raise
    except Exception as exc:
        job.update(status='failed', error=str(exc)[:500])
    finally:
        if job.get('status') in {'succeeded', 'failed'}:
            publish({'type': 'fea_done' if job['status'] == 'succeeded' else 'fea_failed', **public_job(job)})
        done = job.get('done')
        if done is not None:
            done.set()


def start_analysis(session, revision: int, settings: FeaSettings, source: str = 'user') -> dict:
    """Validate, create and launch one FEA job on ``revision``. Raises FeaError."""
    if session.units != 'mm':
        raise FeaError(409, 'FEA requires known millimeter units. Start a CAD session with mesh units set to mm.')
    entry = next((m for m in session.meshes if m['revision'] == revision and m['generated']), None)
    if entry is None or not entry['path'].is_file():
        raise FeaError(404, f'No generated CAD for revision {revision}')
    if any(job['status'] in {'queued', 'running'} for job in session.fea_jobs.values()):
        raise FeaError(409, 'An FEA run is already in progress for this session.')
    parts = entry.get('parts') or []
    if not entry.get('step_path') and len(parts) > 1:
        raise FeaError(409, f'FEA tests one solid at a time, and revision {revision} shows {len(parts)} parts '
                            f'({", ".join(parts)}). Show a single part, then run FEA on that revision.')
    try:
        step = require_step(entry.get('step_path'))
    except ValueError as exc:
        raise FeaError(409, str(exc)) from exc
    analysis_id = uuid.uuid4().hex[:12]
    directory = session.artifacts.root / f'fea-{analysis_id}'
    directory.mkdir()
    (directory / 'settings.json').write_text(settings.model_dump_json(), encoding='utf-8')
    job = {'analysis_id': analysis_id, 'revision': revision, 'status': 'queued', 'stage': 'queued',
           'source': source, 'settings': settings.model_dump(), 'error': None, 'result': None,
           'directory': directory, 'step_path': step, 'done': asyncio.Event(),
           'status_url': f'/api/v1/cad-sessions/{session.id}/fea/{analysis_id}'}
    session.fea_jobs[analysis_id] = job
    session.publish({'type': 'fea_started', **public_job(job)})
    job['task'] = asyncio.create_task(run_analysis(session, job))
    return job


async def list_faces(session, revision: int) -> dict:
    """The revision's CAD faces (numbers, kinds, positions) and a labelled picture, cached on disk."""
    entry = next((m for m in session.meshes if m['revision'] == revision and m['generated']), None)
    if entry is None:
        raise FeaError(404, f'No generated CAD for revision {revision}')
    try:
        step = require_step(entry.get('step_path'))
    except ValueError as exc:
        raise FeaError(409, str(exc)) from exc
    directory = session.artifacts.root / f'fea-faces-{revision:03d}'
    if not (directory / 'faces.json').is_file():
        directory.mkdir(exist_ok=True)
        async with _manager().fea_slots:
            try:
                await run_worker(step, None, directory, timeout=FEA_TIMEOUT_S)
            except TimeoutError as exc:
                raise FeaError(504, 'Listing the faces took too long.') from exc
            except ValueError as exc:
                raise FeaError(409, str(exc)) from exc
    return json.loads((directory / 'faces.json').read_text())


@router.get('/api/v1/fea/capabilities')
def get_capabilities():
    return capabilities()


@router.post('/api/v1/cad-sessions/{session_id}/fea', status_code=202)
async def create_analysis(session_id: str, body: FeaRequest):
    session = _session_or_404(session_id)
    try:
        job = start_analysis(session, body.revision, FeaSettings(**body.model_dump(exclude={'revision'})))
    except FeaError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return public_job(job)


def analysis_or_404(session_id, analysis_id):
    job = _session_or_404(session_id).fea_jobs.get(analysis_id)
    if job is None:
        raise HTTPException(404, 'No such FEA run')
    return job


@router.get('/api/v1/cad-sessions/{session_id}/fea/{analysis_id}')
def get_analysis(session_id: str, analysis_id: str):
    return public_job(analysis_or_404(session_id, analysis_id))


@router.get('/api/v1/cad-sessions/{session_id}/fea/{analysis_id}/files/{filename}')
def get_analysis_file(session_id: str, analysis_id: str, filename: str):
    session = _session_or_404(session_id)
    if filename not in RESULT_FILES or not ANALYSIS_ID.match(analysis_id):
        raise HTTPException(404, 'No such FEA artifact')
    job = session.fea_jobs.get(analysis_id)
    if job is not None and job['status'] != 'succeeded':
        raise HTTPException(404, 'No such FEA artifact')
    # Runs from before a workspace was reopened are not in memory; their files are.
    path = session.artifacts.root / f'fea-{analysis_id}' / filename
    if not path.is_file() or (job is None and not (path.parent / 'report.json').is_file()):
        raise HTTPException(404, 'FEA artifact missing on disk')
    media = {'.glb': 'model/gltf-binary', '.png': 'image/png'}.get(path.suffix, 'application/json')
    return FileResponse(path, media_type=media,
                        filename=filename if filename.endswith('.json') else None,
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})
