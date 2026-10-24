"""Job-based HTTP API for the image-to-metric-mesh scan pipeline.

Endpoints:

- ``GET  /health``                        service and ComfyUI reachability
- ``POST /api/v1/scans``                  submit photos, returns 202 + job id
- ``GET  /api/v1/scans/{job_id}``         poll status, specs, artifact URLs
- ``GET  /api/v1/scans/{job_id}/files/{filename}``  download one artifact

Submission accepts 1-4 turntable photos (``front_image`` required,
``left_image``/``back_image``/``right_image`` optional) plus one scale anchor
form field: ``known_height_mm``, ``known_base_dia_mm``, or ``scale_factor``.
"""
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import load_local_env
from ..scan.comfy import ComfyClient, ComfyConfig
from ..scan.pipeline import run_scan
from .jobs import JobManager

load_local_env()

DEFAULT_STORAGE = Path(os.environ.get('AIDA_SCAN_STORAGE', 'artifacts/scans'))

app = FastAPI(
    title='Aida Scan API',
    description='Images to 1:1 metric mesh and CAD handoff artifacts (Pixal3D + ChArUco).',
    version='1.0.0',
)

manager = JobManager(DEFAULT_STORAGE)


def _comfy_config() -> ComfyConfig:
    config = ComfyConfig(host=os.environ.get('COMFY_HOST', '127.0.0.1:8188'))
    workflow = os.environ.get('AIDA_PIXAL3D_WORKFLOW')
    if workflow:
        config.workflow_path = Path(workflow)
    return config


@app.get('/health')
def health():
    config = _comfy_config()
    return {
        'status': 'ok',
        'comfy_host': config.host,
        'comfy_reachable': ComfyClient(config).health(),
        'active_jobs': manager.count_active(),
    }


@app.post('/api/v1/scans', status_code=202)
async def create_scan(
    front_image: UploadFile = File(...),
    left_image: UploadFile | None = File(None),
    back_image: UploadFile | None = File(None),
    right_image: UploadFile | None = File(None),
    known_height_mm: float | None = Form(None),
    known_base_dia_mm: float | None = Form(None),
    scale_factor: float | None = Form(None),
):
    images = {'front': await front_image.read()}
    for name, upload in (('left', left_image), ('back', back_image), ('right', right_image)):
        if upload is not None:
            images[name] = await upload.read()

    def run(job):
        return run_scan(
            images, job.directory,
            known_height_mm=known_height_mm,
            known_base_dia_mm=known_base_dia_mm,
            scale_factor=scale_factor,
            client=ComfyClient(_comfy_config()),
            progress=lambda stage: manager.set_stage(job, stage))

    job = manager.submit(run)
    return {
        'job_id': job.id,
        'status': job.status,
        'status_url': f'/api/v1/scans/{job.id}',
    }


@app.get('/api/v1/scans/{job_id}')
def get_scan(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f'No such job: {job_id}')
    return job.to_dict()


@app.get('/api/v1/scans/{job_id}/files/{filename}')
def get_scan_file(job_id: str, filename: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f'No such job: {job_id}')
    path = job.files.get(filename)
    if path is None:
        available = ', '.join(sorted(job.files)) or 'none yet'
        raise HTTPException(status_code=404,
                            detail=f'No artifact {filename!r} for job {job_id} (available: {available})')
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f'Artifact missing on disk: {filename}')
    return FileResponse(path, filename=filename)


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get('AIDA_API_HOST', '127.0.0.1'),
                port=int(os.environ.get('AIDA_API_PORT', '8000')))


if __name__ == '__main__':
    main()
