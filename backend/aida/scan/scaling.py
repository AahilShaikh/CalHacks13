"""Scale a unitless Pixal3D mesh to 1:1 metric millimeters.

Ported from ``calibrate_and_scale_mesh.py``. The scale multiplier comes from
one of three anchors: an explicit ``scale_factor``, a measured total height
(``target_height_mm``), or a measured base diameter (``target_base_dia_mm``,
approximated as the mean of the X and Y extents). After scaling the mesh is
re-centered on X/Y with its base resting on Z=0 (the turntable plane).

No anchor means no metric scale: callers must supply one or the result would
silently claim wrong millimeter dimensions.
"""
import json
import trimesh


class ScalingError(ValueError):
    """Raised when no scale anchor is supplied or the mesh cannot be scaled."""


def load_mesh(mesh_path):
    mesh = trimesh.load(str(mesh_path), force='mesh')
    if mesh.is_empty or len(mesh.vertices) == 0:
        raise ScalingError(f'Mesh has no geometry: {mesh_path}')
    return mesh


def scale_factor_for(extents, target_height_mm=None, target_base_dia_mm=None,
                     scale_factor=None):
    """Return the uniform scale multiplier for the given anchor choice."""
    initial_w, initial_d, initial_h = (float(v) for v in extents)
    if scale_factor is not None:
        if scale_factor <= 0:
            raise ScalingError('scale_factor must be positive')
        return float(scale_factor)
    if target_height_mm is not None:
        if target_height_mm <= 0 or initial_h <= 0:
            raise ScalingError('target height and mesh height must be positive')
        return float(target_height_mm) / initial_h
    if target_base_dia_mm is not None:
        base_dia_norm = (initial_w + initial_d) / 2.0
        if target_base_dia_mm <= 0 or base_dia_norm <= 0:
            raise ScalingError('target base diameter and mesh extents must be positive')
        return float(target_base_dia_mm) / base_dia_norm
    raise ScalingError(
        'Provide a scale anchor: target_height_mm, target_base_dia_mm, or scale_factor')


def scale_mesh(mesh_path, output_mesh_path, output_json_path=None,
               target_height_mm=None, target_base_dia_mm=None, scale_factor=None):
    """Scale ``mesh_path`` into millimeters, export it, and return CAD metadata.

    The metadata JSON (bounding box in mm, coordinate frame, volume, and
    recommended nominal CAD dimensions) is written to ``output_json_path`` when
    given — it is the structured handoff for the CAD agent.
    """
    mesh = load_mesh(mesh_path)
    S = scale_factor_for(mesh.extents, target_height_mm, target_base_dia_mm, scale_factor)

    mesh.apply_scale(S)
    # Re-center: base footprint centered on X/Y, bottom surface on Z = 0.
    mesh.apply_translation([
        -(mesh.bounds[0][0] + mesh.bounds[1][0]) / 2.0,
        -(mesh.bounds[0][1] + mesh.bounds[1][1]) / 2.0,
        -mesh.bounds[0][2],
    ])

    final_extents = [float(v) for v in mesh.extents]
    mesh.export(str(output_mesh_path))

    cad_metadata = {
        'unit': 'millimeter (mm)',
        'scale_multiplier_applied': float(S),
        'bounding_box_mm': {
            'width_x': round(final_extents[0], 2),
            'depth_y': round(final_extents[1], 2),
            'height_z': round(final_extents[2], 2),
        },
        'coordinate_frame': {
            'origin': 'Center of base footprint resting at Z=0',
            'ground_plane': 'Z=0 (turntable contact surface)',
        },
        'volume_mm3': round(float(mesh.volume), 1) if mesh.is_volume else 'non-manifold/open surface',
        'recommended_nominal_cad_dimensions': {
            'overall_height_mm': round(final_extents[2], 1),
            'estimated_base_diameter_mm': round((final_extents[0] + final_extents[1]) / 2.0, 1),
        },
    }
    if output_json_path is not None:
        with open(output_json_path, 'w') as out:
            json.dump(cad_metadata, out, indent=2)
    return cad_metadata


def export_stl(mesh_path, output_stl_path):
    """Export a mesh copy as binary STL (the format the Aida CAD CLI accepts)."""
    mesh = load_mesh(mesh_path)
    mesh.export(str(output_stl_path), file_type='stl')
    return str(output_stl_path)
