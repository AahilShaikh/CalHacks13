"""Slice a reference mesh and fit CAD primitives to the section loops.

Section loops are reported in the slice plane's 2D coordinates. For axis
slices those are the two remaining world axes (z -> X,Y; x -> Y,Z; y -> X,Z)
and match build123d's Plane.XY / Plane.YZ / Plane.XZ local coordinates, so a
fitted primitive can be placed with the returned ``plane`` expression as is.
Fits are evidence, not ground truth: each one reports its worst deviation.
"""
import argparse
import json
import math

import numpy as np

AXES = {'x': np.array([1.0, 0, 0]), 'y': np.array([0, 1.0, 0]), 'z': np.array([0, 0, 1.0])}


class ProbeError(ValueError):
    """The requested slice cannot be computed as specified."""


# ---------------------------------------------------------------- geometry

def plane_basis(axis=None, normal=None, offset=0.0):
    """Return (normal, u, v, labels, build123d plane expression) for a slice plane."""
    if axis is not None:
        axis = axis.lower()
        if axis == 'z':
            return AXES['z'], AXES['x'], AXES['y'], ('X', 'Y'), f'Plane.XY.offset({_r(offset)})'
        if axis == 'x':
            return AXES['x'], AXES['y'], AXES['z'], ('Y', 'Z'), f'Plane.YZ.offset({_r(offset)})'
        if axis == 'y':
            # Plane.XZ's normal is -Y, so a +Y offset is a negative plane offset.
            return AXES['y'], AXES['x'], AXES['z'], ('X', 'Z'), f'Plane.XZ.offset({_r(-offset)})'
        raise ProbeError('axis must be x, y or z')
    n = np.asarray(normal, float)
    if n.shape != (3,) or not np.all(np.isfinite(n)) or np.linalg.norm(n) < 1e-12:
        raise ProbeError('normal must be three finite numbers, not all zero')
    n = n / np.linalg.norm(n)
    ref = AXES['x'] if abs(n[0]) < 0.9 else AXES['y']
    u = ref - ref.dot(n) * n
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    origin = n * offset
    expr = (f'Plane(origin={_vec(origin)}, x_dir={_vec(u)}, z_dir={_vec(n)})')
    return n, u, v, ('u', 'v'), expr


def _r(value, digits=4):
    value = float(value)
    text = f'{value:.{digits}f}'.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def _vec(values, digits=4):
    return '(' + ', '.join(_r(v, digits) for v in values) + ')'


def chain_segments(segments, weld):
    """Join 2D segments (k, 2, 2) into polylines; return [(points, closed)]."""
    coords, grid = [], {}

    def node(point):
        key = (int(math.floor(point[0] / weld)), int(math.floor(point[1] / weld)))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in grid.get((key[0] + dx, key[1] + dy), ()):
                    if abs(coords[index][0] - point[0]) <= weld and abs(coords[index][1] - point[1]) <= weld:
                        return index
        coords.append((float(point[0]), float(point[1])))
        grid.setdefault(key, []).append(len(coords) - 1)
        return len(coords) - 1

    edges, seen = [], set()
    for a, b in segments:
        ia, ib = node(a), node(b)
        key = (min(ia, ib), max(ia, ib))
        if ia != ib and key not in seen:  # coplanar facets can repeat a segment
            seen.add(key)
            edges.append((ia, ib))
    adjacency = {}
    for index, (a, b) in enumerate(edges):
        adjacency.setdefault(a, []).append(index)
        adjacency.setdefault(b, []).append(index)
    used = [False] * len(edges)

    def walk(start):
        path, current = [start], start
        while True:
            nxt = next((e for e in adjacency[current] if not used[e]), None)
            if nxt is None:
                return path, False
            used[nxt] = True
            a, b = edges[nxt]
            current = b if a == current else a
            if current == start:
                return path, True
            path.append(current)

    chains = []
    starts = [n for n, e in adjacency.items() if len(e) % 2] + list(adjacency)
    for start in starts:
        while any(not used[e] for e in adjacency[start]):
            path, closed = walk(start)
            if len(path) >= 2:
                chains.append((np.array([coords[i] for i in path]), closed))
    return chains


def signed_area(points):
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def centroid(points):
    x, y = points[:, 0], points[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    area = cross.sum() / 2
    if abs(area) < 1e-15:
        return points.mean(axis=0)
    return np.array([((x + np.roll(x, -1)) * cross).sum(), ((y + np.roll(y, -1)) * cross).sum()]) / (6 * area)


def contains(polygon, point):
    x, y = point
    xs, ys = polygon[:, 0], polygon[:, 1]
    xn, yn = np.roll(xs, -1), np.roll(ys, -1)
    crosses = (ys > y) != (yn > y)
    with np.errstate(divide='ignore', invalid='ignore'):
        at = xs + (y - ys) * (xn - xs) / (yn - ys)
    return bool(np.count_nonzero(crosses & (x < at)) % 2)


def _segment_distance(points, a, b):
    ab = b - a
    denom = float(ab.dot(ab))
    if denom < 1e-24:
        return np.linalg.norm(points - a, axis=1)
    t = np.clip((points - a) @ ab / denom, 0, 1)
    return np.linalg.norm(points - (a + t[:, None] * ab), axis=1)


def rdp(points, eps):
    """Ramer-Douglas-Peucker on an open polyline; returns kept indices."""
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        d = _segment_distance(points[a + 1:b], points[a], points[b])
        i = int(np.argmax(d))
        if d[i] > eps:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return np.flatnonzero(keep)


def fit_circle(points):
    """Algebraic least-squares circle; returns (center, radius, max_residual)."""
    A = np.column_stack([2 * points, np.ones(len(points))])
    b = (points ** 2).sum(axis=1)
    (cx, cy, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    radius = math.sqrt(max(c + cx * cx + cy * cy, 0.0))
    residual = np.abs(np.linalg.norm(points - (cx, cy), axis=1) - radius)
    return np.array([cx, cy]), radius, float(residual.max())


def convex_hull(points):
    pts = sorted(set(map(tuple, np.round(points, 12))))
    if len(pts) <= 2:
        return np.array(pts)

    def half(sequence):
        out = []
        for p in sequence:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(reversed(pts))
    return np.array(lower[:-1] + upper[:-1])


def min_area_rect(points):
    """Minimum-area enclosing rectangle: (center, width, height, angle_deg)."""
    hull = convex_hull(points)
    best = None
    for i in range(len(hull)):
        edge = hull[(i + 1) % len(hull)] - hull[i]
        if np.linalg.norm(edge) < 1e-12:
            continue
        angle = math.atan2(edge[1], edge[0])
        c, s = math.cos(angle), math.sin(angle)
        local = points @ np.array([[c, -s], [s, c]])
        low, high = local.min(axis=0), local.max(axis=0)
        area = float(np.prod(high - low))
        if best is None or area < best[0]:
            mid = (low + high) / 2
            best = (area, np.array([c * mid[0] - s * mid[1], s * mid[0] + c * mid[1]]),
                    *(high - low), math.degrees(angle))
    _, center, width, height, angle = best
    # Normalise to angle in (-45, 45]; swap sides when rotating by 90 degrees.
    while angle > 45:
        angle -= 90
        width, height = height, width
    while angle <= -45:
        angle += 90
        width, height = height, width
    return center, float(width), float(height), float(angle)


def _to_local(points, center, angle):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return (points - center) @ np.array([[c, -s], [s, c]])


def rounded_rect_deviation(points, center, width, height, angle, radius):
    """Max |signed distance| of points to a rounded rectangle boundary."""
    local = np.abs(_to_local(points, center, angle))
    q = local - (np.array([width, height]) / 2 - radius)
    outside = np.linalg.norm(np.maximum(q, 0), axis=1)
    inside = np.minimum(q.max(axis=1), 0)
    return float(np.abs(outside + inside - radius).max())


# ---------------------------------------------------------------- fitting

def fit_loop(points, tol):
    """Best CAD primitive for one closed loop, simplest first."""
    area = abs(signed_area(points))
    if len(points) >= 6:
        center, radius, residual = fit_circle(points)
        if residual <= tol and radius > tol:
            return {'type': 'circle', 'center': center.tolist(), 'radius': radius,
                    'diameter': 2 * radius, 'max_deviation': residual,
                    'build123d': f'Pos({_r(center[0])}, {_r(center[1])}) * Circle({_r(radius)})'}
    center, width, height, angle = min_area_rect(points)
    deviation = rounded_rect_deviation(points, center, width, height, angle, 0.0)
    placement = f'Pos({_r(center[0])}, {_r(center[1])})' + (
        f' * Rot(0, 0, {_r(angle, 3)})' if abs(angle) > 1e-6 else '')
    if deviation <= tol:
        return {'type': 'rectangle', 'center': center.tolist(), 'width': width, 'height': height,
                'angle_deg': angle, 'max_deviation': deviation,
                'build123d': f'{placement} * Rectangle({_r(width)}, {_r(height)})'}
    deficit = max(width * height - area, 0.0)
    radius = min(math.sqrt(deficit / (4 - math.pi)), min(width, height) / 2)
    if radius > tol:
        deviation = rounded_rect_deviation(points, center, width, height, angle, radius)
        if deviation <= tol:
            if radius >= min(width, height) / 2 - tol:
                long_side, short_side = max(width, height), min(width, height)
                turn = angle + (90 if height > width else 0)
                place = f'Pos({_r(center[0])}, {_r(center[1])})' + (
                    f' * Rot(0, 0, {_r(turn, 3)})' if abs(turn) > 1e-6 else '')
                return {'type': 'slot', 'center': center.tolist(), 'length': long_side,
                        'width': short_side, 'angle_deg': turn, 'max_deviation': deviation,
                        'build123d': f'{place} * SlotOverall({_r(long_side)}, {_r(short_side)})'}
            return {'type': 'rounded_rectangle', 'center': center.tolist(), 'width': width,
                    'height': height, 'corner_radius': radius, 'angle_deg': angle,
                    'max_deviation': deviation,
                    'build123d': f'{placement} * RectangleRounded({_r(width)}, {_r(height)}, {_r(radius)})'}
    return fit_profile(points, tol)


def fit_profile(points, tol):
    """Split a loop into lines and arcs that stay within ``tol`` of the section."""
    start = int(np.argmax(np.linalg.norm(points - points.mean(axis=0), axis=1)))
    pts = np.roll(points, -start, axis=0)
    closed = np.vstack([pts, pts[:1]])
    keep = rdp(closed, tol)
    segments, i = [], 0
    while i < len(keep) - 1:
        best = None
        for j in range(i + 3, len(keep)):
            span = closed[keep[i]:keep[j] + 1]
            directions = np.diff(closed[keep[i:j + 1]], axis=0)
            turns = directions[:-1, 0] * directions[1:, 1] - directions[:-1, 1] * directions[1:, 0]
            if not (np.all(turns > 0) or np.all(turns < 0)) or len(span) < 5:
                break
            center, radius, residual = fit_circle(span)
            if residual > tol or radius > 1e3 * max(np.ptp(points, axis=0)):
                break
            best = (j, center, radius, span)
        if best:
            j, center, radius, span = best
            angles = np.unwrap(np.arctan2(span[:, 1] - center[1], span[:, 0] - center[0]))
            middle = span[len(span) // 2]
            segments.append({'type': 'arc', 'start': closed[keep[i]].tolist(),
                             'mid': middle.tolist(), 'end': closed[keep[j]].tolist(),
                             'center': center.tolist(), 'radius': radius,
                             'sweep_deg': math.degrees(angles[-1] - angles[0])})
            i = j
        else:
            a, b = closed[keep[i]], closed[keep[i + 1]]
            segments.append({'type': 'line', 'start': a.tolist(), 'end': b.tolist(),
                             'length': float(np.linalg.norm(b - a))})
            i += 1
    deviation = _profile_deviation(points, segments)
    edges = []
    for seg in segments:
        s, e = _vec(seg['start']), _vec(seg['end'])
        if s == e:
            continue
        edges.append(f'Line({s}, {e})' if seg['type'] == 'line'
                     else f"ThreePointArc({s}, {_vec(seg['mid'])}, {e})")
    return {'type': 'profile', 'segment_count': len(segments),
            'line_count': sum(s['type'] == 'line' for s in segments),
            'arc_count': sum(s['type'] == 'arc' for s in segments),
            'segments': segments, 'max_deviation': deviation,
            'build123d': 'make_face([' + ', '.join(edges) + '])'}


def _profile_deviation(points, segments):
    best = np.full(len(points), np.inf)
    for seg in segments:
        a, b = np.array(seg['start']), np.array(seg['end'])
        if seg['type'] == 'line':
            d = _segment_distance(points, a, b)
        else:
            # Distance to the full circle is a lower bound that suits a loop check.
            d = np.abs(np.linalg.norm(points - seg['center'], axis=1) - seg['radius'])
            d = np.maximum(d, 0)
            near = np.minimum(np.linalg.norm(points - a, axis=1), np.linalg.norm(points - b, axis=1))
            d = np.where(near < 4 * seg['radius'], d, np.inf)
        best = np.minimum(best, d)
    return float(best.max()) if len(best) else 0.0


# ---------------------------------------------------------------- slicing

def load(path):
    import trimesh
    mesh = trimesh.load(str(path), force='mesh')
    if mesh.is_empty or len(mesh.faces) == 0:
        raise ProbeError(f'Mesh has no triangles: {path}')
    return mesh


def default_tolerance(mesh):
    return max(float(np.linalg.norm(mesh.extents)) * 0.0025, 1e-6)


def slice_mesh(mesh, axis=None, normal=None, at=None, tolerance=None, weld=None, fit=True):
    """Section ``mesh`` with a plane and describe (and fit) each loop."""
    import trimesh

    if (axis is None) == (normal is None):
        raise ProbeError('Give exactly one of axis or normal')
    probe_normal = AXES[axis.lower()] if axis else None
    if axis and axis.lower() not in AXES:
        raise ProbeError('axis must be x, y or z')
    if probe_normal is None:
        probe_normal = plane_basis(normal=normal)[0]
    projection = mesh.vertices @ probe_normal
    low, high = float(projection.min()), float(projection.max())
    offset = float(at) if at is not None else (low + high) / 2
    if not low < offset < high:
        raise ProbeError(f'Slice at {offset:g} is outside the mesh range {low:g} to {high:g}')
    n, u, v, labels, plane_expr = plane_basis(axis, normal, offset)
    tol = float(tolerance) if tolerance else default_tolerance(mesh)
    weld = float(weld) if weld else tol * 0.02
    segments = trimesh.intersections.mesh_plane(mesh, plane_normal=n, plane_origin=n * offset)
    if len(segments) == 0:
        raise ProbeError(f'Slice at {offset:g} produced no section; try a slightly different position')
    flat = np.stack([segments @ u, segments @ v], axis=-1)
    chains = [(c, ok or (len(c) >= 3 and np.linalg.norm(c[0] - c[-1]) <= tol))
              for c, ok in chain_segments(flat, weld)]
    closed = [c for c, ok in chains if ok and len(c) >= 3]
    open_chains = [c for c, ok in chains if not ok]
    min_area = (tol * 2) ** 2
    loops = [c for c in closed if abs(signed_area(c)) >= min_area]
    dropped = len(closed) - len(loops)
    loops.sort(key=lambda c: -abs(signed_area(c)))
    records = []
    for index, loop in enumerate(loops):
        parents = [j for j, other in enumerate(loops) if j != index
                   and abs(signed_area(other)) > abs(signed_area(loop)) and contains(other, loop[0])]
        depth = len(parents)
        parent = min(parents, key=lambda j: abs(signed_area(loops[j]))) if parents else None
        low2, high2 = loop.min(axis=0), loop.max(axis=0)
        record = {'id': f'L{index}', 'role': 'outer' if depth % 2 == 0 else 'hole',
                  'parent': f'L{parent}' if parent is not None else None,
                  'area': abs(signed_area(loop)),
                  'perimeter': float(np.linalg.norm(np.diff(np.vstack([loop, loop[:1]]), axis=0), axis=1).sum()),
                  'centroid': centroid(loop).tolist(), 'bbox_min': low2.tolist(),
                  'bbox_max': high2.tolist(), 'size': (high2 - low2).tolist(),
                  'point_count': len(loop)}
        if fit:
            record['fit'] = fit_loop(loop, tol)
        records.append(record)
    material = sum(r['area'] * (1 if r['role'] == 'outer' else -1) for r in records)
    result = {
        'plane': {'axis': axis.lower() if axis else None, 'normal': n.tolist(), 'offset': offset,
                  'u_axis': u.tolist(), 'v_axis': v.tolist(), 'coordinates': list(labels),
                  'build123d_plane': plane_expr},
        'tolerance': tol, 'loop_count': len(records),
        'outer_count': sum(r['role'] == 'outer' for r in records),
        'hole_count': sum(r['role'] == 'hole' for r in records),
        'material_area': material, 'loops': records,
        'dropped_tiny_loops': dropped, 'open_chains': len(open_chains),
    }
    if open_chains:
        result['warning'] = ('Section has open chains: the mesh is not watertight here. '
                             'Fitted loops may be incomplete.')
    if fit and records:
        result['build123d'] = _section_snippet(records, plane_expr)
    result['_points'] = [loop.tolist() for loop in loops]
    return result


def _section_snippet(records, plane_expr):
    lines = [f'plane = {plane_expr}']
    for r in records:
        lines.append(f"{r['id']} = {r['fit']['build123d']}")
    shapes = []
    for r in records:
        if r['role'] != 'outer':
            continue
        holes = [h['id'] for h in records if h['parent'] == r['id'] and h['role'] == 'hole']
        shapes.append(' - '.join([r['id'], *holes]))
    lines.append('section = plane * (' + ' + '.join(f'({s})' for s in shapes) + ')')
    return '\n'.join(lines)


def slice_series(mesh, axis, count=12, start=None, stop=None, tolerance=None, refine=True):
    """Slice along an axis and report where the cross-section changes."""
    if axis.lower() not in AXES:
        raise ProbeError('axis must be x, y or z')
    if not 2 <= int(count) <= 60:
        raise ProbeError('count must be between 2 and 60')
    n = AXES[axis.lower()]
    projection = mesh.vertices @ n
    low, high = float(projection.min()), float(projection.max())
    inset = (high - low) * 0.005
    start = low + inset if start is None else float(start)
    stop = high - inset if stop is None else float(stop)
    if not low < start < stop < high:
        raise ProbeError(f'Need {low:g} < start < stop < {high:g}')
    tol = float(tolerance) if tolerance else default_tolerance(mesh)

    def summary(position):
        try:
            section = slice_mesh(mesh, axis=axis, at=position, tolerance=tol, fit=False)
        except ProbeError:
            return {'at': position, 'loop_count': 0, 'outer_count': 0, 'hole_count': 0,
                    'material_area': 0.0, 'bbox_min': None, 'bbox_max': None}
        loops = section['loops']
        outers = [l for l in loops if l['role'] == 'outer']
        out = {'at': position, 'loop_count': len(loops), 'outer_count': len(outers),
               'hole_count': section['hole_count'], 'material_area': section['material_area'],
               'bbox_min': np.min([l['bbox_min'] for l in outers], axis=0).tolist() if outers else None,
               'bbox_max': np.max([l['bbox_max'] for l in outers], axis=0).tolist() if outers else None}
        if outers:
            out['largest'] = fit_loop(np.array(section['_points'][0]), tol)['type']
        return out

    def same(a, b):
        if (a['outer_count'], a['hole_count']) != (b['outer_count'], b['hole_count']):
            return False
        area = max(abs(a['material_area']), abs(b['material_area']), 1e-12)
        if abs(a['material_area'] - b['material_area']) / area > 0.02:
            return False
        if a['bbox_min'] is None or b['bbox_min'] is None:
            return a['bbox_min'] is b['bbox_min']
        return (np.abs(np.subtract(a['bbox_min'], b['bbox_min'])).max() <= 2 * tol
                and np.abs(np.subtract(a['bbox_max'], b['bbox_max'])).max() <= 2 * tol)

    samples = [summary(p) for p in np.linspace(start, stop, int(count))]
    transitions = []
    for a, b in zip(samples, samples[1:]):
        if same(a, b):
            continue
        lo, hi = a, b
        if refine:
            for _ in range(12):
                if hi['at'] - lo['at'] <= tol:
                    break
                mid = summary((lo['at'] + hi['at']) / 2)
                if same(lo, mid):
                    lo = mid
                else:
                    hi = mid
        transitions.append({'between': [lo['at'], hi['at']], 'before': _brief(lo), 'after': _brief(hi)})
    segments, begin = [], samples[0]
    for a, b in zip(samples, samples[1:]):
        if not same(a, b):
            segments.append({'from': begin['at'], 'to': a['at'], 'profile': _brief(begin)})
            begin = b
    segments.append({'from': begin['at'], 'to': samples[-1]['at'], 'profile': _brief(begin)})
    return {'axis': axis.lower(), 'range': [low, high], 'tolerance': tol,
            'samples': [_brief(s) for s in samples], 'constant_segments': segments,
            'transitions': transitions,
            'note': 'Positions are along the axis; slice near each transition to see what changes.'}


def _brief(sample):
    out = {k: sample[k] for k in ('at', 'outer_count', 'hole_count')}
    out['material_area'] = round(sample['material_area'], 6)
    if sample.get('bbox_min') is not None:
        out['bbox_min'] = [round(v, 6) for v in sample['bbox_min']]
        out['bbox_max'] = [round(v, 6) for v in sample['bbox_max']]
    if 'largest' in sample:
        out['largest_loop'] = sample['largest']
    return out


# ---------------------------------------------------------------- drawing

def draw_section(result, output, units=None):
    """Plot section loops with fitted primitives and dimensions to a PNG."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    unit = units or 'units'
    fig, ax = plt.subplots(figsize=(9, 7), dpi=100, facecolor='white')
    for record, points in zip(result['loops'], result['_points']):
        loop = np.vstack([points, points[:1]])
        outer = record['role'] == 'outer'
        ax.fill(loop[:, 0], loop[:, 1], color='#f2a68c' if outer else 'white', zorder=1 if outer else 2)
        ax.plot(loop[:, 0], loop[:, 1], color='#7a1f0d', linewidth=1.2, zorder=3)
        fit = record.get('fit')
        if fit:
            _draw_fit(ax, fit)
        x, y = record['centroid']
        ax.annotate(f"{record['id']} {_describe(record, unit)}", (x, y), fontsize=8, ha='center',
                    va='center', zorder=6, bbox={'boxstyle': 'round,pad=0.2', 'fc': 'white',
                                                 'ec': '#888', 'alpha': 0.85})
    plane = result['plane']
    u, v = plane['coordinates']
    ax.set_xlabel(f'{u} ({unit})')
    ax.set_ylabel(f'{v} ({unit})')
    label = f"{plane['axis'].upper()} = {plane['offset']:.4g}" if plane['axis'] else \
        f"normal {[round(c, 3) for c in plane['normal']]} at {plane['offset']:.4g}"
    ax.set_title(f"Section {label} | {result['outer_count']} outer, {result['hole_count']} holes | "
                 f"fit tol {result['tolerance']:.3g} {unit}", fontsize=10)
    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(True, color='#ccd', linewidth=0.6)
    ax.minorticks_on()
    ax.grid(True, which='minor', color='#eef', linewidth=0.4)
    fig.tight_layout()
    fig.savefig(output, format='png')
    plt.close(fig)


def _describe(record, unit):
    fit = record.get('fit', {})
    kind = fit.get('type')
    if kind == 'circle':
        return f"Ø{fit['diameter']:.4g} @({fit['center'][0]:.4g}, {fit['center'][1]:.4g})"
    if kind == 'rectangle':
        return f"rect {fit['width']:.4g} x {fit['height']:.4g}"
    if kind == 'rounded_rectangle':
        return f"rrect {fit['width']:.4g} x {fit['height']:.4g} r{fit['corner_radius']:.3g}"
    if kind == 'slot':
        return f"slot {fit['length']:.4g} x {fit['width']:.4g}"
    if kind == 'profile':
        return f"profile {fit['line_count']}L {fit['arc_count']}A"
    return f"area {record['area']:.4g}"


def rounded_rect_points(width, height, radius, per_corner=16):
    """Closed outline of a centred rounded rectangle (radius 0 gives sharp corners)."""
    hx, hy = width / 2 - radius, height / 2 - radius
    points = []
    for cx, cy, start in ((hx, hy, 0), (-hx, hy, 90), (-hx, -hy, 180), (hx, -hy, 270)):
        for t in np.linspace(start, start + 90, per_corner if radius > 0 else 1):
            points.append((cx + radius * math.cos(math.radians(t)), cy + radius * math.sin(math.radians(t))))
    points.append(points[0])
    return np.array(points)


def _draw_fit(ax, fit):
    import matplotlib.patches as patches

    style = {'fill': False, 'edgecolor': '#1f5fd1', 'linestyle': '--', 'linewidth': 1.1, 'zorder': 5}
    kind = fit['type']
    if kind == 'circle':
        ax.add_patch(patches.Circle(fit['center'], fit['radius'], **style))
        ax.plot(*fit['center'], marker='+', color='#1f5fd1', zorder=5)
    elif kind in ('rectangle', 'rounded_rectangle', 'slot'):
        if kind == 'slot':
            w, h, r = fit['length'], fit['width'], fit['width'] / 2
        else:
            w, h, r = fit['width'], fit['height'], fit.get('corner_radius', 0)
        outline = rounded_rect_points(w, h, r)
        angle = math.radians(fit['angle_deg'])
        rotation = np.array([[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]])
        outline = outline @ rotation + fit['center']
        ax.plot(outline[:, 0], outline[:, 1], color='#1f5fd1', linestyle='--', linewidth=1.1, zorder=5)
    elif kind == 'profile':
        for seg in fit['segments']:
            if seg['type'] == 'line':
                ax.plot(*zip(seg['start'], seg['end']), color='#1f5fd1', linestyle='--', linewidth=1.1, zorder=5)
            else:
                c, r = seg['center'], seg['radius']
                a0 = math.degrees(math.atan2(seg['start'][1] - c[1], seg['start'][0] - c[0]))
                a1 = a0 + seg['sweep_deg']
                ax.add_patch(patches.Arc(c, 2 * r, 2 * r, theta1=min(a0, a1), theta2=max(a0, a1),
                                         color='#1f5fd1', linestyle='--', linewidth=1.1, zorder=5))
            ax.plot(*seg['start'], marker='o', markersize=3, color='#1f5fd1', zorder=5)


# ---------------------------------------------------------------- entry point

def public(result):
    """Drop internal point lists before returning JSON to the agent."""
    return {k: v for k, v in result.items() if not k.startswith('_')}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Slice a mesh and fit CAD primitives')
    parser.add_argument('command', choices=['slice', 'series'])
    parser.add_argument('mesh')
    parser.add_argument('--spec', default='{}')
    parser.add_argument('--out', help='PNG drawing path (slice only)')
    parser.add_argument('--units')
    args = parser.parse_args(argv)
    spec = json.loads(args.spec)
    mesh = load(args.mesh)
    if args.command == 'slice':
        allowed = {'axis', 'normal', 'at', 'tolerance', 'weld'}
        if set(spec) - allowed:
            raise ProbeError(f'Unknown slice options: {sorted(set(spec) - allowed)}')
        result = slice_mesh(mesh, **spec)
        if args.out:
            draw_section(result, args.out, args.units)
        print(json.dumps(public(result)))
    else:
        allowed = {'axis', 'count', 'start', 'stop', 'tolerance', 'refine'}
        if set(spec) - allowed:
            raise ProbeError(f'Unknown series options: {sorted(set(spec) - allowed)}')
        print(json.dumps(slice_series(mesh, **spec)))


if __name__ == '__main__':
    main()
