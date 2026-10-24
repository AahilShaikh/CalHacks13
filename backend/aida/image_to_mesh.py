"""Image-to-mesh acquisition, implemented on the aida.scan pipeline.

Consumed by the CAD milestone: ``images_to_mesh`` submits captured images to
the external ComfyUI/Pixal3D instance and writes an STL at ``output``. STL
declares no physical units and this function applies no calibration, so the
resulting mesh's coordinates stay in Pixal3D's normalized space — the CAD
milestone consumes it without changing its coordinate scale. For 1:1 metric
output use :func:`aida.scan.run_scan` (or ``aida scan run``), which anchors
the scale with ChArUco plus a caliper measurement and also emits blueprints
and CAD metadata.
"""
import asyncio
from pathlib import Path
from collections.abc import Sequence


async def images_to_mesh(images: Sequence[Path], output: Path) -> Path:
    """Generate an STL at ``output`` from captured images and return its path.

    Raises :class:`aida.scan.ComfyError` when ComfyUI is unreachable or the
    workflow fails. Physical units are unknown; calibrate before modeling.
    """
    if not images:
        raise ValueError('At least one image is required')
    paths = [Path(image) for image in images]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f'Capture image not found: {path}')

    def generate():
        from .scan.comfy import ComfyClient, ComfyConfig
        from .scan.pipeline import VIEW_ORDER
        from .scan.scaling import export_stl

        client = ComfyClient(ComfyConfig())
        # Capture order matches the turntable rig: front, left, back, right.
        uploaded = {}
        for view, path in zip(VIEW_ORDER, paths):
            uploaded[view] = client.upload_image(path.read_bytes(), f'{view}{path.suffix}')
        glb = Path(output).with_suffix('.glb')
        client.run_pixal3d(uploaded, glb)
        export_stl(glb, output)
        glb.unlink(missing_ok=True)
        return Path(output)

    return await asyncio.to_thread(generate)
