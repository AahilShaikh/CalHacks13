"""Isolated, bounded native solver execution, shared by HTTP and MCP."""
import asyncio
import os
import sys
from pathlib import Path

FEA_TIMEOUT_S = 120


async def run_worker(mesh: Path | None, settings: Path, output: Path,
                     step: Path | None = None, timeout: float = FEA_TIMEOUT_S):
    args = [sys.executable, '-m', 'aida.fea', '--settings', str(settings.resolve()),
            '--out', str(output.resolve())]
    if mesh is not None:
        args += ['--mesh', str(mesh.resolve())]
    if step is not None:
        args += ['--step', str(step.resolve())]
    env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        (output / 'solver.log').write_bytes(stdout + stderr)
        if process.returncode:
            lines = stderr.decode(errors='replace').strip().splitlines()
            raise ValueError(lines[-1][:500] if lines else 'The FEA worker failed to analyze this solid.')
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
