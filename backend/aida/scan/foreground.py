"""Separate the object from the rotating printed board using an empty-table photo."""
import cv2
import numpy as np

from .scaling import ScalingError


def object_foreground(frame, pose, empty_frame, empty_pose, calibration):
    calibration.validate_frame(frame)
    calibration.validate_frame(empty_frame)
    K, dist = calibration.K, calibration.distortion
    current = cv2.undistort(frame, K, dist)
    empty = cv2.undistort(empty_frame, K, dist)
    def homography(record):
        R = np.asarray(record['R'])
        return K @ np.column_stack((R[:, :2], np.asarray(record['t_mm'])))
    H = homography(pose) @ np.linalg.inv(homography(empty_pose))
    height, width = frame.shape[:2]
    warped = cv2.warpPerspective(empty, H, (width, height))
    a = np.linspace(0, 2*np.pi, 100, endpoint=False)
    circle = np.column_stack((100+102*np.cos(a), -100+102*np.sin(a), np.zeros_like(a)))
    R, t = np.asarray(pose['R']), np.asarray(pose['t_mm'])
    projected = (circle @ R.T + t) @ K.T
    pixels = projected[:, :2] / projected[:, 2:3]
    board_region = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(board_region, np.rint(pixels).astype(np.int32), 255)
    expected = empty.copy()
    expected[board_region > 0] = warped[board_region > 0]
    # Correct modest automatic exposure shifts from the static background.
    difference = current.astype(np.float32)-expected.astype(np.float32)
    outside = board_region == 0
    offset = np.median(difference[outside], axis=0) if np.any(outside) else np.zeros(3)
    delta = np.max(np.abs(difference-offset), axis=2)
    mask = (delta > 30).astype(np.uint8)*255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = [i for i in range(1, count)
                  if np.count_nonzero((labels == i) & (board_region > 0)) > 30]
    if not candidates:
        raise ScalingError('No object separated from the empty table. Check lighting, camera position and reference.')
    component = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    ideal_mask = (labels == component).astype(np.uint8)*255
    # Return to original raw pixel coordinates; metric fitting removes distortion once.
    y, x = np.mgrid[:height, :width]
    raw_pixels = np.stack((x, y), axis=-1).astype(np.float32).reshape(-1, 1, 2)
    ideal_pixels = cv2.undistortPoints(raw_pixels, K, dist, P=K).reshape(height, width, 2)
    return cv2.remap(ideal_mask, ideal_pixels[:, :, 0], ideal_pixels[:, :, 1], cv2.INTER_NEAREST)
