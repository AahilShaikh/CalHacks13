"""End-to-end scan pipeline: images -> metric mesh -> CAD handoff artifacts.

Orchestrates the modular pieces:

1. ChArUco ground-plane detection on the front photo (recorded as metadata).
2. Pixal3D mesh generation through ComfyUI (:mod:`aida.scan.comfy`).
3. 1:1 metric scaling (:mod:`aida.scan.scaling`).
4. Orthographic blueprints (:mod:`aida.scan.blueprints`).

Every step reports progress through an optional callback so the CLI, the HTTP
API, and tests can all drive the same code. ``run_scan`` is synchronous and
blocking (generation takes minutes); async callers wrap it in a worker thread.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import blueprints, charuco, scaling
from .comfy import ComfyClient, ComfyConfig
from .foreground import board_object_region

VIEW_ORDER = ('front', 'left', 'back', 'right')


@dataclass
class ScanResult:
    """Artifacts of one scan job, all inside the job directory."""
    directory: Path
    metric_mesh: Path
    metric_stl: Path
    dimensions_json: Path
    blueprint: Path
    cad_specs: dict
    agent_prompt: str
    unscaled_mesh: Path | None = None
    calibration: dict | None = None
    files: dict = field(default_factory=dict)


def agent_prompt_for(cad_specs: dict) -> str:
    """Build the ready-to-paste prompt handed to the CAD agent (build123d-mcp)."""
    box = cad_specs['bounding_box_mm']
    warning = cad_specs.get('registration', {}).get('warning')
    if warning:
        return (
            'Build a parametric CAD approximation of this object using build123d. '
            f'{warning} Estimated bounding dimensions: '
            f"{box['width_x']}mm (W) x {box['depth_y']}mm (D) x {box['height_z']}mm (H). "
            'Base sits at Z=0. Inspect the photos, orthographic blueprints, and dimensions JSON '
            '(object_dimensions.json) before modeling. Preserve the reported fit evidence and '
            'tell the user these dimensions need physical verification before a precision fit.'
        )
    return (
        'Build a 1:1 parametric CAD model of this object using build123d. '
        f"Bounding dimensions: {box['width_x']}mm (W) x {box['depth_y']}mm (D) x "
        f"{box['height_z']}mm (H). Base sits at Z=0. "
        'Inspect the provided orthographic blueprints and dimensions JSON '
        '(object_dimensions.json) before modeling, then verify with measure.'
    )


def run_scan(images, output_dir, *, known_height_mm=None, known_base_dia_mm=None,
             scale_factor=None, client=None, progress=None, workflow=None):
    """Run the full image-to-metric-mesh pipeline and return a :class:`ScanResult`.

    ``images`` maps view names (front/left/back/right) to image bytes or paths;
    ``front`` is required. The scale anchor priority matches
    :func:`aida.scan.scaling.scale_mesh`: explicit ``scale_factor``, then
    ``known_height_mm``, then ``known_base_dia_mm``. Without any anchor the job
    fails rather than claiming incorrect millimeter dimensions.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def report(stage):
        if progress is not None:
            progress(stage)

    def source_bytes(view):
        value = images[view]
        return value if isinstance(value, bytes) else Path(value).read_bytes()

    if not images.get('front'):
        raise ValueError('A front image is required')

    # 1. ChArUco ground plane from the front photo (metadata; not fatal alone).
    report('calibrating')
    front_path = output_dir / 'front_raw.png'
    front_path.write_bytes(source_bytes('front'))
    calibration = None
    calibration_error = None
    try:
        calibration = charuco.detect_charuco_ground_plane(front_path)
    except (charuco.CalibrationError, FileNotFoundError) as exc:
        calibration_error = str(exc)

    # Fail before spending GPU time when no metric anchor exists: ChArUco
    # detection alone cannot size a unitless mesh.
    if (known_height_mm is None and known_base_dia_mm is None and scale_factor is None):
        raise scaling.ScalingError(
            'No scale anchor provided (known_height_mm, known_base_dia_mm, or '
            'scale_factor). ChArUco detection alone cannot size a unitless mesh: '
            + (f'front-photo calibration also failed: {calibration_error}'
               if calibration_error else
               f"front photo reports {calibration['px_per_mm_ground']:.2f} px/mm on the "
               'ground plane; supply a caliper measurement to anchor the mesh.'))

    # 2. Pixal3D mesh generation through external ComfyUI.
    report('generating_mesh')
    client = client or ComfyClient(ComfyConfig(workflow=workflow or 'full'))
    uploaded, regions = {}, {}
    for view in VIEW_ORDER:
        if images.get(view):
            data = source_bytes(view)
            if view != 'front':
                (output_dir / f'{view}_raw.png').write_bytes(data)
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise scaling.ScalingError(f'Cannot read {view} image.')
            region = board_object_region(frame)
            if region is not None:
                regions[view] = region
            uploaded[view] = client.upload_image(data, f'{view}.png')
    unscaled_mesh = output_dir / 'unscaled_model.glb'
    (output_dir / 'object_prompts.json').write_text(json.dumps(regions, indent=2)+'\n')
    client.run_pixal3d(uploaded, unscaled_mesh, **({'object_regions': regions} if regions else {}))

    # 3. Scale to 1:1 metric millimeters.
    report('scaling')
    metric_mesh = output_dir / 'metric_model.glb'
    dimensions_json = output_dir / 'dimensions.json'
    cad_specs = scaling.scale_mesh(
        unscaled_mesh, metric_mesh, dimensions_json,
        target_height_mm=known_height_mm,
        target_base_dia_mm=known_base_dia_mm,
        scale_factor=scale_factor)

    report('exporting_stl')
    metric_stl = output_dir / 'metric_model.stl'
    scaling.export_stl(metric_mesh, metric_stl)

    # 4. Orthographic blueprints for the CAD agent.
    report('rendering_blueprints')
    blueprint = output_dir / 'blueprints.png'
    blueprints.render_mesh_projections(metric_mesh, blueprint)

    if calibration is not None:
        cad_specs['charuco_calibration'] = {
            'px_per_mm_ground': round(calibration['px_per_mm_ground'], 3),
            'camera_distance_mm': round(calibration['camera_distance_mm'], 1),
            'num_corners_detected': calibration['num_corners_detected'],
        }
    elif calibration_error:
        cad_specs['charuco_calibration'] = {'error': calibration_error}
    cad_specs['workflow'] = getattr(getattr(client, 'config', None), 'workflow', 'custom')
    dimensions_json.write_text(json.dumps(cad_specs, indent=2))
    prompt = agent_prompt_for(cad_specs)
    (output_dir / 'agent_prompt.txt').write_text(prompt + '\n')

    return ScanResult(
        directory=output_dir,
        metric_mesh=metric_mesh,
        metric_stl=metric_stl,
        dimensions_json=dimensions_json,
        blueprint=blueprint,
        cad_specs=cad_specs,
        agent_prompt=prompt,
        unscaled_mesh=unscaled_mesh,
        calibration=calibration,
        files={
            'metric_model.glb': metric_mesh,
            'metric_model.stl': metric_stl,
            'dimensions.json': dimensions_json,
            'object_prompts.json': output_dir / 'object_prompts.json',
            'blueprints.png': blueprint,
            'agent_prompt.txt': output_dir / 'agent_prompt.txt',
            'unscaled_model.glb': unscaled_mesh,
            **{p.name: p for p in output_dir.glob('*.png')},
        },
    )
