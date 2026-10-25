"""Session artifacts and image delivery, independent of the agent runtime."""
import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image

# Claude downsizes images beyond 1568 px anyway; the API rejects images over 5 MB.
MAX_IMAGE_SIDE = 1568
MAX_IMAGE_BYTES = 3_500_000


def image_block(path) -> dict:
    """MCP image content for a PNG, shrunk so the model sees the same detail in a small payload."""
    import io
    path = Path(path)
    data, mime = path.read_bytes(), 'image/png'
    with Image.open(path) as img:
        if max(img.size) > MAX_IMAGE_SIDE or len(data) > MAX_IMAGE_BYTES:
            img = img.convert('RGBA' if 'A' in img.getbands() else 'RGB')
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, 'PNG', optimize=True)
            data = out.getvalue()
            if len(data) > MAX_IMAGE_BYTES:  # photos compress poorly as PNG
                out = io.BytesIO()
                img.convert('RGB').save(out, 'JPEG', quality=90)
                data, mime = out.getvalue(), 'image/jpeg'
    return {'type': 'image', 'mimeType': mime, 'data': base64.b64encode(data).decode()}


class Artifacts:
    def __init__(self, base: Path):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.root = (base / f'{stamp}-{uuid4().hex[:8]}').resolve()
        self.root.mkdir(parents=True)
        self.temp = self.root / 'server-output'
        self.temp.mkdir()
        self.counter = 0

    @classmethod
    def open(cls, root: Path) -> 'Artifacts':
        """An existing session folder (a reopened workspace); nothing is created or cleared."""
        self = cls.__new__(cls)
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f'No session folder {self.root}')
        self.temp = self.root / 'server-output'
        self.temp.mkdir(exist_ok=True)
        self.counter = len(list(self.root.glob('code-*.py')))
        return self

    def log(self, event: str, value):
        def redact(item):
            if isinstance(item, dict):
                return {k: ('<image bytes omitted>' if k == 'data' else redact(v)) for k, v in item.items()}
            if isinstance(item, list):
                return [redact(v) for v in item]
            return item
        with (self.root / 'events.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'event': event, 'value': redact(value)}, default=str) + '\n')

    def save_code(self, code: str):
        self.counter += 1
        (self.root / f'code-{self.counter:03}.py').write_text(code, encoding='utf-8')

    def copy_mesh(self, source: Path) -> Path:
        target = self.root / 'reference.stl'
        shutil.copy2(source, target)
        return target

    def output_path(self, filename: str) -> Path:
        path = (self.root / filename).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Output must remain inside the session artifact directory')
        return path

    def image(self, filename: str) -> dict:
        path = Path(filename).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Image must be inside the session artifact directory')
        if path.suffix.lower() != '.png':
            raise ValueError('Only PNG previews are supported')
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Preview exceeds 20 MB')
        with Image.open(path) as img:
            if img.format != 'PNG':
                raise ValueError('Preview is not a PNG')
            img.verify()
        with Image.open(path) as img:
            if all(low == high for low, high in img.convert('RGB').getextrema()):
                raise ValueError('Preview is blank; geometry was not rendered')
        return {'content': [image_block(path)]}
