"""OpenCascade STEP -> Netgen tetrahedra -> NGSolve linear elasticity.

Native OCC libraries stay inside the isolated worker; never import this module
from the API process. Second-order VectorH1 (quadratic tetrahedra) resolves
bending far better than constant-strain elements at the same mesh size.
See https://docu.ngsolve.org/ngs24/SaS/linearelasticity.html.

Every CAD face is named f<i> (its index in the STEP's face order), so supports and
loads can be any faces: picked by number from the face listing, or the two
bounding-box ends of an axis.
"""
from pathlib import Path
from typing import Callable

import numpy as np

from .results import write_results
from .settings import FeaSettings
from .setup_view import describe_faces, render_setup

ORDER = 2
MAX_ELEMENTS = 100_000
MAX_NODES = 30_000
MAX_DOFS = 600_000
# The mesh check re-solves with elements this much smaller.
REFINE = 1.6
# NGSolve's reference tetrahedron: element vertex i sits at REF_VERTICES[i].
REF_VERTICES = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)]


def _ngsolve():
    try:
        import ngsolve as ng
        from netgen.occ import OCCGeometry
    except (ImportError, OSError) as exc:
        raise ValueError('NGSolve could not load its native libraries. Reinstall it with uv sync.') from exc
    ng.SetNumThreads(1)
    return ng, OCCGeometry


def _load(step: Path):
    ng, OCCGeometry = _ngsolve()
    shape = OCCGeometry(str(step.resolve())).shape
    if len(shape.solids) != 1:
        raise ValueError('Test one connected CAD solid at a time; assembly contact is not modeled.')
    bounds = np.array([tuple(p) for p in shape.bounding_box], dtype=float)
    if not np.isfinite(bounds).all() or np.any(bounds[1] - bounds[0] <= 1e-6):
        raise ValueError('CAD must have finite coordinates and nonzero thickness in all axes.')
    for index, face in enumerate(shape.faces):
        face.name = f'f{index}'
    return ng, OCCGeometry, shape, bounds


def _mesh(ng, OCCGeometry, shape, maxh):
    # Mesh the tagged shape: OCCGeometry(path).shape is a Python-side copy.
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
        raise ValueError('FEA requires a tetrahedral mesh with triangular boundaries.')
    names = mesh.GetBoundaries()
    face_of_triangle = np.array([int(names[e.index - 1][1:]) for e in surface])
    return mesh, nodes, elements, triangles, face_of_triangle


def _end_faces(faces, bounds, axis, end):
    """Faces lying entirely on one bounding-box end of an axis."""
    tolerance = max(float(np.ptp(bounds, axis=0).max()) * 1e-6, 1e-6)
    return [f['id'] for f in faces
            if all(abs(f['bounds_mm'][i][axis] - bounds[end][axis]) <= max(tolerance, 2e-3) for i in (0, 1))]


def select_faces(settings: FeaSettings, faces: list[dict], bounds) -> tuple[list[int], list[int]]:
    ids = {f['id'] for f in faces}
    axis = 'xyz'.index(settings.fixed_face[0])
    fixed_end = int(settings.fixed_face[1] == '+')
    for name, chosen in [('support_faces', settings.support_faces), ('load_faces', settings.load_faces)]:
        unknown = sorted(set(chosen or []) - ids)
        if unknown:
            raise ValueError(f'{name} {unknown} are not faces of this part (it has faces 0-{max(ids)}). List the faces first.')
    supports = sorted(set(settings.support_faces)) if settings.support_faces else _end_faces(faces, bounds, axis, fixed_end)
    loads = sorted(set(settings.load_faces)) if settings.load_faces else _end_faces(faces, bounds, axis, 1 - fixed_end)
    if not supports or not loads:
        raise ValueError('There is no flat face at that end of the part to hold or load. '
                         'Choose the support and load faces by number from the face listing instead.')
    if set(supports) & set(loads):
        raise ValueError('A face cannot be both a support and a load face.')
    return supports, loads


def list_faces(step: Path, output: Path, resolution: int = 10) -> dict:
    """Face numbers, kinds and positions, plus faces.png with every face labelled."""
    ng, OCCGeometry, shape, bounds = _load(step)
    _, nodes, _, triangles, face_of_triangle = _mesh(ng, OCCGeometry, shape, float(np.ptp(bounds, axis=0).max() / resolution))
    faces = describe_faces(nodes, triangles, face_of_triangle, len(shape.faces))
    output.mkdir(parents=True, exist_ok=True)
    render_setup(output / 'faces.png', nodes, triangles, face_of_triangle, faces, title='CAD faces by number')
    return {'faces': faces, 'bounds_mm': bounds.tolist(), 'image': str(output / 'faces.png')}


def _solve(ng, mesh, settings, supports, loads):
    """Quadratic elasticity with the support faces clamped and the force spread over the load faces."""
    dirichlet = '|'.join(f'f{i}' for i in supports)
    loaded = '|'.join(f'f{i}' for i in loads)
    fes = ng.VectorH1(mesh, order=ORDER, dirichlet=dirichlet)
    if fes.ndof > MAX_DOFS:
        raise ValueError('The model is too large to solve at this resolution. Use a coarser resolution.')
    u, v = fes.TnT()
    mu = settings.youngs_modulus_mpa / (2 * (1 + settings.poisson_ratio))
    lam = settings.youngs_modulus_mpa * settings.poisson_ratio / ((1 + settings.poisson_ratio) * (1 - 2 * settings.poisson_ratio))

    def stress_of(field):
        strain = ng.Sym(ng.Grad(field))
        return 2 * mu * strain + lam * ng.Trace(strain) * ng.Id(3)

    stiffness = ng.BilinearForm(fes, symmetric=True)
    stiffness += ng.InnerProduct(stress_of(u), ng.Sym(ng.Grad(v))) * ng.dx
    stiffness.Assemble()
    load_area = ng.Integrate(1, mesh, definedon=mesh.Boundaries(loaded))
    if load_area <= 1e-12:
        raise ValueError('The load faces have no measurable area.')
    force_vector = np.zeros(3)
    force_vector['xyz'.index(settings.load_direction[0])] = settings.force_n * (1 if settings.load_direction[1] == '+' else -1)
    force = ng.LinearForm(fes)
    force += ng.CF(tuple(force_vector / load_area)) * v * ng.ds(loaded)
    force.Assemble()
    gfu = ng.GridFunction(fes)
    gfu.vec.data = stiffness.mat.Inverse(fes.FreeDofs(), inverse='sparsecholesky') * force.vec

    # Hierarchical H1 basis: vertex dofs are the nodal values (edge bubbles vanish there),
    # and the vertex hat functions alone sum to one, so their residuals give the reaction.
    dofs = np.array([fes.GetDofNrs(vertex) for vertex in mesh.vertices])
    displacement = gfu.vec.FV().NumPy()[dofs].copy()
    residual = force.vec.CreateVector()
    residual.data = stiffness.mat * gfu.vec - force.vec
    residual_array = residual.FV().NumPy()
    free = np.array(list(fes.FreeDofs()), dtype=bool)
    relative_residual = float(np.linalg.norm(residual_array[free]) / np.linalg.norm(force.vec.FV().NumPy()[free]))
    fixed_dofs = ~free & np.isin(np.arange(fes.ndof), dofs.ravel())
    reaction = residual_array[dofs][fixed_dofs[dofs].all(axis=1)].sum(axis=0)
    if not np.isfinite(relative_residual) or relative_residual > 1e-6:
        raise ValueError('The structural solve failed its equilibrium residual check.')
    if not np.allclose(reaction, -force_vector, rtol=1e-6, atol=settings.force_n * 1e-6):
        raise ValueError('The structural solve failed its support reaction balance check.')

    # Quadratic displacement means stress varies inside each element: evaluate von Mises
    # at every element's corners and centroid, all elements at once.
    sigma = stress_of(gfu)
    deviator = sigma - ng.Trace(sigma) / 3 * ng.Id(3)
    von_mises = ng.sqrt(1.5 * ng.InnerProduct(deviator, deviator))
    rule = ng.IntegrationRule(points=[*REF_VERTICES, (.25, .25, .25)], weights=[0] * 5)
    sampled = np.asarray(von_mises(mesh.MapToAllElements(rule, ng.VOL))).reshape(mesh.ne, 5)
    return {'displacement': displacement, 'sampled': sampled, 'residual': relative_residual,
            'reaction': reaction, 'force_vector': force_vector, 'dofs': int(fes.ndof)}


def _peaks(nodes, elements, triangles, face_of_triangle, supports, sampled):
    """Global peak, and the peak outside the layer of elements touching a support.

    A fully rigid support makes stress at its edges grow with refinement; that layer is
    reported separately so a modelling artifact is not mistaken for the weak point.
    """
    support_nodes = np.unique(triangles[np.isin(face_of_triangle, supports)])
    touching = np.isin(elements, support_nodes).any(axis=1)
    element_peak = sampled.max(axis=1)
    centroids = nodes[elements].mean(axis=1)
    worst = int(element_peak.argmax())
    away = np.where(touching, -np.inf, element_peak)
    worst_away = int(away.argmax()) if np.isfinite(away).any() else worst
    return {
        'peak': float(element_peak[worst]),
        'peak_location_mm': centroids[worst].round(3).tolist(),
        'peak_at_support': bool(touching[worst]),
        'peak_away': float(element_peak[worst_away]),
        'peak_away_location_mm': centroids[worst_away].round(3).tolist(),
    }


def analyze_step(step: Path, settings: FeaSettings, output: Path,
                 on_stage: Callable[[str], None] = lambda stage: None) -> dict:
    on_stage('meshing')
    ng, OCCGeometry, shape, bounds = _load(step)
    maxh = float(np.ptp(bounds, axis=0).max() / settings.resolution)
    mesh, nodes, elements, triangles, face_of_triangle = _mesh(ng, OCCGeometry, shape, maxh)
    faces = describe_faces(nodes, triangles, face_of_triangle, len(shape.faces))
    supports, loads = select_faces(settings, faces, bounds)
    force_vector = np.zeros(3)
    force_vector['xyz'.index(settings.load_direction[0])] = 1 if settings.load_direction[1] == '+' else -1
    output.mkdir(parents=True, exist_ok=True)
    render_setup(output / 'setup.png', nodes, triangles, face_of_triangle, faces, supports=supports, loads=loads,
                 force_vector=force_vector,
                 title=f'FEA setup: held at faces {supports}, {settings.force_n:g} N on faces {loads} toward {settings.load_direction}')

    on_stage('solving')
    solution = _solve(ng, mesh, settings, supports, loads)

    on_stage('post-processing')
    sampled = solution['sampled']
    peaks = _peaks(nodes, elements, triangles, face_of_triangle, supports, sampled)
    corner_vm = sampled[:, :4]
    coordinates = nodes[elements]
    volumes = np.abs(np.linalg.det(coordinates[:, 1:] - coordinates[:, :1])) / 6
    weights = np.bincount(elements.ravel(), weights=np.repeat(volumes, 4), minlength=len(nodes))
    nodal_vm = np.bincount(elements.ravel(), weights=(corner_vm * volumes[:, None]).ravel(),
                           minlength=len(nodes)) / weights
    warnings = ['Quadratic tetrahedral screening model. Compare runs at the same resolution.']
    if peaks['peak_at_support']:
        warnings.append(f"The peak stress ({peaks['peak']:.3g} MPa) is at the edge of a support, where a perfectly "
                        f"rigid hold makes stress grow with refinement. Away from the supports the peak is "
                        f"{peaks['peak_away']:.3g} MPa; judge the part by that unless the support edge is a real weak point.")
    mesh_check = None
    if settings.check_mesh:
        on_stage('checking mesh')
        try:
            fine_mesh, f_nodes, f_elements, f_triangles, f_faces = _mesh(ng, OCCGeometry, shape, maxh / REFINE)
            fine = _solve(ng, fine_mesh, settings, supports, loads)
            fine_peaks = _peaks(f_nodes, f_elements, f_triangles, f_faces, supports, fine['sampled'])
            change = lambda a, b: abs(b - a) / max(abs(a), 1e-30)  # noqa: E731
            mesh_check = {
                'finer_mesh_size_mm': maxh / REFINE,
                'finer_peak_away_from_supports_mpa': fine_peaks['peak_away'],
                'finer_max_displacement_mm': float(np.linalg.norm(fine['displacement'], axis=1).max()),
                'peak_away_change': change(peaks['peak_away'], fine_peaks['peak_away']),
                'displacement_change': change(float(np.linalg.norm(solution['displacement'], axis=1).max()),
                                              float(np.linalg.norm(fine['displacement'], axis=1).max())),
            }
            if max(mesh_check['peak_away_change'], mesh_check['displacement_change']) > 0.1:
                warnings.append(f"A finer mesh changed the result by {100 * max(mesh_check['peak_away_change'], mesh_check['displacement_change']):.0f}%: "
                                'it has not converged, so treat the numbers as rough. Use a finer resolution.')
        except ValueError as exc:
            mesh_check = {'skipped': str(exc)}
    away_ratio = settings.yield_strength_mpa / peaks['peak_away'] if peaks['peak_away'] > 0 else None
    return write_results(
        nodes=nodes, triangles=triangles, bounds=bounds, elements=len(elements),
        cad_volume=float(shape.mass), analysis_volume=float(volumes.sum()), settings=settings, output=output,
        solution={'displacement': solution['displacement'], 'stress': nodal_vm, 'max_stress': peaks['peak'],
                  'residual': solution['residual'], 'reaction': solution['reaction'],
                  'force_vector': solution['force_vector'],
                  'fixed_nodes': np.unique(triangles[np.isin(face_of_triangle, supports)]),
                  'load_nodes': np.unique(triangles[np.isin(face_of_triangle, loads)])},
        metadata={'engine': 'ngsolve', 'method': 'occ_tet10_linear_elastic', 'element_type': 'Quadratic tetrahedra',
                  'mesh_size_mm': maxh, 'dofs': solution['dofs'], 'ngsolve_version': ng.__version__,
                  'support_faces': supports, 'load_faces': loads,
                  'support_faces_detail': [f for f in faces if f['id'] in supports],
                  'load_faces_detail': [f for f in faces if f['id'] in loads],
                  'peak_location_mm': peaks['peak_location_mm'], 'peak_at_support': peaks['peak_at_support'],
                  'peak_away_from_supports_mpa': peaks['peak_away'],
                  'peak_away_location_mm': peaks['peak_away_location_mm'],
                  'yield_ratio_away_from_supports': away_ratio, 'mesh_check': mesh_check},
        warnings=warnings,
    )
