"""Partner-owned Arduino turntable and webcam integration.

Starter module only; no serial connection or hardware motion happens on import.
A continuous-rotation servo accepts speed/direction, not an absolute angle.
The partner can define the serial protocol and implement these functions here.
"""
from pathlib import Path


async def capture_views(output_directory: Path) -> list[Path]:
    """Rotate, stop, allow settling, and capture webcam images into this directory.

    TODO: connect to the Arduino, coordinate motion/capture, and return image paths.
    Keep camera pose/estimated turntable angle metadata alongside the images.
    """
    raise NotImplementedError('Turntable and capture pipeline is ready for partner implementation')


async def stop_turntable() -> None:
    """TODO: send the serial stop command; also use this during error cleanup."""
    raise NotImplementedError('Turntable stop command is not implemented yet')
