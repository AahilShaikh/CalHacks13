import json
from contextlib import contextmanager

import cv2
import pytest
from fastapi.testclient import TestClient

from aida.api.server import app
from aida.api.turntable import get_service
from aida.turntable import TurntableService
from test_camera_calibration import calibration_frames


@pytest.fixture()
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE',str(tmp_path))
    monkeypatch.setenv('AIDA_TURNTABLE_CAMERA','0')
    frames,_ = calibration_frames()
    index = [0]
    class Camera:
        def read(self,deadline):
            frame = frames[index[0] % len(frames)]
            index[0] += 1
            return frame
    class Controller(TurntableService):
        @contextmanager
        def camera_session(self):
            with self._exclusive():
                yield Camera()
    controller = Controller()
    app.dependency_overrides[get_service] = lambda:controller
    with TestClient(app) as http:
        yield http,controller,tmp_path
    app.dependency_overrides.pop(get_service)


def test_preview_sample_solve_and_reset(client):
    http,_,folder = client
    preview = http.get('/api/v1/camera/frame.jpg')
    assert preview.status_code == 200
    assert preview.headers['content-type'] == 'image/jpeg'
    for _ in range(14):
        response = http.post('/api/v1/camera/calibration/samples')
        assert response.status_code == 200,response.text
    response = http.post('/api/v1/camera/calibration/solve')
    assert response.status_code == 200,response.text
    assert response.json()['calibration']['camera_index'] == 0
    assert (folder/'camera.json').exists()
    assert http.get('/api/v1/camera/calibration').json()['samples'] == 14
    assert http.delete('/api/v1/camera/calibration/samples').status_code == 200
    assert not list((folder/'calibration_samples').glob('*.png'))
    assert (folder/'camera.json').exists()


def test_unready_calibration_is_structured_error(client):
    http,_,_ = client
    response = http.post('/api/v1/camera/calibration/solve')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'calibration_required'


def test_camera_cannot_be_used_during_scan(client):
    http,controller,_ = client
    with controller._exclusive():
        response = http.get('/api/v1/camera/frame.jpg')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'busy'
