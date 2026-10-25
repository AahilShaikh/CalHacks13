"""ComfyUI client tests: workflow wiring and the REST protocol against a stub server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from aida.scan.comfy import (DEFAULT_WORKFLOW_PATH, ComfyClient, ComfyConfig,
                             ComfyError, wire_workflow)


def test_wire_workflow_sets_present_views():
    workflow = json.loads(DEFAULT_WORKFLOW_PATH.read_text())
    wired = wire_workflow(workflow, {'front': 'a.png', 'left': 'b.png'}, ComfyConfig())
    assert wired['364']['inputs']['image'] == 'a.png'
    assert wired['377']['inputs']['image'] == 'b.png'
    assert wired['324']['inputs']['left'] == ['aida_left_prepare', 0]
    assert wired['aida_left_prepare']['inputs']['images'] == ['377', 0]
    assert wired['aida_left_prepare']['inputs']['masks'] == ['aida_left_mask', 0]
    assert wired['aida_left_mask']['inputs']['image'] == ['377', 0]
    assert 'back' not in wired['324']['inputs']
    assert 'right' not in wired['324']['inputs']
    # The template itself is not mutated.
    assert workflow['364']['inputs']['image'] != 'a.png'
    assert '377' not in workflow


def test_wire_workflow_consumes_all_four_views():
    workflow = json.loads(DEFAULT_WORKFLOW_PATH.read_text())
    images = {name: f'{name}.png' for name in ('front', 'left', 'back', 'right')}
    wired = wire_workflow(workflow, images, ComfyConfig())
    for view, node_id in ComfyConfig().load_image_nodes.items():
        assert wired[node_id]['inputs']['image'] == images[view]
        assert view in wired['324']['inputs']


def test_wire_workflow_rejects_disconnected_supplied_view():
    workflow = json.loads(DEFAULT_WORKFLOW_PATH.read_text())
    workflow['377'] = {'class_type': 'LoadImage', 'inputs': {'image': 'old.png'}}
    with pytest.raises(ComfyError, match='not connected'):
        wire_workflow(workflow, {'front': 'front.png', 'left': 'left.png'}, ComfyConfig())


def test_wire_workflow_requires_matching_node():
    with pytest.raises(ComfyError):
        wire_workflow({}, {'front': 'a.png'}, ComfyConfig())
    with pytest.raises(ComfyError):
        wire_workflow({'364': {'class_type': 'KSampler', 'inputs': {}}},
                      {'front': 'a.png'}, ComfyConfig())


class StubComfy(BaseHTTPRequestHandler):
    """Minimal ComfyUI: upload -> prompt -> history -> view."""

    history_hits = 0
    fail = False
    mask_nodes = {}
    native_3d = False
    missing_mesh = False

    def log_message(self, *args):
        pass

    def _send(self, payload, content_type='application/json'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        if self.path == '/upload/image':
            self._send({'name': 'front.png', 'subfolder': ''})
        elif self.path == '/prompt':
            payload = json.loads(body)
            assert payload['prompt']['364']['inputs']['image'] == 'front.png'
            type(self).mask_nodes = {node_id: {'images':[{'filename':node_id+'.png','type':'output'}]}
                                    for node_id in payload['prompt'] if node_id.endswith('_mask_save')}
            self._send({'prompt_id': 'p1'})
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/history/p1':
            type(self).history_hits += 1
            if self.fail:
                self._send({'p1': {'status': {'status_str': 'error',
                                              'messages': [['execution_error', 'boom']]}}})
            elif self.history_hits < 2:
                self._send({})
            else:
                mesh_outputs = ({'323': {'3d': [{'filename': 'preview.glb', 'type': 'temp'}]},
                                 '372': {'3d': [{'filename': 'mesh.glb', 'subfolder': '3d', 'type': 'output'}]}}
                                if self.native_3d else
                                {'285': {'files': [{'filename': 'mesh.glb', 'subfolder': '3d', 'type': 'output'}]}})
                self._send({'p1': {
                    'status': {'status_str': 'success', 'completed': True, 'messages': []},
                    'outputs': {**({} if self.missing_mesh else mesh_outputs), **self.mask_nodes},
                }})
        elif parsed.path == '/view':
            query = parse_qs(parsed.query)
            if query['filename'] == ['mesh.glb']:
                self._send(b'GLB-BYTES', content_type='application/octet-stream')
            else:
                self._send(b'MASK-BYTES', content_type='image/png')
        elif parsed.path == '/system_stats':
            self._send({'system': 'stub'})
        else:
            self.send_error(404)


@pytest.fixture()
def stub_server():
    StubComfy.history_hits = 0
    StubComfy.fail = False
    StubComfy.native_3d = False
    StubComfy.missing_mesh = False
    server = ThreadingHTTPServer(('127.0.0.1', 0), StubComfy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


def make_client(host):
    return ComfyClient(ComfyConfig(host=host, poll_interval=0.01, timeout=5))


def test_run_pixal3d_downloads_mesh(stub_server, tmp_path):
    client = make_client(stub_server)
    name = client.upload_image(b'PNG', 'front.png')
    assert name == 'front.png'
    out = tmp_path / 'mesh.glb'
    result = client.run_pixal3d({'front': name}, out)
    assert result == out
    assert out.read_bytes() == b'GLB-BYTES'
    assert StubComfy.history_hits >= 2


def test_run_pixal3d_retains_original_coordinate_masks(stub_server, tmp_path):
    mesh,masks = make_client(stub_server).run_pixal3d_with_masks(
        {'front':'front.png','left':'left.png'},tmp_path)
    assert mesh.read_bytes() == b'GLB-BYTES'
    assert set(masks) == {'front','left'}
    assert all(path.read_bytes() == b'MASK-BYTES' for path in masks.values())


def test_native_3d_response_prefers_saved_mesh_over_preview(stub_server, tmp_path):
    StubComfy.native_3d = True
    client = make_client(stub_server)
    mesh, masks = client.run_pixal3d_with_masks({'front': 'front.png', 'left': 'left.png'}, tmp_path)
    assert mesh.read_bytes() == b'GLB-BYTES'
    assert set(masks) == {'front', 'left'}


@pytest.mark.parametrize('with_masks', [False, True])
def test_completed_workflow_without_mesh_fails_immediately(stub_server, tmp_path, with_masks):
    StubComfy.missing_mesh = True
    client = make_client(stub_server)
    with pytest.raises(ComfyError, match='completed without a downloadable mesh'):
        if with_masks:
            client.run_pixal3d_with_masks({'front': 'front.png'}, tmp_path)
        else:
            client.run_pixal3d({'front': 'front.png'}, tmp_path / 'mesh.glb')
    assert StubComfy.history_hits == 2


def test_run_pixal3d_requires_front(stub_server, tmp_path):
    with pytest.raises(ComfyError):
        make_client(stub_server).run_pixal3d({}, tmp_path / 'mesh.glb')


def test_workflow_failure_raises(stub_server, tmp_path):
    StubComfy.fail = True
    with pytest.raises(ComfyError, match='boom'):
        make_client(stub_server).run_pixal3d({'front': 'front.png'}, tmp_path / 'mesh.glb')


def test_unreachable_host_raises(tmp_path):
    client = ComfyClient(ComfyConfig(host='127.0.0.1:1', poll_interval=0.01, timeout=2))
    with pytest.raises(ComfyError, match='Cannot reach ComfyUI'):
        client.run_pixal3d({'front': 'front.png'}, tmp_path / 'mesh.glb')
    assert client.health() is False
