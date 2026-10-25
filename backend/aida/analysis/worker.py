"""Isolated, bounded native solver execution, shared by HTTP, the agent and MCP."""
import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable

FEA_TIMEOUT_S = 120

StageCallback = Callable[[str], Awaitable[None] | None]


async def run_worker(step: Path, settings: Path | None, output: Path,
                     timeout: float = FEA_TIMEOUT_S, on_stage: StageCallback | None = None):
    """Run ``python -m aida.analysis`` and forward its ``STAGE`` lines to ``on_stage``.
    Without ``settings`` it lists the part's faces (faces.json) instead of solving."""
    args = [sys.executable, '-m', 'aida.analysis', '--step', str(step.resolve()), '--out', str(output.resolve())]
    args += ['--settings', str(settings.resolve())] if settings is not None else ['--faces']
    env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    log = bytearray()

    async def read_stdout():
        async for line in process.stdout:
            log.extend(line)
            text = line.decode(errors='replace').strip()
            if text.startswith('STAGE ') and on_stage is not None:
                result = on_stage(text.removeprefix('STAGE '))
                if inspect.isawaitable(result):
                    await result

    async def finish():
        _, stderr = await asyncio.gather(read_stdout(), process.stderr.read())
        await process.wait()
        return stderr

    try:
        stderr = await asyncio.wait_for(finish(), timeout=timeout)
        (output / 'solver.log').write_bytes(bytes(log) + stderr)
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
