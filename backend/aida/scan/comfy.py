"""ComfyUI REST client for the Pixal3D image-to-mesh workflow.

The ComfyUI installation (with its models and its own virtual environment) is
expected to run externally, typically at ``http://127.0.0.1:8188``. Nothing in
this module assumes the ComfyUI output directory is on the local filesystem:
generated meshes are fetched over the ``/view`` endpoint, so a remote
``COMFY_HOST`` works the same as a local one.

The two API-format presets (fast geometry and full PBR) share fixed node ids:

- ``364`` front ``LoadImage`` (wired through background removal)
- ``377`` left, ``380`` back, ``383`` right ``LoadImage`` (optional views)
- ``285`` ``MeshToFile3D`` (writes the generated mesh file)

The bundled front-image preparation path is expanded for optional views.
Supplied views must reach the output node; unsupported exports fail explicitly.
"""
import io
import json
import math
import time
import uuid
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

WORKFLOWS = {name: Path(__file__).parent / 'assets' / f'pixal3d_{name}_api.json'
             for name in ('fast', 'full')}
DEFAULT_WORKFLOW_PATH = WORKFLOWS['full']

# LoadImage node ids per view, shared by the shipped Pixal3D workflow variants.
LOAD_IMAGE_NODES = {'front': '364', 'left': '377', 'back': '380', 'right': '383'}
OUTPUT_NODE = '285'

VIEW_ORDER = ('front', 'left', 'back', 'right')


class ComfyError(RuntimeError):
    """Raised when ComfyUI rejects a request or the workflow fails."""


@dataclass
class ComfyConfig:
    host: str = '127.0.0.1:8188'
    workflow_path: Path | None = None
    timeout: float = 1800.0
    poll_interval: float = 2.0
    load_image_nodes: dict = field(default_factory=lambda: dict(LOAD_IMAGE_NODES))
    output_node: str = OUTPUT_NODE
    workflow: str = 'full'
    camera_focal_px: float | None = None

    def __post_init__(self):
        if self.workflow not in WORKFLOWS:
            raise ValueError(f'Unknown workflow: {self.workflow}; choose fast or full')
        if self.workflow_path is None:
            self.workflow_path = WORKFLOWS[self.workflow]

    @property
    def base_url(self) -> str:
        host = self.host.strip().rstrip('/')
        if not host.startswith(('http://', 'https://')):
            host = f'http://{host}'
        return host


def wire_workflow(workflow: dict, images: dict, config: ComfyConfig, object_regions=None) -> dict:
    """Return a copy of ``workflow`` with uploaded image names set per view.

    ``images`` maps view names (front/left/back/right) to ComfyUI image names
    as returned by :meth:`ComfyClient.upload_image`. For the bundled workflow,
    optional views receive their own background removal and crop path.
    """
    wired = json.loads(json.dumps(workflow))
    conditioning = wired.get('324', {})
    direct_object_preparation = wired.get('500', {}).get('class_type') == 'AidaObjectOnlyViews'
    if direct_object_preparation and config.camera_focal_px is not None:
        wired['500']['inputs']['camera_focal_px'] = config.camera_focal_px
        conditioning['inputs']['fov'] = ['500',10]
    bundled_preparation = (
        conditioning.get('class_type') == 'Pixal3DMultiViewConditioning' and
        wired.get('192', {}).get('class_type') == 'RemoveBackground' and
        wired.get('312', {}).get('class_type') == 'ImageCropToMask' and
        wired['192']['inputs'].get('image') == [config.load_image_nodes.get('front'), 0] and
        wired['312']['inputs'].get('images') == [config.load_image_nodes.get('front'), 0]
    )
    for view, name in images.items():
        if name and view not in VIEW_ORDER:
            raise ComfyError(f'Unsupported view: {view}')
    for view in VIEW_ORDER:
        name = images.get(view)
        node_id = config.load_image_nodes.get(view)
        if not name:
            if direct_object_preparation and view != 'front':
                wired[node_id]['inputs']['image'] = '(none)'
                wired['500']['inputs'].pop(view, None)
            if conditioning.get('class_type') == 'Pixal3DMultiViewConditioning':
                conditioning['inputs'].pop(view, None)
            continue
        if node_id is None:
            raise ComfyError(f'No LoadImage mapping configured for {view}')
        if node_id not in wired:
            if not bundled_preparation or view == 'front':
                raise ComfyError(f'Workflow cannot consume the supplied {view} view (node {node_id} missing)')
            mask_id, prepare_id = f'aida_{view}_mask', f'aida_{view}_prepare'
            if mask_id in wired or prepare_id in wired:
                raise ComfyError(f'Workflow contains reserved preparation IDs for {view}')
            wired[node_id] = {'class_type': 'LoadImage', 'inputs': {'image': name}}
            wired[mask_id] = json.loads(json.dumps(wired['192']))
            wired[mask_id]['inputs']['image'] = [node_id, 0]
            wired[prepare_id] = json.loads(json.dumps(wired['312']))
            wired[prepare_id]['inputs'].update(images=[node_id, 0], masks=[mask_id, 0])
            conditioning['inputs'][view] = [prepare_id, 0]
        node = wired[node_id]
        if node.get('class_type') not in ('LoadImage', 'AidaOptionalImage'):
            raise ComfyError(f'Workflow node {node_id} is not a LoadImage node')
        node['inputs']['image'] = name
        if direct_object_preparation:
            wired['500']['inputs'][view] = [node_id, 0]
            conditioning['inputs'][view] = ['500', VIEW_ORDER.index(view)]
    # BiRefNet sees the board itself as a foreground object. For detected board
    # photos, use an object box derived from the projected print to prompt SAM.
    # Its final mask is computed on the ORIGINAL image, preserving object edges.
    if object_regions and not (bundled_preparation or direct_object_preparation):
        raise ComfyError('Board object segmentation requires the bundled preparation paths.')
    for view, region in (object_regions or {}).items() if not direct_object_preparation else []:
        if view not in images or not images[view]:
            raise ComfyError(f'Object prompt has no supplied image: {view}')
        prepare_id = conditioning['inputs'][view][0]
        mask_id = wired[prepare_id]['inputs']['masks'][0]
        wired['aida_object_segmenter'] = {'class_type': 'CheckpointLoaderSimple', 'inputs': {
            'ckpt_name': 'sam3.1_multiplex_fp16.safetensors'}}
        wired[mask_id] = {'class_type': 'SAM3_Detect', 'inputs': {
            'model': ['aida_object_segmenter', 0], 'image': [config.load_image_nodes[view], 0],
            'bboxes': region, 'threshold': 0.5, 'refine_iterations': 1, 'individual_masks': False}}

    # Reject silent omissions even when an export contains disconnected image nodes.
    reachable = set()
    def visit(node_id):
        if node_id in reachable or node_id not in wired:
            return
        reachable.add(node_id)
        for value in wired[node_id].get('inputs', {}).values():
            if (isinstance(value, list) and len(value) == 2 and
                    isinstance(value[0], str) and isinstance(value[1], int)):
                visit(value[0])
    visit(config.output_node)
    for view, name in images.items():
        if name and config.load_image_nodes.get(view) not in reachable:
            raise ComfyError(f'The supplied {view} image is not connected to mesh output {config.output_node}')
    if not any(images.values()):
        raise ComfyError('No images supplied')
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

    def _queue(self, wired, client_id=None):
        queued = self._json('/prompt', {'prompt': wired, 'client_id': client_id or str(uuid.uuid4())})
        if queued.get('node_errors'):
            raise ComfyError(f"ComfyUI rejected workflow nodes: {queued['node_errors']}")
        return queued

    def _frame_board_views(self, wired, images, output_dir):
        """Segment first, then use one crop scale for the complete camera orbit.

        Independent tight crops make a narrow side as wide as the front, which
        contradicts the fixed-orbit model's projection geometry.
        """
        saves = {}
        mask_graph = {}
        def include(node_id):
            if node_id in mask_graph:
                return
            mask_graph[node_id] = wired[node_id]
            for value in wired[node_id].get('inputs', {}).values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    include(value[0])
        for view, name in images.items():
            if not name:
                continue
            prepare = wired[wired['324']['inputs'][view][0]]
            image_id, save_id = f'aida_{view}_frame_mask_image', f'aida_{view}_frame_mask_save'
            wired[image_id] = {'class_type':'MaskToImage', 'inputs':{'mask':prepare['inputs']['masks']}}
            wired[save_id] = {'class_type':'SaveImage', 'inputs':{
                'images':[image_id,0], 'filename_prefix':f'aida/{uuid.uuid4().hex}_{view}_silhouette'}}
            saves[view] = save_id
            include(save_id)
        queued = self._queue(mask_graph)
        prompt_id = queued.get('prompt_id')
        if not prompt_id:
            raise ComfyError(f'ComfyUI rejected segmentation: {queued}')
        deadline = time.monotonic()+self.config.timeout
        masks = {}
        while time.monotonic() < deadline:
            entry = self._json(f'/history/{prompt_id}').get(prompt_id)
            if entry:
                self._raise_on_error(entry)
                for view, save in saves.items():
                    records = entry.get('outputs', {}).get(save, {}).get('images', [])
                    if not records:
                        raise ComfyError(f'Object segmentation returned no {view} mask')
                    path = self._download(records[0], output_dir/f'{view}_mask.png')
                    mask = Image.open(path).convert('L').point(lambda x:255 if x>127 else 0)
                    if mask.getbbox() is None:
                        raise ComfyError(f'Object segmentation returned an empty {view} mask')
                    masks[view] = mask
                break
            time.sleep(self.config.poll_interval)
        if not masks:
            raise ComfyError('Object segmentation timed out')
        self._condition_from_masks(wired, images, masks, output_dir)
        return wired

    def _condition_from_masks(self, wired, images, masks, output_dir):
        boxes = {view:mask.getbbox() for view, mask in masks.items()}
        side = max(max(box[2]-box[0], box[3]-box[1]) for box in boxes.values())
        side = max(1, round(side*1.1))
        for view, mask in masks.items():
            query = urllib.parse.urlencode({'filename':images[view], 'type':'input'})
            photo = Image.open(io.BytesIO(self._request('/view?'+query))).convert('RGB')
            if photo.size != mask.size:
                raise ComfyError(f'{view} mask resolution differs from the original photo')
            composite = Image.composite(photo, Image.new('RGB', photo.size), mask)
            composite.save(output_dir/f'{view}_object.png')
            x0,y0,x1,y1 = boxes[view]
            left, top = round((x0+x1-side)/2), round((y0+y1-side)/2)
            framed = composite.crop((left,top,left+side,top+side)).resize((1024,1024),Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            framed.save(buffer,format='PNG')
            (output_dir/f'{view}_conditioned.png').write_bytes(buffer.getvalue())
            name = self.upload_image(buffer.getvalue(),f'{view}_conditioned.png')
            node_id = f'aida_{view}_conditioned'
            wired[node_id] = {'class_type':'LoadImage','inputs':{'image':name}}
            wired['324']['inputs'][view] = [node_id,0]
        if self.config.camera_focal_px is not None:
            wired['324']['inputs']['fov'] = math.degrees(2*math.atan(side/(2*self.config.camera_focal_px)))
        (output_dir/'framing.json').write_text(json.dumps({
            'common_crop_pixels':side, 'boxes':boxes,
            'fov_degrees':wired['324']['inputs'].get('fov',20.)},indent=2)+'\n')

    def run_pixal3d(self, images: dict, output_path: Path, *, object_regions=None) -> Path:
        """Run the Pixal3D workflow on uploaded views and save the mesh to ``output_path``.

        ``images`` maps view names (front/left/back/right) to ComfyUI image
        names. At least ``front`` is required. Returns the saved mesh path.
        """
        if object_regions:
            mesh, _ = self.run_pixal3d_with_masks(images, Path(output_path).parent, object_regions=object_regions)
            if mesh != Path(output_path):
                Path(output_path).write_bytes(mesh.read_bytes())
            return Path(output_path)
        if not images.get('front'):
            raise ComfyError('Pixal3D requires at least the front view image')
        workflow_path = Path(self.config.workflow_path)
        if not workflow_path.is_file():
            raise ComfyError(f'Workflow template not found: {workflow_path}')
        workflow = json.loads(workflow_path.read_text())
        wired = wire_workflow(workflow, images, self.config, object_regions)

        client_id = str(uuid.uuid4())
        queued = self._queue(wired, client_id)
        prompt_id = queued.get('prompt_id')
        if not prompt_id:
            raise ComfyError(f'ComfyUI rejected the workflow: {queued}')
        return self._wait_for_mesh(prompt_id, output_path)

    def run_pixal3d_with_masks(self, images: dict, output_dir: Path, *, object_regions=None):
        """Generate geometry plus foreground masks in original photo coordinates."""
        if not images.get('front'):
            raise ComfyError('Pixal3D requires a front image')
        output_dir = Path(output_dir)
        wired = wire_workflow(json.loads(Path(self.config.workflow_path).read_text()), images, self.config, object_regions)
        output_dir.mkdir(parents=True, exist_ok=True)
        # Preserve the original mask links before conditioning is replaced with
        # equally framed images; metric fitting stays in raw photo coordinates.
        if wired.get('500', {}).get('class_type') == 'AidaObjectOnlyViews':
            original_masks = {view:['500', 5+VIEW_ORDER.index(view)]
                              for view,name in images.items() if name}
        else:
            try:
                original_masks = {view:wired[wired['324']['inputs'][view][0]]['inputs']['masks']
                                  for view, name in images.items() if name}
            except KeyError as exc:
                raise ComfyError('Automatic scaling requires the bundled mask/crop preparation paths.') from exc
            if object_regions:
                self._frame_board_views(wired, images, output_dir)
        save_nodes = {}
        for view, name in images.items():
            if not name:
                continue
            mask_link = original_masks[view]
            image_id, save_id = f'aida_{view}_mask_image', f'aida_{view}_mask_save'
            wired[image_id] = {'class_type': 'MaskToImage', 'inputs': {'mask': mask_link}}
            wired[save_id] = {'class_type': 'SaveImage', 'inputs': {
                'images': [image_id, 0], 'filename_prefix': f'aida/{uuid.uuid4().hex}_{view}_mask'}}
            save_nodes[view] = save_id
        queued = self._queue(wired)
        if not queued.get('prompt_id'):
            raise ComfyError(f'ComfyUI rejected the workflow: {queued}')
        deadline = time.monotonic() + self.config.timeout
        while time.monotonic() < deadline:
            entry = (self._json(f"/history/{queued['prompt_id']}") or {}).get(queued['prompt_id'])
            if entry:
                self._raise_on_error(entry)
                record = self._find_mesh_record(entry)
                if record:
                    mesh = self._download(record, output_dir / 'unscaled_model.glb')
                    masks = {}
                    for view, save_id in save_nodes.items():
                        records = entry.get('outputs', {}).get(save_id, {}).get('images', [])
                        if not records:
                            raise ComfyError(f'ComfyUI did not return the original-coordinate {view} mask')
                        masks[view] = self._download(records[0], output_dir / f'{view}_mask.png')
                    return mesh, masks
                if entry.get('status', {}).get('completed'):
                    raise ComfyError('ComfyUI completed without a downloadable mesh output.')
            time.sleep(self.config.poll_interval)
        raise ComfyError(f'Pixal3D exceeded {self.config.timeout:.0f}s including foreground masks')

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
                if entry.get('status', {}).get('completed'):
                    raise ComfyError('ComfyUI completed without a downloadable mesh output.')
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
        candidates = []
        for node_id in node_ids:
            node = outputs.get(node_id, {})
            # New ComfyUI versions expose MeshToFile3D through downstream 3D
            # preview/save nodes under `3d`, rather than the legacy `files` key.
            for value in node.get('files', []) + node.get('3d', []):
                if (isinstance(value, dict) and isinstance(value.get('filename'), str)
                        and Path(value['filename']).suffix.lower() in ('.glb', '.gltf', '.obj', '.stl', '.ply')):
                    candidates.append((value.get('type') == 'temp',
                                       node_id != self.config.output_node, value))
        return min(candidates, key=lambda item: item[:2])[2] if candidates else None

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
