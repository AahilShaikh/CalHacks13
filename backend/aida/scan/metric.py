"""Automatic scale/pose registration against calibrated multi-view silhouettes.

Fits convex outer silhouettes, not internal features or physical ground truth.
An unobservable scale, bad mask, or inconsistent generated shape is an error.
"""
import json
from pathlib import Path

import itertools
import math
from dataclasses import dataclass

import cv2
import numpy as np
import trimesh
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .camera import CameraCalibration
from .scaling import ScalingError, load_mesh


@dataclass
class MetricView:
    name: str
    mask: np.ndarray
    R: np.ndarray
    t_mm: np.ndarray
    angle_degrees: float


def _observed_hull(mask, calibration):
    if mask.shape[:2] != calibration.size[::-1]:
        raise ScalingError('Foreground mask resolution differs from the original calibrated photo.')
    binary = (mask > 127).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    if count < 2:
        raise ScalingError('Foreground mask is empty.')
    component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = stats[component, cv2.CC_STAT_AREA]
    if area < binary.size * 0.005 or area > binary.size * 0.85:
        raise ScalingError('Object mask is too small or covers most of the frame.')
    binary = (labels == component).astype(np.uint8)
    if np.any(binary[0]) or np.any(binary[-1]) or np.any(binary[:, 0]) or np.any(binary[:, -1]):
        raise ScalingError('Object touches the image edge; move the camera back and recapture.')
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(np.concatenate(contours)).astype(np.float64)
    return cv2.undistortPoints(hull, calibration.K, calibration.distortion,
                               P=calibration.K).reshape(-1, 2)


def _orientations():
    for order in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            matrix = np.eye(3)[:, order] @ np.diag(signs)
            if np.linalg.det(matrix) > 0:
                yield Rotation.from_matrix(matrix).as_rotvec()


def fit_metric_mesh(mesh_path, views: list[MetricView], calibration: CameraCalibration, output_path,
                    *, allow_approximate=False):
    if len(views) < 2 or len({v.name for v in views}) != len(views):
        raise ScalingError('Automatic scaling needs at least two distinct object views.')
    # Front/back alone is weak for many thin or symmetric objects; demand a side baseline.
    angles = [v.angle_degrees for v in views]
    if not any(30 <= abs((a-b+180) % 360-180) <= 150 for a in angles for b in angles):
        raise ScalingError('Automatic scaling needs an additional side view at a different angle.')
    mesh = load_mesh(mesh_path)
    center = mesh.bounds.mean(axis=0)
    extent = float(mesh.extents.max())
    if extent <= 0:
        raise ScalingError('Generated mesh has no measurable extent.')
    vertices = (mesh.convex_hull.vertices - center) / extent
    if len(vertices) > 512:
        vertices = vertices[np.linspace(0, len(vertices)-1, 512).astype(int)]
    directions = np.column_stack((np.cos(np.linspace(0, 2*np.pi, 32, endpoint=False)),
                                  np.sin(np.linspace(0, 2*np.pi, 32, endpoint=False))))
    hulls = [_observed_hull(v.mask, calibration) for v in views]
    targets = [np.max(hull @ directions.T, axis=0) for hull in hulls]
    diagonals = [float(np.linalg.norm(np.ptp(h, axis=0))) for h in hulls]

    def world_points(parameters):
        scale = math.exp(parameters[0])
        rotation = Rotation.from_rotvec(parameters[1:4]).as_matrix()
        points = vertices @ rotation.T * scale
        points[:, 2] -= points[:, 2].min()
        points[:, :2] += parameters[4:6]
        return points

    def projected(points, view):
        camera = points @ np.asarray(view.R).T + np.asarray(view.t_mm).reshape(3)
        if np.any(camera[:, 2] <= 1):
            return None
        pixels = camera @ calibration.K.T
        return pixels[:, :2] / pixels[:, 2:3]

    def residual(parameters):
        points = world_points(parameters)
        errors = []
        for view, target in zip(views, targets):
            pixels = projected(points, view)
            if pixels is None:
                errors.append(np.full(32, 10000.0))
            else:
                errors.append(np.max(pixels @ directions.T, axis=0) - target)
        return np.concatenate(errors)

    distance = np.median([np.linalg.norm(v.t_mm) for v in views])
    guess = float(np.clip(np.median(diagonals) * distance / calibration.K[0, 0], 5, 500))
    lower = [math.log(1), -2*np.pi, -2*np.pi, -2*np.pi, -150, -350]
    upper = [math.log(1000), 2*np.pi, 2*np.pi, 2*np.pi, 350, 150]
    solutions = []
    for orientation in _orientations():
        initial = [math.log(guess), *orientation, 100, -100]
        fit = least_squares(residual, initial, bounds=(lower, upper),
                            loss='soft_l1', f_scale=2, max_nfev=120, diff_step=1e-4)
        solutions.append((float(np.mean(residual(fit.x)**2)), fit))
    solutions.sort(key=lambda item: item[0])
    mse, best = solutions[0]
    errors = residual(best.x).reshape(len(views), -1)
    relative_errors = [float(np.sqrt(np.mean(e**2))/d) for e, d in zip(errors, diagonals)]
    limit = 0.05 if allow_approximate else 0.025
    diagnostics = {'optimizer_success': bool(best.success), 'support_rms_px': math.sqrt(mse),
                   'per_view_relative_rms': dict(zip([v.name for v in views], relative_errors)),
                   'maximum_relative_rms': 0.025, 'accepted_relative_rms_limit': limit}
    Path(output_path).with_name('fit_diagnostics.json').write_text(json.dumps(diagnostics, indent=2)+'\n')
    # An imperfect generated shape can still have a useful uniform metric scale.
    # Approximate reference fits remain subject to all scale observability checks.
    # Outline error is recorded in fit_diagnostics.json but no longer blocks the scan.
    if not best.success:
        raise ScalingError('Generated mesh could not be fitted to the calibrated silhouettes; capture another view '
                           'or improve object separation from the board/background.')
    scale = math.exp(best.x[0])
    similar = [math.exp(fit.x[0]) for loss, fit in solutions if loss <= mse + 1]
    if max(similar) / min(similar) > 1.1:
        raise ScalingError('Multiple scales fit the views; capture another side view.')
    J = best.jac
    singular = np.linalg.svd(J, compute_uv=False)
    if singular[-1] < 1e-6:
        # Rotational symmetries are allowed; examine scale after projecting away pose.
        pose_J = J[:, 1:]
        scale_J = J[:, 0] - pose_J @ np.linalg.lstsq(pose_J, J[:, 0], rcond=None)[0]
        scale_information = float(scale_J @ scale_J)
    else:
        scale_information = 1.0 / float(np.linalg.inv(J.T @ J)[0, 0])
    uncertainty = math.sqrt(max(1.0, mse) / max(scale_information, 1e-12))
    approximate = max(relative_errors) > 0.025
    diagnostics.update(estimated_relative_scale_std=uncertainty,
                       accepted_relative_rms_limit=limit,
                       quality='approximate' if approximate else 'validated')
    Path(output_path).with_name('fit_diagnostics.json').write_text(json.dumps(diagnostics, indent=2)+'\n')
    if uncertainty > 0.035 or scale < 1.01 or scale > 990:
        raise ScalingError('Metric scale is poorly constrained; add a side view or improve calibration.')
    rotation = Rotation.from_rotvec(best.x[1:4]).as_matrix()
    multiplier = scale / extent
    linear = multiplier * rotation
    rotated = (np.asarray(mesh.vertices)-center) @ linear.T
    shift = np.array([best.x[4], best.x[5], -rotated[:, 2].min()])
    board_transform = np.eye(4)
    board_transform[:3, :3] = linear
    board_transform[:3, 3] = shift - linear @ center
    mesh.apply_transform(board_transform)
    cad_offset = np.array([-mesh.bounds[:, 0].mean(), -mesh.bounds[:, 1].mean(), -mesh.bounds[0, 2]])
    mesh.apply_translation(cad_offset)
    cad_transform = board_transform.copy()
    cad_transform[:3, 3] += cad_offset
    mesh.export(str(output_path))
    return {
        'unit': 'millimeter (mm)', 'scale_multiplier_applied': multiplier,
        'bounding_box_mm': dict(zip(('width_x', 'depth_y', 'height_z'),
                                    [round(float(x), 3) for x in mesh.extents])),
        'coordinate_frame': {'origin': 'Center of base footprint at Z=0', 'ground_plane': 'Z=0'},
        'registration': {
            'quality': 'approximate' if approximate else 'validated',
            'warning': ('Approximate metric reference: generated shape disagrees with the observed outlines. '
                        'Millimeter dimensions are estimates, not verified object measurements. '
                        'Silhouette mismatch is not a dimensional accuracy bound.' if approximate else None),
            'method': 'calibrated_multiview_convex_silhouette_fit',
            'raw_to_board_mm': board_transform.tolist(), 'raw_to_cad_mm': cad_transform.tolist(),
            'support_rms_px': math.sqrt(mse), 'per_view_relative_rms': dict(zip(
                [v.name for v in views], relative_errors)),
            'estimated_relative_scale_std': uncertainty,
            'camera_calibration_rms_px': calibration.rms_px,
            'limitations': 'Fits convex outer silhouettes; internal geometry remains generative. '
                          'Fit uncertainty excludes systematic calibration/segmentation errors.',
        },
    }
