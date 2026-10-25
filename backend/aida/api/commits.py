"""Explicit, atomic workspace commits. Geometry IDs are rendering artifacts, not history."""
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..workspace import ASSEMBLY, WORKSPACE_FILE

router = APIRouter()
COMMITS = 'commits'
STATE_FILE = 'commit.json'
HEAD_FILE = 'commit-head.json'


def _model_files(root: Path) -> list[Path]:
    files = [Path(WORKSPACE_FILE)]
    files += sorted(p.relative_to(root) for p in (root / 'parts').glob('*/model.json'))
    files.append(Path(ASSEMBLY) / 'model.json')
    return [f for f in files if (root / f).is_file()]


def design_hash(root: Path) -> str:
    """Design intent only: navigation, verification and operation counters are not edits."""
    design = {}
    for rel in _model_files(root):
        data = json.loads((root / rel).read_text(encoding='utf-8'))
        if rel == Path(WORKSPACE_FILE):
            data = {'parts': sorted(data.get('parts', []), key=lambda p: p['name'])}
        else:
            data = {k: data.get(k) for k in ('params', 'features', 'placement')}
        design[rel.as_posix()] = data
    return hashlib.sha256(json.dumps(design, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def list_commits(root: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding='utf-8'))
            for p in sorted((root / COMMITS).glob('c*/commit.json'))]


def current_commit(root: Path) -> int | None:
    path = root / HEAD_FILE
    return json.loads(path.read_text(encoding='utf-8'))['number'] if path.is_file() else None


def _write_head(root: Path, number: int):
    path = root / HEAD_FILE
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'number': number}), encoding='utf-8')
    os.replace(temp, path)


def publish_working_state(session):
    root = session.artifacts.root
    head = current_commit(root)
    base = next((c for c in list_commits(root) if c['number'] == head), None)
    if base:
        dirty = design_hash(root) != base['hash']
    else:
        workspace = root / WORKSPACE_FILE
        dirty = bool(json.loads(workspace.read_text(encoding='utf-8')).get('parts', [])) if workspace.is_file() else False
    geometry = next((m['revision'] for m in reversed(session.meshes) if m.get('generated')), None)
    session.publish({'type': 'working_state', 'current_commit': head, 'dirty': dirty,
                     'geometry_id': geometry if session.workspace.objects() else None})


def public_commit(commit):
    return {k: v for k, v in commit.items() if k != 'hash'}


def save_commit(session, message: str) -> dict | None:
    """Called under the workspace mutation lock, after refreshing the geometry snapshot."""
    message = message.strip()
    if not message or len(message) > 400:
        raise ValueError('Give the commit a message of 1 to 400 characters.')
    root = session.artifacts.root
    files = _model_files(root)
    if not (root / WORKSPACE_FILE).is_file():
        return None
    commits = list_commits(root)
    head = current_commit(root)
    base = next((c for c in commits if c['number'] == head), None)
    digest = design_hash(root)
    if not base and not json.loads((root / WORKSPACE_FILE).read_text(encoding='utf-8')).get('parts') and not any(f.name == 'model.json' for f in files):
        return None
    if base and base['hash'] == digest:
        return None
    number = max((c['number'] for c in commits), default=0) + 1
    geometry = next((m for m in reversed(session.meshes) if m.get('generated')), None)
    has_shape = bool(session.workspace.objects())
    if has_shape and (geometry is None or not geometry['path'].is_file()):
        raise ValueError('Could not capture the current geometry; retry the commit after rebuilding.')
    boundary = next((i for i in range(len(session.events) - 1, -1, -1)
                     if session.events[i]['type'] in ('commit', 'commit_restored')), -1)
    steps = [e for e in session.events[boundary + 1:]
             if e['type'] in ('user_prompt', 'tool_call', 'tool_result', 'notice')
             and e.get('name') != 'commit_changes']
    if not any(e['type'] == 'user_prompt' for e in steps):
        prompt = next((e for e in reversed(session.events) if e['type'] == 'user_prompt'), None)
        if prompt:
            steps.insert(0, prompt)
    commit = {'number': number, 'message': message, 'at': time.time(), 'hash': digest,
              'geometry_id': geometry['revision'] if geometry and has_shape else None, 'steps': steps,
              'geometry': {k: geometry[k] for k in ('revision', 'triangles', 'extents', 'parts') if k in geometry}
              if geometry and has_shape else None}
    directory = root / COMMITS
    directory.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.pending-', dir=directory))
    target = directory / f'c{number:03d}'
    try:
        for rel in files:
            dest = temp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)
        if geometry and has_shape:
            shutil.copy2(geometry['path'], temp / 'mesh.glb')
            if geometry.get('step_path'):
                shutil.copy2(geometry['step_path'], temp / 'cad.step')
        (temp / STATE_FILE).write_text(json.dumps(commit, indent=2), encoding='utf-8')
        # Windows file indexing can briefly hold a newly copied directory open.
        for attempt in range(5):
            try:
                os.replace(temp, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * 2 ** attempt)
        try:
            _write_head(root, number)
        except BaseException:
            shutil.rmtree(target)
            raise
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    session.publish({'type': 'commit', **public_commit(commit)})
    publish_working_state(session)
    return commit


async def restore_commit(session, number: int) -> list[str]:
    """Put the live workspace back to commit ``number`` and rebuild it in the CAD engine.
    Returns notes about anything that did not rebuild."""
    from ..model import _empty_state
    from ..workspace import read_workspace

    root = session.artifacts.root
    folder = root / COMMITS / f'c{number:03d}'
    if not (folder / STATE_FILE).is_file():
        raise KeyError(number)
    commit = json.loads((folder / STATE_FILE).read_text(encoding='utf-8'))
    workspace = session.workspace

    # The design files as they were (models absent from the commit had no state yet).
    shutil.copy2(folder / WORKSPACE_FILE, root / WORKSPACE_FILE)
    for current in (root / 'parts').glob('*/model.json'):
        if not (folder / current.relative_to(root)).is_file():
            current.unlink()
    for saved in (folder / 'parts').glob('*/model.json'):
        target = root / saved.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(saved, target)
    assembly_saved = folder / ASSEMBLY / 'model.json'
    if assembly_saved.is_file():
        (root / ASSEMBLY).mkdir(exist_ok=True)
        shutil.copy2(assembly_saved, root / ASSEMBLY / 'model.json')
    else:
        (root / ASSEMBLY / 'model.json').unlink(missing_ok=True)

    # The live workspace follows: same part objects where they still exist (the agent's
    # tools hold the workspace), new ones for parts that came back, none for later parts.
    specs, active = read_workspace(root)
    names = [spec['name'] for spec in specs]
    mesh = getattr(session.cad, 'mesh', None)
    for name in list(workspace.parts):
        if name not in names:
            del workspace.parts[name]
            if mesh is not None and hasattr(mesh, 'references'):
                mesh.references.pop(name, None)
    for spec in specs:
        model = workspace.parts.get(spec['name'])
        if model is None:
            model = workspace._add(spec['name'], spec.get('reference'), spec.get('description', ''))
            if mesh is not None and hasattr(mesh, 'references') and model.reference:
                mesh.references[spec['name']] = model.reference
        elif not model.load_saved():
            model.state = _empty_state()
        model.undo_stack = []
    workspace.parts = {name: workspace.parts[name] for name in names}
    if not workspace.assembly.load_saved():
        workspace.assembly.state = _empty_state()
    workspace.assembly.undo_stack = []
    workspace.assembly.layout = {'variants': [], 'instances': []}
    workspace.active = active if active in workspace.parts else next(iter(workspace.parts), None)

    notes = await workspace.restore()
    if notes:
        raise ValueError('Could not fully restore commit: ' + '; '.join(notes))
    workspace.save()
    session.publish({'type': 'workspace_state', **workspace.snapshot()})
    async with session.cad.lock:
        if session.cad.on_execute is not None:
            await session.cad.on_execute()  # a viewer revision for the restored shape
    prompt = commit.get('message') or ''
    session.pending.append(f'The user restored commit {number} (after "{prompt[:120]}"). '
                           'The parts and assembly are back to how they were then; call '
                           'workspace_status before changing them.')
    _write_head(root, number)
    session.publish({'type': 'commit_restored', 'number': number})
    publish_working_state(session)
    return notes


def _live_session(workspace_id: str):
    from .cad_sessions import _manager
    session = _manager().live(workspace_id)
    if session is None or session.workspace is None or session.cad is None:
        raise HTTPException(status_code=409, detail='Open the workspace and wait for it to be ready first')
    return session


@router.get('/api/v1/workspaces/{workspace_id}/commits')
def get_commits(workspace_id: str):
    from .cad_sessions import _manager
    folder = _manager().folder(workspace_id)
    if folder is None:
        raise HTTPException(status_code=404, detail='No such workspace')
    return [public_commit(c) for c in list_commits(folder)]


@router.get('/api/v1/workspaces/{workspace_id}/commits/{number}/mesh.glb')
def commit_mesh(workspace_id: str, number: int):
    from .cad_sessions import _manager
    folder = _manager().folder(workspace_id)
    path = folder / COMMITS / f'c{number:03d}' / 'mesh.glb' if folder else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail='This commit has no geometry')
    return FileResponse(path, media_type='model/gltf-binary')


@router.post('/api/v1/workspaces/{workspace_id}/commits/{number}/restore')
async def restore(workspace_id: str, number: int):
    session = _live_session(workspace_id)
    if session.stage != 'idle':
        raise HTTPException(status_code=409, detail='Aida is working; stop it or wait before restoring')
    session.set_stage('restoring')
    try:
        async with session.parts_lock:
            notes = await restore_commit(session, number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f'No commit {number}') from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.set_stage('idle')
    for note in notes:
        session.publish({'type': 'notice', 'text': note})
    return {'restored': number, 'notes': notes}
