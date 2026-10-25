import os
import sys

if sys.platform == 'win32':
    # Media Foundation webcams take ~12 s to open with hardware transforms on.
    # OpenCV reads this once, so it must be set before cv2 opens any camera.
    os.environ.setdefault('OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS', '0')
