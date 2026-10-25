import cv2
import numpy as np
import pytest

from aida.scan.camera import CameraCalibration, board_pose, calibrate_frames
from aida.scan.charuco import CalibrationError, make_board


def calibration_frames():
    board, _ = make_board()
    texture = board.generateImage((1000, 1000), marginSize=0)
    K = np.array([[850., 0, 640], [0, 850, 360], [0, 0, 1]])
    corners = np.float32([[0, 0, 0], [200, 0, 0], [200, 200, 0], [0, 200, 0]])
    source = np.float32([[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
    frames = []
    for i in range(14):
        rotation = np.array([0.30*np.sin(i*1.3), 0.35*np.cos(i*.9), .15*np.sin(i)])
        translation = np.array([-100+20*np.sin(i), -100+20*np.cos(i), 530+30*np.sin(i*.7)])
        pixels, _ = cv2.projectPoints(corners, rotation, translation, K, np.zeros(5))
        H = cv2.getPerspectiveTransform(source, pixels.astype(np.float32))
        frames.append(cv2.warpPerspective(texture, H, (1280, 720), borderValue=255))
    return frames, K


def test_calibrate_actual_board_images_and_persist(tmp_path):
    frames, expected = calibration_frames()
    calibration, report = calibrate_frames(frames)
    assert calibration.rms_px < 0.5
    np.testing.assert_allclose(calibration.K[:2, :2], expected[:2, :2], atol=20)
    assert report['samples'] == 14
    path = tmp_path / 'camera.json'
    calibration.save(path)
    loaded = CameraCalibration.load(path)
    np.testing.assert_allclose(loaded.K, calibration.K)
    pose = board_pose(frames[0], loaded)
    assert pose['reprojection_rms_px'] < 1
    assert np.linalg.det(pose['R']) == pytest.approx(1)
    assert np.dot(np.array(pose['R'])[:, 2], pose['t_mm']) < 0


def test_insufficient_samples_and_changed_camera():
    with pytest.raises(CalibrationError, match='10'):
        calibrate_frames([])
    calibration = CameraCalibration(np.eye(3), np.zeros(5), (1280, 720), 0, .1)
    with pytest.raises(CalibrationError, match='selection changed'):
        calibration.validate_frame(np.zeros((720, 1280)), 1)
    with pytest.raises(CalibrationError, match='resolution changed'):
        calibration.validate_frame(np.zeros((480, 640)))


def test_flat_duplicate_board_does_not_calibrate():
    frames, _ = calibration_frames()
    with pytest.raises(CalibrationError):
        calibrate_frames([frames[0]]*12)


def test_missing_calibration_is_not_replaced_by_guess(tmp_path):
    with pytest.raises(CalibrationError):
        CameraCalibration.load(tmp_path / 'missing.json')
