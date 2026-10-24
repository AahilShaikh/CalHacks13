import pytest
from PIL import Image

from aida.mesh_render import SpecError, camera_frame, normalize_spec, render


def test_spec_validation():
    assert normalize_spec({})['grid'] is True
    for bad, message in (({'zoom': 2}, 'Unknown'), ({'direction': 'up'}, 'direction'),
                         ({'direction': 'top', 'view_dir': [0, 0, 1]}, 'not both'),
                         ({'fit_box': [[0, 0, 0], [1, 1, 1]], 'view_width': 3}, 'fit_box'),
                         ({'fit_box': [[1, 0, 0], [0, 1, 1]]}, 'exceed'),
                         ({'view_width': 0}, 'positive'), ({'slice_at': 2}, 'slice_at'),
                         ({'view_dir': [0, 0, 0]}, 'zero'), ({'center': [1, 2]}, 'three')):
        with pytest.raises(SpecError, match=message):
            normalize_spec(bad)


def test_camera_frame_fit_and_zoom():
    frame = camera_frame((0, 0, 1), (0, 0, 0), (-30, -20, -3), (30, 20, 3))
    assert frame['screen_right'] == pytest.approx([1, 0, 0])
    assert frame['screen_up'] == pytest.approx([0, 1, 0])
    assert frame['visible_width'] >= 60 and frame['visible_height'] >= 40
    zoomed = camera_frame((0, 0, 1), (15, 5, 0), (-30, -20, -3), (30, 20, 3), view_width=16)
    assert zoomed['visible_width'] == pytest.approx(16)
    assert zoomed['units_per_pixel'] == pytest.approx(0.02)


def test_zoomed_render_maps_pixels_to_model(plate_stl, tmp_path):
    out = tmp_path / 'zoom.png'
    meta = render(plate_stl, out, {'direction': 'top', 'center': [15, 5, 0], 'view_width': 16,
                                   'grid': False}, 'mm')
    assert meta['units_per_pixel'] == pytest.approx(0.02)
    image = Image.open(out).convert('RGB')
    assert image.getpixel((400, 300)) == (255, 255, 255)  # centre of the d=8 hole
    # 6 mm right of the hole centre lies on the plate: px = 400 + 6 / 0.02.
    assert image.getpixel((700, 300)) != (255, 255, 255)


def test_slice_render_shows_cut_face(plate_stl, tmp_path):
    out = tmp_path / 'slice.png'
    meta = render(plate_stl, out, {'slice_axis': 'y', 'slice_at': 5, 'grid': False}, 'mm')
    assert meta['slice']['label'] == 'y=5'
    assert meta['view'] == 'facing slice'
    pixels = Image.open(out).convert('RGB').resize((200, 150)).getcolors(200 * 150)
    assert any(r > 200 and 70 < g < 150 and b < 120 for _, (r, g, b) in pixels)  # orange cap


def test_render_errors(plate_stl, tmp_path):
    with pytest.raises(SpecError, match='Nothing visible'):
        render(plate_stl, tmp_path / 'a.png', {'direction': 'top', 'center': [500, 0, 0], 'view_width': 5})
    with pytest.raises(SpecError, match='misses'):
        render(plate_stl, tmp_path / 'b.png', {'slice_axis': 'z', 'slice_at': 50})
