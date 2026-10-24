"""USB webcam selection, preview, varied board samples and measured calibration."""
import json
import os
import time
import uuid
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..scan.camera import CameraCalibration, board_points, calibrate_frames
from ..scan.charuco import CalibrationError
from ..turntable import TurntableService
from ..hardware import cameras
from ..hardware.cameras import selected_camera, hardware_preferences
from ..hardware.control import TurntableError
from .turntable import get_service

router = APIRouter(prefix='/api/v1/camera', tags=['camera setup'])


def storage():
    return Path(os.environ.get('AIDA_TURNTABLE_STORAGE', 'artifacts/turntable'))


def selection_status():
    devices = cameras.list_cameras()
    try:
        selected, error = selected_camera(devices), None
    except TurntableError as exc:
        selected, error = None, str(exc)
    return {'devices': devices, 'selected': selected, 'selection_error': error}


class CameraRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device_id: str


@router.get('/devices')
def devices():
    return selection_status()


@router.post('/selection')
def select_camera(request: CameraRequest, controller: TurntableService = Depends(get_service)):
    with controller._exclusive():
        available = cameras.list_cameras()
        selected = next((d for d in available if d['device_id'] == request.device_id and d['is_usb']), None)
        if selected is None:
            raise HTTPException(409, 'Select a connected USB camera, then refresh the preview.')
        override = os.environ.get('AIDA_TURNTABLE_CAMERA_ID')
        index_override = os.environ.get('AIDA_TURNTABLE_CAMERA')
        if ((override and override != request.device_id) or
                (index_override is not None and int(index_override) != selected['index'])):
            raise HTTPException(409, 'Camera environment settings override this choice. Remove the old '
                                'AIDA_TURNTABLE_CAMERA / CAMERA_ID override and restart the API.')
        saved = hardware_preferences()
        saved['camera_id'] = selected['device_id']
        storage().mkdir(parents=True, exist_ok=True)
        temporary = storage() / 'hardware.tmp'
        temporary.write_text(json.dumps(saved)+'\n')
        temporary.replace(storage() / 'hardware.json')
        return selection_status()


@router.get('/frame.jpg')
def preview(controller: TurntableService = Depends(get_service)):
    frame = controller.camera_frame()
    ok, encoded = cv2.imencode('.jpg', frame)
    if not ok:
        raise HTTPException(500, 'Cannot encode camera preview')
    return Response(encoded.tobytes(), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.get('/calibration')
def status():
    selection = selection_status()
    samples = list((storage() / 'calibration_samples').glob('*.png'))
    try:
        calibration = CameraCalibration.load(storage() / 'camera.json')
        data = calibration.to_dict()
        data['camera_matches'] = (selection['selected'] is not None and
                                  calibration.camera_id == selection['selected']['device_id'])
    except CalibrationError:
        data = None
    return {**selection, 'camera_index': selection['selected']['index'] if selection['selected'] else None,
            'samples': len(samples), 'calibration': data}


@router.post('/calibration/samples')
def sample(controller: TurntableService = Depends(get_service)):
    with controller.camera_session() as camera:
        selected = selected_camera()
        frame = camera.read(time.monotonic()+10)
        objects, _ = board_points(frame)
        folder = storage() / 'calibration_samples'
        folder.mkdir(parents=True, exist_ok=True)
        if len(list(folder.glob('*.png'))) >= 40:
            raise HTTPException(409, 'Sample limit reached; solve or reset calibration samples.')
        path = folder / f'{uuid.uuid4().hex}.png'
        if not cv2.imwrite(str(path), frame):
            raise HTTPException(500, 'Cannot save calibration photo')
        path.with_suffix('.json').write_text(json.dumps({
            'camera_index': selected['index'], 'camera_id': selected['device_id'],
            'size': [frame.shape[1], frame.shape[0]]}))
        return {'sample_id': path.stem, 'corners': len(objects),
                'samples': len(list(folder.glob('*.png')))}


@router.post('/calibration/solve')
def solve(controller: TurntableService = Depends(get_service)):
    with controller._exclusive():
        paths = sorted((storage() / 'calibration_samples').glob('*.png'))
        selected = selected_camera()
        index = selected['index']
        for path in paths:
            if json.loads(path.with_suffix('.json').read_text()).get('camera_id') != selected['device_id']:
                raise CalibrationError('Samples belong to a different camera; reset and recapture.')
        frames = [cv2.imread(str(p)) for p in paths]
        if any(frame is None for frame in frames):
            raise CalibrationError('Calibration sample is unreadable; reset and recapture.')
        calibration, report = calibrate_frames(frames, index, selected['device_id'])
        calibration.save(storage() / 'camera.json')
        (storage() / 'camera_report.json').write_text(json.dumps(report, indent=2)+'\n')
        return {'status': 'succeeded', 'calibration': calibration.to_dict(), 'report': report}


@router.delete('/calibration/samples')
def reset_samples(controller: TurntableService = Depends(get_service)):
    with controller._exclusive():
        folder = storage() / 'calibration_samples'
        for path in folder.glob('*'):
            if path.suffix in ('.png', '.json') and len(path.stem) == 32:
                path.unlink()
        return {'status': 'succeeded', 'samples': 0}
