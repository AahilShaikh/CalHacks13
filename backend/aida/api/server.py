"""Job-based HTTP API for the image-to-metric-mesh scan pipeline, live CAD
sessions (``/api/v1/cad-sessions`` in :mod:`aida.api.cad_sessions`), and the
web UI at ``/``.

The UI is the React app in ``frontend/`` once it has been built (``npm run build``
writes ``frontend/dist``); the older single-file viewer in ``aida/web`` is then
served at ``/legacy``. Without a build, ``aida/web`` is served at ``/``.

Scan endpoints:

- ``GET  /health``                        service and ComfyUI reachability
- ``POST /api/v1/scans``                  submit photos, returns 202 + job id
- ``GET  /api/v1/scans/{job_id}``         poll status, specs, artifact URLs
- ``GET  /api/v1/scans/{job_id}/files/{filename}``  download one artifact

Submission accepts 1-4 turntable photos (``front_image`` required,
``left_image``/``back_image``/``right_image`` optional) plus one scale anchor
form field: ``known_height_mm``, ``known_base_dia_mm``, or ``scale_factor``.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import REPO_ROOT, load_local_env
from ..scan.comfy import ComfyClient, ComfyConfig
from ..scan.pipeline import run_scan
from .cad_sessions import CadSessionManager, router as cad_sessions_router
from .jobs import JobManager
from .replays import router as replays_router
from .versions import router as versions_router
from .workspaces import router as workspaces_router

load_local_env()

# Relative to the repository root, so starting the server from another folder
# (e.g. frontend/) does not scatter session artifacts there.
DEFAULT_STORAGE = Path(os.environ.get('AIDA_SCAN_STORAGE', REPO_ROOT / 'artifacts' / 'scans'))
CAD_STORAGE = Path(os.environ.get('AIDA_CAD_STORAGE', REPO_ROOT / 'artifacts' / 'cad'))
WEB_DIR = Path(__file__).resolve().parent.parent / 'web'
FRONTEND_DIST = Path(os.environ.get('AIDA_FRONTEND_DIST',
                                    Path(__file__).resolve().parents[3] / 'frontend' / 'dist'))

@asynccontextmanager
async def lifespan(_app):
    # Start a CAD engine now so the first prompt does not wait ~10 s for it.
    if os.environ.get('ANTHROPIC_API_KEY'):
        cad_manager.prewarm()
    # Workspaces nobody watches free their CAD engine; they reopen from disk.
    cad_manager.start_reaper()
    yield
    await cad_manager.shutdown()


app = FastAPI(
    lifespan=lifespan,
    title='Aida Scan API',
    description='Images to 1:1 metric mesh and CAD handoff artifacts (Pixal3D + ChArUco).',
    version='1.0.0',
)

manager = JobManager(DEFAULT_STORAGE)
cad_manager = CadSessionManager(CAD_STORAGE)


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
        'agent_ready': bool(os.environ.get('ANTHROPIC_API_KEY')),
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


app.include_router(cad_sessions_router)
app.include_router(replays_router)
app.include_router(workspaces_router)
app.include_router(versions_router)
# The web UI is mounted last so API routes match first.
if (FRONTEND_DIST / 'index.html').is_file():
    # The app's own pages (a workspace, a replay) all load index.html; the app routes them.
    @app.get('/w/{workspace_id}', include_in_schema=False)
    @app.get('/replay/{replay_id}', include_in_schema=False)
    def app_page(workspace_id: str = '', replay_id: str = ''):
        return FileResponse(FRONTEND_DIST / 'index.html')

    app.mount('/legacy', StaticFiles(directory=WEB_DIR, html=True), name='legacy-web')
    app.mount('/', StaticFiles(directory=FRONTEND_DIST, html=True), name='web')
else:
    app.mount('/', StaticFiles(directory=WEB_DIR, html=True), name='web')


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get('AIDA_API_HOST', '127.0.0.1'),
                port=int(os.environ.get('AIDA_API_PORT', '8000')))


if __name__ == '__main__':
    main()
