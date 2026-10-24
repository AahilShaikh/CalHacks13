"""Render STL triangles directly; avoid the CAD server's lossy STEP round-trip."""
import argparse
import math


def render(path, output, direction='iso', azimuth=0.0, elevation=0.0):
    import vtk

    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(path))
    reader.Update()
    mesh = reader.GetOutput()
    if mesh.GetNumberOfCells() == 0:
        raise ValueError('STL contains no renderable triangles')
    bounds = mesh.GetBounds()
    if not all(math.isfinite(x) for x in bounds):
        raise ValueError('STL contains invalid bounds')
    center = [(bounds[i] + bounds[i+1]) / 2 for i in (0, 2, 4)]
    radius = max(bounds[i+1] - bounds[i] for i in (0, 2, 4))
    if radius <= 0:
        raise ValueError('STL has zero extent')
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputData(mesh)
    normals.ConsistencyOn()
    normals.AutoOrientNormalsOn()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(normals.GetOutputPort())
    mapper.ScalarVisibilityOff()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(.25, .55, .85)
    actor.GetProperty().SetAmbient(.25)
    actor.GetProperty().SetDiffuse(.75)
    actor.GetProperty().BackfaceCullingOff()
    renderer = vtk.vtkRenderer()
    renderer.AddActor(actor)
    renderer.SetBackground(1, 1, 1)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(800, 600)
    window.AddRenderer(renderer)
    camera = renderer.GetActiveCamera()
    vector = {'front': (0, -1, 0), 'side': (1, 0, 0), 'top': (0, 0, 1), 'iso': (1, -1, 1)}[direction]
    camera.SetFocalPoint(*center)
    camera.SetPosition(*[c + v * radius * 3 for c, v in zip(center, vector)])
    camera.SetViewUp(*( (0, 1, 0) if direction == 'top' else (0, 0, 1)))
    camera.ParallelProjectionOn()
    renderer.ResetCamera()
    camera.Azimuth(azimuth)
    camera.Elevation(elevation)
    camera.OrthogonalizeViewUp()
    renderer.ResetCameraClippingRange()
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mesh')
    parser.add_argument('output')
    parser.add_argument('--direction', choices=['front', 'side', 'top', 'iso'], default='iso')
    parser.add_argument('--azimuth', type=float, default=0)
    parser.add_argument('--elevation', type=float, default=0)
    args = parser.parse_args()
    render(args.mesh, args.output, args.direction, args.azimuth, args.elevation)
