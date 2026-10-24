"""ChArUco detection tests on synthetic board images (no camera needed)."""
import cv2
import numpy as np
import pytest

from aida.scan import charuco


@pytest.fixture()
def board_image(tmp_path):
    board, _ = charuco.make_board()
    img = board.generateImage((1000, 1000), marginSize=10)
    path = tmp_path / 'board.png'
    cv2.imwrite(str(path), img)
    return path


def test_detect_ground_plane_on_board(board_image):
    info = charuco.detect_charuco_ground_plane(board_image)
    assert info['num_corners_detected'] >= 4
    # 200 mm of grid across ~1000 px -> about 5 px/mm.
    assert 3.0 < info['px_per_mm_ground'] < 7.0
    assert info['camera_distance_mm'] > 0
    assert info['R'].shape == (3, 3)


def test_detect_rejects_image_without_board(tmp_path):
    blank = tmp_path / 'blank.png'
    cv2.imwrite(str(blank), np.full((480, 640, 3), 255, dtype=np.uint8))
    with pytest.raises(charuco.CalibrationError):
        charuco.detect_charuco_ground_plane(blank)


def test_detect_missing_file():
    with pytest.raises(FileNotFoundError):
        charuco.detect_charuco_ground_plane('/does/not/exist.png')


def test_inspect_image_writes_annotation(board_image, tmp_path):
    out = tmp_path / 'annotated.jpg'
    report = charuco.inspect_image(board_image, out)
    assert out.is_file()
    assert len(report['corner_ids']) >= 4
    assert len(report['marker_ids']) >= 4
    assert report['camera_distance_mm'] > 0
    assert report['annotated_image'] == str(out)
