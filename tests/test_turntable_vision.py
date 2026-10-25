"""Real OpenCV feedback from synthetic perspective-projected rotating boards."""
import math
import time

import cv2
import numpy as np
import pytest

from aida.hardware.control import TurntableError, angle_error
from aida.hardware.vision import CharucoFeedback
from aida.scan.charuco import make_board


class Camera:
    def __init__(self, frame):
        self.frame = frame

    def read(self, deadline):
        return self.frame.copy()


def board_frame(angle=0):
    board, _ = make_board()
    board_pixels = board.generateImage((1000, 1000), marginSize=0)
    # Map 200 mm of board into a fixed perspective view of the plane.
    source = np.float32([[0, 0], [200, 0], [200, 200], [0, 200]])
    destination = np.float32([[280, 220], [1110, 320], [1050, 1020], [220, 1100]])
    view = cv2.getPerspectiveTransform(source, destination)
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    rotate = np.array([[c, -s, 100 - c*100 + s*100],
                       [s, c, 100 - s*100 - c*100], [0, 0, 1]])
    pixels_to_mm = np.diag([0.2, 0.2, 1])
    return cv2.warpPerspective(board_pixels, view @ rotate @ pixels_to_mm,
                               (1400, 1400), borderValue=255)


@pytest.mark.parametrize('angle', [0, 30, 90, 179, 270, 355])
def test_angle_relative_to_saved_zero_under_perspective(tmp_path, angle):
    camera = Camera(board_frame())
    feedback = CharucoFeedback(camera, tmp_path / 'zero.json')
    feedback.set_zero(time.monotonic() + 5)
    camera.frame = board_frame(angle)
    # Reload reference to exercise persistence across HTTP requests.
    feedback = CharucoFeedback(camera, tmp_path / 'zero.json')
    measured = feedback.read_angle(time.monotonic() + 5)
    assert abs(angle_error(angle, measured)) < 0.3
    assert feedback.last_frame is not None


def test_occluded_board_fails(tmp_path):
    camera = Camera(np.full((1000, 1000, 3), 255, dtype=np.uint8))
    with pytest.raises(TurntableError, match='six visible'):
        CharucoFeedback(camera, tmp_path / 'zero.json').set_zero(time.monotonic() + 5)


def test_missing_zero_does_not_read_camera(tmp_path):
    camera = Camera(None)
    with pytest.raises(TurntableError) as error:
        CharucoFeedback(camera, tmp_path / 'zero.json').read_angle(time.monotonic() + 5)
    assert error.value.code == 'zero_required'


def test_changed_resolution_requires_zero_again(tmp_path):
    camera = Camera(board_frame())
    feedback = CharucoFeedback(camera, tmp_path / 'zero.json')
    feedback.set_zero(time.monotonic() + 5)
    camera.frame = camera.frame[:1000]
    with pytest.raises(TurntableError, match='resolution changed'):
        feedback.read_angle(time.monotonic() + 5)


@pytest.mark.parametrize('platform,backend', [('win32',cv2.CAP_MSMF),('darwin',cv2.CAP_AVFOUNDATION),('linux',cv2.CAP_ANY)])
def test_webcam_uses_native_backend_and_releases_capture(monkeypatch, platform, backend):
    from aida.hardware.vision import Webcam
    camera = Webcam(1)
    opened, released = [], []
    class Capture:
        def set(self, *args): pass
        def isOpened(self): return True
        def read(self):
            camera.closed.set()
            return True, np.zeros((720,1280,3),np.uint8)
        def release(self): released.append(True)
    def open_capture(index, selected_backend):
        opened.append((index, selected_backend))
        return Capture()
    monkeypatch.delenv('AIDA_TURNTABLE_CAMERA_FOCUS', raising=False)
    monkeypatch.setattr('aida.hardware.vision.sys.platform', platform)
    monkeypatch.setattr('aida.hardware.vision.cv2.VideoCapture', open_capture)
    camera._run()
    assert opened == [(1,backend)] and released == [True]
    assert camera.sequence == 1 and camera.error is None


def test_webcam_deadline_reports_camera_error_instead_of_turntable_timeout():
    from aida.hardware.vision import Webcam
    camera = Webcam(1)
    with pytest.raises(TurntableError) as error:
        camera.read(time.monotonic() - .01)
    assert error.value.code == 'camera_timeout' and error.value.http_status == 504
    assert 'camera index 1' in str(error.value)
