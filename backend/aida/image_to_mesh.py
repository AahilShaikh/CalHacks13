"""Partner-owned image-to-mesh integration.

Implement the ComfyUI / image-to-3D pipeline here. Keep its dependencies separate
from agent.py and cad.py so acquisition and CAD work can proceed in parallel.
The first CAD milestone consumes STL without changing its coordinate scale.
"""
from pathlib import Path
from collections.abc import Sequence


async def images_to_mesh(images: Sequence[Path], output: Path) -> Path:
    """Generate an STL at ``output`` from captured images and return its path.

    TODO: submit images to ComfyUI, wait for completion, and save/export STL.
    Do not claim physical units unless the capture pipeline establishes scale.
    """
    raise NotImplementedError('Image-to-mesh pipeline is ready for partner implementation')
