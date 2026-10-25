"""Metric scaling tests: anchors, re-centering, metadata, and STL export."""
import json

import pytest
import trimesh

from aida.scan.scaling import (ScalingError, export_stl, load_mesh, scale_factor_for,
                               scale_mesh)


def make_box(path, extents=(1.0, 2.0, 4.0)):
    trimesh.creation.box(extents=extents).export(path)
    return path


def test_scale_factor_by_height(tmp_path):
    mesh = make_box(tmp_path / 'box.glb')
    out = tmp_path / 'mm.glb'
    metadata = scale_mesh(mesh, out, tmp_path / 'dims.json', target_height_mm=60.0)
    assert metadata['scale_multiplier_applied'] == pytest.approx(15.0)
    assert metadata['bounding_box_mm']['height_z'] == pytest.approx(60.0)
    assert metadata['bounding_box_mm']['width_x'] == pytest.approx(15.0)
    assert metadata['bounding_box_mm']['depth_y'] == pytest.approx(30.0)
    assert (tmp_path / 'dims.json').is_file()
    assert json.loads((tmp_path / 'dims.json').read_text())['unit'] == 'millimeter (mm)'


def test_scaled_mesh_base_on_z0_and_centered(tmp_path):
    mesh = make_box(tmp_path / 'box.glb', extents=(2.0, 2.0, 2.0))
    out = tmp_path / 'mm.glb'
    scale_mesh(mesh, out, target_height_mm=10.0)
    scaled = load_mesh(out)
    assert scaled.bounds[0][2] == pytest.approx(0.0)
    assert scaled.bounds[0][0] == pytest.approx(-5.0)
    assert scaled.bounds[0][1] == pytest.approx(-5.0)


def test_scale_by_base_diameter(tmp_path):
    mesh = make_box(tmp_path / 'box.glb', extents=(1.0, 3.0, 2.0))
    metadata = scale_mesh(mesh, tmp_path / 'mm.glb', target_base_dia_mm=40.0)
    # base diameter anchor = mean of X and Y extents = 2.0 -> scale 20
    assert metadata['scale_multiplier_applied'] == pytest.approx(20.0)
    assert metadata['bounding_box_mm']['width_x'] == pytest.approx(20.0)


def test_explicit_scale_factor(tmp_path):
    mesh = make_box(tmp_path / 'box.glb')
    metadata = scale_mesh(mesh, tmp_path / 'mm.glb', scale_factor=2.5)
    assert metadata['bounding_box_mm']['height_z'] == pytest.approx(10.0)


def test_no_anchor_is_rejected(tmp_path):
    mesh = make_box(tmp_path / 'box.glb')
    with pytest.raises(ScalingError):
        scale_mesh(mesh, tmp_path / 'mm.glb')
    with pytest.raises(ScalingError):
        scale_factor_for((1.0, 1.0, 1.0))


def test_export_stl(tmp_path):
    mesh = make_box(tmp_path / 'box.glb')
    out = tmp_path / 'box.stl'
    export_stl(mesh, out)
    stl = trimesh.load(out, force='mesh')
    assert len(stl.faces) > 0


def test_metric_glb_preserves_pbr_maps(tmp_path):
    from PIL import Image
    import numpy as np
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals
    from aida.scan.scaling import scale_mesh, load_mesh
    mesh = trimesh.creation.box()
    color = Image.new('RGB', (8, 8), (200, 80, 30))
    normal = Image.new('RGB', (8, 8), (128, 128, 255))
    material = PBRMaterial(baseColorTexture=color, normalTexture=normal,
                           metallicRoughnessTexture=color, occlusionTexture=color)
    mesh.visual = TextureVisuals(uv=np.zeros((len(mesh.vertices), 2)), material=material)
    source, target = tmp_path/'raw.glb', tmp_path/'metric.glb'
    mesh.export(source)
    scale_mesh(source, target, scale_factor=40)
    result = load_mesh(target)
    assert result.extents == pytest.approx([40,40,40])
    assert result.visual.kind == 'texture'
    for name in ('baseColorTexture','normalTexture','metallicRoughnessTexture','occlusionTexture'):
        assert getattr(result.visual.material, name) is not None
