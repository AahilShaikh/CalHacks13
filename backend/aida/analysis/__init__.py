"""Structural analysis (Netgen/NGSolve on the STEP snapshot), shared by the web API, agent and MCP adapter."""
from importlib.util import find_spec
from pathlib import Path
from typing import Callable

from .settings import FeaSettings


def capabilities() -> dict:
    return {'ngsolve': find_spec('ngsolve') is not None, 'install_hint': 'uv sync'}


def require_step(step: Path | None) -> Path:
    """The solver meshes the exact CAD, so it needs the revision's STEP snapshot."""
    if not capabilities()['ngsolve']:
        raise ValueError('NGSolve is not installed. Run uv sync.')
    if step is None or not Path(step).is_file():
        raise ValueError('This CAD revision has no STEP snapshot. Rebuild the model, then run FEA again.')
    return Path(step)


def analyze_model(step: Path, settings: FeaSettings, output: Path,
                  on_stage: Callable[[str], None] = lambda stage: None) -> dict:
    from .ngsolve_backend import analyze_step

    return analyze_step(require_step(step), settings, output, on_stage)
