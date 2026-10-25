"""Fixed four-view capture and automatic image-to-metric-mesh orchestration."""
import json
import os
from pathlib import Path

import cv2
import numpy as np
from . import blueprints
from .comfy import ComfyClient, ComfyConfig
from .metric import MetricView, fit_metric_mesh
from .foreground import board_object_region
from .pipeline import ScanResult, agent_prompt_for
from .scaling import ScalingError, export_stl


def configured_comfy(workflow=None):
    config = ComfyConfig(host=os.environ.get('COMFY_HOST', '127.0.0.1:8188'), workflow=workflow or 'full')
    if os.environ.get('AIDA_PIXAL3D_WORKFLOW'):
        config.workflow_path = Path(os.environ['AIDA_PIXAL3D_WORKFLOW'])
    return ComfyClient(config)


def reconstruct(captures, calibration, directory, client, progress):
    names = set(captures)
    if 'front' not in names or not names.intersection({'left', 'right'}):
        raise ScalingError('Capture front and at least one side view before reconstruction.')
    directory = Path(directory)
    progress('uploading_images')
    if hasattr(client, 'config'):
        client.config.camera_focal_px = float(calibration.K[0, 0])
    uploaded, regions = {}, {}
    for name, record in captures.items():
        frame = cv2.imread(record['path'])
        if frame is None:
            raise ScalingError(f'Cannot read {name} capture.')
        region = board_object_region(frame)
        if region is not None:
            regions[name] = region
        uploaded[name] = client.upload_image(Path(record['path']).read_bytes(), f'{name}.png')
    (directory / 'object_prompts.json').write_text(json.dumps(regions, indent=2)+'\n')
    progress('generating_mesh_and_masks')
    options = {'object_regions': regions} if regions else {}
    raw_mesh, masks = client.run_pixal3d_with_masks(uploaded, directory, **options)
    views = []
    for name, record in captures.items():
        mask = cv2.imread(str(masks[name]), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ScalingError(f'Cannot read {name} foreground mask.')
        cv2.imwrite(str(directory / f'{name}_raw_mask.png'), mask)
        frame = cv2.imread(record['path'])
        cv2.imwrite(str(directory / f'{name}_object.png'), cv2.bitwise_and(frame, frame, mask=mask))
        cv2.imwrite(str(directory / f'{name}_foreground.png'), mask)
        pose = record['board_pose']
        views.append(MetricView(name, mask, np.asarray(pose['R']), np.asarray(pose['t_mm']),
                                record['measured_angle']))
    progress('fitting_metric_scale')
    metric_mesh = directory / 'metric_model.glb'
    specs = fit_metric_mesh(raw_mesh, views, calibration, metric_mesh, allow_approximate=True)
    specs['workflow'] = getattr(getattr(client, 'config', None), 'workflow', 'custom')
    specs['camera_calibration'] = calibration.to_dict()
    specs['capture_poses'] = {name: {k: v for k, v in record.items() if k != 'path'}
                              for name, record in captures.items()}
    dimensions = directory / 'dimensions.json'
    dimensions.write_text(json.dumps(specs, indent=2)+'\n')
    stl = directory / 'metric_model.stl'
    export_stl(metric_mesh, stl)
    progress('rendering_blueprints')
    blueprint = directory / 'blueprints.png'
    blueprints.render_mesh_projections(metric_mesh, blueprint)
    prompt = scan_agent_prompt(specs)
    (directory / 'agent_prompt.txt').write_text(prompt+'\n')
    # Keep every captured pose/mask and calibration file available for inspection.
    files = {p.name: p for p in directory.iterdir() if p.is_file()}
    return ScanResult(directory, metric_mesh, stl, dimensions, blueprint, specs, prompt,
                      unscaled_mesh=raw_mesh, files=files)


def scan_agent_prompt(specs):
    """What the agent is told about a finished scan whose evidence is in dimensions.json."""
    return (agent_prompt_for(specs).replace('object_dimensions.json', 'dimensions.json') +
            '\nThe reference mesh is ALREADY in millimeters. Do not apply the scale multiplier again. '
            'Calibration and fit evidence are in dimensions.json. Treat internal details as estimates.\n' +
            json.dumps(specs, separators=(',', ':')))


def check_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError('Scan cancelled; no further captures or workspace attachment will occur.')


def run_capture_scan(directory, *, controller, progress=lambda _: None,
                     client=None, cancelled=None, workflow='full'):
    """Capture exactly four views, release the rig, then reconstruct and fit millimeters."""
    def report(stage):
        check_cancelled(cancelled)
        progress(stage)

    report('checking_comfy')
    client = client or configured_comfy(workflow)
    if isinstance(client, ComfyClient):
        client.cancelled = cancelled
    if not client.health():
        raise RuntimeError('ComfyUI is not ready at ' + client.config.base_url)
    if isinstance(client, ComfyClient):
        client.validate_workflow()
    check_cancelled(cancelled)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with controller.capture_session(directory) as rig:
        calibration = rig.calibration
        calibration.save(directory / 'camera.json')
        for view in ('front', 'left', 'back', 'right'):
            report(f'capturing_{view}')
            rig.capture(view)
        captures = dict(rig.views)
    # Mesh generation can take minutes; it must not reserve the camera or motor.
    check_cancelled(cancelled)
    result = reconstruct(captures, calibration, directory, client, report)
    check_cancelled(cancelled)
    return result
