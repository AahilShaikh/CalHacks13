"""Per-workspace metadata kept beside the session files: a name, units, where each
reference came from, and the agent conversation to resume when it is reopened.

workspace-meta.json is small and rewritten whole; everything heavier (models,
meshes, the event log) already lives in the session folder.
"""
import json
import os
import time
from pathlib import Path

META_FILE = 'workspace-meta.json'
THUMBNAIL = 'thumbnail.png'


def read_meta(root: Path) -> dict:
    path = Path(root) / META_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_meta(root: Path, **changes) -> dict:
    """Merge ``changes`` into the metadata (dict values are merged one level deep)."""
    root = Path(root)
    meta = read_meta(root)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(meta.get(key), dict):
            meta[key] = {**meta[key], **value}
        else:
            meta[key] = value
    meta['updated_at'] = time.time()
    path = root / META_FILE
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    os.replace(temp, path)
    return meta


def reference_items(root: Path, session_id: str) -> list[dict]:
    """What the agent can look at: each part's scan (with the file it came from) and photos."""
    from ..workspace import read_workspace

    root = Path(root)
    meta = read_meta(root)
    files = meta.get('reference_files') or {}
    photos = meta.get('photo_files') or {}
    parts, _ = read_workspace(root)
    items = [{'kind': 'mesh', 'part': p['name'], 'name': files.get(p['name']) or f"{p['name']}.stl"}
             for p in parts if p.get('reference')]
    for png in sorted(root.glob('input-*.png')):
        items.append({'kind': 'photo', 'name': photos.get(png.name, png.name),
                      'url': f'/api/v1/cad-sessions/{session_id}/files/{png.name}'})
    return items


def transcript_exists(root: Path, agent_session: str) -> bool:
    """The agent's saved conversation is still on disk (so it can be resumed)."""
    projects = Path(root) / 'claude-runtime' / 'projects'
    return projects.is_dir() and any(projects.rglob(f'{agent_session}.jsonl'))
