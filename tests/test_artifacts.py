import base64

import pytest
from PIL import Image

from aida.artifacts import Artifacts


def test_image_roundtrip_and_boundaries(tmp_path):
    artifacts = Artifacts(tmp_path / 'sessions')
    image = artifacts.root / 'render.png'
    img = Image.new('RGB', (16, 16), 'red')
    img.putpixel((8, 8), (0, 0, 255))
    img.save(image)
    block = artifacts.image(str(image))['content'][0]
    assert block['type'] == 'image'
    assert base64.b64decode(block['data']) == image.read_bytes()
    outside = tmp_path / 'outside.png'
    Image.new('RGB', (16, 16)).save(outside)
    with pytest.raises(ValueError, match='session'):
        artifacts.image(str(outside))
    link = artifacts.root / 'link.png'
    link.symlink_to(outside)
    with pytest.raises(ValueError, match='session'):
        artifacts.image(str(link))
    with pytest.raises(ValueError):
        artifacts.output_path('../escape.step')


def test_bad_image_and_log_redaction(tmp_path):
    artifacts = Artifacts(tmp_path)
    bad = artifacts.root / 'bad.png'
    bad.write_text('not an image')
    with pytest.raises(OSError):
        artifacts.image(str(bad))
    artifacts.log('image', {'data': 'secret-image-bytes'})
    assert 'secret-image-bytes' not in (artifacts.root / 'events.jsonl').read_text()


def test_blank_render_rejected(tmp_path):
    artifacts = Artifacts(tmp_path)
    blank = artifacts.root / 'blank.png'
    Image.new('RGB', (800, 600), 'white').save(blank)
    with pytest.raises(ValueError, match='blank'):
        artifacts.image(str(blank))
