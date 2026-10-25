"""Install Aida's direct-editor scan nodes and its two workflows into ComfyUI."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-root', type=Path, required=True)
    args = parser.parse_args()
    host = args.comfy_root.expanduser().resolve()
    if not (host/'main.py').is_file():
        parser.error('--comfy-root must point to the ComfyUI directory containing main.py')
    source = Path(__file__).resolve().parents[1]/'backend/aida/scan'
    target = host/'custom_nodes/aida_scan'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            target.symlink_to(source/'comfy_nodes', target_is_directory=True)
        except OSError:
            shutil.copytree(source/'comfy_nodes',target,ignore=shutil.ignore_patterns('__pycache__'))
    elif target.resolve() != (source/'comfy_nodes').resolve():
        for name in ('__init__.py','board.py'):
            shutil.copy2(source/'comfy_nodes'/name,target/name)
    workflows = host/'user/default/workflows'
    workflows.mkdir(parents=True, exist_ok=True)
    for name in ('aida_fast.json','aida_full.json'):
        destination = workflows/name
        if destination.exists():
            archive = workflows/'archive/before_automatic_object_isolation'
            archive.mkdir(parents=True,exist_ok=True)
            if not (archive/name).exists():
                shutil.copy2(destination,archive/name)
        shutil.copy2(source/'assets/ui'/name,destination)
    print('Installed aida_scan, aida_fast.json and aida_full.json.')
    print('Restart ComfyUI. If custom nodes are disabled, add --whitelist-custom-nodes aida_scan.')
    print('Refresh the browser and reopen aida_fast.json; leave unused views set to (none).')


if __name__ == '__main__':
    main()
