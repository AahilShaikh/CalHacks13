"""Shared arbitrary-angle photograph operation for HTTP and Aida's tool."""
import asyncio
import json
import uuid
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from ..turntable import HardwareSettings
from .workspace_meta import update_meta


async def capture_angle(session, controller, *, angle, relative=False, tolerance=1.0, timeout_s=30.0):
    previous_stage = session.stage if session else None
    if session:
        if session.closing or session.status not in ('queued', 'running'):
            raise HTTPException(409, 'Open the workspace before capturing a photograph.')
        session.set_stage('capturing_angle')
    try:
        result = await asyncio.to_thread(controller.rotate, angle % 360, relative=relative,
            tolerance=tolerance, timeout_s=timeout_s, capture=True)
        result['requested_angle'] = angle % 360
        if session:
            async with session.parts_lock:
                if session.closing or session.status not in ('queued', 'running'):
                    raise HTTPException(409, 'The workspace closed while the photograph was being captured.')
                source = HardwareSettings.from_env().storage / 'captures' / Path(result['capture_url']).name
                identity = uuid.uuid4().hex
                relative_path = f'captures/{identity}.png'
                image = session.artifacts.root / relative_path
                image.parent.mkdir(exist_ok=True)
                with Image.open(source) as photo:
                    photo.convert('RGB').save(image, format='PNG')
                (image.with_suffix('.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
                update_meta(session.artifacts.root, photo_files={relative_path:
                    f"Turntable {result['measured_angle']:.1f}°"})
                result['capture_url'] = f'/api/v1/cad-sessions/{session.id}/files/{relative_path}'
                result['image_path'] = image
                session.pending.append(f'A turntable photograph at {result["measured_angle"]:.1f} degrees '
                                       f'is saved at {relative_path}; use view_image to inspect it if needed.')
                session.publish(session.references_event())
                session.publish({'type': 'render', 'url': result['capture_url']})
        return result
    except OSError as exc:
        raise HTTPException(500, f'Could not persist the turntable photograph: {exc}') from exc
    finally:
        if session and session.stage == 'capturing_angle':
            session.set_stage(previous_stage)
