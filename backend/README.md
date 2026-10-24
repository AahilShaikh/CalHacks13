# Backend collaboration

The backend is a Python package named `aida`. Run commands from the repository
root using `uv`; `uv sync --locked` installs the shared environment.

## Partner's starting points

- `aida/turntable.py`: Arduino serial control and webcam capture.
  `capture_views(output_directory)` returns captured image paths. Still an
  unimplemented async stub; importing it performs no hardware operations.
- `aida/image_to_mesh.py`: implemented on the `aida.scan` pipeline.
  `images_to_mesh(images, output)` submits captures to the external
  ComfyUI/Pixal3D instance and writes an STL (unknown physical units).
- `aida/scan/`: the modular image-to-mesh + ChArUco metric calibration
  pipeline (`comfy`, `charuco`, `scaling`, `blueprints`, `boards`, `pipeline`),
  driven by the `aida scan` CLI and the `aida.api` HTTP service.

## Handoff to CAD

Save the reconstructed STL, then run:

```powershell
uv run aida --mesh "C:\path\to\reconstruction.stl"
```

Pass `--units mm` only if the reconstruction is calibrated to millimeters.
Keep source photographs and pose/scale metadata alongside the mesh for later
integration. For metric output, use `aida scan run` (or the HTTP API) with a
caliper anchor; it writes `metric_model.stl` plus blueprints and
dimensions.json for the agent. The terminal agent does not call the capture
stub automatically yet.

CAD/agent work lives in `agent.py`, `cad.py`, `artifacts.py`, `mesh_render.py`,
and `cli.py`. Keeping capture work in its two starter modules avoids conflicts.
Add dependencies with `uv add <package>` and include the updated lockfile.
