"""Revision-bound structural analysis jobs and their immutable result artifacts."""
import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from ..analysis import capabilities, select_backend
from ..analysis.settings import FeaSettings
from ..analysis.worker import FEA_TIMEOUT_S, run_worker
from .cad_sessions import _manager, _session_or_404

router = APIRouter()
RESULT_FILES = {'stress.glb', 'displacement.glb', 'report.json'}


class FeaRequest(FeaSettings):
    revision: int = Field(ge=1)


async def run_analysis(session, job, mesh_path):
    """Keep native meshing/solving outside the API, and kill overdue workers."""
    try:
        async with _manager().fea_slots:
            job['status'] = 'running'
            await run_worker(mesh_path, job['directory'] / 'settings.json', job['directory'],
                             step=job.get('step_path'), timeout=FEA_TIMEOUT_S)
            report_path = job['directory'] / 'report.json'
            result = json.loads(report_path.read_text())
            result['revision'] = job['revision']
            result['analysis_id'] = job['analysis_id']
            report_path.write_text(json.dumps(result, allow_nan=False, indent=2), encoding='utf-8')
            base = f'/api/v1/cad-sessions/{session.id}/fea/{job["analysis_id"]}/files'
            job['result'] = {**result, 'stress_url': f'{base}/stress.glb',
                             'displacement_url': f'{base}/displacement.glb', 'report_url': f'{base}/report.json'}
            job['status'] = 'succeeded'
    except TimeoutError:
        job.update(status='failed', error='FEA exceeded the two-minute limit. Try a coarser grid.')
    except asyncio.CancelledError:
        job.update(status='failed', error='FEA stopped because the server shut down.')
        raise
    except Exception as exc:
        job.update(status='failed', error=str(exc)[:500])


def public_job(job):
    return {key: value for key, value in job.items() if key not in {'directory', 'task', 'step_path'}}


@router.get('/api/v1/fea/capabilities')
def get_capabilities():
    return capabilities()


@router.post('/api/v1/cad-sessions/{session_id}/fea', status_code=202)
async def create_analysis(session_id: str, body: FeaRequest):
    session = _session_or_404(session_id)
    if session.units != 'mm':
        raise HTTPException(409, 'FEA requires known millimeter units. Start a CAD session with mesh units set to mm.')
    entry = next((m for m in session.meshes if m['revision'] == body.revision and m['generated']), None)
    if entry is None or not entry['path'].is_file():
        raise HTTPException(404, f'No generated CAD mesh for revision {body.revision}')
    if any(job['status'] in {'queued', 'running'} for job in session.fea_jobs.values()):
        raise HTTPException(409, 'An FEA run is already in progress for this session.')
    try:
        engine = select_backend(body, entry['path'], entry.get('step_path'))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    analysis_id = uuid.uuid4().hex[:12]
    directory = session.artifacts.root / f'fea-{analysis_id}'
    directory.mkdir()
    settings = body.model_dump(exclude={'revision'})
    (directory / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    job = {'analysis_id': analysis_id, 'revision': body.revision, 'status': 'queued',
           'error': None, 'result': None, 'directory': directory, 'engine': engine,
           'step_path': entry.get('step_path'),
           'status_url': f'/api/v1/cad-sessions/{session_id}/fea/{analysis_id}'}
    session.fea_jobs[analysis_id] = job
    job['task'] = asyncio.create_task(run_analysis(session, job, entry['path']))
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
    job = analysis_or_404(session_id, analysis_id)
    if filename not in RESULT_FILES or job['status'] != 'succeeded':
        raise HTTPException(404, 'No such FEA artifact')
    path = job['directory'] / filename
    if not path.is_file():
        raise HTTPException(404, 'FEA artifact missing on disk')
    return FileResponse(path, media_type='model/gltf-binary' if filename.endswith('.glb') else 'application/json',
                        filename=filename if filename.endswith('.json') else None,
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})
