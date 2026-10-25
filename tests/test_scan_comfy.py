"""ComfyUI client tests: workflow wiring and the REST protocol against a stub server."""
import json
from pathlib import Path
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
    assert wired['324']['inputs']['front'] == ['500',0]
    assert wired['324']['inputs']['left'] == ['500',1]
    assert wired['500']['inputs']['front'] == ['364',0]
    assert wired['500']['inputs']['left'] == ['377',0]
    assert 'back' not in wired['324']['inputs'] and 'back' not in wired['500']['inputs']
    assert 'right' not in wired['324']['inputs'] and 'right' not in wired['500']['inputs']
    assert wired['380']['inputs']['image'] == '(none)'
    assert workflow['364']['inputs']['image'] != 'a.png'
    assert workflow['500']['inputs']['back'] == ['380',0]


def test_wire_workflow_consumes_all_four_views():
    workflow = json.loads(DEFAULT_WORKFLOW_PATH.read_text())
    images = {name: f'{name}.png' for name in ('front', 'left', 'back', 'right')}
    wired = wire_workflow(workflow, images, ComfyConfig())
    for view, node_id in ComfyConfig().load_image_nodes.items():
        assert wired[node_id]['inputs']['image'] == images[view]
        assert view in wired['324']['inputs']


def test_wire_workflow_rejects_disconnected_supplied_view():
    workflow = json.loads(DEFAULT_WORKFLOW_PATH.read_text())
    workflow['500']['inputs'].pop('left')
    workflow['324']['inputs'].pop('left')
    workflow['500']['inputs']['front'] = ['364',0]
    # A disconnected image on a custom graph must still be rejected.
    workflow.pop('500')
    workflow['324']['inputs']['front'] = ['364',0]
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
    image_payload = b'MASK-BYTES'

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
                self._send(self.image_payload, content_type='image/png')
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
    StubComfy.image_payload = b'MASK-BYTES'
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


def test_exactly_two_quality_presets_and_full_texture_output():
    from aida.scan.comfy import WORKFLOWS
    assert set(WORKFLOWS) == {'fast', 'full'}
    fast, full = [json.loads(WORKFLOWS[name].read_text()) for name in ('fast', 'full')]
    assert ComfyConfig().workflow_path == WORKFLOWS['full']
    assert ComfyConfig(workflow='fast').workflow_path == WORKFLOWS['fast']
    assert fast['94']['class_type'] == 'Trellis2UpsampleStage'
    assert fast['94']['inputs']['target_resolution'] == 1024
    assert full['18']['inputs']['steps'] > fast['18']['inputs']['steps']
    assert full['285']['inputs']['mesh'] == ['210', 0]
    for name in ('base_color', 'metallic', 'roughness', 'normal_map', 'occlusion'):
        assert name in full['210']['inputs']
    assert 'VaeDecodeTextureTrellis' not in {n['class_type'] for n in fast.values()}
    for preset in ('fast', 'full'):
        workflow = json.loads(WORKFLOWS[preset].read_text())
        wired = wire_workflow(workflow, {v: f'{v}.png' for v in ('front','left','back','right')},
                              ComfyConfig(workflow=preset))
        assert all(v in wired['324']['inputs'] for v in ('front','left','back','right'))
    with pytest.raises(ValueError, match='Unknown workflow'):
        ComfyConfig(workflow='turbo')


def test_partial_workflow_rejection_surfaces_node_errors(monkeypatch):
    client = ComfyClient()
    monkeypatch.setattr(client, '_json', lambda *args: {
        'prompt_id':'partial', 'node_errors': {'372': {'errors':['required input missing']}}})
    with pytest.raises(ComfyError, match='rejected workflow nodes'):
        client._queue({})


@pytest.mark.parametrize('preset', ['fast','full'])
def test_board_photos_use_prompted_sam_instead_of_birefnet(preset):
    from aida.scan.comfy import WORKFLOWS
    regions = {'front': {'x':100,'y':120,'width':200,'height':150},
               'left': {'x':80,'y':100,'width':160,'height':180}}
    workflow = json.loads(WORKFLOWS[preset].read_text())
    wired = wire_workflow(workflow, {'front':'front.png','left':'left.png'},
                          ComfyConfig(workflow=preset), regions)
    assert wired['501']['inputs']['ckpt_name'] == 'sam3.1_multiplex_fp16.safetensors'
    assert wired['500']['class_type'] == 'AidaObjectOnlyViews'
    for view in regions:
        assert wired['500']['inputs'][view] == [ComfyConfig().load_image_nodes[view],0]
        assert wired['324']['inputs'][view] == ['500', ('front','left','back','right').index(view)]


def test_common_framing_preserves_relative_view_sizes(tmp_path, monkeypatch):
    import io
    import numpy as np
    from PIL import Image
    photo = Image.new('RGB',(100,100),'white')
    data = io.BytesIO(); photo.save(data,format='PNG')
    client = ComfyClient(ComfyConfig(camera_focal_px=100.))
    monkeypatch.setattr(client,'_request',lambda *args,**kwargs:data.getvalue())
    uploads = {}
    def upload(content, filename):
        uploads[filename] = Image.open(io.BytesIO(content)).copy()
        return filename
    monkeypatch.setattr(client,'upload_image',upload)
    front = np.zeros((100,100),np.uint8); front[40:60,30:70] = 255
    left = np.zeros((100,100),np.uint8); left[40:60,40:60] = 255
    masks = {'front':Image.fromarray(front),'left':Image.fromarray(left)}
    wired = {'324':{'inputs':{}}}
    client._condition_from_masks(wired,{'front':'front.png','left':'left.png'},masks,tmp_path)
    widths = []
    for name in ('front','left'):
        image = np.asarray(uploads[f'{name}_conditioned.png'])
        active = np.argwhere(image[:,:,0] > 127)
        widths.append(np.ptp(active[:,1])+1)
        assert image.shape == (1024,1024,3)
        assert wired['324']['inputs'][name] == [f'aida_{name}_conditioned',0]
    assert widths[0]/widths[1] == pytest.approx(2,rel=.01)
    framing = json.loads((tmp_path/'framing.json').read_text())
    assert framing['common_crop_pixels'] == 44
    assert framing['fov_degrees'] == pytest.approx(24.814837, rel=1e-5)


def test_board_segmentation_then_shared_framing_rest_protocol(stub_server, tmp_path):
    import io
    from PIL import Image
    payload = io.BytesIO(); Image.new('RGB',(64,64),'white').save(payload,format='PNG')
    StubComfy.image_payload = payload.getvalue()
    client = make_client(stub_server)
    mesh, masks = client.run_pixal3d_with_masks({'front':'front.png','left':'left.png'},tmp_path,
        object_regions={v:{'x':8,'y':8,'width':48,'height':48} for v in ('front','left')})
    assert mesh.read_bytes() == b'GLB-BYTES'
    assert all(Image.open(path).size == (64,64) for path in masks.values())
    assert set(masks) == {'front','left'}


@pytest.mark.parametrize('preset', ['fast','full'])
@pytest.mark.parametrize('views', [('front',),('front','right'),('front','back','right'),('front','left','back','right')])
def test_both_workflows_accept_one_to_four_inputs(preset, views):
    from aida.scan.comfy import WORKFLOWS, VIEW_ORDER
    graph = wire_workflow(json.loads(WORKFLOWS[preset].read_text()),
                          {v:f'{v}.png' for v in views}, ComfyConfig(workflow=preset))
    assert {v for v in VIEW_ORDER if v in graph['500']['inputs']} == set(views)
    assert {v for v in VIEW_ORDER if v in graph['324']['inputs']} == set(views)
    for view in set(VIEW_ORDER)-set(views):
        assert graph[ComfyConfig().load_image_nodes[view]]['inputs']['image'] == '(none)'


def test_cancelled_scan_does_not_submit_more_comfy_requests(monkeypatch):
    import threading
    from aida.scan.comfy import ComfyError
    event = threading.Event()
    event.set()
    client = ComfyClient()
    client.cancelled = event
    def forbidden(*args, **kwargs):
        raise AssertionError('Cancelled scans must not access ComfyUI again')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    with pytest.raises(ComfyError, match='cancelled'):
        client.upload_image(b'photo', 'front.png')


def test_http_validation_error_preserves_missing_node_message(monkeypatch):
    import io, urllib.error
    detail = {'error': {'type':'missing_node_type', 'message':"Node AidaObjectOnlyViews not found"}}
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError('http://fake/prompt',400,'Bad Request',{},io.BytesIO(json.dumps(detail).encode()))
    monkeypatch.setattr('urllib.request.urlopen',fail)
    with pytest.raises(ComfyError,match='AidaObjectOnlyViews') as error:
        ComfyClient()._json('/prompt',{'prompt':{}})
    assert 'HTTP 400' in str(error.value) and 'Cannot reach' not in str(error.value)


def test_preflight_reports_missing_nodes_and_models_before_capture(monkeypatch):
    client = ComfyClient()
    monkeypatch.setattr(client,'_json',lambda _: {
        'CheckpointLoaderSimple': {'input':{'required':{'ckpt_name':[[],{}]}}}})
    with pytest.raises(ComfyError) as error:
        client.validate_workflow()
    assert 'AidaObjectOnlyViews' in str(error.value)
    assert 'sam3.1_multiplex_fp16.safetensors' in str(error.value)


def test_preflight_does_not_require_photos_that_have_not_been_captured(monkeypatch):
    client=ComfyClient()
    workflow=json.loads(Path(client.config.workflow_path).read_text())
    definitions={node['class_type']:{'input':{'required':{}}} for node in workflow.values()}
    definitions['LoadImage']['input']['required']['image']=[['different_photo.png'],{}]
    monkeypatch.setattr(client,'_json',lambda _:definitions)
    client.validate_workflow()
