"""Optional stdio MCP facade over the same bounded FEA worker as the web UI."""
import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import capabilities, require_step
from .settings import FeaSettings
from .worker import run_worker


def create_server(root: Path) -> MCPServer:
    root = root.resolve()
    server = MCPServer('aida-fea', instructions='Analyze a CAD solid in millimeters. Forces are N and stresses MPa. Input files and generated results stay under the configured workspace.')
    slots = asyncio.Semaphore(1)

    @server.tool()
    def fea_capabilities() -> dict[str, Any]:
        """List installed solvers, supported inputs and physical units."""
        return {**capabilities(), 'root': str(root), 'formats': ['step', 'stp'],
                'units': {'length': 'mm', 'force': 'N', 'stress': 'MPa'}}

    @server.tool()
    async def run_fea(cad_file: str, settings: FeaSettings | None = None) -> dict[str, Any]:
        """Run a clamped-end static structural test on a workspace STEP file in mm.

        Netgen meshes the exact CAD with quadratic tetrahedra and NGSolve solves it.
        Returns stress/deformation GLB paths and a JSON report. The selected
        fixed face is clamped; total force acts uniformly on its opposite end.
        """
        source = (root / cad_file).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ToolError('cad_file must be an existing file inside the configured FEA workspace.')
        if source.suffix.lower() not in {'.step', '.stp'}:
            raise ToolError('FEA needs a STEP file (.step or .stp).')
        settings = settings or FeaSettings()
        try:
            step = require_step(source)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        async with slots:
            output = root / f'fea-{uuid.uuid4().hex[:12]}'
            output.mkdir()
            config = output / 'settings.json'
            config.write_text(settings.model_dump_json(), encoding='utf-8')
            try:
                await run_worker(step, config, output)
            except TimeoutError as exc:
                raise ToolError('FEA exceeded the two-minute limit. Try a coarser mesh.') from exc
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
            return {**json.loads((output / 'report.json').read_text()),
                    'files': {name: str(output / name) for name in ['report.json', 'stress.glb', 'displacement.glb']}}

    return server


def main():
    create_server(Path(os.environ.get('AIDA_FEA_ROOT', '.'))).run(transport='stdio')


if __name__ == '__main__':
    main()
