"""ComfyUI REST client for the Pixal3D image-to-mesh workflow.

The ComfyUI installation (with its models and its own virtual environment) is
expected to run externally, typically at ``http://127.0.0.1:8188``. Nothing in
this module assumes the ComfyUI output directory is on the local filesystem:
generated meshes are fetched over the ``/view`` endpoint, so a remote
``COMFY_HOST`` works the same as a local one.

The workflow template (an API-format export of the Pixal3D multi-view ComfyUI
workflows) uses fixed node ids shared by all four shipped variants:

- ``364`` front ``LoadImage`` (wired through background removal)
- ``377`` left, ``380`` back, ``383`` right ``LoadImage`` (optional views)
- ``285`` ``MeshToFile3D`` (writes the generated mesh file)

Only the nodes present in the template are wired; a template exported without
the optional views simply ignores them.
"""
import json
import time
import uuid
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WORKFLOW_PATH = Path(__file__).parent / 'assets' / 'pixal3d_workflow_api.json'

# LoadImage node ids per view, shared by the shipped Pixal3D workflow variants.
LOAD_IMAGE_NODES = {'front': '364', 'left': '377', 'back': '380', 'right': '383'}
OUTPUT_NODE = '285'

VIEW_ORDER = ('front', 'left', 'back', 'right')


class ComfyError(RuntimeError):
    """Raised when ComfyUI rejects a request or the workflow fails."""


@dataclass
class ComfyConfig:
    host: str = '127.0.0.1:8188'
    workflow_path: Path = DEFAULT_WORKFLOW_PATH
    timeout: float = 300.0
    poll_interval: float = 2.0
    load_image_nodes: dict = field(default_factory=lambda: dict(LOAD_IMAGE_NODES))
    output_node: str = OUTPUT_NODE

    @property
    def base_url(self) -> str:
        host = self.host.strip().rstrip('/')
        if not host.startswith(('http://', 'https://')):
            host = f'http://{host}'
        return host


def wire_workflow(workflow: dict, images: dict, config: ComfyConfig) -> dict:
    """Return a copy of ``workflow`` with uploaded image names set per view.

    ``images`` maps view names (front/left/back/right) to ComfyUI image names
    as returned by :meth:`ComfyClient.upload_image`. Views whose LoadImage node
    is absent from the workflow are skipped.
    """
    wired = json.loads(json.dumps(workflow))
    for view in VIEW_ORDER:
        name = images.get(view)
        node_id = config.load_image_nodes.get(view)
        if not name or node_id is None or node_id not in wired:
            continue
        node = wired[node_id]
        if node.get('class_type') != 'LoadImage':
            raise ComfyError(f'Workflow node {node_id} is not a LoadImage node')
        node['inputs']['image'] = name
    if not any(config.load_image_nodes.get(view) in wired
               for view, name in images.items() if name):
        raise ComfyError('No LoadImage node in the workflow matches the supplied views')
    return wired


class ComfyClient:
    def __init__(self, config: ComfyConfig | None = None):
        self.config = config or ComfyConfig()

    # -- HTTP helpers -----------------------------------------------------

    def _request(self, path, data=None, headers=None, timeout=60):
        request = urllib.request.Request(
            f'{self.config.base_url}{path}', data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except OSError as exc:
            raise ComfyError(
                f'Cannot reach ComfyUI at {self.config.base_url}: {exc}') from exc

    def _json(self, path, payload=None, timeout=60):
        if payload is None:
            raw = self._request(path, timeout=timeout)
        else:
            raw = self._request(path, data=json.dumps(payload).encode(),
                                headers={'Content-Type': 'application/json'},
                                timeout=timeout)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ComfyError(f'ComfyUI returned invalid JSON for {path}') from exc

    def health(self) -> bool:
        try:
            self._json('/system_stats', timeout=10)
        except ComfyError:
            return False
        return True

    # -- Pipeline steps ---------------------------------------------------

    def upload_image(self, data: bytes, filename: str) -> str:
        """Upload one input image and return the name ComfyUI stores it under."""
        boundary = f'----AidaBoundary{uuid.uuid4().hex}'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        raw = self._request(
            '/upload/image', data=body,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
            timeout=120)
        try:
            return json.loads(raw).get('name', filename)
        except ValueError as exc:
            raise ComfyError('ComfyUI upload returned invalid JSON') from exc

    def run_pixal3d(self, images: dict, output_path: Path) -> Path:
        """Run the Pixal3D workflow on uploaded views and save the mesh to ``output_path``.

        ``images`` maps view names (front/left/back/right) to ComfyUI image
        names. At least ``front`` is required. Returns the saved mesh path.
        """
        if not images.get('front'):
            raise ComfyError('Pixal3D requires at least the front view image')
        workflow_path = Path(self.config.workflow_path)
        if not workflow_path.is_file():
            raise ComfyError(f'Workflow template not found: {workflow_path}')
        workflow = json.loads(workflow_path.read_text())
        wired = wire_workflow(workflow, images, self.config)

        client_id = str(uuid.uuid4())
        queued = self._json('/prompt', {'prompt': wired, 'client_id': client_id})
        prompt_id = queued.get('prompt_id')
        if not prompt_id:
            raise ComfyError(f'ComfyUI rejected the workflow: {queued}')
        return self._wait_for_mesh(prompt_id, output_path)

    def _wait_for_mesh(self, prompt_id: str, output_path: Path) -> Path:
        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            history = self._json(f'/history/{prompt_id}') or {}
            entry = history.get(prompt_id)
            if entry:
                self._raise_on_error(entry)
                record = self._find_mesh_record(entry)
                if record:
                    return self._download(record, output_path)
            time.sleep(self.config.poll_interval)
        raise ComfyError(
            f'Pixal3D generation exceeded {self.config.timeout:.0f}s (prompt {prompt_id})')

    @staticmethod
    def _raise_on_error(entry: dict) -> None:
        status = entry.get('status', {})
        messages = [m for m in status.get('messages', [])
                    if isinstance(m, list) and len(m) == 2 and m[0] == 'execution_error']
        if status.get('status_str') == 'error' or messages:
            details = '; '.join(str(m[1]) for m in messages) or 'workflow execution error'
            raise ComfyError(f'Pixal3D workflow failed: {details}')

    def _find_mesh_record(self, entry: dict) -> dict | None:
        outputs = entry.get('outputs', {})
        node_ids = [self.config.output_node] + [k for k in outputs if k != self.config.output_node]
        for node_id in node_ids:
            for value in outputs.get(node_id, {}).get('files', []):
                if isinstance(value, dict) and value.get('filename'):
                    return value
        return None

    def _download(self, record: dict, output_path: Path) -> Path:
        query = urllib.parse.urlencode({
            'filename': record['filename'],
            'subfolder': record.get('subfolder', ''),
            'type': record.get('type', 'output'),
        })
        data = self._request(f'/view?{query}', timeout=300)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
        return output_path
