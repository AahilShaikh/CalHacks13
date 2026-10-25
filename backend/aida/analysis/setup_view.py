"""Pictures of an FEA setup: the part from two sides with numbered faces, supports and loads.

Rendered with VTK off screen in the worker process; labels and the force arrow are drawn
with Pillow on the projected positions, so they stay legible at any part size.
"""
from pathlib import Path

import numpy as np

VIEW = (560, 440)
GRAY, GREEN, ORANGE = (190, 196, 205), (60, 180, 90), (245, 140, 40)
# Two iso views (Z up): front-right and back-left, so every face is seen at least once.
DIRECTIONS = [np.array([1.0, -1.3, 0.9]), np.array([-1.0, 1.3, 0.9])]


def _palette(face):
    """A distinct, mid-tone colour per face number, for the face listing."""
    hue = (face * 0.618034) % 1
    r, g, b = (np.clip(np.abs((hue * 6 + np.array([0, 4, 2])) % 6 - 3) - 1, 0, 1) * 0.55 + 0.35)
    return tuple(int(c * 255) for c in (r, g, b))


def _render_view(nodes, triangles, colours, direction, size, marks):
    """Render one view; return its pixels, ``marks`` projected to pixels, and the view direction."""
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray

    poly = vtk.vtkPolyData()
    points = vtk.vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(nodes, dtype=float), deep=True))
    poly.SetPoints(points)
    cells = vtk.vtkCellArray()
    cells.SetData(numpy_to_vtkIdTypeArray(np.arange(0, 3 * len(triangles) + 1, 3, dtype=np.int64), deep=True),
                  numpy_to_vtkIdTypeArray(np.ascontiguousarray(triangles, dtype=np.int64).ravel(), deep=True))
    poly.SetPolys(cells)
    rgb = numpy_to_vtk(np.ascontiguousarray(colours, dtype=np.uint8), deep=True)
    rgb.SetName('colours')
    poly.GetCellData().SetScalars(rgb)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(poly)
    mapper.SetColorModeToDirectScalars()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetInterpolationToFlat()
    edges = vtk.vtkFeatureEdges()
    edges.SetInputData(poly)
    edges.BoundaryEdgesOff()
    edges.ManifoldEdgesOff()
    edges.NonManifoldEdgesOff()
    edges.FeatureEdgesOn()
    edges.SetFeatureAngle(30)
    edge_mapper = vtk.vtkPolyDataMapper()
    edge_mapper.SetInputConnection(edges.GetOutputPort())
    edge_mapper.ScalarVisibilityOff()
    edge_actor = vtk.vtkActor()
    edge_actor.SetMapper(edge_mapper)
    edge_actor.GetProperty().SetColor(.15, .15, .17)
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1, 1, 1)
    renderer.AddActor(actor)
    renderer.AddActor(edge_actor)
    center = (nodes.min(axis=0) + nodes.max(axis=0)) / 2
    camera = renderer.GetActiveCamera()
    camera.SetFocalPoint(*center)
    camera.SetPosition(*(center + direction / np.linalg.norm(direction) * np.ptp(nodes, axis=0).max() * 4))
    camera.SetViewUp(0, 0, 1)
    renderer.ResetCamera()
    camera.Zoom(1.05)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(*size)
    window.AddRenderer(renderer)
    try:
        window.Render()
        capture = vtk.vtkWindowToImageFilter()
        capture.SetInput(window)
        capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()
        image = capture.GetOutput()
        width, height, _ = image.GetDimensions()
        from vtk.util.numpy_support import vtk_to_numpy
        pixels = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(height, width, 3)[::-1]

        projected = []
        for point in marks:
            renderer.SetWorldPoint(*point, 1.0)
            renderer.WorldToDisplay()
            x, y, _ = renderer.GetDisplayPoint()
            projected.append((x, height - y))
        return pixels.copy(), np.array(projected).reshape(-1, 2), np.array(camera.GetDirectionOfProjection())
    finally:
        window.Finalize()


def render_setup(path: Path, nodes, triangles, face_of_triangle, faces: list[dict], *,
                 supports=(), loads=(), force_vector=None, title=''):
    """Write a two-view PNG. With supports/loads: gray part, green supports, orange loads and the
    force arrow. Without: every face in its own colour. Faces are labelled with their numbers."""
    from PIL import Image, ImageDraw, ImageFont

    supports, loads = set(supports), set(loads)
    setup = bool(supports or loads)
    colour_of = {f['id']: (GREEN if f['id'] in supports else ORANGE if f['id'] in loads else GRAY)
                 if setup else _palette(f['id']) for f in faces}
    colours = np.array([colour_of[f] for f in face_of_triangle])
    canvas = Image.new('RGB', (VIEW[0] * 2, VIEW[1] + 56), 'white')
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('arial.ttf', 15)
        small = ImageFont.truetype('arial.ttf', 13)
    except OSError:
        font = small = ImageFont.load_default()
    extent = np.ptp(nodes, axis=0).max()
    unit = start = None
    points = [f['on_surface_mm'] for f in faces]
    if force_vector is not None and loads:
        start = np.mean([f['on_surface_mm'] for f in faces if f['id'] in loads], axis=0)
        unit = np.asarray(force_vector, float) / np.linalg.norm(force_vector)
        points += [start - unit * extent * 0.3, start]
    for index, direction in enumerate(DIRECTIONS):
        pixels, projected, view_dir = _render_view(nodes, triangles, colours, direction, VIEW, points)
        canvas.paste(Image.fromarray(pixels), (index * VIEW[0], 56))
        offset = np.array([index * VIEW[0], 56])
        for number, face in enumerate(faces):
            # Label faces turned towards this camera (curved faces always: some of it is visible).
            if face['kind'] == 'flat' and np.dot(face['normal'], view_dir) > -0.05:
                continue
            if setup and face['id'] not in supports | loads and face['area_mm2'] < 0.02 * extent ** 2:
                continue  # keep small, uninvolved faces unlabelled in a setup picture
            x, y = projected[number] + offset
            text = str(face['id'])
            box = draw.textbbox((x, y), text, font=small, anchor='mm')
            draw.rounded_rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), 4, fill='white', outline=(80, 80, 90))
            draw.text((x, y), text, fill=(20, 20, 25), font=small, anchor='mm')
        if start is not None:
            a, b = projected[-2] + offset, projected[-1] + offset
            draw.line([tuple(a), tuple(b)], fill=(200, 60, 20), width=5)
            d = (b - a) / max(np.linalg.norm(b - a), 1e-9)
            n = np.array([-d[1], d[0]])
            draw.polygon([tuple(b), tuple(b - d * 18 + n * 9), tuple(b - d * 18 - n * 9)], fill=(200, 60, 20))
    draw.text((12, 10), title, fill=(20, 20, 25), font=font)
    legend = 'green = held (supports)   orange = loaded faces   arrow = force direction' if setup \
        else 'each colour is one CAD face; numbers are face ids for support_faces / load_faces'
    draw.text((12, 32), legend, fill=(90, 90, 100), font=small)
    canvas.save(path)


def describe_faces(nodes, triangles, face_of_triangle, count: int) -> list[dict]:
    """Per CAD face: flat or curved, outward normal (flat), area, centre and bounds, from the mesh."""
    corners = nodes[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    areas = np.linalg.norm(cross, axis=1) / 2
    normals = cross / np.maximum(2 * areas, 1e-30)[:, None]
    centroids = corners.mean(axis=1)
    faces = []
    for face in range(count):
        mask = face_of_triangle == face
        if not mask.any():
            continue
        weights = areas[mask]
        mean = (normals[mask] * weights[:, None]).sum(axis=0)
        mean /= max(np.linalg.norm(mean), 1e-30)
        flat = bool(np.all(normals[mask] @ mean > 0.9995))
        points = corners[mask].reshape(-1, 3)
        center = (centroids[mask] * weights[:, None]).sum(axis=0) / weights.sum()
        # A curved face's centre can sit in the air; label it on its own surface.
        anchor = centroids[mask][np.linalg.norm(centroids[mask] - center, axis=1).argmin()]
        faces.append({
            'id': face, 'kind': 'flat' if flat else 'curved',
            'normal': [round(float(v), 3) for v in mean] if flat else None,
            'area_mm2': round(float(weights.sum()), 2),
            'center_mm': [round(float(v), 3) for v in center],
            'on_surface_mm': [round(float(v), 3) for v in anchor],
            'bounds_mm': [[round(float(v), 3) for v in points.min(axis=0)], [round(float(v), 3) for v in points.max(axis=0)]],
        })
    return faces
