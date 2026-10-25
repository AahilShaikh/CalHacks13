import math

import cv2
import numpy as np
import pytest
import trimesh
from scipy.spatial.transform import Rotation

from aida.scan.camera import CameraCalibration
from aida.scan.metric import MetricView, fit_metric_mesh
from aida.scan.scaling import ScalingError


def scene(tmp_path, angles=(0, 90, 180, 270), raw_rotation=None):
    box = trimesh.creation.box((1, .6, 1.5))
    physical = box.vertices * 40
    physical += [100, -100, 30]
    if raw_rotation is not None:
        box.apply_transform(trimesh.transformations.rotation_matrix(raw_rotation, [1, 0, 0]))
    path = tmp_path / 'raw.glb'
    box.export(path)
    K = np.array([[800., 0, 400], [0, 800, 300], [0, 0, 1]])
    calibration = CameraCalibration(K, np.zeros(5), (800, 600), 0, .2)
    views = []
    for name, angle in zip(('front', 'left', 'back', 'right'), angles):
        a = math.radians(angle)
        camera = np.array([100+350*math.sin(a), -100-350*math.cos(a), 250])
        forward = np.array([100, -100, 30])-camera
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, [0, 0, 1])
        right /= np.linalg.norm(right)
        down = np.cross(forward, right)
        R = np.vstack((right, down, forward))
        t = -R @ camera
        q = (physical @ R.T + t) @ K.T
        pixels = q[:, :2] / q[:, 2:3]
        mask = np.zeros((600, 800), np.uint8)
        cv2.fillConvexPoly(mask, cv2.convexHull(np.rint(pixels).astype(np.int32)), 255)
        views.append(MetricView(name, mask, R, t, angle))
    return path, views, calibration


@pytest.mark.parametrize('raw_rotation', [None, .7])
def test_recovers_millimeters_without_object_dimension(tmp_path, raw_rotation):
    path, views, calibration = scene(tmp_path, raw_rotation=raw_rotation)
    output = tmp_path / 'metric.glb'
    metadata = fit_metric_mesh(path, views, calibration, output)
    assert metadata['scale_multiplier_applied'] == pytest.approx(40, rel=.025)
    result = trimesh.load(output, force='mesh')
    np.testing.assert_allclose(sorted(result.extents), [24, 40, 60], atol=1.5)
    assert result.bounds[0, 2] == pytest.approx(0, abs=1e-5)
    assert metadata['registration']['estimated_relative_scale_std'] < .035
    assert metadata['registration']['camera_calibration_rms_px'] == .2


def test_requires_independent_side_view(tmp_path):
    path, views, calibration = scene(tmp_path, angles=(0, 180))
    with pytest.raises(ScalingError, match='side view'):
        fit_metric_mesh(path, views, calibration, tmp_path/'metric.glb')


def test_empty_or_cropped_masks_are_errors(tmp_path):
    path, views, calibration = scene(tmp_path)
    views[0].mask[:] = 0
    with pytest.raises(ScalingError, match='empty'):
        fit_metric_mesh(path, views, calibration, tmp_path/'metric.glb')


def test_inconsistent_geometry_is_rejected(tmp_path):
    path, views, calibration = scene(tmp_path)
    # One view shows an object nearly twice as wide, incompatible with a rigid mesh.
    views[1].mask = cv2.resize(views[1].mask, None, fx=1.8, fy=1)
    views[1].mask = views[1].mask[:, 320:1120].copy()
    with pytest.raises(ScalingError):
        fit_metric_mesh(path, views, calibration, tmp_path/'metric.glb', allow_approximate=True)


def test_approximate_reference_preserves_scale_with_moderate_shape_error(tmp_path):
    path, views, calibration = scene(tmp_path)
    mesh = trimesh.load(path, force='mesh')
    mesh.apply_scale([1.25, 1, 1])
    mesh.export(path)
    with pytest.raises(ScalingError, match='silhouettes'):
        fit_metric_mesh(path, views, calibration, tmp_path/'strict.glb')
    result = fit_metric_mesh(path, views, calibration, tmp_path/'approximate.glb', allow_approximate=True)
    registration = result['registration']
    assert registration['quality'] == 'approximate'
    assert 'not verified' in registration['warning']
    assert result['scale_multiplier_applied'] == pytest.approx(40, rel=.2)
    assert registration['estimated_relative_scale_std'] < .035
    from aida.scan.pipeline import agent_prompt_for
    prompt = agent_prompt_for(result)
    assert 'approximation' in prompt and 'physical verification' in prompt
    assert '1:1' not in prompt
