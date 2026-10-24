"""Replay finished CAD sessions from disk, without Claude or the CAD engine.

Every session writes the events it streamed to ``ui-events.jsonl`` in its
artifacts folder. These routes list those folders and serve their events with
file URLs rewritten to the folder, so a replay keeps working after the server
restarts. The layout is static on purpose (``index.json``, ``events.json``,
``files/...``): the same tree could be copied to a static host.
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

REPLAY_LOG = 'ui-events.jsonl'
_FOLDER = re.compile(r'^[\w.-]+$')
_SESSION_FILES = re.compile(r'/api/v1/cad-sessions/[0-9a-f]+/files/')


def _storage() -> Path:
    from .cad_sessions import _manager
    return _manager().storage_dir.resolve()


def _folder(replay_id: str) -> Path:
    folder = (_storage() / replay_id).resolve() if _FOLDER.match(replay_id) else None
    if folder is None or folder.parent != _storage() or not (folder / REPLAY_LOG).is_file():
        raise HTTPException(status_code=404, detail=f'No replay {replay_id!r}')
    return folder


def _read_events(folder: Path) -> list[dict]:
    events = []
    for line in (folder / REPLAY_LOG).read_text(encoding='utf-8').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # a line cut off by a crash
    return events


def _rewrite(event: dict, replay_id: str) -> dict:
    """Point file URLs at this replay's folder instead of the (gone) live session."""
    files = f'/api/v1/replays/{replay_id}/files/'
    kind = event.get('type')
    if kind == 'mesh':
        return {**event, 'url': f"{files}mesh-{event['revision']:03d}.glb"}
    if kind == 'heatmap':
        part = f"-{event['part']}" if event.get('part') else ''  # one heatmap per part since workspaces
        return {**event, 'url': f"{files}heatmap-{event['revision']:03d}{part}.glb"}
    if kind == 'render':
        return {**event, 'url': _SESSION_FILES.sub(files, event['url'])}
    return event


@router.get('/api/v1/replays/index.json')
def list_replays():
    """Sessions that can be replayed, newest first."""
    storage = _storage()
    out = []
    if storage.is_dir():
        for folder in storage.iterdir():
            log = folder / REPLAY_LOG
            if not log.is_file():
                continue
            events = _read_events(folder)
            prompt = next((e.get('text') for e in events if e.get('type') == 'user_prompt'), '')
            revisions = sum(1 for e in events if e.get('type') == 'mesh' and e.get('generated'))
            if not events:
                continue
            out.append({'id': folder.name, 'prompt': prompt, 'revisions': revisions,
                        'events': len(events), 'started_at': events[0].get('at'),
                        'duration_s': round(events[-1].get('at', 0) - events[0].get('at', 0), 1)})
    return sorted(out, key=lambda r: r['started_at'] or 0, reverse=True)


@router.get('/api/v1/replays/{replay_id}/events.json')
def replay_events(replay_id: str):
    folder = _folder(replay_id)
    return [_rewrite(e, replay_id) for e in _read_events(folder)]


@router.get('/api/v1/replays/{replay_id}/files/{name}')
def replay_file(replay_id: str, name: str):
    folder = _folder(replay_id)
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail=f'No file {name!r}')
    return FileResponse(path, headers={'Cache-Control': 'public, max-age=31536000, immutable'})
