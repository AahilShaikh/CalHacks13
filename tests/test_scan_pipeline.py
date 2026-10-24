"""End-to-end pipeline tests with a fake ComfyUI client (no GPU, no network)."""
import json
from pathlib import Path

import pytest
import trimesh
from PIL import Image

from aida.scan import pipeline
from aida.scan.scaling import ScalingError


class FakeClient:
    """Stands in for ComfyUI: generates a deterministic box mesh."""

    def __init__(self):
        self.uploads = {}
        self.ran = False

    def upload_image(self, data, filename):
        self.uploads[filename] = data
        return filename

    def run_pixal3d(self, images, output_path):
        self.ran = True
        assert images.get('front')
        trimesh.creation.box(extents=(1.0, 2.0, 3.0)).export(output_path)
        return Path(output_path)


def front_png(tmp_path):
    path = tmp_path / 'front.png'
    Image.new('RGB', (64, 64), 'white').save(path)
    return path.read_bytes()


def test_run_scan_full_pipeline(tmp_path):
    client = FakeClient()
    stages = []
    result = pipeline.run_scan(
        {'front': front_png(tmp_path)}, tmp_path / 'job',
        known_height_mm=60.0, client=client,
        progress=stages.append)

    assert client.ran
    assert 'generating_mesh' in stages and 'scaling' in stages
    assert result.metric_mesh.is_file() and result.metric_stl.is_file()
    assert result.blueprint.is_file() and result.dimensions_json.is_file()

    specs = result.cad_specs
    assert specs['bounding_box_mm']['height_z'] == pytest.approx(60.0)
    assert specs['bounding_box_mm']['width_x'] == pytest.approx(20.0)
    assert specs['bounding_box_mm']['depth_y'] == pytest.approx(40.0)
    assert json.loads(result.dimensions_json.read_text()) == specs

    # The blueprint is a real, non-blank PNG.
    with Image.open(result.blueprint) as img:
        assert img.format == 'PNG'
        assert any(low != high for low, high in img.convert('RGB').getextrema())

    # Handoff artifacts for the CAD agent.
    prompt = (result.directory / 'agent_prompt.txt').read_text()
    assert '60.0mm (W)' in prompt or 'build123d' in prompt
    for name, path in result.files.items():
        assert Path(path).is_file(), name

    stl = trimesh.load(result.metric_stl, force='mesh')
    assert len(stl.faces) > 0


def test_run_scan_records_calibration_failure(tmp_path):
    client = FakeClient()
    result = pipeline.run_scan(
        {'front': front_png(tmp_path)}, tmp_path / 'job',
        scale_factor=2.0, client=client)
    # No board in the photo -> recorded as metadata, pipeline still succeeds.
    assert 'error' in result.cad_specs['charuco_calibration']


def test_run_scan_rejects_missing_anchor_before_generating(tmp_path):
    client = FakeClient()
    with pytest.raises(ScalingError, match='scale anchor'):
        pipeline.run_scan({'front': front_png(tmp_path)}, tmp_path / 'job', client=client)
    assert not client.ran, 'expensive generation must not run without a scale anchor'


def test_run_scan_requires_front_image(tmp_path):
    with pytest.raises(ValueError):
        pipeline.run_scan({}, tmp_path / 'job', client=FakeClient())
