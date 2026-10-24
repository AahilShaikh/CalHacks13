import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aida.api.server import app
from aida.hardware.cameras import selected_camera
from aida.hardware.control import TurntableError
from aida.scan.camera import CameraCalibration
from aida.scan.charuco import CalibrationError

BUILTIN = {'index':0,'device_id':'builtin','name':'MacBook Camera','is_usb':False}
USB = {'index':1,'device_id':'usb-123','name':'USB Webcam','is_usb':True}


@pytest.fixture()
def camera_env(tmp_path,monkeypatch):
    monkeypatch.delenv('AIDA_TURNTABLE_CAMERA_ID',raising=False)
    monkeypatch.delenv('AIDA_TURNTABLE_CAMERA',raising=False)
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE',str(tmp_path))
    monkeypatch.setattr('aida.hardware.cameras.list_cameras',lambda:[BUILTIN,USB])
    return tmp_path


def test_auto_selects_usb_instead_of_builtin(camera_env):
    assert selected_camera()['device_id'] == 'usb-123'


def test_missing_usb_never_falls_back_to_builtin(camera_env):
    with pytest.raises(TurntableError) as error:
        selected_camera([BUILTIN])
    assert error.value.code == 'usb_camera_required'


def test_camera_identity_survives_index_reordering(camera_env):
    (camera_env/'hardware.json').write_text(json.dumps({'camera_id':'usb-123'}))
    assert selected_camera([{**USB,'index':0},{**BUILTIN,'index':1}])['index'] == 0
    calibration=CameraCalibration(np.eye(3),np.zeros(5),(640,480),1,.1,'usb-123')
    calibration.validate_frame(np.zeros((480,640,3)),0,'usb-123')
    with pytest.raises(CalibrationError):
        calibration.validate_frame(np.zeros((480,640,3)),1,'builtin')


def test_legacy_builtin_index_override_is_rejected(camera_env,monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_CAMERA','0')
    with pytest.raises(TurntableError): selected_camera()


def test_multiple_usb_devices_require_choice(camera_env):
    with pytest.raises(TurntableError): selected_camera([USB,{**USB,'device_id':'usb-456','index':2}])


def test_selecting_usb_preserves_serial_port(camera_env):
    (camera_env/'hardware.json').write_text(json.dumps({'port':'/dev/cu.usbmodem101'}))
    with TestClient(app) as http:
        response=http.post('/api/v1/camera/selection',json={'device_id':'usb-123'})
        assert response.status_code == 200,response.text
        assert response.json()['selected']['is_usb']
        assert http.post('/api/v1/camera/selection',json={'device_id':'builtin'}).status_code == 409
    assert json.loads((camera_env/'hardware.json').read_text()) == {
        'port':'/dev/cu.usbmodem101','camera_id':'usb-123'}


def test_old_camera_calibration_requires_replacement(camera_env):
    CameraCalibration(np.eye(3),np.zeros(5),(640,480),0,.1).save(camera_env/'camera.json')
    with TestClient(app) as http:
        assert not http.get('/api/v1/camera/calibration').json()['calibration']['camera_matches']
