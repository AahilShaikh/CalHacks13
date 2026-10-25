"""Shared photo-only ChArUco locator, usable in Aida and standalone ComfyUI."""
import cv2
import numpy as np


class SegmentationError(ValueError):
    pass


def make_board():
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    return cv2.aruco.CharucoBoard((10, 10), .020, .015, dictionary), dictionary


def clean_object_mask(foreground):
    """Remove registration fringes and fill subtraction holes without a box crop."""
    binary = (foreground > 127).astype(np.uint8) * 255
    size = max(3, round(min(binary.shape) * 0.015)) | 1
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    # Opening disconnects thin cables/fringes. Restore the original contour with
    # constrained dilation, so rounded corners do not shave off the object.
    core = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((size, size), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(core)
    if count < 2:
        raise SegmentationError('No substantial object region detected on the board.')
    component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[component, cv2.CC_STAT_AREA] < binary.size * 0.005:
        raise SegmentationError('Object region is too small; improve lighting or framing.')
    support = cv2.dilate((labels == component).astype(np.uint8), np.ones((size, size), np.uint8))
    mask = cv2.bitwise_and(binary, support * 255)
    # Pad before flood fill: the top-left input pixel may itself be foreground.
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flooded = padded.copy()
    cv2.floodFill(flooded, None, (0, 0), 255)
    return (padded | cv2.bitwise_not(flooded))[1:-1, 1:-1]


def board_object_region(frame):
    """Locate an object prompt from disagreement with the known printed plane.

    This is a loose prompt for SAM, not the final silhouette. It needs neither
    a stored background photograph nor camera intrinsics. The surrounding scene
    is excluded by projecting the ChArUco print from its detected corners.
    """
    board, _ = make_board()
    corners, ids, _, marker_ids = cv2.aruco.CharucoDetector(board).detectBoard(frame)
    if ids is None or len(ids) < 8:
        if marker_ids is not None and len(marker_ids) >= 2:
            raise SegmentationError('Board detected but too few visible corners to locate the object. '
                               'Expose at least eight ChArUco corners.')
        return None
    objects = board.getChessboardCorners()[ids.flatten(), :2] * 1000.
    H, _ = cv2.findHomography(objects, corners.reshape(-1, 2), cv2.RANSAC, 2.)
    if H is None:
        raise SegmentationError('Cannot project the ChArUco print to locate the object.')
    template = board.generateImage((2000, 2000), marginSize=0, borderBits=1)
    height, width = frame.shape[:2]
    warp = H @ np.diag([.1, .1, 1.])
    printed = cv2.warpPerspective(template, warp, (width, height))
    valid = cv2.warpPerspective(np.full_like(template, 255), warp, (width, height))
    # The printed board is clipped to the circular turntable, not its square PDF.
    circle = np.zeros_like(template)
    cv2.circle(circle, (1000, 1000), 980, 255, -1)
    valid &= cv2.warpPerspective(circle, warp, (width, height))
    domain = cv2.erode(valid, np.ones((9, 9), np.uint8)) > 127
    blurred = cv2.GaussianBlur(printed, (0, 0), .7)
    lum = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    black, white = domain & (blurred < 20), domain & (blurred > 235)
    if min(np.count_nonzero(black), np.count_nonzero(white)) < 100:
        raise SegmentationError('Insufficient visible print to locate an object on the board.')
    lo, hi = np.median(lum[black]), np.median(lum[white])
    expected = lo + (hi-lo)*blurred.astype(np.float32)/255.
    residual = cv2.erode(np.abs(lum-expected), np.ones((3, 3), np.uint8))
    candidate = ((residual > 25) & domain).astype(np.uint8)*255
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = clean_object_mask(candidate)
    x, y, w, h = cv2.boundingRect(mask)
    if w*h > np.count_nonzero(domain)*.65:
        raise SegmentationError('Object prompt covers most of the board; improve lighting or framing.')
    # A small margin recovers edges that match a white/black printed square.
    margin = max(6, round(max(w, h)*.04))
    x0, y0 = max(0,x-margin), max(0,y-margin)
    return {'x':x0, 'y':y0, 'width':min(width,x+w+margin)-x0,
            'height':min(height,y+h+margin)-y0}
