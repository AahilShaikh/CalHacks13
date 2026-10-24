"""USB camera selection shared by preview, calibration, feedback and scanning."""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .control import TurntableError

_build_lock = threading.Lock()


def camera_storage():
    return Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable'))


def hardware_preferences():
    path = camera_storage() / 'hardware.json'
    return json.loads(path.read_text()) if path.exists() else {}


def list_cameras():
    if sys.platform != 'darwin':
        override = os.environ.get('AIDA_TURNTABLE_CAMERA')
        return [] if override is None else [{'index': int(override), 'device_id': f'index:{override}',
                                             'name': f'USB camera index {override}', 'is_usb': True}]
    source = Path(__file__).with_name('camera_devices.m')
    folder = camera_storage() / 'camera-tools'
    executable = folder / 'devices'
    try:
        with _build_lock:
            if not executable.exists() or executable.stat().st_mtime < source.stat().st_mtime:
                folder.mkdir(parents=True, exist_ok=True)
                temporary = folder / f'devices-{uuid.uuid4().hex}'
                subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wno-deprecated-declarations',
                                str(source), '-framework', 'AVFoundation', '-framework', 'Foundation',
                                '-o', str(temporary)], check=True, capture_output=True, timeout=45)
                temporary.replace(executable)
        result = subprocess.run([str(executable.resolve())], check=True, capture_output=True, timeout=15)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise TurntableError('camera_discovery_failed',
                             'Cannot enumerate cameras. On macOS install Xcode Command Line Tools '
                             '(xcode-select --install), then refresh camera devices.') from exc


def selected_camera(devices=None):
    devices = list_cameras() if devices is None else devices
    chosen_id = os.environ.get('AIDA_TURNTABLE_CAMERA_ID') or hardware_preferences().get('camera_id')
    override = os.environ.get('AIDA_TURNTABLE_CAMERA')
    if chosen_id:
        matches = [d for d in devices if d['device_id'] == chosen_id]
    elif override is not None:
        try:
            index = int(override)
        except ValueError as exc:
            raise TurntableError('configuration_error', 'AIDA_TURNTABLE_CAMERA must be an integer.') from exc
        matches = [d for d in devices if d['index'] == index]
    else:
        matches = [d for d in devices if d['is_usb']]
    if len(matches) != 1 or not matches[0]['is_usb']:
        raise TurntableError('usb_camera_required',
                             'Connect the USB webcam, refresh camera devices, and select it at /setup.html. '
                             'The built-in and Continuity cameras are not used.', 409)
    return matches[0]
