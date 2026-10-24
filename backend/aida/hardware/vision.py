"""Webcam feedback on a ChArUco board rotating in a fixed plane.

A zero homography maps image pixels back into the stationary table plane, so
perspective is removed without assuming webcam intrinsics. Moving the camera,
tilting the table or changing its height requires setting zero again.
"""
import json
import hashlib
import math
import threading
import sys
from pathlib import Path

import cv2
import numpy as np

from ..scan.charuco import make_board
from .control import TurntableError, remaining


class Webcam:
    """Continuously drain webcam buffers; callers wait for fresh frames by deadline."""
    def __init__(self, index: int, device_id: str | None = None):
        self.index = index
        self.device_id = device_id
        self.condition = threading.Condition()
        self.closed = threading.Event()
        self.sequence = 0
        self.frame = None
        self.error = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name='turntable-webcam')
        self.thread.start()

    def _run(self):
        camera = None
        try:
            backend = cv2.CAP_AVFOUNDATION if sys.platform == 'darwin' else cv2.CAP_ANY
            camera = cv2.VideoCapture(self.index, backend)
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            if not camera.isOpened():
                raise RuntimeError(f'Cannot open webcam index {self.index}.')
            while not self.closed.is_set():
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError('Webcam stopped delivering frames.')
                with self.condition:
                    self.frame = frame
                    self.sequence += 1
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.condition.notify_all()
        finally:
            if camera is not None:
                camera.release()

    def read(self, deadline: float):
        with self.condition:
            # Skip two frames captured after this request to clear buffered exposures.
            required_sequence = self.sequence + 3
            while self.sequence < required_sequence:
                if self.error:
                    raise TurntableError('camera_unavailable', self.error)
                if self.closed.is_set():
                    raise TurntableError('camera_unavailable', 'Webcam has been closed.')
                self.condition.wait(timeout=remaining(deadline))
            remaining(deadline)
            return self.frame.copy()

    def close(self):
        self.closed.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=0.2)


class CharucoFeedback:
    def __init__(self, camera: Webcam, zero_path: Path):
        self.camera = camera
        self.zero_path = zero_path
        self.board, _ = make_board()
        self.detector = cv2.aruco.CharucoDetector(self.board)
        self.reference = None
        self.last_frame = None
        self.calibration = None
        self.calibration_signature = None
        calibration_path = zero_path.parent / 'camera.json'
        if calibration_path.exists():
            from ..scan.camera import CameraCalibration
            self.calibration = CameraCalibration.load(calibration_path)
            self.calibration_signature = hashlib.sha256(calibration_path.read_bytes()).hexdigest()

    def _plane_frame(self, frame):
        if self.calibration is None:
            return frame
        self.calibration.validate_frame(frame, self.camera.index, self.camera.device_id)
        return cv2.undistort(frame, self.calibration.K, self.calibration.distortion)

    def _corners(self, frame):
        corners, ids, _, _ = self.detector.detectBoard(frame)
        if ids is None or len(ids) < 6:
            raise TurntableError('feedback_lost', 'Need at least six visible ChArUco corners.')
        points = self.board.getChessboardCorners()[ids.flatten(), :2] * 1000.0
        if np.any(np.ptp(points, axis=0) < 40):
            raise TurntableError('feedback_lost', 'Visible corners must span 40 mm on both board axes.')
        return points, corners.reshape(-1, 2)

    def load_zero(self):
        try:
            data = json.loads(self.zero_path.read_text())
            if data.get('calibration_signature') != self.calibration_signature:
                raise ValueError('Camera calibration changed; set zero again.')
            matrix = np.asarray(data['homography'], dtype=np.float64)
            if (data['version'] != 1 or matrix.shape != (3, 3) or
                    not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12):
                raise ValueError('Invalid zero reference.')
            self.reference = (np.linalg.inv(matrix), tuple(data['frame_size']))
        except (OSError, ValueError, KeyError, TypeError, np.linalg.LinAlgError) as exc:
            raise TurntableError('zero_required',
                                 'Set turntable zero using POST /api/v1/turntable/zero.', 409) from exc

    def set_zero(self, deadline: float):
        frame = self.camera.read(deadline)
        points, pixels = self._corners(self._plane_frame(frame))
        matrix, mask = cv2.findHomography(points, pixels, cv2.RANSAC, 2.0)
        if matrix is None or mask is None or int(mask.sum()) < 6:
            raise TurntableError('feedback_invalid', 'Cannot establish a reliable table plane.')
        remaining(deadline)
        try:
            inverse = np.linalg.inv(matrix)
        except np.linalg.LinAlgError as exc:
            raise TurntableError('feedback_invalid', 'Zero reference is singular.') from exc
        data = {'version': 1, 'homography': matrix.tolist(), 'calibration_signature': self.calibration_signature,
                'frame_size': list(frame.shape[:2])}
        try:
            self.zero_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.zero_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, indent=2) + '\n')
            temporary.replace(self.zero_path)
        except OSError as exc:
            raise TurntableError('calibration_save_failed', f'Could not save zero reference: {exc}', 500) from exc
        self.reference = (inverse, tuple(frame.shape[:2]))
        self.last_frame = frame

    def read_angle(self, deadline: float) -> float:
        if self.reference is None:
            self.load_zero()
        inverse, frame_size = self.reference
        frame = self.camera.read(deadline)
        if frame.shape[:2] != frame_size:
            raise TurntableError('zero_required', 'Webcam resolution changed; set zero again.', 409)
        points, pixels = self._corners(self._plane_frame(frame))
        in_plane = cv2.perspectiveTransform(pixels.reshape(-1, 1, 2), inverse).reshape(-1, 2)
        transform, inliers = cv2.estimateAffinePartial2D(
            points, in_plane, method=cv2.RANSAC, ransacReprojThreshold=2.0)
        if transform is None or inliers is None or int(inliers.sum()) < 6:
            raise TurntableError('feedback_invalid', 'Board motion does not match the saved table plane.')
        scale = math.hypot(transform[0, 0], transform[1, 0])
        if not 0.95 <= scale <= 1.05:
            raise TurntableError('zero_required', 'Board scale changed; check camera and set zero again.', 409)
        remaining(deadline)
        self.last_frame = frame
        return math.degrees(math.atan2(transform[1, 0], transform[0, 0])) % 360.0
