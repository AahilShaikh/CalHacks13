"""Structural analysis backends shared by the web API, CLI and MCP adapter."""
from importlib.util import find_spec
from pathlib import Path

from .settings import FeaSettings


def capabilities() -> dict:
    return {
        'ngsolve': find_spec('ngsolve') is not None,
        'voxel': True,
        'install_hint': 'uv sync --extra fea',
    }


def select_backend(settings: FeaSettings, mesh: Path | None, step: Path | None) -> str:
    has_step = step is not None and step.is_file()
    if settings.engine == 'ngsolve':
        if not capabilities()['ngsolve']:
            raise ValueError('NGSolve is not installed. Run uv sync --extra fea, or select the voxel solver.')
        if not has_step:
            raise ValueError('This CAD revision has no STEP snapshot. Rebuild it or select the voxel solver.')
        return 'ngsolve'
    if settings.engine == 'auto' and has_step and capabilities()['ngsolve']:
        return 'ngsolve'
    if mesh is None or not mesh.is_file():
        raise ValueError('A CAD mesh is required for the voxel solver. For STEP input, install NGSolve with uv sync --extra fea.')
    return 'voxel'


def analyze_model(mesh: Path | None, settings: FeaSettings, output: Path,
                  step: Path | None = None) -> dict:
    """Select once; errors from a chosen solver never silently change methods."""
    engine = select_backend(settings, mesh, step)
    if engine == 'ngsolve':
        from .ngsolve_backend import analyze_step

        result = analyze_step(step, settings, output)
    else:
        from ..fea import analyze

        result = analyze(mesh, settings, output)
        if settings.engine == 'auto':
            reason = 'NGSolve is not installed' if not capabilities()['ngsolve'] else 'this revision has no STEP snapshot'
            result['warnings'].insert(0, f'Using the voxel solver because {reason}. For CAD-conforming meshing, run uv sync --extra fea and rebuild the CAD.')
    return result
