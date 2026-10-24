"""Measure how far generated CAD is from the reference mesh.

Both surfaces are sampled evenly by area. Reference samples get a signed
distance to the CAD solid: positive means the reference lies outside the CAD
(the CAD is missing material there), negative means it lies inside (the CAD
has extra material). CAD samples get the distance back to the reference, which
catches CAD features the reference does not have. Samples beyond the
threshold are grouped into regions with a bounding box to zoom or slice into.
"""
import argparse
import json
import math

import numpy as np

MISSING, EXTRA, OFF = 'cad_missing_material', 'cad_extra_material', 'cad_surface_off_reference'


class CompareError(ValueError):
    """The comparison cannot be computed as requested."""


def _load(path, label):
    import trimesh
    mesh = trimesh.load(str(path), force='mesh')
    if mesh.is_empty or len(mesh.faces) == 0:
        raise CompareError(f'{label} has no triangles')
    return mesh


def _vtk_poly(path):
    import vtk
    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(path))
    reader.Update()
    return reader.GetOutput()


def signed_distances(target_path, points):
    """Signed distance from each point to the surface in ``target_path`` (negative inside)."""
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

    implicit = vtk.vtkImplicitPolyDataDistance()
    implicit.SetInput(_vtk_poly(target_path))
    values = vtk.vtkDoubleArray()
    implicit.FunctionValue(numpy_to_vtk(np.ascontiguousarray(points, float), deep=True), values)
    return vtk_to_numpy(values).copy()


def sample(mesh, count, seed=0):
    """Area-weighted surface samples and the face each one lies on."""
    import trimesh
    points, faces = trimesh.sample.sample_surface(mesh, count, seed=seed)
    return np.asarray(points), np.asarray(faces)


def _stats(values, threshold):
    magnitude = np.abs(values)
    return {'mean_abs': float(magnitude.mean()), 'rms': float(np.sqrt((values ** 2).mean())),
            'p95_abs': float(np.percentile(magnitude, 95)), 'max_abs': float(magnitude.max()),
            'share_within_threshold': float((magnitude <= threshold).mean())}


def surface_patches(mesh, smooth_deg=30.0):
    """Label each face with its smooth patch: faces joined by edges below ``smooth_deg``."""
    import trimesh
    edges = mesh.face_adjacency[mesh.face_adjacency_angles < np.radians(smooth_deg)]
    labels = np.empty(len(mesh.faces), np.int64)
    for label, component in enumerate(trimesh.graph.connected_components(
            edges, nodes=np.arange(len(mesh.faces)), min_len=1)):
        labels[component] = label
    return labels


def _blobs(points, cell):
    """Split points into groups that touch on a grid of size ``cell`` (26-neighbourhood)."""
    cells = {}
    for index, key in enumerate(map(tuple, np.floor(points / cell).astype(np.int64))):
        cells.setdefault(key, []).append(index)
    seen, groups = set(), []
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        stack, members = [start], []
        while stack:
            key = stack.pop()
            members.extend(cells[key])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        other = (key[0] + dx, key[1] + dy, key[2] + dz)
                        if other in cells and other not in seen:
                            seen.add(other)
                            stack.append(other)
        groups.append(members)
    return groups


def find_regions(mesh, faces, points, values, threshold, area_per_sample, kind_of, limit=8,
                 smooth_deg=30.0):
    """Connected groups of out-of-threshold samples on ``mesh``, worst first.

    Samples are grouped by smooth surface patch (sharp edges split patches) and
    by deviation sign, then split into spatially separate blobs. A curved wall
    stays one region, a top face and the hole wall meeting it stay separate, and
    a smooth scan still yields one region per local defect.
    """
    mask = np.abs(values) > threshold
    if not mask.any():
        return []
    pts, vals, fcs = points[mask], values[mask], faces[mask]
    patch = surface_patches(mesh, smooth_deg)[fcs]
    cell = float(np.linalg.norm(mesh.extents)) * 0.04
    keyed = {}
    for index, key in enumerate(zip(patch.tolist(), (vals > 0).tolist())):
        keyed.setdefault(key, []).append(index)
    regions = []
    for indices in keyed.values():
        indices = np.array(indices)
        for blob in _blobs(pts[indices], cell):
            members = indices[blob]
            if len(members) < 3:  # isolated samples are noise
                continue
            p, v = pts[members], vals[members]
            normal = mesh.face_normals[fcs[members]].mean(axis=0)
            regions.append({
                'kind': kind_of(v), 'center': p.mean(axis=0).round(6).tolist(),
                'bbox_min': p.min(axis=0).round(6).tolist(), 'bbox_max': p.max(axis=0).round(6).tolist(),
                'surface_normal': (normal / (np.linalg.norm(normal) or 1.0)).round(3).tolist(),
                'max_deviation': float(np.abs(v).max()), 'mean_signed_deviation': float(v.mean()),
                'approx_area': float(len(members) * area_per_sample), 'samples': len(members)})
    regions.sort(key=lambda r: -r['approx_area'] * r['max_deviation'])
    return regions[:limit]


def deviation(reference_path, cad_path, threshold=None, samples=20000, seed=0):
    """Compare a CAD tessellation with the reference mesh; returns a JSON-able report."""
    if not 1000 <= int(samples) <= 200000:
        raise CompareError('samples must be between 1000 and 200000')
    reference, cad = _load(reference_path, 'Reference mesh'), _load(cad_path, 'CAD tessellation')
    diag = float(np.linalg.norm(reference.extents))
    threshold = float(threshold) if threshold else max(diag * 0.005, 1e-6)
    if threshold <= 0:
        raise CompareError('threshold must be positive')
    ref_points, ref_faces = sample(reference, int(samples), seed)
    cad_points, cad_faces = sample(cad, int(samples), seed + 1)
    to_cad = signed_distances(cad_path, ref_points)
    to_ref = signed_distances(reference_path, cad_points)
    signed_back = bool(reference.is_watertight)
    if not signed_back:
        to_ref = np.abs(to_ref)  # an open scan has no inside, so only the magnitude means anything
    ref_regions = find_regions(reference, ref_faces, ref_points, to_cad, threshold,
                               reference.area / len(ref_points),
                               lambda v: MISSING if v.mean() > 0 else EXTRA)
    # A CAD point inside the reference means the CAD surface sits below it: material missing.
    cad_regions = find_regions(cad, cad_faces, cad_points, to_ref, threshold, cad.area / len(cad_points),
                               (lambda v: MISSING if v.mean() < 0 else EXTRA) if signed_back
                               else (lambda v: OFF))
    regions = sorted(ref_regions + cad_regions, key=lambda r: -r['approx_area'] * r['max_deviation'])
    ref_stats, cad_stats = _stats(to_cad, threshold), _stats(to_ref, threshold)
    ref_stats['share_cad_missing_material'] = float((to_cad > threshold).mean())
    ref_stats['share_cad_extra_material'] = float((to_cad < -threshold).mean())
    report = {
        'threshold': threshold,
        'reference_to_cad': ref_stats,
        'cad_to_reference': cad_stats,
        'bbox': {'reference': reference.bounds.round(6).tolist(), 'cad': cad.bounds.round(6).tolist(),
                 'delta_min': (cad.bounds[0] - reference.bounds[0]).round(6).tolist(),
                 'delta_max': (cad.bounds[1] - reference.bounds[1]).round(6).tolist()},
        'regions': regions[:10],
        'verdict': ('within_threshold' if max(ref_stats['p95_abs'], cad_stats['p95_abs']) <= threshold
                    and max(ref_stats['max_abs'], cad_stats['max_abs']) <= 3 * threshold else 'deviates'),
        'note': ('Positive signed deviation = reference outside the CAD (CAD missing material). '
                 'Zoom into a region with view_mesh fit_box=[bbox_min, bbox_max] or slice its center.'),
    }
    if reference.is_watertight and cad.is_watertight:
        report['volume'] = {'reference': float(reference.volume), 'cad': float(cad.volume),
                            'cad_over_reference': float(cad.volume / reference.volume)
                            if reference.volume else None}
    if not signed_back:
        report['note'] += ' The reference is not watertight, so CAD-to-reference distances are unsigned.'
    # Saturate the heatmap at the 95th percentile, but never below a few thresholds.
    report['heatmap_range'] = float(max(4 * threshold, ref_stats['p95_abs']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare CAD with the reference mesh')
    parser.add_argument('reference')
    parser.add_argument('cad')
    parser.add_argument('--spec', default='{}', help='JSON: threshold, samples, view (view_mesh options)')
    parser.add_argument('--out', help='Heatmap PNG path')
    parser.add_argument('--units')
    args = parser.parse_args(argv)
    spec = json.loads(args.spec)
    unknown = set(spec) - {'threshold', 'samples', 'view'}
    if unknown:
        raise CompareError(f'Unknown compare options: {sorted(unknown)}')
    report = deviation(args.reference, args.cad, spec.get('threshold'), spec.get('samples', 20000))
    if args.out:
        from .mesh_render import render
        report['heatmap'] = render(args.reference, args.out, spec.get('view') or {}, args.units,
                                   heatmap={'cad': args.cad, 'range': report['heatmap_range']})
    print(json.dumps(report))


if __name__ == '__main__':
    main()
