import asyncio
import json
import time
import threading
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
from aida.scan.acquisition import CaptureAgent
from aida.scan.camera import CameraCalibration
from aida.scan.pipeline import ScanResult
from aida.turntable import CaptureSession, HardwareSettings
from test_turntable_control import SimulatedTable


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


async def test_agent_tools_cannot_capture_after_hardware_failure(tmp_path):
    class Rig:
        views = {}
        def capture(self, view):
            raise RuntimeError('USB disconnected')
    agent = CaptureAgent(Rig(), tmp_path, None)
    tools = {t.name: t for t in agent.tools()}
    assert set(tools) == {'capture_view', 'reconstruct'}
    assert (await tools['capture_view'].handler({'view':'front'}))['is_error']
    assert (await tools['capture_view'].handler({'view':'left'}))['is_error']
    assert 'already failed' in (await tools['capture_view'].handler({'view':'front'}))['content'][0]['text']


async def test_agent_metric_failure_can_request_unused_view(tmp_path, monkeypatch):
    from aida.scan.scaling import ScalingError
    def fail(*args):
        raise ScalingError('Fit ambiguous')
    monkeypatch.setattr('aida.scan.acquisition.reconstruct', fail)
    rig = SimpleNamespace(views={'front': {}, 'left': {}}, calibration=None)
    agent = CaptureAgent(rig, tmp_path, None)
    result = await agent.build({})
    assert result['is_error']
    assert 'back' in result['content'][0]['text']
    assert agent.fatal_error is None


def fake_result(directory, prompt, **kwargs):
    directory = Path(directory)
    kwargs['progress']('fitting_metric_scale')
    path = directory/'metric_model.stl'
    path.write_bytes(trimesh.creation.box((40,24,60)).export(file_type='stl'))
    dimensions = directory/'dimensions.json'
    dimensions.write_text(json.dumps({'scale_multiplier_applied':40}))
    specs = {'bounding_box_mm': {'width_x':40,'depth_y':24,'height_z':60}}
    return ScanResult(directory, directory/'metric_model.glb', path, dimensions,
                      directory/'blueprints.png', specs,
                      'Mesh ALREADY in millimeters. Do not apply scale again.', files={path.name:path})


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'fake')
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(tmp_path/'rig'))
    monkeypatch.setenv('AIDA_ACQUISITION_STORAGE', str(tmp_path/'acquisitions'))
    folder = tmp_path/'rig'
    folder.mkdir()
    CameraCalibration(np.array([[800.,0,640],[0,800,360],[0,0,1]]), np.zeros(5), (1280,720),0,.1,
                      'test-usb-camera').save(folder/'camera.json')
    (folder/'zero.json').write_text('{}')
    (folder/'empty_table.json').write_text('{}')
    (folder/'empty_table.png').write_bytes(b'placeholder')
    manager = AcquisitionManager(fake_result)
    monkeypatch.setattr('aida.api.acquisition.manager', manager)
    seen = []
    async def cad_runner(session):
        seen.append((session.units, await session.turns.get(), session.artifacts.root))
        session.set_status('done')
    monkeypatch.setattr(server, 'cad_manager', CadSessionManager(tmp_path/'cad', runner=cad_runner))
    with TestClient(server.app) as http:
        yield http, manager, seen


def wait_done(http, url):
    for _ in range(200):
        result = http.get(url).json()
        if result['status'] in ('failed','succeeded','cancelled'):
            return result
        time.sleep(.01)
    raise AssertionError('Workflow did not finish')


def test_capture_to_cad_handoff_copies_metric_evidence(client):
    http, _, seen = client
    response = http.post('/api/v1/acquisitions', json={'policy':'four_views'})
    assert response.status_code == 202
    data = wait_done(http, response.json()['status_url'])
    assert data['status'] == 'succeeded', data
    assert data['stage'] == 'cad_started'
    assert data['cad_session_id']
    for _ in range(100):
        if seen: break
        time.sleep(.01)
    assert seen[0][0] == 'mm'
    assert 'ALREADY in millimeters' in seen[0][1]
    assert (seen[0][2]/'dimensions.json').exists()
    assert http.get(data['files']['metric_model.stl']).status_code == 200


def test_bad_metric_fit_never_starts_cad(client):
    http, manager, seen = client
    def fail(*args, **kwargs):
        raise RuntimeError('Metric scale is ambiguous')
    manager.runner = fail
    response = http.post('/api/v1/acquisitions', json={})
    result = wait_done(http, response.json()['status_url'])
    assert result['status'] == 'failed'
    assert result['cad_session_id'] is None
    assert not seen


def test_missing_camera_calibration_prevents_motion(client, monkeypatch):
    http, _, seen = client
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', '/nonexistent-rig')
    response = http.post('/api/v1/acquisitions', json={})
    assert response.status_code == 409
    assert not seen


def test_metric_only_does_not_start_paid_cad(client):
    http, _, seen = client
    response = http.post('/api/v1/acquisitions', json={'start_cad':False,'policy':'four_views'})
    result = wait_done(http, response.json()['status_url'])
    assert result['status'] == 'succeeded'
    assert result['stage'] == 'metric_mesh_ready'
    assert not seen


def test_busy_workflow_and_cancel_prevent_cad_handoff(client):
    http,manager,seen = client
    entered,release = threading.Event(),threading.Event()
    def slow(directory,prompt,**kwargs):
        entered.set()
        assert release.wait(5)
        return fake_result(directory,prompt,**kwargs)
    manager.runner = slow
    response = http.post('/api/v1/acquisitions',json={})
    assert entered.wait(5)
    try:
        assert http.post('/api/v1/acquisitions',json={}).status_code == 409
        identity = response.json()['job_id']
        assert http.post(f'/api/v1/acquisitions/{identity}/cancel').json()['status'] == 'cancelling'
    finally:
        release.set()
    assert wait_done(http,response.json()['status_url'])['status'] == 'cancelled'
    assert not seen


async def test_agent_serializes_parallel_capture_requests(tmp_path):
    class Rig:
        def __init__(self):
            self.views = {}
            self.active = False
        def capture(self,view):
            assert not self.active
            self.active = True
            time.sleep(.02)
            path=tmp_path/f'{view}.png'
            cv2.imwrite(str(path),np.zeros((20,20,3),np.uint8))
            self.views[view]={'path':str(path)}
            self.active = False
            return self.views[view]
    agent=CaptureAgent(Rig(),tmp_path,None)
    handler=agent.tools()[0].handler
    results=await asyncio.gather(handler({'view':'front'}),handler({'view':'left'}))
    assert all(not r.get('is_error') for r in results)
    assert set(agent.rig.views) == {'front','left'}


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


def test_acquisition_workflow_forwarded_and_validated(client):
    http, manager, _ = client
    seen = []
    def record(directory, prompt, **kwargs):
        seen.append(kwargs['workflow'])
        return fake_result(directory, prompt, **kwargs)
    manager.runner = record
    for preset in ('fast', 'full'):
        response = http.post('/api/v1/acquisitions', json={'workflow':preset, 'start_cad':False})
        assert wait_done(http, response.json()['status_url'])['status'] == 'succeeded'
    assert seen == ['fast', 'full']
    assert http.post('/api/v1/acquisitions', json={'workflow':'turbo'}).status_code == 422


def test_capture_no_longer_requires_empty_background_reference(client, monkeypatch):
    from aida.turntable import HardwareSettings
    http, _, _ = client
    folder = HardwareSettings.from_env().storage
    (folder/'empty_table.png').unlink()
    (folder/'empty_table.json').unlink()
    response = http.post('/api/v1/acquisitions', json={'policy':'four_views','start_cad':False})
    assert response.status_code == 202
    assert wait_done(http,response.json()['status_url'])['status'] == 'succeeded'
