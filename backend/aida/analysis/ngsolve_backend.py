"""OpenCascade STEP -> Netgen tetrahedra -> NGSolve linear elasticity.

Native OCC libraries stay inside the isolated worker; never import this module
from the API process. First-order VectorH1 uses constant strain tetrahedra.
See https://docu.ngsolve.org/ngs24/SaS/linearelasticity.html.
"""
from pathlib import Path

import numpy as np

from .results import write_results
from .settings import FeaSettings

MAX_ELEMENTS = 100_000
MAX_NODES = 30_000


def analyze_step(step: Path, settings: FeaSettings, output: Path) -> dict:
    try:
        import ngsolve as ng
        from netgen.occ import OCCGeometry
    except (ImportError, OSError) as exc:
        raise ValueError('NGSolve could not load its native libraries. Run uv sync --extra fea or select the voxel solver.') from exc

    ng.SetNumThreads(1)
    geometry = OCCGeometry(str(step.resolve()))
    shape = geometry.shape
    if len(shape.solids) != 1:
        raise ValueError('Test one connected CAD solid at a time; assembly contact is not modeled.')
    bounds = np.array([tuple(p) for p in shape.bounding_box], dtype=float)
    extents = bounds[1] - bounds[0]
    if not np.isfinite(bounds).all() or np.any(extents <= 1e-6):
        raise ValueError('CAD must have finite coordinates and nonzero thickness in all axes.')
    axis = 'xyz'.index(settings.fixed_face[0])
    fixed_end = int(settings.fixed_face[1] == '+')
    tolerance = max(float(extents.max()) * 1e-7, 1e-6)
    counts = {'fixed': 0, 'load': 0}
    for face in shape.faces:
        face.name = 'surface'
        face_bounds = np.array([tuple(p) for p in face.bounding_box])
        for label, end in [('fixed', fixed_end), ('load', 1 - fixed_end)]:
            if np.all(np.abs(face_bounds[:, axis] - bounds[end, axis]) <= tolerance):
                face.name = label
                counts[label] += 1
    if not all(counts.values()):
        raise ValueError('The STEP solver needs planar faces at both selected bounding ends. Choose another fixed axis or use the voxel solver.')

    maxh = float(extents.max() / settings.resolution)
    # Rebuild from the tagged shape: OCCGeometry.shape is a Python-side copy.
    native = OCCGeometry(shape).GenerateMesh(maxh=maxh)
    mesh = ng.Mesh(native)
    if mesh.ne > MAX_ELEMENTS or mesh.nv > MAX_NODES:
        raise ValueError('CAD meshing exceeded the analysis size limit. Use a coarser resolution or simplify small features.')
    if mesh.ne < 1:
        raise ValueError('Netgen could not produce a volume mesh from this CAD solid.')
    nodes = np.array([tuple(p.p) for p in native.Points()])
    elements = np.array([[v.nr - 1 for v in e.vertices] for e in native.Elements3D()])
    surface = list(native.Elements2D())
    triangles = np.array([[v.nr - 1 for v in e.vertices] for e in surface])
    if elements.shape[1] != 4 or triangles.shape[1] != 3:
        raise ValueError('The STEP solver requires a tetrahedral mesh with triangular boundaries.')
    names = mesh.GetBoundaries()
    fixed_triangles = triangles[[names[e.index - 1] == 'fixed' for e in surface]]
    load_triangles = triangles[[names[e.index - 1] == 'load' for e in surface]]
    fixed_nodes = np.unique(fixed_triangles)
    load_nodes = np.unique(load_triangles)
    if not len(fixed_nodes) or not len(load_nodes) or np.intersect1d(fixed_nodes, load_nodes).size:
        raise ValueError('The support and load must occupy distinct meshed faces.')

    fes = ng.VectorH1(mesh, order=1, dirichlet='fixed')
    u, v = fes.TnT()
    mu = settings.youngs_modulus_mpa / (2 * (1 + settings.poisson_ratio))
    lam = settings.youngs_modulus_mpa * settings.poisson_ratio / ((1 + settings.poisson_ratio) * (1 - 2 * settings.poisson_ratio))
    strain = ng.Sym(ng.Grad(u))
    stress = 2 * mu * strain + lam * ng.Trace(strain) * ng.Id(3)
    stiffness = ng.BilinearForm(fes, symmetric=True)
    stiffness += ng.InnerProduct(stress, ng.Sym(ng.Grad(v))) * ng.dx
    stiffness.Assemble()
    load_area = ng.Integrate(1, mesh, definedon=mesh.Boundaries('load'))
    if load_area <= 1e-12:
        raise ValueError('The load face has no measurable area.')
    force_vector = np.zeros(3)
    force_vector['xyz'.index(settings.load_direction[0])] = settings.force_n * (1 if settings.load_direction[1] == '+' else -1)
    force = ng.LinearForm(fes)
    force += ng.CF(tuple(force_vector / load_area)) * v * ng.ds('load')
    force.Assemble()
    gfu = ng.GridFunction(fes)
    gfu.vec.data = stiffness.mat.Inverse(fes.FreeDofs(), inverse='sparsecholesky') * force.vec
    dofs = np.array([fes.GetDofNrs(vertex) for vertex in mesh.vertices])
    displacement = gfu.vec.FV().NumPy()[dofs].copy()
    residual = force.vec.CreateVector()
    residual.data = stiffness.mat * gfu.vec - force.vec
    residual_array = residual.FV().NumPy()
    free = np.array(list(fes.FreeDofs()), dtype=bool)
    relative_residual = float(np.linalg.norm(residual_array[free]) / np.linalg.norm(force.vec.FV().NumPy()[free]))
    reaction = residual_array[dofs[fixed_nodes]].sum(axis=0)
    if not np.isfinite(relative_residual) or relative_residual > 1e-6:
        raise ValueError('The structural solve failed its equilibrium residual check.')
    if not np.allclose(reaction, -force_vector, rtol=1e-6, atol=settings.force_n * 1e-6):
        raise ValueError('The structural solve failed its support reaction balance check.')

    # P1 tetrahedra have an affine displacement field and constant stress.
    coordinates = nodes[elements]
    affine = np.concatenate([np.ones((len(elements), 4, 1)), coordinates], axis=2)
    gradients = np.linalg.inv(affine)[:, 1:, :]
    grad_u = np.einsum('eia,ebi->eab', displacement[elements], gradients)
    epsilon = (grad_u + grad_u.transpose(0, 2, 1)) / 2
    sigma = 2 * mu * epsilon + lam * np.trace(epsilon, axis1=1, axis2=2)[:, None, None] * np.eye(3)
    deviator = sigma - np.trace(sigma, axis1=1, axis2=2)[:, None, None] * np.eye(3) / 3
    element_vm = np.sqrt(1.5 * np.sum(deviator**2, axis=(1, 2)))
    volumes = np.abs(np.linalg.det(coordinates[:, 1:] - coordinates[:, :1])) / 6
    weights = np.bincount(elements.ravel(), weights=np.repeat(volumes, 4), minlength=len(nodes))
    nodal_vm = np.bincount(elements.ravel(), weights=np.repeat(element_vm * volumes, 4), minlength=len(nodes)) / weights
    return write_results(
        nodes=nodes, triangles=triangles, bounds=bounds, elements=len(elements),
        cad_volume=float(shape.mass), analysis_volume=float(volumes.sum()), settings=settings, output=output,
        solution={'displacement': displacement, 'stress': nodal_vm, 'max_stress': float(element_vm.max()),
                  'residual': relative_residual, 'reaction': reaction, 'force_vector': force_vector,
                  'fixed_nodes': fixed_nodes, 'load_nodes': load_nodes},
        metadata={'engine': 'ngsolve', 'method': 'occ_tet4_linear_elastic', 'element_type': 'T4 tetrahedra',
                  'mesh_size_mm': maxh, 'cell_size_mm': None, 'ngsolve_version': ng.__version__},
        warnings=['First-order tetrahedral screening model. Refine the mesh and assess convergence, especially for bending and small features.'],
    )
