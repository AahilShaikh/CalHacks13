"""Measured webcam intrinsics and metric board poses; no guessed camera model."""
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .charuco import CalibrationError, make_board


@dataclass
class CameraCalibration:
    K: np.ndarray
    distortion: np.ndarray
    size: tuple[int, int]  # width, height
    camera_index: int
    rms_px: float
    camera_id: str | None = None

    def validate_frame(self, frame, camera_index=None, camera_id=None):
        if (frame.shape[1], frame.shape[0]) != self.size:
            raise CalibrationError('Camera resolution changed; recalibrate the webcam.')
        if camera_id is not None and camera_id != self.camera_id:
            raise CalibrationError('Camera selection changed; recalibrate the USB webcam.')
        if camera_id is None and camera_index is not None and camera_index != self.camera_index:
            raise CalibrationError('Camera selection changed; recalibrate the webcam.')

    def to_dict(self):
        return {'version': 1, 'K': self.K.tolist(), 'distortion': self.distortion.tolist(),
                'size': list(self.size), 'camera_index': self.camera_index,
                'rms_px': self.rms_px, 'camera_id': self.camera_id, 'board': 'aida-10x10-20mm-15mm-5x5_100'}

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.to_dict(), indent=2) + '\n')
        temporary.replace(path)

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text())
            result = cls(np.asarray(data['K'], dtype=float),
                         np.asarray(data['distortion'], dtype=float),
                         tuple(data['size']), int(data['camera_index']), float(data['rms_px']), data.get('camera_id'))
            if (data['version'] != 1 or data['board'] != 'aida-10x10-20mm-15mm-5x5_100' or
                    result.K.shape != (3, 3) or not np.isfinite(result.K).all() or
                    not np.isfinite(result.distortion).all() or
                    result.distortion.size not in (4, 5, 8, 12, 14) or
                    result.K[0, 0] <= 0 or result.K[1, 1] <= 0 or
                    len(result.size) != 2 or min(result.size) <= 0 or
                    not 0 <= result.rms_px <= 1.0):
                raise ValueError('Invalid camera calibration')
            return result
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise CalibrationError('Calibrate the webcam at /setup.html before an automatic scan.') from exc


def board_points(frame):
    board, _ = make_board()
    corners, ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(frame)
    if ids is None or len(ids) < 8:
        raise CalibrationError('Need at least eight sharp, visible ChArUco corners.')
    objects = board.getChessboardCorners()[ids.flatten()] * 1000.0
    if np.any(np.ptp(objects[:, :2], axis=0) < 60):
        raise CalibrationError('Visible board corners must span at least 60 mm in both directions.')
    return objects.astype(np.float32), corners.reshape(-1, 2).astype(np.float32)


def calibrate_frames(frames, camera_index=0, camera_id=None):
    """Solve from varied board tilts, rejecting a flat-board orbit as degenerate."""
    if len(frames) < 10:
        raise CalibrationError('Take at least 10 calibration samples at varied tilts and positions.')
    size = (frames[0].shape[1], frames[0].shape[0])
    objects, pixels = [], []
    for frame in frames:
        if (frame.shape[1], frame.shape[0]) != size:
            raise CalibrationError('All calibration samples must have the same resolution.')
        obj, image = board_points(frame)
        objects.append(obj)
        pixels.append(image)
    try:
        rms, K, distortion, rvecs, tvecs = cv2.calibrateCamera(
            objects, pixels, size, None, None, flags=cv2.CALIB_FIX_K3)
    except cv2.error as exc:
        raise CalibrationError('Calibration could not be solved; use sharper samples at varied tilts.') from exc
    if not np.isfinite(rms) or rms > 1.0:
        raise CalibrationError(f'Calibration reprojection error is {rms:.2f} px; retake sharper samples.')
    normals = np.array([cv2.Rodrigues(r)[0][:, 2] for r in rvecs])
    # Rotation about the board normal provides no independent intrinsic constraints.
    if np.linalg.svd(normals - normals.mean(axis=0), compute_uv=False)[1] < 0.12:
        raise CalibrationError('Tilt the board in both directions; flat turntable rotation is insufficient.')
    if not (0.2*size[0] < K[0, 0] < 5*size[0] and
            0.2*size[0] < K[1, 1] < 5*size[0] and
            0 < K[0, 2] < size[0] and 0 < K[1, 2] < size[1]):
        raise CalibrationError('Unreliable camera intrinsics; vary board tilts and frame coverage.')
    errors = []
    for obj, image, r, t in zip(objects, pixels, rvecs, tvecs):
        projected, _ = cv2.projectPoints(obj, r, t, K, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2)-image)**2, axis=1)))))
    result = CameraCalibration(K, distortion, size, camera_index, float(rms), camera_id)
    return result, {'samples': len(frames), 'rms_px': float(rms), 'per_sample_rms_px': errors}


def board_pose(frame, calibration):
    """Board-to-camera transform in millimeters, including per-view fit evidence."""
    calibration.validate_frame(frame)
    objects, pixels = board_points(frame)
    ok, rvec, tvec = cv2.solvePnP(objects, pixels, calibration.K, calibration.distortion)
    if not ok:
        raise CalibrationError('Could not solve the ChArUco board pose.')
    projected, _ = cv2.projectPoints(objects, rvec, tvec, calibration.K, calibration.distortion)
    error = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2)-pixels)**2, axis=1))))
    R = cv2.Rodrigues(rvec)[0]
    if error > 1.5 or np.any((objects @ R.T + tvec.reshape(3))[:, 2] <= 0):
        raise CalibrationError(f'Unreliable board pose ({error:.2f} px reprojection error).')
    # Flip Y and Z to keep a right-handed frame with positive height above print.
    # The 200 mm board occupies X=[0,200], Y=[-200,0], Z=0.
    above_board = np.diag([1.0, -1.0, -1.0])
    return {'R': (R @ above_board).tolist(), 't_mm': tvec.reshape(3).tolist(),
            'reprojection_rms_px': error, 'corners': len(objects)}
