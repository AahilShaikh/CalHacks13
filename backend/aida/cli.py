"""Terminal entry point."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from .artifacts import Artifacts
from .config import load_local_env
from .cad import open_cad, text_of


def parser():
    result = argparse.ArgumentParser(
        description='Aida: inspect STL meshes and build CAD with Claude',
        epilog="Scan pipeline stages live under 'aida scan' (see: aida scan --help).")
    result.add_argument('--mesh', type=Path, required=True, help='Input STL file')
    result.add_argument('--units', choices=['mm'], help='Explicit input units; otherwise unknown')
    result.add_argument('--artifacts', type=Path, default=Path('artifacts'), help='Session output parent')
    result.add_argument('--check', action='store_true', help='Check CAD and render the mesh without calling Claude')
    result.add_argument('--prompt', help='Run one request and exit (uses Claude API credits)')
    return result


async def run(args):
    artifacts = Artifacts(args.artifacts)
    print(f'Artifacts: {artifacts.root}', flush=True)
    mesh = artifacts.copy_mesh(args.mesh.resolve())
    artifacts.log('session', {'mesh': str(mesh), 'units': args.units})
    async with open_cad(artifacts) as cad:
        await cad.startup(mesh)
        if args.check:
            result = await cad.call('render_view', {'objects': 'reference_mesh', 'direction': 'iso'})
            print(text_of(result))
            if result.get('isError'):
                raise RuntimeError('Mesh rendering failed')
            print('CAD startup, STL import, and mesh rendering passed.')
        else:
            from .agent import chat
            await chat(cad, artifacts, args.units, args.prompt)


def main():
    load_local_env()
    if sys.argv[1:2] == ['scan']:
        from .scan.cli import main as scan_main
        raise SystemExit(scan_main(sys.argv[2:]))
    argparser = parser()
    args = argparser.parse_args()
    if not args.mesh.is_file() or args.mesh.suffix.lower() != '.stl':
        argparser.error('--mesh must name an existing .stl file')
    if not args.check and not os.getenv('ANTHROPIC_API_KEY'):
        argparser.error('Set ANTHROPIC_API_KEY in .env or your environment, or use --check for a local test')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\nStopped. Session artifacts have been preserved.', file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        def explain(error):
            if isinstance(error, BaseExceptionGroup):
                return '; '.join(explain(e) for e in error.exceptions)
            return str(error)
        print(f'Aida failed: {explain(exc)}', file=sys.stderr)
        raise SystemExit(1)
