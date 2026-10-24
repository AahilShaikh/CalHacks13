"""Image-to-mesh acquisition, metric calibration, and CAD handoff artifacts.

Modular pipeline originally developed as ``cad_calibration`` in the CalHacks
project. The ComfyUI/Pixal3D runtime stays external (``COMFY_HOST``); this
package owns the client, the ChArUco calibration, metric scaling, blueprint
rendering, and the orchestration used by both the ``aida scan`` CLI and the
job-based HTTP API in :mod:`aida.api`.
"""
from .charuco import (CalibrationError, detect_charuco_ground_plane, inspect_image,
                      make_board)
from .comfy import ComfyClient, ComfyConfig, ComfyError
from .pipeline import ScanResult, agent_prompt_for, run_scan
from .scaling import ScalingError, export_stl, scale_mesh

__all__ = [
    'CalibrationError', 'ComfyClient', 'ComfyConfig', 'ComfyError',
    'ScalingError', 'ScanResult', 'agent_prompt_for', 'detect_charuco_ground_plane',
    'export_stl', 'inspect_image', 'make_board', 'run_scan', 'scale_mesh',
]
