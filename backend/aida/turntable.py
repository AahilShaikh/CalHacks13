"""Blocking turntable service. No devices are opened on import."""
import asyncio
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .hardware.arduino import ArduinoDrive
from .hardware.control import ControlConfig, PositionController, TurntableError, remaining
from .hardware.cameras import selected_camera

VIEW_ANGLES = {'front': 0.0, 'left': 90.0, 'back': 180.0, 'right': 270.0}


@dataclass(frozen=True)
class HardwareSettings:
    port: str
    camera_index: int
    neutral_us: int
    storage: Path
    control: ControlConfig
    camera_id: str | None = None
    camera_name: str | None = None

    @classmethod
    def from_env(cls):
        try:
            folder = Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable'))
            saved = json.loads((folder/'hardware.json').read_text()) if (folder/'hardware.json').exists() else {}
            port = os.environ.get('AIDA_TURNTABLE_PORT') or saved.get('port', '')
            if not port:
                raise TurntableError('not_configured', 'Set AIDA_TURNTABLE_PORT to the Arduino USB port.')
            neutral = int(os.environ.get('AIDA_TURNTABLE_NEUTRAL_US', '1450'))
            control = ControlConfig(
                positive_pulse_us=int(os.environ.get('AIDA_TURNTABLE_POSITIVE_US', str(neutral + 100))),
                negative_pulse_us=int(os.environ.get('AIDA_TURNTABLE_NEGATIVE_US', str(neutral - 100))),
                positive_deg_per_s=float(os.environ.get('AIDA_TURNTABLE_POSITIVE_DPS', '90')),
                negative_deg_per_s=float(os.environ.get('AIDA_TURNTABLE_NEGATIVE_DPS', '90')),
                fine_positive_pulse_us=int(os.environ.get('AIDA_TURNTABLE_FINE_POSITIVE_US', str(neutral + 60))),
                fine_negative_pulse_us=int(os.environ.get('AIDA_TURNTABLE_FINE_NEGATIVE_US', str(neutral - 60))),
                fine_positive_deg_per_s=float(os.environ.get('AIDA_TURNTABLE_FINE_POSITIVE_DPS', '30')),
                fine_negative_deg_per_s=float(os.environ.get('AIDA_TURNTABLE_FINE_NEGATIVE_DPS', '30')),
                settle_s=float(os.environ.get('AIDA_TURNTABLE_SETTLE_S', '0.25')),
            )
            if not 1000 <= neutral <= 2000 or neutral in (
                    control.positive_pulse_us, control.negative_pulse_us):
                raise ValueError('Neutral must be in [1000, 2000] and differ from drive pulses.')
            if (control.positive_pulse_us - neutral) * (control.negative_pulse_us - neutral) >= 0:
                raise ValueError('Positive and negative drive pulses must be on opposite sides of neutral.')
            for coarse, fine in ((control.positive_pulse_us, control.fine_positive_pulse_us),
                                 (control.negative_pulse_us, control.fine_negative_pulse_us)):
                if (coarse - neutral) * (fine - neutral) <= 0 or abs(fine-neutral) > abs(coarse-neutral):
                    raise ValueError('Fine pulses must lie between neutral and their corresponding coarse pulses.')
            camera = selected_camera()
            return cls(port, camera['index'], neutral, folder, control,
                       camera_id=camera['device_id'], camera_name=camera['name'])
        except (ValueError, OSError) as exc:
            raise TurntableError('configuration_error', str(exc)) from exc


@contextmanager
def hardware_session(settings: HardwareSettings, deadline: float, require_zero: bool):
    from .hardware.vision import CharucoFeedback, Webcam

    drive = ArduinoDrive(settings.port, settings.neutral_us)
    camera = Webcam(settings.camera_index, settings.camera_id)
    feedback = CharucoFeedback(camera, settings.storage / 'zero.json')
    if require_zero:
        feedback.load_zero()
    operation_error = None
    try:
        drive.connect(deadline)
        camera.start()
        yield drive, feedback
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        # Cleanup gets a bounded allowance after a motion timeout.
        stop_error = None
        if drive.connection is not None:
            try:
                drive.stop(time.monotonic() + 0.5)
            except Exception as exc:
                stop_error = exc
        try:
            camera.close()
        finally:
            drive.close()
        if stop_error is not None:
            message = f'Could not confirm final STOP: {stop_error}'
            if operation_error is not None:
                message += f' Original failure: {operation_error}. Position is unverified.'
            raise TurntableError('stop_unconfirmed', message) from stop_error


class TurntableService:
    def __init__(self, session_factory=hardware_session):
        self._lock = threading.Lock()
        self._session_factory = session_factory

    @contextmanager
    def _exclusive(self):
        if not self._lock.acquire(blocking=False):
            raise TurntableError('busy', 'Another turntable operation is in progress.', 409)
        try:
            yield
        finally:
            self._lock.release()

    def rotate(self, angle: float, *, relative: bool = False, tolerance: float = 1.0,
               timeout_s: float = 30.0, capture: bool = True) -> dict:
        started = time.monotonic()
        deadline = started + timeout_s
        with self._exclusive():
            settings = HardwareSettings.from_env()
            with self._session_factory(settings, deadline, True) as (drive, feedback):
                result = PositionController(drive, feedback, settings.control).rotate(
                    angle, tolerance, deadline, relative)
                data = {'status': 'succeeded', **asdict(result), 'tolerance_degrees': tolerance,
                        'feedback': 'charuco', 'capture_url': None}
                if capture:
                    path = self._save_capture(settings, feedback, data, deadline)
                    data['capture_url'] = f'/api/v1/turntable/captures/{path.name}'
            data['elapsed_s'] = round(time.monotonic() - started, 3)
            return data

    @staticmethod
    def _save_capture(settings, feedback, data, deadline):
        import cv2

        remaining(deadline)
        try:
            folder = settings.storage / 'captures'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'{uuid.uuid4().hex}.jpg'
            # This is the very frame that supplied the final verified angle.
            if not cv2.imwrite(str(path), feedback.last_frame):
                raise TurntableError('capture_failed', 'Could not save the verified webcam frame.', 500)
            data['capture_url'] = f'/api/v1/turntable/captures/{path.name}'
            metadata = {**data, 'status': 'position_verified'}
            path.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
        except (OSError, cv2.error) as exc:
            raise TurntableError('capture_failed', f'Could not save webcam capture: {exc}', 500) from exc
        remaining(deadline)
        return path

    def zero(self, timeout_s: float = 15.0, empty_table_reference: bool = False) -> dict:
        deadline = time.monotonic() + timeout_s
        with self._exclusive():
            settings = HardwareSettings.from_env()
            with self._session_factory(settings, deadline, False) as (drive, feedback):
                drive.stop(deadline)
                if remaining(deadline) <= settings.control.settle_s:
                    raise TurntableError('timeout', 'Timed out before setting zero.', 504)
                time.sleep(settings.control.settle_s)
                feedback.set_zero(deadline)
                if empty_table_reference:
                    import cv2
                    from .scan.camera import CameraCalibration, board_pose
                    calibration = CameraCalibration.load(settings.storage / 'camera.json')
                    frame = feedback.last_frame
                    calibration.validate_frame(frame, settings.camera_index, settings.camera_id)
                    pose = board_pose(frame, calibration)
                    if not cv2.imwrite(str(settings.storage / 'empty_table.png'), frame):
                        raise TurntableError('capture_failed', 'Could not save empty-table reference.', 500)
                    (settings.storage / 'empty_table.json').write_text(json.dumps({
                        'board_pose': pose, 'zero_signature': hashlib.sha256(
                            (settings.storage/'zero.json').read_bytes()).hexdigest()}, indent=2)+'\n')
            return {'status': 'succeeded', 'angle': 0.0, 'feedback': 'charuco'}

    def camera_frame(self, timeout_s=10.0):
        """Calibration/preview capture without opening or moving the Arduino."""
        with self.camera_session() as camera:
            return camera.read(time.monotonic()+timeout_s)

    @contextmanager
    def camera_session(self):
        from .hardware.vision import Webcam
        with self._exclusive():
            selected = selected_camera()
            camera = Webcam(selected['index'], selected['device_id'])
            try:
                camera.start()
                yield camera
            finally:
                camera.close()

    @contextmanager
    def capture_session(self, output_directory, tolerance=1.0, move_timeout_s=30.0):
        """Reserve one object scan; keep camera/serial open between agent calls."""
        from .scan.camera import CameraCalibration

        with self._exclusive():
            settings = HardwareSettings.from_env()
            calibration = CameraCalibration.load(settings.storage / 'camera.json')
            if calibration.camera_id != settings.camera_id:
                raise TurntableError('calibration_required', 'Selected camera differs from its calibration.', 409)
            try:
                reference = json.loads((settings.storage / 'empty_table.json').read_text())
                if reference['zero_signature'] != hashlib.sha256((settings.storage/'zero.json').read_bytes()).hexdigest():
                    raise ValueError('Zero changed')
                if not (settings.storage / 'empty_table.png').is_file():
                    raise ValueError('Reference missing')
            except (OSError, ValueError, KeyError) as exc:
                raise TurntableError('reference_required', 'Set zero with an empty-table reference at /setup.html.', 409) from exc
            output_directory = Path(output_directory)
            output_directory.mkdir(parents=True, exist_ok=True)
            for name in ('empty_table.png', 'empty_table.json'):
                shutil.copy2(settings.storage / name, output_directory / name)
            with self._session_factory(settings, time.monotonic()+move_timeout_s, True) as (drive, feedback):
                yield CaptureSession(drive, feedback, settings, calibration, Path(output_directory),
                                     tolerance, move_timeout_s)


class CaptureSession:
    def __init__(self, drive, feedback, settings, calibration, directory, tolerance, timeout):
        self.controller = PositionController(drive, feedback, settings.control)
        self.feedback = feedback
        self.settings = settings
        self.calibration = calibration
        self.directory = directory
        self.tolerance = tolerance
        self.timeout = timeout
        self.views = {}

    def capture(self, view):
        import cv2
        from .scan.camera import board_pose

        if view not in VIEW_ANGLES:
            raise ValueError('Choose front, left, back, or right.')
        if view in self.views:
            return self.views[view]
        if len(self.views) >= 4:
            raise ValueError('At most four object views per scan.')
        result = self.controller.rotate(VIEW_ANGLES[view], self.tolerance,
                                        time.monotonic()+self.timeout)
        frame = self.feedback.last_frame
        self.calibration.validate_frame(frame, self.settings.camera_index, self.settings.camera_id)
        pose = board_pose(frame, self.calibration)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f'{view}.png'
        if not cv2.imwrite(str(path), frame):
            raise TurntableError('capture_failed', 'Cannot save scan photo.', 500)
        record = {'view': view, 'path': str(path), **asdict(result), 'board_pose': pose}
        (self.directory / f'{view}_pose.json').write_text(json.dumps(record, indent=2)+'\n')
        self.views[view] = record
        return record


service = TurntableService()


async def capture_views(output_directory: Path) -> list[Path]:
    """Capture four scan views after verified moves; a zero reference is required."""
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, angle in (('front', 0), ('left', 90), ('back', 180), ('right', 270)):
        result = await asyncio.to_thread(service.rotate, angle, capture=True)
        source = HardwareSettings.from_env().storage / 'captures' / result['capture_url'].rsplit('/', 1)[1]
        target = output_directory / f'{name}.jpg'
        shutil.copy2(source, target)
        target.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n')
        paths.append(target)
    return paths
