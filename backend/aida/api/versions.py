"""Versions: one saved state of the whole workspace per prompt that changed it.

After each agent turn, if any part or the assembly changed, their saved models
(workspace.json, every parts/<name>/model.json, assembly/model.json) are copied to
versions/v<NNN>/ with the prompt that led there and the viewer revision showing it.
Restoring copies them back and rebuilds the live workspace, so the user can go back to
how things were after any earlier prompt (and forward again: later versions stay).
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..workspace import ASSEMBLY, WORKSPACE_FILE

router = APIRouter()

VERSIONS = 'versions'
STATE_FILE = 'version.json'


def _model_files(root: Path) -> list[Path]:
    """The files that make up the workspace's design, relative to ``root``."""
    files = [Path(WORKSPACE_FILE)]
    files += sorted(p.relative_to(root) for p in (root / 'parts').glob('*/model.json'))
    if (root / ASSEMBLY / 'model.json').is_file():
        files.append(Path(ASSEMBLY) / 'model.json')
    return [f for f in files if (root / f).is_file()]


def design_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for rel in _model_files(root):
        digest.update(str(rel).replace('\\', '/').encode())
        digest.update((root / rel).read_bytes())
    return digest.hexdigest()


def list_versions(root: Path) -> list[dict]:
    out = []
    for folder in sorted((Path(root) / VERSIONS).glob('v*')):
        try:
            out.append(json.loads((folder / STATE_FILE).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    return out


def save_version(session, prompt: str) -> dict | None:
    """Record the workspace as a new version if it changed since the last one. Returns the
    version (also published as a ``version`` event), or None when nothing changed."""
    root = session.artifacts.root
    files = _model_files(root)
    if not any(str(f).endswith('model.json') for f in files):
        return None  # nothing designed yet
    digest = design_hash(root)
    versions = list_versions(root)
    if versions and versions[-1].get('hash') == digest:
        return None
    number = (versions[-1]['number'] + 1) if versions else 1
    folder = root / VERSIONS / f'v{number:03d}'
    for rel in files:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    generated = [m['revision'] for m in session.meshes if m.get('generated')]
    version = {'number': number, 'prompt': prompt.strip()[:400], 'at': time.time(), 'hash': digest,
               'mesh_revision': generated[-1] if generated else None}
    (folder / STATE_FILE).write_text(json.dumps(version, indent=2), encoding='utf-8')
    session.publish({'type': 'version', **{k: v for k, v in version.items() if k != 'hash'}})
    return version


async def restore_version(session, number: int) -> list[str]:
    """Put the live workspace back to version ``number`` and rebuild it in the CAD engine.
    Returns notes about anything that did not rebuild."""
    from ..model import _empty_state
    from ..workspace import read_workspace

    root = session.artifacts.root
    folder = root / VERSIONS / f'v{number:03d}'
    if not (folder / STATE_FILE).is_file():
        raise KeyError(number)
    version = json.loads((folder / STATE_FILE).read_text(encoding='utf-8'))
    workspace = session.workspace

    # The design files as they were (models absent from the version had no state yet).
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
    workspace.save()
    session.publish({'type': 'workspace_state', **workspace.snapshot()})
    async with session.cad.lock:
        if session.cad.on_execute is not None:
            await session.cad.on_execute()  # a viewer revision for the restored shape
    prompt = version.get('prompt') or ''
    session.pending.append(f'The user restored version {number} (after "{prompt[:120]}"). '
                           'The parts and assembly are back to how they were then; call '
                           'workspace_status before changing them.')
    session.publish({'type': 'version_restored', 'number': number})
    return notes


def _live_session(workspace_id: str):
    from .cad_sessions import _manager

    session = _manager().live(workspace_id)
    if session is None or session.workspace is None or session.cad is None:
        raise HTTPException(status_code=409, detail='Open the workspace (and wait for it to be ready) first')
    return session


@router.get('/api/v1/workspaces/{workspace_id}/versions')
def get_versions(workspace_id: str):
    from .cad_sessions import _manager

    folder = _manager().folder(workspace_id)
    if folder is None:
        raise HTTPException(status_code=404, detail=f'No workspace {workspace_id!r}')
    return [{k: v for k, v in version.items() if k != 'hash'} for version in list_versions(folder)]


@router.post('/api/v1/workspaces/{workspace_id}/versions/{number}/restore')
async def restore(workspace_id: str, number: int):
    session = _live_session(workspace_id)
    if session.stage != 'idle':
        raise HTTPException(status_code=409, detail='Aida is working; stop it or wait before restoring')
    session.set_stage('restoring')
    try:
        notes = await restore_version(session, number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f'No version {number}') from exc
    finally:
        session.set_stage('idle')
    for note in notes:
        session.publish({'type': 'notice', 'text': note})
    return {'restored': number, 'notes': notes}
