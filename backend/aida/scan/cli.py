"""``aida scan`` command line: run the pipeline and its individual stages.

Subcommands mirror the standalone tools from the original ``cad_calibration``
project: ``run`` (full pipeline), ``scale``, ``blueprint``, ``board``, and
``inspect``.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import blueprints, boards, charuco, pipeline, scaling


def _anchor_args(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--height-mm', type=float, dest='height_mm',
                       help='Anchor total height in millimeters (caliper measurement)')
    group.add_argument('--base-dia-mm', type=float, dest='base_dia_mm',
                       help='Anchor base diameter in millimeters')
    group.add_argument('--scale-factor', type=float, dest='scale_factor',
                       help='Explicit uniform scale multiplier')


def parser():
    result = argparse.ArgumentParser(prog='aida scan',
                                    description='Image-to-mesh scan pipeline tools')
    sub = result.add_subparsers(dest='command', required=True)

    run = sub.add_parser('run', help='Run the full scan pipeline on captured photos')
    run.add_argument('--front', type=Path, required=True, help='Front photo (ChArUco board visible)')
    run.add_argument('--left', type=Path, help='Optional left (90 deg) photo')
    run.add_argument('--back', type=Path, help='Optional back (180 deg) photo')
    run.add_argument('--right', type=Path, help='Optional right (270 deg) photo')
    run.add_argument('--out', type=Path, required=True, help='Output directory for job artifacts')
    run.add_argument('--comfy-host', default=os.environ.get('COMFY_HOST', '127.0.0.1:8188'),
                     help='ComfyUI host (default: $COMFY_HOST or 127.0.0.1:8188)')
    run.add_argument('--workflow', type=Path,
                     default=(Path(os.environ['AIDA_PIXAL3D_WORKFLOW'])
                              if os.environ.get('AIDA_PIXAL3D_WORKFLOW') else None),
                     help='API-format workflow JSON (default: bundled Pixal3D template)')
    _anchor_args(run)

    scale = sub.add_parser('scale', help='Scale a unitless Pixal3D mesh to metric millimeters')
    scale.add_argument('--mesh', type=Path, required=True, help='Unscaled .glb/.stl mesh')
    scale.add_argument('--out-mesh', type=Path, required=True, help='Output scaled mesh path')
    scale.add_argument('--out-json', type=Path, help='Output CAD metadata JSON path')
    _anchor_args(scale)

    blueprint = sub.add_parser('blueprint', help='Render orthographic CAD blueprints')
    blueprint.add_argument('--mesh', type=Path, required=True, help='Metric mesh (.glb/.stl)')
    blueprint.add_argument('--out', type=Path, required=True, help='Output PNG path')

    board = sub.add_parser('board', help='Generate printable 1:1 ChArUco calibration PDFs')
    board.add_argument('--kind', choices=['turntable', 'rectangular', 'both'], default='both')
    board.add_argument('--format', choices=['letter', 'a4', 'both'], default='both',
                       dest='paper_format', help='Paper format')
    board.add_argument('--out-dir', type=Path, default=Path('.'), help='Output directory')

    inspect = sub.add_parser('inspect', help='Annotate a photo with ChArUco detection debug info')
    inspect.add_argument('--image', type=Path, required=True, help='Photo to inspect')
    inspect.add_argument('--board', choices=sorted(charuco.BOARD_PRESETS), default='turntable',
                         help='Board preset')
    inspect.add_argument('--squares-x', type=int, help='Override grid columns')
    inspect.add_argument('--squares-y', type=int, help='Override grid rows')
    inspect.add_argument('--out', type=Path, default=Path('annotated_charuco.jpg'),
                         help='Output annotated image path')

    return result


def main(argv=None):
    args = parser().parse_args(argv)

    if args.command == 'run':
        images = {'front': args.front.read_bytes()}
        for view in ('left', 'back', 'right'):
            path = getattr(args, view)
            if path:
                images[view] = path.read_bytes()
        from .comfy import ComfyClient, ComfyConfig
        config = ComfyConfig(host=args.comfy_host)
        if args.workflow:
            config.workflow_path = args.workflow
        client = ComfyClient(config)
        result = pipeline.run_scan(
            images, args.out,
            known_height_mm=args.height_mm,
            known_base_dia_mm=args.base_dia_mm,
            scale_factor=args.scale_factor,
            client=client,
            progress=lambda stage: print(f'[{stage}]', flush=True))
        box = result.cad_specs['bounding_box_mm']
        print(f"Metric mesh: {result.metric_mesh}")
        print(f"STL copy:    {result.metric_stl}")
        print(f"Blueprints:  {result.blueprint}")
        print(f"Dimensions:  {result.dimensions_json}")
        print(f"Bounding box: {box['width_x']} x {box['depth_y']} x {box['height_z']} mm")

    elif args.command == 'scale':
        metadata = scaling.scale_mesh(
            args.mesh, args.out_mesh, args.out_json,
            target_height_mm=args.height_mm,
            target_base_dia_mm=args.base_dia_mm,
            scale_factor=args.scale_factor)
        print(json.dumps(metadata, indent=2))

    elif args.command == 'blueprint':
        blueprints.render_mesh_projections(args.mesh, args.out)
        print(f'Blueprint: {args.out}')

    elif args.command == 'board':
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for path in boards.build_pdfs(args.out_dir, args.kind, args.paper_format):
            print(f'Created: {path}')

    elif args.command == 'inspect':
        report = charuco.inspect_image(args.image, args.out, args.board,
                                       args.squares_x, args.squares_y)
        print(json.dumps(report, indent=2, default=str))

    return 0


if __name__ == '__main__':
    sys.exit(main())
