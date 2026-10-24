"""Deviation heatmap for the web viewer: the reference mesh coloured by signed
distance to a generated CAD revision, written as a vertex-coloured GLB.

Same convention and colours as ``compare_to_mesh``: positive distance means the
reference lies outside the CAD (the CAD is missing material, red); negative
means it lies inside (the CAD has extra material, blue).
"""
from pathlib import Path

import numpy as np

from ..mesh_compare import signed_distances
from ..mesh_render import DIVERGING

# Coarse references (CAD-like STLs) have few vertices, so long edges are split
# until colours can vary across a face. Dense scans are already fine enough.
SUBDIVIDE_BELOW_FACES = 60_000
MAX_FACES = 150_000


def _colours(distances, limit):
    """RGBA uint8 per vertex on the blue-white-red scale, saturating at +-limit."""
    t = np.clip(distances / limit, -1.0, 1.0)[:, None]
    white = np.array(DIVERGING[1])
    far = np.where(t < 0, np.array(DIVERGING[0]), np.array(DIVERGING[2]))
    rgb = white + (far - white) * np.abs(t)
    alpha = np.ones((len(rgb), 1))
    return (np.hstack([rgb, alpha]) * 255).round().astype(np.uint8)


def build_heatmap(reference_path: Path, cad_stl_paths: list[Path], glb_path: Path,
                  threshold: float | None = None) -> dict:
    """Write ``glb_path`` and return the colour scale: ``{range, threshold, vertices}``."""
    import trimesh

    reference = trimesh.load(str(reference_path), force='mesh')
    if reference.is_empty or len(reference.faces) == 0:
        raise ValueError('reference mesh has no triangles')
    if not cad_stl_paths:
        raise ValueError('no CAD surfaces to compare against')
    diagonal = float(np.linalg.norm(reference.extents))
    threshold = float(threshold) if threshold else max(diagonal * 0.005, 1e-6)

    if len(reference.faces) < SUBDIVIDE_BELOW_FACES:
        try:
            vertices, faces = trimesh.remesh.subdivide_to_size(
                reference.vertices, reference.faces, max_edge=diagonal / 40, max_iter=8)
            if len(faces) <= MAX_FACES:
                reference = trimesh.Trimesh(vertices, faces, process=False)
        except ValueError:
            pass  # could not reach the edge length in max_iter; colour the original

    # Signed distance to a union of solids is the minimum over the solids.
    distances = np.min([signed_distances(path, reference.vertices) for path in cad_stl_paths], axis=0)
    magnitude = np.abs(distances)
    limit = float(max(4 * threshold, np.percentile(magnitude, 95)))
    reference.visual = trimesh.visual.ColorVisuals(reference, vertex_colors=_colours(distances, limit))
    reference.export(str(glb_path), file_type='glb')
    return {'range': limit, 'threshold': threshold, 'vertices': int(len(reference.vertices)),
            'share_within': float((magnitude <= threshold).mean())}
