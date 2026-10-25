import asyncio
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from aida.api import server
from aida.api.acquisition import AcquisitionManager
from aida.api.cad_sessions import CadSessionManager
from aida.scan.acquisition import run_capture_scan
from aida.scan.camera import CameraCalibration
from aida.scan.pipeline import ScanResult
from aida.turntable import CaptureSession, HardwareSettings
from aida.workspace import Workspace
from test_turntable_control import SimulatedTable
from test_cad_sessions import stub_runner_factory
from test_workspace import LocalCad


def fake_result(directory, **kwargs):
    directory = Path(directory)
    kwargs['progress']('fitting_metric_scale')
    path = directory / 'metric_model.stl'
    path.write_bytes(trimesh.creation.box((40,24,60)).export(file_type='stl'))
    specs = {'bounding_box_mm': {'width_x': 40, 'depth_y': 24, 'height_z': 60},
             'registration': {'accepted': True}}
    dimensions = directory / 'dimensions.json'
    dimensions.write_text(json.dumps(specs))
    for name in ('front','left','back','right','blueprints'):
        cv2.imwrite(str(directory / f'{name}.png'), np.zeros((24,32,3), np.uint8))
    return ScanResult(directory, directory / 'metric_model.glb', path, dimensions,
                      directory / 'blueprints.png', specs, 'Mesh ALREADY in millimeters.')


class ImportCad(LocalCad):
    async def load_reference(self, path, part):
        self.references[part] = path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'fake')
    folder = tmp_path / 'rig'
    folder.mkdir()
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(folder))
    CameraCalibration(np.array([[800.,0,640],[0,800,360],[0,0,1]]), np.zeros(5), (1280,720),0,.1,
                      'test-usb-camera').save(folder / 'camera.json')
    (folder / 'zero.json').write_text('{}')
    controller = SimpleNamespace(ensure_zero_ready=lambda: None)
    manager = AcquisitionManager(fake_result, controller=controller)
    monkeypatch.setattr('aida.api.acquisition.manager', manager)
    cad_manager = CadSessionManager(tmp_path / 'cad', runner=stub_runner_factory([]))
    monkeypatch.setattr(server, 'cad_manager', cad_manager)
    session = cad_manager.create(units='mm')
    session.cad = ImportCad(tmp_path)
    session.cad.lock = asyncio.Lock()
    session.workspace = Workspace.load(session.cad, session.artifacts)
    session.workspace.add_part('existing', 'A previously designed part')
    session.status, session.stage = 'running', 'idle'
    with TestClient(server.app) as http:
        yield http, manager, session


def wait_done(http, url):
    for _ in range(300):
        result = http.get(url).json()
        if result['status'] in ('failed','succeeded','cancelled'):
            return result
        time.sleep(.01)
    raise AssertionError('Scan did not finish')


def submit(client, **fields):
    http, _, session = client
    return http.post('/api/v1/acquisitions', json={'session_id': session.id, **fields})


def test_scan_attaches_to_existing_workspace_without_starting_agent_or_commit(client):
    http, manager, session = client
    existing = session.workspace.parts['existing']
    response = submit(client)
    assert response.status_code == 202, response.text
    data = wait_done(http, response.json()['status_url'])
    assert data['status'] == 'succeeded', data
    assert data['session_id'] == session.id and data['stage'] == 'mesh_attached'
    assert data['parts'] == ['scanned_object']
    assert session.workspace.parts['existing'] is existing
    assert session.workspace.parts['scanned_object'].reference.is_file()
    assert session.workspace.active == 'scanned_object'
    assert session.units == session.cad.mesh.units == 'mm'
    assert session.task is None and session.turns.empty()
    assert session.images == []  # automated photos do not consume the manual upload allowance
    assert not (session.artifacts.root / 'commits').exists()
    assert next(e for e in reversed(session.events) if e['type'] == 'working_state')['dirty']
    references = next(e for e in reversed(session.events) if e['type'] == 'references')['items']
    assert len([r for r in references if r['kind'] == 'photo']) == 5
    assert http.get(data['files']['metric_model.stl']).status_code == 200
    assert http.get(data['files']['front.png']).content.startswith(b'\x89PNG')
    assert (manager.jobs[data['job_id']].directory / 'dimensions.json').is_file()
    assert any('ALREADY in millimeters' in message for message in session.pending)


def test_repeat_scans_add_unique_parts(client):
    http, _, session = client
    for _ in range(2):
        response = submit(client)
        assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    assert set(session.workspace.parts) == {'existing', 'scanned_object', 'scanned_object_2'}


def test_bad_metric_fit_never_attaches(client):
    http, manager, session = client
    def fail(*args, **kwargs):
        raise RuntimeError('Metric scale is ambiguous')
    manager.runner = fail
    response = submit(client)
    result = wait_done(http, response.json()['status_url'])
    assert result['status'] == 'failed' and 'ambiguous' in result['error']
    assert result['parts'] == [] and set(session.workspace.parts) == {'existing'}
    assert not any(e['type'] == 'workspace_state' for e in session.events)


def test_failed_cad_import_rolls_back_workspace(client, monkeypatch):
    http, _, session = client
    before = (session.artifacts.root / 'workspace.json').read_bytes()
    async def fail(*args):
        raise RuntimeError('CAD import failed')
    monkeypatch.setattr(session.cad, 'load_reference', fail)
    response = submit(client)
    result = wait_done(http, response.json()['status_url'])
    assert result['status'] == 'failed' and 'CAD import failed' in result['error']
    assert (session.artifacts.root / 'workspace.json').read_bytes() == before
    assert set(session.workspace.parts) == {'existing'} and session.cad.mesh.references == {}
    assert not any(e['type'] in ('references', 'workspace_state') for e in session.events)
    assert http.get(result['files']['front.png']).status_code == 200  # diagnostic evidence survives


@pytest.mark.parametrize('missing', ['camera.json', 'zero.json'])
def test_missing_calibration_or_zero_prevents_job(client, missing):
    http, manager, _ = client
    (HardwareSettings.from_env().storage / missing).unlink()
    assert submit(client).status_code == 409
    assert not manager.jobs


def test_request_requires_open_mm_workspace_and_rejects_removed_agent_policy(client):
    http, manager, session = client
    assert http.post('/api/v1/acquisitions', json={}).status_code == 422
    assert submit(client, policy='agent').status_code == 422
    assert submit(client, start_cad=True).status_code == 422
    assert http.post('/api/v1/acquisitions', json={'session_id': 'missing'}).status_code == 409
    session.units = None
    assert submit(client).status_code == 409
    assert not manager.jobs


def test_busy_scan_and_cancel_prevent_attachment(client):
    http, manager, session = client
    entered, release = threading.Event(), threading.Event()
    def slow(directory, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake_result(directory, **kwargs)
    manager.runner = slow
    response = submit(client)
    assert entered.wait(5)
    try:
        assert submit(client).status_code == 409
        identity = response.json()['job_id']
        assert http.post(f'/api/v1/acquisitions/{identity}/cancel').json()['status'] == 'cancelling'
    finally:
        release.set()
    assert wait_done(http, response.json()['status_url'])['status'] == 'cancelled'
    assert set(session.workspace.parts) == {'existing'}


def test_workflow_presets(client):
    http, manager, _ = client
    seen = []
    def record(directory, **kwargs):
        seen.append(kwargs['workflow'])
        return fake_result(directory, **kwargs)
    manager.runner = record
    for preset in ('fast', 'full'):
        response = submit(client, workflow=preset)
        assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    assert seen == ['fast', 'full']
    assert submit(client, workflow='turbo').status_code == 422


def test_fixed_four_view_pipeline_releases_hardware_before_reconstruction(tmp_path, monkeypatch):
    held, captured = [], []
    calibration = SimpleNamespace(save=lambda p: p.write_text('{}'))
    views = {}
    def capture(view):
        assert held
        captured.append(view)
        views[view] = {'path': str(tmp_path / f'{view}.png')}
    @contextmanager
    def capture_session(directory):
        held.append(True)
        try:
            yield SimpleNamespace(calibration=calibration, views=views, capture=capture)
        finally:
            held.clear()
    def reconstruct(photos, calibration, directory, client, progress):
        assert not held and list(photos) == ['front','left','back','right']
        return 'metric mesh'
    monkeypatch.setattr('aida.scan.acquisition.reconstruct', reconstruct)
    client = SimpleNamespace(health=lambda: True)
    result = run_capture_scan(tmp_path, controller=SimpleNamespace(capture_session=capture_session), client=client)
    assert result == 'metric mesh' and captured == ['front','left','back','right']


def test_cancellation_between_views_stops_motion(tmp_path):
    cancelled = threading.Event()
    captured = []
    def capture(view):
        captured.append(view)
        cancelled.set()
    @contextmanager
    def rig(directory):
        yield SimpleNamespace(calibration=SimpleNamespace(save=lambda _: None), views={}, capture=capture)
    with pytest.raises(RuntimeError, match='cancelled'):
        run_capture_scan(tmp_path, controller=SimpleNamespace(capture_session=rig),
                         client=SimpleNamespace(health=lambda: True), cancelled=cancelled)
    assert captured == ['front']


def test_comfy_unavailable_prevents_camera_and_motion(tmp_path):
    @contextmanager
    def rig(directory):
        raise AssertionError('Should not touch hardware')
        yield
    with pytest.raises(RuntimeError, match='ComfyUI is not ready'):
        run_capture_scan(tmp_path, controller=SimpleNamespace(capture_session=rig),
                         client=SimpleNamespace(health=lambda: False, config=SimpleNamespace(base_url='http://fake')))


def test_capture_budget_and_duplicate_views_do_not_move_again(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'fake')
    monkeypatch.setenv('AIDA_TURNTABLE_NEUTRAL_US', '1500')
    monkeypatch.setattr('aida.hardware.control.time.sleep', lambda _: None)
    monkeypatch.setattr('aida.scan.camera.board_pose', lambda *_: {'R': np.eye(3).tolist(), 't_mm': [0,0,500]})
    table = SimulatedTable()
    table.last_frame = np.zeros((720, 1280, 3), np.uint8)
    calibration = CameraCalibration(np.eye(3), np.zeros(5), (1280, 720), 0, .1, 'test-usb-camera')
    rig = CaptureSession(table, table, HardwareSettings.from_env(), calibration, tmp_path, 1, 30)
    first = rig.capture('front')
    assert rig.capture('front') is first
    assert not table.pulses
    for view in ('left', 'back', 'right'):
        rig.capture(view)
    assert len(list(tmp_path.glob('*.png'))) == 4
    with pytest.raises(ValueError):
        rig.capture('top')


def test_reconstruction_runs_segmentation_metric_fit_and_exports(tmp_path,monkeypatch):
    from aida.scan.acquisition import reconstruct
    from test_metric_registration import scene
    raw,views,calibration=scene(tmp_path)
    directory=tmp_path/'scan'
    directory.mkdir()
    cv2.imwrite(str(directory/'empty_table.png'),np.full((600,800,3),220,np.uint8))
    empty_pose={'R':views[0].R.tolist(),'t_mm':views[0].t_mm.tolist()}
    (directory/'empty_table.json').write_text(json.dumps({'board_pose':empty_pose}))
    captures={}
    for view in views:
        frame=np.full((600,800,3),220,np.uint8)
        frame[view.mask>0]=[20,60,180]
        path=directory/f'{view.name}.png'
        cv2.imwrite(str(path),frame)
        captures[view.name]={'path':str(path),'measured_angle':view.angle_degrees,
                             'board_pose':{'R':view.R.tolist(),'t_mm':view.t_mm.tolist()}}
    class Comfy:
        def __init__(self): self.uploads={}
        def upload_image(self,data,filename):
            self.uploads[filename]=data
            return filename
        def run_pixal3d_with_masks(self,uploaded,folder):
            path=folder/'unscaled_model.glb'
            path.write_bytes(raw.read_bytes())
            masks={}
            for view,filename in uploaded.items():
                image=cv2.imdecode(np.frombuffer(self.uploads[filename],np.uint8),cv2.IMREAD_COLOR)
                # The preparation retains background context inside a padded ROI;
                # emulate semantic removal of that background, not a nonzero mask.
                mask=np.all(image == [20,60,180],axis=2).astype(np.uint8)*255
                target=folder/f'{view}_mask.png'
                cv2.imwrite(str(target),mask)
                masks[view]=target
            return path,masks
    monkeypatch.setattr('aida.scan.acquisition.blueprints.render_mesh_projections',
                        lambda mesh,path:cv2.imwrite(str(path),np.zeros((40,40,3),np.uint8)))
    stages=[]
    result=reconstruct(captures,calibration,directory,Comfy(),stages.append)
    assert result.metric_stl.is_file()
    assert result.cad_specs['scale_multiplier_applied'] == pytest.approx(40,rel=.03)
    assert 'ALREADY in millimeters' in result.agent_prompt
    assert 'front_mask.png' in result.files
    assert stages[-1] == 'rendering_blueprints'




async def test_main_aida_scan_tool_returns_metric_parts_and_blueprint(client):
    from aida.agent import host_tools
    from aida.api.agent_host import SessionHost
    _, _, session = client
    host = SessionHost(session)
    tools = {t.name: t for t in host_tools(host)}
    assert {'scan_object', 'capture_angle'} <= tools.keys()
    result = await tools['scan_object'].handler({})
    assert not result.get('is_error'), result
    summary = json.loads(result['content'][0]['text'])
    assert summary['parts'] == ['scanned_object']
    assert summary['dimensions_mm'] == {'width_x':40, 'depth_y':24, 'height_z':60}
    assert summary['fit_evidence'] == {'accepted': True}
    assert result['content'][1]['type'] == 'image'
    assert 'blueprint_path' not in summary
    assert 'Do not scale it again' in summary['note']


def test_cancel_during_cad_import_rolls_back_reference(client, monkeypatch):
    http, manager, session = client
    async def cancel_import(path, part):
        session.cad.references[part] = path
        manager.cancel_for_session(session.id)
    monkeypatch.setattr(session.cad, 'load_reference', cancel_import)
    response = submit(client)
    data = wait_done(http, response.json()['status_url'])
    assert data['status'] == 'cancelled'
    assert set(session.workspace.parts) == {'existing'} and session.cad.references == {}
    assert not any(e['type'] == 'references' for e in session.events)


def test_scan_sets_units_on_empty_unknown_workspace(client):
    http, _, session = client
    session.units = None
    session.workspace.parts = {}
    session.workspace.active = None
    session.workspace.save()
    response = submit(client)
    assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    assert any(e['type'] == 'workspace_units' and e['units'] == 'mm' for e in session.events)


def test_scan_camera_identity_mismatch_prevents_job(client):
    _, manager, _ = client
    path = HardwareSettings.from_env().storage / 'camera.json'
    calibration = CameraCalibration.load(path)
    calibration.camera_id = 'another-camera'
    calibration.save(path)
    assert submit(client).status_code == 409
    assert not manager.jobs


def test_fixed_capture_failure_stops_remaining_views(tmp_path):
    captured = []
    def fail(view):
        captured.append(view)
        raise RuntimeError('Camera disconnected')
    @contextmanager
    def rig(directory):
        yield SimpleNamespace(calibration=SimpleNamespace(save=lambda _: None), views={}, capture=fail)
    with pytest.raises(RuntimeError, match='disconnected'):
        run_capture_scan(tmp_path, controller=SimpleNamespace(capture_session=rig),
                         client=SimpleNamespace(health=lambda: True))
    assert captured == ['front']


@pytest.mark.parametrize('action', ['interrupt', 'close'])
def test_stopping_workspace_cancels_scan_before_attachment(client, action):
    http, manager, session = client
    entered, release = threading.Event(), threading.Event()
    def slow(directory, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake_result(directory, **kwargs)
    async def interrupt():
        return None
    session.client = SimpleNamespace(interrupt=interrupt)
    manager.runner = slow
    response = submit(client)
    assert entered.wait(5)
    try:
        if action == 'interrupt':
            assert http.post(f'/api/v1/cad-sessions/{session.id}/interrupt').status_code == 202
        else:
            assert http.post(f'/api/v1/cad-sessions/{session.id}/close').status_code == 202
        assert manager.jobs[response.json()['job_id']].cancelled.is_set()
    finally:
        release.set()
    assert wait_done(http, response.json()['status_url'])['status'] == 'cancelled'
    assert set(session.workspace.parts) == {'existing'}


def test_progress_events_run_on_session_event_loop(client, monkeypatch):
    http, manager, session = client
    worker_threads, publish_threads = [], []
    publish = session.publish
    def record(event):
        if event.get('type') == 'stage' and event.get('stage', '').startswith('scan_'):
            publish_threads.append(threading.get_ident())
        return publish(event)
    def runner(directory, **kwargs):
        worker_threads.append(threading.get_ident())
        return fake_result(directory, **kwargs)
    monkeypatch.setattr(session, 'publish', record)
    manager.runner = runner
    response = submit(client)
    assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    assert publish_threads and all(t not in worker_threads for t in publish_threads)


def test_http_scan_rejects_active_agent_but_main_tool_can_scan(client):
    _, manager, session = client
    session.stage = 'agent_running'
    assert submit(client).status_code == 409
    assert not manager.jobs


def test_scanner_photos_and_previews_remain_available_in_replay(client):
    http, _, session = client
    response = submit(client)
    assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    events = http.get(f'/api/v1/replays/{session.id}/events.json').json()
    prefix = f'/api/v1/replays/{session.id}/files/'
    render = next(e['url'] for e in events if e['type'] == 'render')
    assert render.startswith(prefix) and '/scans/' in render
    assert http.get(render).content.startswith(b'\x89PNG')
    refs = next(e['items'] for e in events if e['type'] == 'references')
    photo = next(item['url'] for item in refs if item['kind'] == 'photo')
    assert photo.startswith(prefix) and http.get(photo).status_code == 200
    assert http.get(prefix + '..%2F..%2Foutside.txt').status_code == 404


def test_workflow_preflight_failure_prevents_turntable_capture(tmp_path,monkeypatch):
    from aida.scan.comfy import ComfyClient,ComfyError
    client=ComfyClient()
    monkeypatch.setattr(client,'health',lambda:True)
    def fail():raise ComfyError('Missing AidaObjectOnlyViews')
    monkeypatch.setattr(client,'validate_workflow',fail)
    @contextmanager
    def rig(_):
        raise AssertionError('Invalid workflows must not move the turntable')
        yield
    with pytest.raises(ComfyError,match='AidaObjectOnlyViews'):
        run_capture_scan(tmp_path,controller=SimpleNamespace(capture_session=rig),client=client)
