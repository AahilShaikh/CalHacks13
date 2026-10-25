"""Direct-editor view selection and packaged workflows do not depend on the API."""
import json
from pathlib import Path

from aida.scan.comfy import WORKFLOWS
from aida.scan.comfy_nodes import AidaOptionalImage, NONE_IMAGE


def test_unused_view_needs_no_placeholder_image():
    loader = AidaOptionalImage()
    assert loader.load(NONE_IMAGE) == (None,)
    assert loader.VALIDATE_INPUTS(NONE_IMAGE) is True
    assert loader.IS_CHANGED(NONE_IMAGE) == NONE_IMAGE


def test_editor_workflows_preprocess_every_connected_image_automatically():
    for preset, api_path in WORKFLOWS.items():
        ui = json.loads((api_path.parent/'ui'/f'aida_{preset}.json').read_text())
        nodes = {n['id']:n for n in ui['nodes']}
        links = {link[0]:link for link in ui['links']}
        assert nodes[500]['type'] == 'AidaObjectOnlyViews'
        assert not any(n['type']=='PrimitiveBoundingBox' for n in nodes.values())
        for node_id in (377,380,383):
            assert nodes[node_id]['type'] == 'AidaOptionalImage'
            assert nodes[node_id]['widgets_values'][0] == NONE_IMAGE
        for inp in nodes[324]['inputs']:
            if inp['name'] in ('front','left','back','right'):
                assert links[inp['link']][1] == 500
        for lid,origin,slot,target,index,_ in ui['links']:
            assert nodes[target]['inputs'][index]['link'] == lid
            assert lid in nodes[origin]['outputs'][slot]['links']


def test_calibrated_capture_passes_focal_length_into_shared_preprocessing():
    from aida.scan.comfy import ComfyConfig, wire_workflow
    config = ComfyConfig(workflow='fast',camera_focal_px=1257.7)
    graph = wire_workflow(json.loads(WORKFLOWS['fast'].read_text()),{'front':'front.png'},config)
    assert graph['500']['inputs']['camera_focal_px'] == 1257.7
    assert graph['324']['inputs']['fov'] == ['500',10]
