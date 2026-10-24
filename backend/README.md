# Backend collaboration

The backend is a Python package named `aida`. Run commands from the repository
root using `uv`; `uv sync --locked` installs the shared environment.

## Partner's starting points

- `aida/turntable.py`: Arduino serial control and webcam capture.
  `capture_views(output_directory)` returns captured image paths.
- `aida/image_to_mesh.py`: ComfyUI/image-to-3D integration.
  `images_to_mesh(images, output)` writes an STL and returns its path.

These are deliberately unimplemented async stubs. Importing them performs no
hardware or network operations. Extend them and add pipeline-specific modules
as needed; the initial function signatures are a small integration boundary,
not a prescribed hardware protocol.

## Handoff to CAD

Save the reconstructed STL, then run:

```bash
uv run aida --mesh /absolute/path/reconstruction.stl
```

Pass `--units mm` only if the reconstruction is calibrated to millimeters.
Keep source photographs and pose/scale metadata alongside the mesh for later
integration. The current terminal agent does not call the capture stubs yet.

CAD/agent work lives in `agent.py`, `cad.py`, `artifacts.py`, `mesh_render.py`,
and `cli.py`. Keeping capture work in its two starter modules avoids conflicts.
Add dependencies with `uv add <package>` and include the updated lockfile.
