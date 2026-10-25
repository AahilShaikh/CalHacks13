from contextlib import contextmanager

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aida.api import server
from aida.api.turntable import get_service
from aida.hardware.control import TurntableError
from aida.scan.camera import CameraCalibration
from aida.turntable import TurntableService
from test_camera_calibration import calibration_frames
from test_turntable_control import SimulatedTable


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'simulated')
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(tmp_path))
    monkeypatch.setattr('aida.turntable.time.sleep', lambda _: None)
    frames, K = calibration_frames()
    CameraCalibration(K, np.zeros(5), (1280, 720), 0, .2, 'test-usb-camera').save(tmp_path/'camera.json')
    table = SimulatedTable()
    table.last_frame = frames[0]
    zeros = []
    def set_zero(deadline):
        zeros.append(deadline)
        (tmp_path/'zero.json').write_text(f'zero-{len(zeros)}')
    table.set_zero = set_zero
    @contextmanager
    def session(settings, deadline, require_zero):
        try:
            yield table, table
        finally:
            table.stop(deadline)
    controller = TurntableService(session)
    return controller, table, tmp_path, zeros


def test_startup_observes_current_pose_without_driving_or_assuming_empty(rig):
    controller, table, folder, zeros = rig
    assert controller.initialize_zero()['status'] == 'ready'
    assert len(zeros) == 1
    assert table.stops == 2
    assert not table.pulses
    assert controller.status()['zero_ready']
    assert not controller.status()['empty_reference_ready']
    assert not (folder/'empty_table.png').exists()


def test_failed_startup_prevents_use_of_previous_zero(rig):
    controller, table, folder, _ = rig
    (folder/'zero.json').write_text('old-zero')
    (folder/'camera.json').unlink()
    assert controller.initialize_zero()['status'] == 'failed'
    with pytest.raises(TurntableError) as error:
        controller.rotate(90)
    assert error.value.code == 'zero_required'
    assert not table.pulses
    assert not table.stops


def test_empty_reference_does_not_reset_zero_and_survives_restart_zero(rig):
    controller, table, folder, zeros = rig
    controller.initialize_zero()
    original = (folder/'zero.json').read_bytes()
    result = controller.capture_empty_reference()
    assert result['zero_changed'] is False
    assert (folder/'zero.json').read_bytes() == original
    assert controller.status()['empty_reference_ready']
    controller.initialize_zero()
    assert len(zeros) == 2
    assert (folder/'zero.json').read_bytes() != original
    assert controller.status()['empty_reference_ready']
    assert not table.pulses


def test_reference_invalidated_by_changed_camera_calibration(rig):
    controller, _, folder, _ = rig
    controller.initialize_zero()
    controller.capture_empty_reference()
    calibration = CameraCalibration.load(folder/'camera.json')
    calibration.rms_px = .3
    calibration.save(folder/'camera.json')
    assert not controller.status()['empty_reference_ready']


def test_busy_retry_preserves_valid_zero(rig):
    controller, _, _, _ = rig
    controller.initialize_zero()
    with controller._exclusive(), pytest.raises(TurntableError) as error:
        controller.initialize_zero()
    assert error.value.code == 'busy'
    assert controller.startup_zero['status'] == 'ready'


def test_moving_board_does_not_become_ready(rig):
    controller, table, _, _ = rig
    table.read_angle = lambda _: 2.0
    result = controller.initialize_zero()
    assert result['status'] == 'failed'
    assert result['code'] == 'table_unstable'
    assert not table.pulses


def test_initialize_and_reference_api(rig):
    controller, _, folder, _ = rig
    server.app.dependency_overrides[get_service] = lambda: controller
    try:
        with TestClient(server.app) as http:
            assert http.post('/api/v1/turntable/initialize').status_code == 200
            zero = (folder/'zero.json').read_bytes()
            assert http.post('/api/v1/turntable/reference', json={}).status_code == 200
            assert (folder/'zero.json').read_bytes() == zero
            status = http.get('/api/v1/turntable/status').json()
            assert status['zero_ready'] and status['empty_reference_ready']
    finally:
        server.app.dependency_overrides.pop(get_service)


@pytest.mark.parametrize('enabled', [True, False])
def test_server_lifespan_automatically_initializes_once(monkeypatch, enabled):
    seen = []
    class Controller:
        def initialize_zero(self):
            seen.append(True)
            return {'status':'ready'}
    monkeypatch.setattr(server, 'turntable_service', Controller())
    monkeypatch.setenv('AIDA_TURNTABLE_AUTO_ZERO', '1' if enabled else '0')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with TestClient(server.app) as http:
        assert http.get('/setup.html').status_code == 200
    assert seen == ([True] if enabled else [])
