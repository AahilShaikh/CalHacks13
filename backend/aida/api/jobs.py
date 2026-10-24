"""In-memory scan job tracking with background execution.

Each submitted scan runs in a single-worker thread pool (Pixal3D generation is
GPU-bound; serializing jobs keeps the external ComfyUI instance stable). Job
state lives in memory and artifacts live on disk under the storage directory,
which is the right trade-off for a single-user local service.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

QUEUED = 'queued'
RUNNING = 'running'
SUCCEEDED = 'succeeded'
FAILED = 'failed'


@dataclass
class Job:
    id: str
    directory: Path
    status: str = QUEUED
    stage: str = 'queued'
    error: str | None = None
    cad_specs: dict | None = None
    agent_prompt: str | None = None
    files: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self):
        return {
            'job_id': self.id,
            'status': self.status,
            'stage': self.stage,
            'error': self.error,
            'cad_specs': self.cad_specs,
            'agent_prompt': self.agent_prompt,
            'files': {name: f'/api/v1/scans/{self.id}/files/{name}' for name in self.files},
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class JobManager:
    def __init__(self, storage_dir, max_workers=1):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._jobs = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, run) -> Job:
        """Register a job and start ``run(job)`` in the background worker."""
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, directory=self.storage_dir / job_id)
        job.directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._execute, job, run)
        return job

    def _execute(self, job, run):
        self._update(job, status=RUNNING, stage='starting')
        try:
            result = run(job)
        except Exception as exc:
            self._update(job, status=FAILED, error=str(exc) or exc.__class__.__name__)
            return
        self._update(job, status=SUCCEEDED, stage='done',
                     cad_specs=result.cad_specs,
                     agent_prompt=result.agent_prompt,
                     files={name: Path(path) for name, path in result.files.items()})

    def get(self, job_id) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def count_active(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values()
                       if job.status not in (SUCCEEDED, FAILED))

    def _update(self, job, **changes):
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = time.time()

    def set_stage(self, job, stage):
        self._update(job, stage=stage)
