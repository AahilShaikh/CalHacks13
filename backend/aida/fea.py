"""Bounded, small-strain 3D elasticity on a voxel approximation of a CAD solid.

Eight-node hexahedra, 2x2x2 Gauss integration, isotropic elasticity and a
Jacobi-preconditioned sparse conjugate-gradient solve. Units are N, mm, MPa.
This is a coarse screening tool; voxel boundaries and clamped-edge stresses
need mesh-convergence assessment. No contact, plasticity or buckling is modeled.
Formulation: https://solidmechanics.org/Text/Chapter8_1/Chapter8_1.htm
"""
from itertools import product
from pathlib import Path

import numpy as np

from .analysis.settings import FeaSettings
from .analysis.results import colours, write_results


# Standard H8 node ordering, shared by the interpolation and voxel connectivity.
CORNERS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
# Outward-oriented quads: x-, x+, y-, y+, z-, z+.
QUADS = [[0, 4, 7, 3], [1, 2, 6, 5], [0, 1, 5, 4],
         [3, 7, 6, 2], [0, 3, 2, 1], [4, 5, 6, 7]]


def elasticity(youngs: float, poisson: float) -> np.ndarray:
    """Voigt order xx, yy, zz, xy, yz, xz; engineering shear strains."""
    mu = youngs / (2 * (1 + poisson))
    lam = youngs * poisson / ((1 + poisson) * (1 - 2 * poisson))
    matrix = np.zeros((6, 6))
    matrix[:3, :3] = lam
    np.fill_diagonal(matrix[:3, :3], lam + 2 * mu)
    matrix[3:, 3:] = np.eye(3) * mu
    return matrix


def strain_matrix(size: np.ndarray, point: np.ndarray) -> np.ndarray:
    signs = 2 * CORNERS - 1
    grad = np.empty((8, 3))
    for axis in range(3):
        other = [i for i in range(3) if i != axis]
        grad[:, axis] = (signs[:, axis] * np.prod(1 + signs[:, other] * point[other], axis=1)
                         / (4 * size[axis]))
    b = np.zeros((6, 24))
    b[0, 0::3], b[1, 1::3], b[2, 2::3] = grad[:, 0], grad[:, 1], grad[:, 2]
    b[3, 0::3], b[3, 1::3] = grad[:, 1], grad[:, 0]
    b[4, 1::3], b[4, 2::3] = grad[:, 2], grad[:, 1]
    b[5, 0::3], b[5, 2::3] = grad[:, 2], grad[:, 0]
    return b


def voxel_mesh(mesh, resolution: int):
    """Sample cell centers inside the closed surface; preserve holes at grid scale."""
    import vtk
    from scipy.ndimage import label
    from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray, vtk_to_numpy

    mesh.merge_vertices()
    if not np.isfinite(mesh.vertices).all() or np.any(mesh.extents <= 1e-8):
        raise ValueError('CAD must have finite coordinates and nonzero thickness in all axes.')
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError('FEA needs a closed, consistently oriented solid. Repair the CAD before testing.')
    if mesh.body_count != 1:
        raise ValueError('Test one connected CAD solid at a time; assembly contact is not modeled.')
    counts = np.maximum(2, np.ceil(mesh.extents / mesh.extents.max() * resolution).astype(int))
    size = mesh.extents / counts
    cells = np.indices(counts).reshape(3, -1).T
    centers = mesh.bounds[0] + (cells + 0.5) * size

    surface = vtk.vtkPolyData()
    points = vtk.vtkPoints()
    points.SetData(numpy_to_vtk(np.asarray(mesh.vertices), deep=True))
    surface.SetPoints(points)
    faces = vtk.vtkCellArray()
    faces.SetData(numpy_to_vtkIdTypeArray(np.arange(0, 3 * len(mesh.faces) + 1, 3, dtype=np.int64), deep=True),
                  numpy_to_vtkIdTypeArray(np.asarray(mesh.faces, dtype=np.int64).ravel(), deep=True))
    surface.SetPolys(faces)
    samples = vtk.vtkPolyData()
    sample_points = vtk.vtkPoints()
    sample_points.SetData(numpy_to_vtk(centers, deep=True))
    samples.SetPoints(sample_points)
    enclosed = vtk.vtkSelectEnclosedPoints()
    enclosed.SetInputData(samples)
    enclosed.SetSurfaceData(surface)
    enclosed.SetTolerance(1e-9)
    enclosed.Update()
    occupied = vtk_to_numpy(enclosed.GetOutput().GetPointData().GetArray('SelectedPoints')).astype(bool)
    cells = cells[occupied]
    if len(cells) < 2:
        raise ValueError('The solid is too thin for this grid. Increase the mesh resolution.')
    if label(occupied.reshape(tuple(counts)))[1] != 1:
        raise ValueError('The analysis grid has disconnected regions. Increase resolution or test one solid.')
    lattice, inverse = np.unique((cells[:, None, :] + CORNERS).reshape(-1, 3), axis=0, return_inverse=True)
    nodes = mesh.bounds[0] + lattice * size
    return nodes, inverse.reshape(-1, 8), cells, size


def von_mises(stress: np.ndarray) -> np.ndarray:
    x, y, z, xy, yz, xz = np.moveaxis(stress, -1, 0)
    return np.sqrt(0.5 * ((x - y)**2 + (y - z)**2 + (z - x)**2)
                   + 3 * (xy**2 + yz**2 + xz**2))


def solve(nodes, elements, cells, size, settings: FeaSettings) -> dict:
    from scipy.sparse import coo_matrix, diags
    from scipy.sparse.linalg import cg

    material = elasticity(settings.youngs_modulus_mpa, settings.poisson_ratio)
    gauss = [strain_matrix(size, np.array(p) / np.sqrt(3)) for p in product([-1, 1], repeat=3)]
    local = sum(b.T @ material @ b for b in gauss) * np.prod(size) / 8
    dofs = (elements[:, :, None] * 3 + np.arange(3)).reshape(-1, 24)
    rows = np.broadcast_to(dofs[:, :, None], (len(elements), 24, 24)).ravel()
    cols = np.broadcast_to(dofs[:, None, :], (len(elements), 24, 24)).ravel()
    stiffness = coo_matrix((np.tile(local.ravel(), len(elements)), (rows, cols)),
                           shape=(nodes.size, nodes.size)).tocsr()

    axis = 'xyz'.index(settings.fixed_face[0])
    positive = settings.fixed_face[1] == '+'
    support_level = cells[:, axis].max() if positive else cells[:, axis].min()
    load_level = cells[:, axis].min() if positive else cells[:, axis].max()
    if support_level == load_level:
        raise ValueError('The support and load overlap on this grid. Increase resolution.')
    support = np.unique(elements[cells[:, axis] == support_level][:, QUADS[axis * 2 + int(positive)]])
    load_quads = elements[cells[:, axis] == load_level][:, QUADS[axis * 2 + int(not positive)]]
    weights = np.bincount(load_quads.ravel(), minlength=len(nodes)).astype(float)
    weights /= weights.sum()
    load_nodes = np.flatnonzero(weights)
    force_vector = np.zeros(3)
    force_vector['xyz'.index(settings.load_direction[0])] = settings.force_n * (1 if settings.load_direction[1] == '+' else -1)
    force = (weights[:, None] * force_vector).ravel()
    constrained = (support[:, None] * 3 + np.arange(3)).ravel()
    free = np.setdiff1d(np.arange(nodes.size), constrained)
    reduced = stiffness[free][:, free]
    diagonal = reduced.diagonal()
    if np.any(diagonal <= 0):
        raise ValueError('The model contains unsupported degrees of freedom.')
    displacement = np.zeros(nodes.size)
    displacement[free], info = cg(reduced, force[free], M=diags(1 / diagonal), rtol=1e-8, atol=0, maxiter=3000)
    if info != 0 or not np.isfinite(displacement).all():
        raise ValueError('The structural solve did not converge. Try a coarser grid or a thicker support face.')
    residual = stiffness @ displacement - force
    relative_residual = np.linalg.norm(residual[free]) / np.linalg.norm(force[free])
    if relative_residual > 1e-6:
        raise ValueError('The structural solve failed its equilibrium residual check.')
    local_u = displacement[dofs]
    stresses = np.stack([local_u @ (material @ b).T for b in gauss], axis=1)
    element_vm = von_mises(stresses).max(axis=1)
    # Smooth only the display; the reported peak uses unaveraged integration points.
    nodal_vm = np.bincount(elements.ravel(), weights=np.repeat(element_vm, 8), minlength=len(nodes))
    nodal_vm /= np.bincount(elements.ravel(), minlength=len(nodes))
    return {'displacement': displacement.reshape(-1, 3), 'stress': nodal_vm,
            'max_stress': float(element_vm.max()), 'residual': float(relative_residual),
            'reaction': residual.reshape(-1, 3)[support].sum(axis=0),
            'fixed_nodes': support, 'load_nodes': load_nodes, 'force_vector': force_vector}


def boundary_triangles(elements, cells):
    """External voxel faces only, including any resolved hole walls."""
    occupied = set(map(tuple, cells))
    faces = []
    for direction, quad in enumerate(QUADS):
        offset = np.zeros(3, dtype=int)
        offset[direction // 2] = 1 if direction % 2 else -1
        mask = np.array([tuple(cell + offset) not in occupied for cell in cells])
        q = elements[mask][:, quad]
        faces.extend([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
    return np.concatenate(faces)



def analyze(mesh_path: Path, settings: FeaSettings, output: Path) -> dict:
    import trimesh

    mesh = trimesh.load(str(mesh_path), force='mesh')
    if mesh.is_empty or len(mesh.faces) > 500_000:
        raise ValueError('FEA requires a nonempty CAD mesh with at most 500,000 triangles.')
    nodes, elements, cells, size = voxel_mesh(mesh, settings.resolution)
    solution = solve(nodes, elements, cells, size, settings)
    return write_results(
        nodes=nodes, triangles=boundary_triangles(elements, cells), solution=solution,
        bounds=mesh.bounds, cad_volume=float(abs(mesh.volume)),
        analysis_volume=float(len(elements) * np.prod(size)), elements=len(elements),
        settings=settings, output=output,
        metadata={'engine': 'voxel', 'method': 'voxel_h8_linear_elastic',
                  'element_type': 'H8 hexahedra', 'cell_size_mm': size.tolist(),
                  'mesh_size_mm': float(size.max())},
        warnings=['Coarse voxel approximation: small holes, fillets and thin features may be lost. Refine the grid to assess convergence.'],
    )


def main():
    """Run in an isolated API worker process, with its timeout owned by the API."""
    import argparse
    import json

    from .analysis import analyze_model

    parser = argparse.ArgumentParser()
    parser.add_argument('--mesh', type=Path)
    parser.add_argument('--step', type=Path)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = analyze_model(args.mesh, FeaSettings.model_validate_json(args.settings.read_text()), args.out, args.step)
        (args.out / 'report.json').write_text(json.dumps(result, allow_nan=False, indent=2), encoding='utf-8')
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
