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
