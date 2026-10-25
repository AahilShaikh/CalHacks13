"""Render STL triangles directly; avoid the CAD server's lossy STEP round-trip.

Every render reports its exact camera window (focal point, screen axes and
model units per pixel), so a pixel position maps back to model coordinates.
Framing is stateless: each call names the region it shows, so a zoom or pan is
just a render with a different ``center``/``view_width`` or ``fit_box``.
"""
import argparse
import json
import math

WIDTH, HEIGHT = 800, 600
# Direction from the part toward the camera.
PRESETS = {'front': (0, -1, 0), 'rear': (0, 1, 0), 'back': (0, 1, 0), 'side': (1, 0, 0),
           'right': (1, 0, 0), 'left': (-1, 0, 0), 'top': (0, 0, 1), 'bottom': (0, 0, -1),
           'iso': (1, -1, 1)}
AXES = {'x': (1.0, 0.0, 0.0), 'y': (0.0, 1.0, 0.0), 'z': (0.0, 0.0, 1.0)}
AXIS_NAMES = 'XYZ'
SPEC_KEYS = {'direction', 'view_dir', 'azimuth', 'elevation', 'center', 'view_width',
             'fit_box', 'slice_axis', 'slice_normal', 'slice_at', 'slice_flip', 'grid'}


class SpecError(ValueError):
    """The requested view cannot be rendered as specified."""


def _number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SpecError(f'{name} must be a number') from None
    if not math.isfinite(number):
        raise SpecError(f'{name} must be finite')
    return number


def _vector(value, name, nonzero=False):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SpecError(f'{name} must be a list of three numbers')
    vector = tuple(_number(v, name) for v in value)
    if nonzero and math.hypot(*vector) < 1e-12:
        raise SpecError(f'{name} must not be zero')
    return vector


def _unit(v):
    length = math.hypot(*v)
    return tuple(c / length for c in v)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def normalize_spec(spec):
    """Validate a view request and return it with defaults applied."""
    spec = {k: v for k, v in dict(spec or {}).items() if v is not None and v != ''}
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise SpecError(f'Unknown view options: {sorted(unknown)}')
    out = {'azimuth': _number(spec.get('azimuth', 0), 'azimuth'),
           'elevation': _number(spec.get('elevation', 0), 'elevation'),
           'grid': bool(spec.get('grid', True)), 'slice_flip': bool(spec.get('slice_flip', False))}
    if 'direction' in spec:
        direction = str(spec['direction']).lower()
        if direction not in PRESETS:
            raise SpecError(f'direction must be one of {sorted(PRESETS)}')
        out['direction'] = direction
    if 'view_dir' in spec:
        if 'direction' in out:
            raise SpecError('Give direction or view_dir, not both')
        out['view_dir'] = _unit(_vector(spec['view_dir'], 'view_dir', nonzero=True))
    if 'fit_box' in spec:
        if 'center' in spec or 'view_width' in spec:
            raise SpecError('fit_box replaces center and view_width; give one or the other')
        box = spec['fit_box']
        if not isinstance(box, (list, tuple)) or len(box) != 2:
            raise SpecError('fit_box must be [[xmin, ymin, zmin], [xmax, ymax, zmax]]')
        low, high = _vector(box[0], 'fit_box min'), _vector(box[1], 'fit_box max')
        if any(a > b for a, b in zip(low, high)):
            raise SpecError('fit_box min must not exceed max')
        out['fit_box'] = (low, high)
    if 'center' in spec:
        out['center'] = _vector(spec['center'], 'center')
    if 'view_width' in spec:
        width = _number(spec['view_width'], 'view_width')
        if width <= 0:
            raise SpecError('view_width must be positive')
        out['view_width'] = width
    if 'slice_axis' in spec:
        axis = str(spec['slice_axis']).lower()
        if axis not in AXES:
            raise SpecError('slice_axis must be x, y or z')
        if 'slice_normal' in spec:
            raise SpecError('Give slice_axis or slice_normal, not both')
        out['slice_normal'] = AXES[axis]
        out['slice_axis'] = axis
    elif 'slice_normal' in spec:
        out['slice_normal'] = _unit(_vector(spec['slice_normal'], 'slice_normal', nonzero=True))
    if 'slice_at' in spec:
        if 'slice_normal' not in out:
            raise SpecError('slice_at needs slice_axis or slice_normal')
        out['slice_at'] = _number(spec['slice_at'], 'slice_at')
    return out


def nice_step(value):
    """Largest 1/2/5 x 10^k step not above ``value``."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    for mantissa in (5, 2, 1):
        step = mantissa * 10 ** exponent
        if step <= value:
            return step
    return 10 ** exponent


def _fmt(value, step):
    decimals = max(0, -math.floor(math.log10(step))) if step > 0 else 2
    text = f'{value:.{decimals}f}'
    return '0' if text.strip('-0.') == '' else text


def camera_frame(look_from, focal, fit_min, fit_max, azimuth=0.0, elevation=0.0,
                 view_width=None, camera=None):
    """Return the orthographic window for a view; ``camera`` (VTK) is set up when given.

    Without VTK the azimuth/elevation rotation is skipped, so callers that rely on
    it must pass a camera.
    """
    look = _unit(look_from)
    up = (0.0, 1.0, 0.0) if abs(look[2]) > 0.99 else (0.0, 0.0, 1.0)
    corners = [(x, y, z) for x in (fit_min[0], fit_max[0]) for y in (fit_min[1], fit_max[1])
               for z in (fit_min[2], fit_max[2])]
    span = max(math.dist(fit_min, fit_max), 1e-9)
    distance = span * 4 + max(math.dist(focal, c) for c in corners) * 2 + 1
    if camera is not None:
        camera.SetFocalPoint(*focal)
        camera.SetPosition(*[f + v * distance for f, v in zip(focal, look)])
        camera.SetViewUp(*up)
        camera.ParallelProjectionOn()
        camera.Azimuth(azimuth)
        camera.Elevation(elevation)
        camera.OrthogonalizeViewUp()
        projection = tuple(camera.GetDirectionOfProjection())
        up = _unit(tuple(camera.GetViewUp()))
    else:
        projection = tuple(-c for c in look)
        along = _dot(up, look)
        up = _unit(tuple(u - along * l for u, l in zip(up, look)))
    right = _unit(_cross(projection, up))
    aspect = WIDTH / HEIGHT
    if view_width is not None:
        half_height = view_width / aspect / 2
    else:
        hx = max(abs(_dot([c - f for c, f in zip(corner, focal)], right)) for corner in corners)
        hy = max(abs(_dot([c - f for c, f in zip(corner, focal)], up)) for corner in corners)
        half_height = max(hy, hx / aspect, span * 1e-3) * 1.08
    if camera is not None:
        camera.SetParallelScale(half_height)
    visible_height = 2 * half_height
    visible_width = visible_height * aspect
    return {
        'focal_point': [round(c, 6) for c in focal],
        'view_dir': [round(-c, 6) for c in projection],
        'screen_right': [round(c, 6) for c in right],
        'screen_up': [round(c, 6) for c in up],
        'visible_width': visible_width,
        'visible_height': visible_height,
        'units_per_pixel': visible_width / WIDTH,
        'image_size': [WIDTH, HEIGHT],
    }


def _look_from(spec):
    """Camera direction before azimuth/elevation, and a caption for it."""
    if 'view_dir' in spec:
        return spec['view_dir'], f"dir {[round(c, 3) for c in spec['view_dir']]}"
    if 'direction' in spec:
        return PRESETS[spec['direction']], spec['direction']
    if 'slice_normal' in spec:
        normal = spec['slice_normal']
        return (tuple(-n for n in normal) if spec['slice_flip'] else normal), 'facing slice'
    return PRESETS['iso'], 'iso'


def _section_plane(spec, mesh_min, mesh_max, look_from):
    if 'slice_normal' not in spec:
        return None
    normal = spec['slice_normal']
    center = [(a + b) / 2 for a, b in zip(mesh_min, mesh_max)]
    offset = spec.get('slice_at', _dot(center, normal))
    origin = tuple(n * offset for n in normal)
    # Remove the half facing the camera so the cut face is visible; flip inverts.
    removed = normal if _dot(look_from, normal) >= 0 else tuple(-n for n in normal)
    if spec['slice_flip'] and ('view_dir' in spec or 'direction' in spec):
        removed = tuple(-n for n in removed)
    label = (f"{spec['slice_axis']}={offset:.4g}" if 'slice_axis' in spec
             else f'n={[round(n, 3) for n in normal]} at {offset:.4g}')
    return {'normal': normal, 'offset': offset, 'origin': origin, 'removed_side': removed,
            'label': label}


# Signed-distance colours: blue = CAD has extra material, red = CAD is missing material.
DIVERGING = ((0.15, 0.35, 0.85), (1.0, 1.0, 1.0), (0.85, 0.15, 0.12))


def diverging_color(t):
    """Colour for t in [-1, 1] on the blue-white-red deviation scale."""
    t = max(-1.0, min(1.0, t))
    far = DIVERGING[0] if t < 0 else DIVERGING[2]
    return tuple(w + (f - w) * abs(t) for w, f in zip(DIVERGING[1], far))


def _read_stl(vtk, path, label='STL'):
    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(path))
    reader.Update()
    mesh = reader.GetOutput()
    if mesh.GetNumberOfCells() == 0:
        raise ValueError(f'{label} contains no renderable triangles')
    bounds = mesh.GetBounds()
    if not all(math.isfinite(x) for x in bounds):
        raise ValueError(f'{label} contains invalid bounds')
    return mesh


def _add_surface(vtk, renderer, poly, section, color, opacity=1.0, cap_color=None,
                 line_color=None, lut=None):
    """Add a (possibly clipped) surface; return how many section segments it has."""
    surface, cut_cells = poly, 0
    if section:
        plane = vtk.vtkPlane()
        plane.SetOrigin(*section['origin'])
        plane.SetNormal(*section['removed_side'])
        cutter = vtk.vtkCutter()
        cutter.SetCutFunction(plane)
        cutter.SetInputData(poly)
        cutter.Update()
        cut_cells = cutter.GetOutput().GetNumberOfCells()
        clip = vtk.vtkClipPolyData()
        clip.SetInputData(poly)
        clip.SetClipFunction(plane)
        clip.InsideOutOn()  # keep the side opposite the removed side
        clip.Update()
        surface = clip.GetOutput()
        stripper = vtk.vtkStripper()
        stripper.SetInputConnection(cutter.GetOutputPort())
        stripper.JoinContiguousSegmentsOn()
        layers = [(stripper, line_color, True)]
        if cap_color:
            cap = vtk.vtkContourTriangulator()
            cap.SetInputConnection(stripper.GetOutputPort())
            layers.insert(0, (cap, cap_color, False))
        for source, layer_color, lines in layers:
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(source.GetOutputPort())
            mapper.ScalarVisibilityOff()
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*layer_color)
            if lines:
                actor.GetProperty().LightingOff()
                actor.GetProperty().SetLineWidth(2)
            else:
                actor.GetProperty().SetAmbient(.4)
            renderer.AddActor(actor)

    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(surface)
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(normals.GetOutputPort())
    if lut is not None:
        mapper.SetLookupTable(lut)
        mapper.SetScalarRange(*lut.GetTableRange())
        mapper.SetScalarModeToUsePointData()
        mapper.ScalarVisibilityOn()
    else:
        mapper.ScalarVisibilityOff()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*color)
    actor.GetProperty().SetOpacity(opacity)
    actor.GetProperty().SetAmbient(.25)
    actor.GetProperty().SetDiffuse(.75)
    actor.GetProperty().BackfaceCullingOff()
    renderer.AddActor(actor)
    return cut_cells


def _deviation_lut(vtk, limit):
    lut = vtk.vtkLookupTable()
    lut.SetNumberOfTableValues(256)
    lut.SetTableRange(-limit, limit)
    lut.Build()
    for i in range(256):
        lut.SetTableValue(i, *diverging_color(i / 255 * 2 - 1), 1.0)
    return lut


def signed_distance_to(vtk, mesh, target):
    """Point data 'Distance' on ``mesh``: signed distance to ``target`` (negative inside it)."""
    distance = vtk.vtkDistancePolyDataFilter()
    distance.SetInputData(0, mesh)
    distance.SetInputData(1, target)
    distance.SignedDistanceOn()
    distance.ComputeSecondDistanceOff()
    distance.Update()
    output = vtk.vtkPolyData()
    output.DeepCopy(distance.GetOutput())
    output.GetPointData().SetActiveScalars('Distance')
    return output


def _add_edges(vtk, renderer, poly):
    """Dark feature edges (sharp creases and open boundaries) so CAD faces and openings read."""
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(poly)
    edges.SetFeatureAngle(25)
    edges.BoundaryEdgesOn()
    edges.FeatureEdgesOn()
    edges.ManifoldEdgesOff()
    edges.NonManifoldEdgesOff()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(edges.GetOutputPort())
    mapper.ScalarVisibilityOff()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(.15, .16, .19)
    actor.GetProperty().LightingOff()
    actor.GetProperty().SetLineWidth(1.5)
    renderer.AddActor(actor)


# Generated CAD: steel gray with a light orange section cap.
CAD_COLOR = (.66, .71, .78)
CAD_CAP = (.95, .62, .4)


def render(path, output, spec=None, units=None, overlay=None, heatmap=None, style='scan'):
    """Render ``path`` to ``output`` PNG and return the camera/window metadata.

    ``style='cad'`` draws a generated CAD tessellation: steel gray with its feature edges.

    ``overlay`` is a CAD STL drawn opaque green over a translucent reference.
    ``heatmap`` is ``{'cad': stl_path, 'range': limit}``: the reference is coloured by
    signed distance to that CAD surface. Neither is part of the agent's view spec;
    the tool layer supplies them from a tessellated CAD object.
    """
    import vtk

    spec = normalize_spec(spec)
    mesh = _read_stl(vtk, path)
    bounds = mesh.GetBounds()
    mesh_min, mesh_max = tuple(bounds[0::2]), tuple(bounds[1::2])
    if max(b - a for a, b in zip(mesh_min, mesh_max)) <= 0:
        raise ValueError('STL has zero extent')

    vtk.vtkMapper.SetResolveCoincidentTopologyToPolygonOffset()
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1, 1, 1)
    look_from, view_label = _look_from(spec)
    section = _section_plane(spec, mesh_min, mesh_max, look_from)

    reference, lut = mesh, None
    if heatmap:
        cad = _read_stl(vtk, heatmap['cad'], 'CAD tessellation')
        # Colours are per vertex; split long edges so large flat faces and tall
        # walls are sampled, not just their corners.
        sampled = mesh
        if mesh.GetNumberOfCells() < 100_000:  # dense scans are already sampled finely
            subdivide = vtk.vtkAdaptiveSubdivisionFilter()
            subdivide.SetInputData(mesh)
            subdivide.SetMaximumEdgeLength(math.dist(mesh_min, mesh_max) / 50)
            subdivide.SetMaximumNumberOfPasses(10)
            subdivide.Update()
            sampled = subdivide.GetOutput()
        reference = signed_distance_to(vtk, sampled, cad)
        lut = _deviation_lut(vtk, heatmap['range'])
    cad_style = style == 'cad' and not overlay and not heatmap
    cut = _add_surface(vtk, renderer, reference, section, CAD_COLOR if cad_style else (.25, .55, .85),
                       opacity=.3 if overlay else 1.0,
                       cap_color=None if overlay else (CAD_CAP if cad_style else (.93, .42, .3)),
                       line_color=(.6, .05, .05) if overlay else (.45, .08, .04), lut=lut)
    if cad_style and not section:
        _add_edges(vtk, renderer, reference)
    if section and cut == 0:
        raise SpecError(f"Slice {section['label']} misses the mesh; "
                        f'mesh bounds are {list(mesh_min)} to {list(mesh_max)}')
    if overlay:
        cad = _read_stl(vtk, overlay, 'CAD tessellation')
        _add_surface(vtk, renderer, cad, section, (.35, .72, .38), cap_color=(.62, .88, .6),
                     line_color=(.02, .32, .08))
        renderer.SetUseDepthPeeling(1)
        renderer.SetMaximumNumberOfPeels(8)

    if spec['azimuth'] or spec['elevation']:
        view_label += f" az {spec['azimuth']:g} el {spec['elevation']:g}"
    fit_min, fit_max = spec.get('fit_box', (mesh_min, mesh_max))
    focal = spec.get('center', tuple((a + b) / 2 for a, b in zip(fit_min, fit_max)))
    meta = camera_frame(look_from, focal, fit_min, fit_max, spec['azimuth'], spec['elevation'],
                        spec.get('view_width'), renderer.GetActiveCamera())
    renderer.ResetCameraClippingRange()

    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(WIDTH, HEIGHT)
    if overlay:
        window.SetAlphaBitPlanes(1)
        window.SetMultiSamples(0)
    window.AddRenderer(renderer)
    try:
        window.Render()
        capture = vtk.vtkWindowToImageFilter()
        capture.SetInput(window)
        capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(str(output))
        writer.SetInputConnection(capture.GetOutputPort())
        writer.Write()
    finally:
        window.Finalize()

    meta.update({'units': units or 'unknown', 'view': view_label,
                 'mesh_bounds': [list(mesh_min), list(mesh_max)],
                 'pixel_to_world': 'point = focal_point + (px - 400) * units_per_pixel * screen_right'
                                   ' + (300 - py) * units_per_pixel * screen_up'})
    if overlay:
        meta['overlay'] = ('reference mesh: translucent blue, cut outline dark red; '
                           'CAD: opaque green, cut outline dark green')
    if heatmap:
        meta['heatmap'] = {'range': heatmap['range'],
                           'red': 'reference outside the CAD: CAD is missing material',
                           'blue': 'reference inside the CAD: CAD has extra material'}
    if section:
        meta['slice'] = {'normal': list(section['normal']), 'offset': section['offset'],
                         'removed_side': list(section['removed_side']), 'label': section['label']}
    _annotate(output, meta, spec['grid'])
    return meta


def _annotate(output, meta, grid):
    """Draw grid, scale bar, axis triad and a caption; fail if nothing is visible."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    base = Image.open(output).convert('RGB')
    covered = float((np.asarray(base) != 255).any(axis=2).mean())
    meta['mesh_coverage'] = round(covered, 4)
    if covered == 0:
        raise SpecError('Nothing visible in this window; recenter using mesh_bounds '
                        f"{meta['mesh_bounds']}")
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.load_default(size=13)
    upp = meta['units_per_pixel']
    unit = meta['units'] if meta['units'] != 'unknown' else 'units'
    focal = meta['focal_point']
    step = nice_step(meta['visible_width'] / 8)

    if grid:
        for screen, vector in (('x', meta['screen_right']), ('y', meta['screen_up'])):
            k = max(range(3), key=lambda i: abs(vector[i]))
            if abs(vector[k]) < 0.999:
                continue  # oblique axis: coordinates along it are not a single world axis
            sign = 1 if vector[k] > 0 else -1
            half = (WIDTH if screen == 'x' else HEIGHT) / 2 * upp
            low, high = focal[k] - half, focal[k] + half
            value = math.ceil(low / step) * step
            while value <= high:
                offset = (value - focal[k]) * sign / upp
                label = f'{AXIS_NAMES[k]}={_fmt(value, step)}'
                if screen == 'x':
                    px = WIDTH / 2 + offset
                    draw.line([(px, 0), (px, HEIGHT)], fill=(90, 90, 90, 70))
                    draw.text((px + 2, HEIGHT - 16), label, fill=(60, 60, 60, 255), font=font)
                else:
                    py = HEIGHT / 2 - offset
                    draw.line([(0, py), (WIDTH, py)], fill=(90, 90, 90, 70))
                    draw.text((2, py - 15), label, fill=(60, 60, 60, 255), font=font)
                value += step

    bar = nice_step(meta['visible_width'] / 5)
    length = bar / upp
    x1, y = WIDTH - 20, HEIGHT - 30
    x0 = x1 - length
    draw.rectangle([x0 - 6, y - 20, x1 + 6, y + 8], fill=(255, 255, 255, 210))
    draw.line([(x0, y), (x1, y)], fill=(0, 0, 0, 255), width=3)
    for x in (x0, x1):
        draw.line([(x, y - 6), (x, y + 6)], fill=(0, 0, 0, 255), width=2)
    draw.text((x0, y - 19), f'{_fmt(bar, bar)} {unit}', fill=(0, 0, 0, 255), font=font)

    ox, oy = 50, HEIGHT - 70
    toward = meta['view_dir']
    for index, color in enumerate(((200, 30, 30), (20, 150, 40), (30, 60, 210))):
        axis = [0, 0, 0]
        axis[index] = 1
        dx = _dot(axis, meta['screen_right']) * 36
        dy = -_dot(axis, meta['screen_up']) * 36
        if math.hypot(dx, dy) < 6:
            mark = 'out' if _dot(axis, toward) > 0 else 'in'
            draw.text((ox + 6, oy + 6 + 14 * index), f'+{AXIS_NAMES[index]} {mark}',
                      fill=(*color, 255), font=font)
            continue
        draw.line([(ox, oy), (ox + dx, oy + dy)], fill=(*color, 255), width=3)
        draw.text((ox + dx * 1.2 - 4, oy + dy * 1.2 - 7), AXIS_NAMES[index], fill=(*color, 255), font=font)

    if 'heatmap' in meta:
        limit = meta['heatmap']['range']
        top, bottom, left = 70, 330, WIDTH - 34
        draw.rectangle([left - 150, top - 22, WIDTH - 4, bottom + 22], fill=(255, 255, 255, 215))
        for y in range(top, bottom + 1):
            t = 1 - 2 * (y - top) / (bottom - top)
            draw.line([(left, y), (left + 18, y)],
                      fill=tuple(int(255 * c) for c in diverging_color(t)) + (255,))
        for y, text in ((top, f'+{_fmt(limit, limit / 10)} CAD missing'), ((top + bottom) / 2, '0'),
                        (bottom, f'-{_fmt(limit, limit / 10)} CAD extra')):
            draw.text((left - 6, y), text, fill=(0, 0, 0, 255), font=font, anchor='rm')
        draw.text((left + 9, top - 18), unit, fill=(0, 0, 0, 255), font=font, anchor='ma')

    caption = (f"{meta['view']} | window {meta['visible_width']:.4g} x {meta['visible_height']:.4g} {unit}"
               f" | {upp:.3g} {unit}/px")
    if 'slice' in meta:
        caption += f" | slice {meta['slice']['label']}"
    box = draw.textbbox((6, 6), caption, font=font)
    draw.rectangle([box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3], fill=(255, 255, 255, 215))
    draw.text((6, 6), caption, fill=(0, 0, 0, 255), font=font)
    Image.alpha_composite(base.convert('RGBA'), layer).convert('RGB').save(output, format='PNG')


SHEET_VIEWS = ('front', 'right', 'top', 'iso')


def render_sheet(path, output, units=None, style='scan', views=SHEET_VIEWS):
    """Four standard views (front, right, top, iso) in one 2x2 image, each labelled, so a
    model's openings and proportions can be checked in a single look. Returns per-view metadata."""
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    output = Path(output)
    sheet = Image.new('RGB', (WIDTH * 2, HEIGHT * 2), 'white')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=22)
    metas = {}
    for index, view in enumerate(views):
        tile = output.with_name(f'{output.stem}-{view}.png')
        metas[view] = render(path, tile, {'direction': view}, units, style=style)
        x, y = (index % 2) * WIDTH, (index // 2) * HEIGHT
        with Image.open(tile) as image:
            sheet.paste(image.convert('RGB'), (x, y))
        tile.unlink(missing_ok=True)
        draw.rectangle([x, y, x + WIDTH - 1, y + HEIGHT - 1], outline=(200, 200, 205), width=2)
        draw.text((x + WIDTH - 16, y + 12), view.upper(), fill=(194, 65, 12), font=font, anchor='ra')
    sheet.save(output)
    return {'sheet': list(views), 'layout': 'top-left front, top-right right, bottom-left top, bottom-right iso',
            'units': units or 'unknown',
            'views': {v: {k: m[k] for k in ('view', 'visible_width', 'visible_height', 'units_per_pixel')
                          if k in m} for v, m in metas.items()},
            'mesh_bounds': next(iter(metas.values()))['mesh_bounds']}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mesh')
    parser.add_argument('output')
    parser.add_argument('--spec', default='{}', help='JSON view options (see SPEC_KEYS)')
    parser.add_argument('--units')
    parser.add_argument('--overlay', help='CAD STL drawn over a translucent reference')
    parser.add_argument('--style', choices=('scan', 'cad'), default='scan')
    parser.add_argument('--sheet', action='store_true', help='front, right, top and iso views in one image')
    args = parser.parse_args(argv)
    if args.sheet:
        meta = render_sheet(args.mesh, args.output, args.units, style=args.style)
    else:
        meta = render(args.mesh, args.output, json.loads(args.spec), args.units, overlay=args.overlay,
                      style=args.style)
    print(json.dumps(meta))


if __name__ == '__main__':
    main()
