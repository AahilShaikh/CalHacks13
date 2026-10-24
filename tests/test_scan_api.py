"""HTTP API tests: job lifecycle, artifact download, and error surfacing."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import aida.api.server as server
from aida.scan.pipeline import ScanResult
from aida.scan.scaling import ScalingError


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'manager', server.JobManager(tmp_path / 'storage'))
    return TestClient(server.app)


@pytest.fixture()
def png_bytes(tmp_path):
    path = tmp_path / 'front.png'
    Image.new('RGB', (32, 32), 'white').save(path)
    return path.read_bytes()


def stub_success(images, output_dir, **kwargs):
    output_dir = Path(output_dir)
    written = {}
    for name in ('metric_model.glb', 'metric_model.stl', 'blueprints.png',
                 'dimensions.json', 'agent_prompt.txt'):
        path = output_dir / name
        path.write_bytes(name.encode())
        written[name] = path
    specs = {'bounding_box_mm': {'width_x': 1.0, 'depth_y': 2.0, 'height_z': 3.0}}
    return ScanResult(directory=output_dir, metric_mesh=written['metric_model.glb'],
                      metric_stl=written['metric_model.stl'],
                      dimensions_json=written['dimensions.json'],
                      blueprint=written['blueprints.png'],
                      cad_specs=specs, agent_prompt='Build it', files=written)


def wait_for(client, job_id, deadline=5.0):
    end = time.time() + deadline
    while time.time() < end:
        data = client.get(f'/api/v1/scans/{job_id}').json()
        if data['status'] in ('succeeded', 'failed'):
            return data
        time.sleep(0.02)
    raise AssertionError(f'job {job_id} did not finish: {data}')


def test_submit_poll_and_download(client, png_bytes, monkeypatch):
    monkeypatch.setattr(server, 'run_scan', stub_success)
    response = client.post('/api/v1/scans',
                           files={'front_image': ('front.png', png_bytes, 'image/png')},
                           data={'known_height_mm': '60'})
    assert response.status_code == 202
    job_id = response.json()['job_id']

    data = wait_for(client, job_id)
    assert data['status'] == 'succeeded'
    assert data['cad_specs']['bounding_box_mm']['height_z'] == 3.0
    assert data['agent_prompt'] == 'Build it'
    assert data['files']['metric_model.stl'] == f'/api/v1/scans/{job_id}/files/metric_model.stl'

    download = client.get(f'/api/v1/scans/{job_id}/files/metric_model.stl')
    assert download.status_code == 200
    assert download.content == b'metric_model.stl'


def test_failed_job_reports_error(client, png_bytes, monkeypatch):
    def boom(images, output_dir, **kwargs):
        raise ScalingError('No scale anchor provided')

    monkeypatch.setattr(server, 'run_scan', boom)
    job_id = client.post('/api/v1/scans',
                         files={'front_image': ('front.png', png_bytes, 'image/png')}
                         ).json()['job_id']
    data = wait_for(client, job_id)
    assert data['status'] == 'failed'
    assert 'scale anchor' in data['error']


def test_unknown_job_and_artifact_404(client, png_bytes, monkeypatch):
    monkeypatch.setattr(server, 'run_scan', stub_success)
    assert client.get('/api/v1/scans/nope').status_code == 404
    assert client.get('/api/v1/scans/nope/files/x').status_code == 404

    job_id = client.post('/api/v1/scans',
                         files={'front_image': ('front.png', png_bytes, 'image/png')}
                         ).json()['job_id']
    wait_for(client, job_id)
    missing = client.get(f'/api/v1/scans/{job_id}/files/unknown.bin')
    assert missing.status_code == 404


def test_front_image_is_required(client):
    assert client.post('/api/v1/scans', data={'known_height_mm': '60'}).status_code == 422


def test_health_reports_comfy_host(client):
    data = client.get('/health').json()
    assert data['status'] == 'ok'
    assert 'comfy_host' in data
    assert 'comfy_reachable' in data
