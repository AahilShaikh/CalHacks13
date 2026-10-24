"""Saved workspaces: list, open (rebuild in a fresh CAD engine), rename, delete, thumbnail.

A workspace is one session folder under the CAD storage directory; its id is the
folder name and is also the id of the live session that has it open. Everything
needed to reopen it is already in the folder (workspace.json, each part's
model.json, the event log), plus workspace-meta.json for the name and the agent
conversation to resume.
"""
import asyncio
import io
import json
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .cad_sessions import REPLAY_LOG, WORKSPACE_JSON, _manager
from .workspace_meta import THUMBNAIL, read_meta, update_meta

router = APIRouter()

TRASH = '.trash'
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
UNTITLED = 'Untitled workspace'


def _first_prompt(folder: Path) -> str:
    log = folder / REPLAY_LOG
    if not log.is_file():
        return ''
    with log.open(encoding='utf-8') as lines:
        for line in lines:
            if '"user_prompt"' not in line:
                continue
            try:
                return json.loads(line).get('text', '')
            except ValueError:
                continue
    return ''


def _revisions(folder: Path) -> int:
    """Generated CAD revisions, counted from the event log (scans are revisions too)."""
    log = folder / REPLAY_LOG
    if not log.is_file():
        return 0
    count = 0
    with log.open(encoding='utf-8') as lines:
        for line in lines:
            if '"type": "mesh"' in line and '"generated": true' in line:
                count += 1
    return count


def summary(folder: Path) -> dict:
    """What the home screen shows for one workspace."""
    from ..workspace import read_workspace

    meta = read_meta(folder)
    parts = [p['name'] for p in read_workspace(folder)[0]]
    prompt = _first_prompt(folder)
    stamps = [p.stat().st_mtime for p in (folder / REPLAY_LOG, folder / WORKSPACE_JSON) if p.is_file()]
    live = _manager().live(folder.name)
    name = (meta.get('name') or '').strip()
    return {
        'id': folder.name,
        'name': name or (prompt[:60].strip() if prompt else '') or UNTITLED,
        'named': bool(name),
        'prompt': prompt,
        'parts': parts,
        'revisions': _revisions(folder),
        'created_at': meta.get('created_at') or folder.stat().st_ctime,
        'updated_at': max([*stamps, meta.get('updated_at') or 0]) or folder.stat().st_mtime,
        'thumbnail': f'/api/v1/workspaces/{folder.name}/thumbnail.png' if (folder / THUMBNAIL).is_file() else None,
        'status': 'open' if live is not None else 'closed',
        'empty': not parts and not prompt and not name,
    }


def _folder_or_404(workspace_id: str) -> Path:
    folder = _manager().folder(workspace_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=f'No workspace {workspace_id!r}')
    return folder


@router.get('/api/v1/workspaces')
def list_workspaces(include_empty: bool = False):
    """Saved workspaces, most recently changed first. Workspaces that were opened and left
    without a part, a prompt or a name are hidden unless ``include_empty``."""
    storage = _manager().storage_dir
    out = []
    if storage.is_dir():
        for folder in storage.iterdir():
            if folder.name.startswith(('.', '_')) or not (folder / WORKSPACE_JSON).is_file():
                continue
            item = summary(folder)
            if include_empty or not item['empty']:
                out.append(item)
    return sorted(out, key=lambda w: w['updated_at'], reverse=True)


@router.get('/api/v1/workspaces/{workspace_id}')
def get_workspace(workspace_id: str):
    return summary(_folder_or_404(workspace_id))


class WorkspaceChange(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch('/api/v1/workspaces/{workspace_id}')
def rename_workspace(workspace_id: str, body: WorkspaceChange):
    folder = _folder_or_404(workspace_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail='Give the workspace a name')
    update_meta(folder, name=name)
    return summary(folder)


@router.post('/api/v1/workspaces/{workspace_id}/open', status_code=202)
async def open_workspace(workspace_id: str):
    """Open a saved workspace: the live session if it is running, else a new one that
    rebuilds its parts and resumes its agent conversation. Subscribe to events_url after."""
    _folder_or_404(workspace_id)
    manager = _manager()
    live = manager.live(workspace_id)
    if live is None:
        try:
            session = await asyncio.to_thread(manager.reopen, workspace_id)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=f'No workspace {workspace_id!r}') from exc
        manager.start(session)
    else:
        session = live
    return {'session_id': session.id, 'reopened': live is None, 'events_url': session.events_url,
            'status_url': f'/api/v1/cad-sessions/{session.id}'}


@router.delete('/api/v1/workspaces/{workspace_id}')
async def delete_workspace(workspace_id: str):
    """Close the workspace if it is open, then move its folder to the trash folder
    (nothing is erased; restore it by moving the folder back)."""
    folder = _folder_or_404(workspace_id)
    manager = _manager()
    session = manager.get(workspace_id)
    if session is not None:
        await manager.close(session)
        manager.forget(workspace_id)
    trash = manager.storage_dir / TRASH
    trash.mkdir(exist_ok=True)
    target = trash / workspace_id
    if target.exists():
        target = trash / f'{workspace_id}-{int(time.time())}'
    try:
        await asyncio.to_thread(shutil.move, str(folder), str(target))
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f'Could not move the workspace to the trash: {exc}') from exc
    return {'deleted': workspace_id}


@router.put('/api/v1/workspaces/{workspace_id}/thumbnail.png', status_code=204)
async def put_thumbnail(workspace_id: str, request: Request):
    """The viewer's latest frame as a PNG (the body), for the home screen."""
    folder = _folder_or_404(workspace_id)
    data = await request.body()
    if not data or len(data) > MAX_THUMBNAIL_BYTES:
        raise HTTPException(status_code=400, detail='Send a PNG of at most 2 MB')
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG':
                raise ValueError('not a PNG')
            image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail='The thumbnail must be a PNG') from exc
    path = folder / THUMBNAIL
    temp = path.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.replace(path)


@router.get('/api/v1/workspaces/{workspace_id}/thumbnail.png')
def get_thumbnail(workspace_id: str):
    path = _folder_or_404(workspace_id) / THUMBNAIL
    if not path.is_file():
        raise HTTPException(status_code=404, detail='No thumbnail yet')
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-cache'})
