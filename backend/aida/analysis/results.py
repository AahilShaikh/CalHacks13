"""Common scalar report and vertex-coloured GLB outputs for every solver."""
from pathlib import Path

import numpy as np

from .settings import FeaSettings


def colours(values, maximum):
    stops = np.array([[38, 89, 217], [34, 211, 238], [250, 204, 21], [239, 68, 68]])
    t = np.clip(values / max(maximum, 1e-30), 0, 1) * 3
    rgb = np.column_stack([np.interp(t, np.arange(4), stops[:, i]) for i in range(3)])
    return np.column_stack([rgb, np.full(len(rgb), 255)]).round().astype(np.uint8)


def write_results(*, nodes, triangles, solution, bounds, cad_volume, analysis_volume,
                  elements: int, settings: FeaSettings, output: Path, metadata: dict,
                  warnings: list[str]) -> dict:
    import trimesh

    displacement = solution['displacement']
    magnitude = np.linalg.norm(displacement, axis=1)
    maximum = float(magnitude.max())
    extents = np.diff(bounds, axis=0)[0]
    scale = float(max(1, min(100_000, np.linalg.norm(extents) * 0.12 / max(maximum, 1e-30))))
    if cad_volume <= 1e-12:
        raise ValueError('The CAD solid has no measurable volume.')
    volume_error = abs(analysis_volume / cad_volume - 1)
    peak = solution['max_stress']
    if not all(np.isfinite(v).all() for v in [displacement, solution['stress'], peak, solution['reaction']]):
        raise ValueError('The structural solve produced nonfinite results.')
    output.mkdir(parents=True, exist_ok=True)
    for name, vertices, values, limit in [
        ('stress', nodes, solution['stress'], peak),
        ('displacement', nodes + displacement * scale, magnitude, maximum),
    ]:
        surface = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False,
                                 vertex_colors=colours(values, limit))
        surface.remove_unreferenced_vertices()
        surface.export(str(output / f'{name}.glb'), file_type='glb')
    warnings = [*warnings,
        'Linear, isotropic, small-strain elasticity; no contact, plasticity, buckling, fatigue or print anisotropy.',
        'Clamped-edge stress peaks depend on mesh resolution. Surface stress colours are nodally averaged; peak stress is unaveraged.',
        'Material values are assumptions; enter properties for your actual material.',
    ]
    if volume_error > 0.15:
        warnings.append('The analysis volume differs from CAD by over 15%. Increase resolution before interpreting results.')
    if maximum / min(extents) > 0.05:
        warnings.append('Displacement exceeds 5% of the smallest model dimension; the small-deformation assumption may be invalid.')
    if peak >= settings.yield_strength_mpa:
        warnings.append('Estimated stress exceeds the entered yield strength; this elastic model does not simulate yielding.')
    return {
        **metadata, 'units': {'length': 'mm', 'force': 'N', 'stress': 'MPa'},
        'settings': settings.model_dump(), 'elements': elements, 'nodes': len(nodes),
        'cad_volume_mm3': float(cad_volume), 'analysis_volume_mm3': float(analysis_volume),
        'volume_error_fraction': float(volume_error), 'max_von_mises_mpa': peak,
        'max_displacement_mm': maximum, 'yield_ratio': float(settings.yield_strength_mpa / peak) if peak > 0 else None,
        'deformation_scale': scale, 'relative_residual': solution['residual'],
        'reaction_force_n': solution['reaction'].tolist(), 'force_vector_n': solution['force_vector'].tolist(),
        'fixed_center_mm': nodes[solution['fixed_nodes']].mean(axis=0).tolist(),
        'load_center_mm': nodes[solution['load_nodes']].mean(axis=0).tolist(),
        'bounds_mm': bounds.tolist(), 'warnings': warnings,
    }
