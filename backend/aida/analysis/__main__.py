"""FEA worker process: ``python -m aida.analysis``. The API owns its timeout.

Progress goes to stdout as ``STAGE <name>`` lines so the caller can show it live.
``--faces`` lists the part's faces (faces.json, faces.png) instead of solving.
"""
import argparse
import json
from pathlib import Path

from . import analyze_model, require_step
from .settings import FeaSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--step', type=Path, required=True)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--faces', action='store_true')
    args = parser.parse_args()
    try:
        if args.faces:
            from .ngsolve_backend import list_faces

            result = list_faces(require_step(args.step), args.out)
            (args.out / 'faces.json').write_text(json.dumps(result, indent=1), encoding='utf-8')
            return
        settings = FeaSettings.model_validate_json(args.settings.read_text())
        result = analyze_model(args.step, settings, args.out,
                               on_stage=lambda stage: print(f'STAGE {stage}', flush=True))
        (args.out / 'report.json').write_text(json.dumps(result, allow_nan=False, indent=2), encoding='utf-8')
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
