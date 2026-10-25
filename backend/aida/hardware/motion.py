"""Hardware-bound timing profiles and inspectable motion traces."""
import json
import time
import uuid
from pathlib import Path


PROFILE_FIELDS = {f'{prefix}{direction}_{quantity}'
                  for prefix in ('', 'fine_') for direction in ('positive', 'negative')
                  for quantity in ('pulse_us', 'deg_per_s')}


def load_profile(folder, port, camera_id, neutral_us):
    path = Path(folder) / 'motion_profile.json'
    if not path.exists():
        return {}
    profile = json.loads(path.read_text())
    if not isinstance(profile, dict):
        raise ValueError('Motion profile must be a JSON object.')
    if (profile.get('version'), profile.get('port'), profile.get('camera_id'), profile.get('neutral_us')) != (
            1, port, camera_id, neutral_us):
        return {}
    if not isinstance(profile.get('control'), dict):
        raise ValueError('Motion profile is missing control settings.')
    return {key: value for key, value in profile['control'].items() if key in PROFILE_FIELDS}


def save_profile(settings, controller):
    profile = {'version': 1, 'port': settings.port, 'camera_id': settings.camera_id,
               'neutral_us': settings.neutral_us, 'updated_at': time.time(),
               'control': controller.profile()}
    path = settings.storage / 'motion_profile.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(profile, indent=2) + '\n')
    temporary.replace(path)


class MotionTrace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.move_id = None

    def __call__(self, event):
        if event['event'] == 'start':
            self.move_id = uuid.uuid4().hex
        with self.path.open('a') as output:
            output.write(json.dumps({'move_id': self.move_id, **event}) + '\n')
