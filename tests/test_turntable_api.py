"""Blocking HTTP integration with simulated hardware, real JPEG persistence."""
import threading
from contextlib import contextmanager

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aida.api import server
from aida.api.turntable import get_service
from aida.hardware.control import TurntableError
from aida.turntable import TurntableService
from test_turntable_control import SimulatedTable


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'simulated')
    monkeypatch.setenv('AIDA_TURNTABLE_NEUTRAL_US', '1500')
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(tmp_path))
    monkeypatch.setattr('aida.hardware.control.time.sleep', lambda _: None)
    from aida.api.cad_sessions import CadSessionManager
    from test_cad_sessions import stub_runner_factory
    monkeypatch.setattr(server, 'cad_manager', CadSessionManager(tmp_path / 'cad', runner=stub_runner_factory([])))
    table = SimulatedTable()
    table.last_frame = np.full((48, 64, 3), 125, dtype=np.uint8)
    table.zeroed = False
    table.set_zero = lambda _: setattr(table, 'zeroed', True)
    @contextmanager
    def session(settings, deadline, require_zero):
        try:
            yield table, table
        finally:
            table.stop(deadline)
    service = TurntableService(session)
    server.app.dependency_overrides[get_service] = lambda: service
    with TestClient(server.app) as http:
        yield http, table, service, tmp_path
    server.app.dependency_overrides.pop(get_service)


def test_blocks_until_aligned_and_photo_saved(client):
    http, table, _, folder = client
    response = http.post('/api/v1/turntable/rotate', json={'angle': 90})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'succeeded'
    assert abs(data['error_degrees']) <= 1
    assert table.stops >= 2
    assert table.pulses
    download = http.get(data['capture_url'])
    assert download.status_code == 200
    assert download.headers['content-type'] == 'image/jpeg'
    assert download.content.startswith(b'\xff\xd8')
    assert len(list((folder / 'captures').glob('*.json'))) == 1


@pytest.mark.parametrize('payload', [
    {}, {'angle': 'NaN'}, {'angle': 'inf'}, {'angle': 20, 'timeout_s': 0},
    {'angle': 20, 'tolerance_degrees': 0}, {'angle': 20, 'extra': True},
])
def test_bad_input_never_moves_motor(client, payload):
    http, table, _, _ = client
    assert http.post('/api/v1/turntable/rotate', json=payload).status_code == 422
    assert not table.pulses
    assert not table.stops


def test_zero_stops_motor_and_sets_reference(client):
    http, table, _, _ = client
    response = http.post('/api/v1/turntable/zero', json={})
    assert response.status_code == 200
    assert table.zeroed
    assert table.stops == 2


def test_busy_request_is_rejected_without_hardware_access(client):
    http, table, service, _ = client
    with service._exclusive():
        response = http.post('/api/v1/turntable/rotate', json={'angle': 90})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'busy'
    assert table.stops == 0


@pytest.mark.parametrize(('code', 'status'), [('timeout', 504), ('feedback_lost', 503)])
def test_motion_error_stops_and_does_not_save_photo(client, code, status):
    http, table, _, folder = client
    def fail(_):
        raise TurntableError(code, 'Simulated failure', status)
    table.read_angle = fail
    response = http.post('/api/v1/turntable/rotate', json={'angle': 90})
    assert response.status_code == status
    assert response.json()['detail']['code'] == code
    assert table.stops >= 2
    assert not list(folder.rglob('*.jpg'))


def test_capture_write_failure_is_an_error_and_releases_lock(client, monkeypatch):
    http, table, _, _ = client
    monkeypatch.setattr('cv2.imwrite', lambda *_: False)
    response = http.post('/api/v1/turntable/rotate', json={'angle': 0})
    assert response.status_code == 500
    assert response.json()['detail']['code'] == 'capture_failed'
    assert table.stops == 2
    assert http.post('/api/v1/turntable/zero', json={}).status_code == 200


def test_real_concurrent_request_gets_busy(client):
    http, table, _, _ = client
    entered = threading.Event()
    release = threading.Event()
    original_read = table.read_angle
    def slow_read(deadline):
        entered.set()
        assert release.wait(5)
        return original_read(deadline)
    table.read_angle = slow_read
    results = []
    worker = threading.Thread(target=lambda: results.append(
        http.post('/api/v1/turntable/rotate', json={'angle': 0})))
    worker.start()
    try:
        assert entered.wait(5)
        response = http.post('/api/v1/turntable/rotate', json={'angle': 90})
        assert response.status_code == 409
    finally:
        release.set()
        worker.join(5)
    assert results[0].status_code == 200


def test_unconfigured_service_never_reports_success(monkeypatch, tmp_path):
    monkeypatch.delenv('AIDA_TURNTABLE_PORT', raising=False)
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(tmp_path))
    with TestClient(server.app) as http:
        response = http.post('/api/v1/turntable/rotate', json={'angle': 90})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'not_configured'


def test_choose_detected_usb_port_without_opening_motor(client, monkeypatch):
    from aida.turntable import HardwareSettings
    http,table,_,_ = client
    monkeypatch.delenv('AIDA_TURNTABLE_PORT',raising=False)
    monkeypatch.setattr('aida.api.turntable.serial_ports',lambda:[{
        'port':'/dev/cu.usbmodem101','description':'Arduino Mega'}])
    response=http.post('/api/v1/turntable/configuration',json={'port':'/dev/cu.usbmodem101'})
    assert response.status_code == 200
    assert HardwareSettings.from_env().port == '/dev/cu.usbmodem101'
    assert not table.pulses
    assert table.stops == 0
    assert http.post('/api/v1/turntable/configuration',json={'port':'/tmp/not-a-port'}).status_code == 409


def test_angle_capture_persists_workspace_photo_and_reference(client):
    from aida.api.workspace_meta import reference_items
    http, _, _, _ = client
    session = server.cad_manager.create(units='mm')
    session.status, session.stage = 'running', 'idle'
    response = http.post('/api/v1/turntable/rotate', json={'angle': 450, 'session_id': session.id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['requested_angle'] == data['target_angle'] == 90
    assert abs(data['measured_angle'] - 90) <= 1
    assert 'image_path' not in data
    assert http.get(data['capture_url']).content.startswith(b'\x89PNG')
    assert len(session.images) == 0
    photos = [r for r in reference_items(session.artifacts.root, session.id) if r['kind'] == 'photo']
    assert len(photos) == 1 and photos[0]['url'] == data['capture_url']
    assert not (session.artifacts.root / 'commits').exists()
    assert session.stage == 'idle'


async def test_aida_capture_tool_returns_real_image_content(client, monkeypatch):
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost
    http, _, controller, _ = client
    monkeypatch.setattr('aida.turntable.service', controller)
    session = server.cad_manager.create(units='mm')
    session.status, session.stage = 'running', 'idle'
    tool = next(t for t in host_tools(SessionHost(session)) if t.name == 'capture_angle')
    result = await tool.handler({'angle': -270})
    assert not result.get('is_error'), result
    assert result['content'][1]['type'] == 'image' and result['content'][1]['mimeType'] == 'image/png'
    import base64, json
    assert base64.b64decode(result['content'][1]['data']).startswith(b'\x89PNG')
    summary = json.loads(result['content'][0]['text'])
    assert summary['requested_angle'] == 90 and 'image_path' not in summary
    assert abs(summary['measured_angle'] - 90) <= 1


def test_invalid_session_prevents_rotation(client):
    http, table, _, _ = client
    response = http.post('/api/v1/turntable/rotate', json={'angle': 90, 'session_id': 'missing'})
    assert response.status_code == 404
    assert not table.pulses
