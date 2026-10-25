# Local setup: ComfyUI + Pixal3D → Aida scan pipeline

This guide sets up the image-to-mesh half of Aida on one machine, from a bare
checkout of ComfyUI to a 1:1 metric mesh with CAD handoff artifacts, and explains
what each stage actually does.

ComfyUI is **not** vendored into this repo. It is a separate installation with
its own Python environment and multi-gigabyte model weights; Aida talks to it
over HTTP (`COMFY_HOST`). For the pipeline to be usable, ComfyUI must be running
before you submit a scan.

```
 captured photos (1-4)          ComfyUI + Pixal3D               Aida (this repo)
┌────────────────────┐         ┌──────────────────┐        ┌───────────────────────┐
│ front / left /     │  HTTP   │ LoadImage 364    │  HTTP  │ scan.pipeline.run_scan│
│ back / right JPG   │────────▶│ RemoveBackground │───────▶│ 1. ChArUco calibration│
│ + ChArUco board    │         │ Pixal3D cond.    │  GLB   │ 2. pixel→mesh (Comfy) │
└────────────────────┘         │ MeshToFile 285   │        │ 3. metric scaling     │
                               └──────────────────┘        │ 4. blueprints + JSON  │
                                                           └───────────┬───────────┘
                                                                       ▼
                                                     aida --mesh metric_model.stl
                                                     (Claude + build123d-mcp)
```

## Tested environment

The versions below are what the current installation runs; treat them as the
reference, not a hard minimum.

| Component | Version / detail |
|---|---|
| Machine | Apple Silicon (arm64), 32 GB unified memory |
| OS | macOS 26.7.1 |
| ComfyUI | 0.42.0 (`a50102b5`, 21 commits past the tag) |
| ComfyUI venv | Python 3.11.17 |
| PyTorch | 2.14.1, MPS backend available |
| Aida environment | Python 3.12 via `uv` (separate from ComfyUI's venv) |

Apple Silicon is required for this guide's performance notes: Pixal3D runs on
the MPS/Metal backend, and the memory ceiling advice below assumes unified
memory rather than a discrete GPU.

## Part 1 — ComfyUI with Pixal3D

### 1.1 Install ComfyUI

```bash
cd ~                                      # pick a parent directory; it stays outside this repo
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Python 3.11 is what the working install uses; ComfyUI itself also supports 3.12,
but do not reuse Aida's `.venv` — the two projects have independent dependency
sets and must stay separate.

Pixal3D generation ships with ComfyUI core. Aida additionally installs its small
`aida_scan` extension for automatic object-only preprocessing and optional inputs.
The generation nodes are:

| Node | Source |
|---|---|
| `Pixal3DMultiViewConditioning` | `comfy_extras/nodes_trellis2.py` |
| `MeshToFile3D`, `Save3DAdvanced` | `comfy_extras/nodes_mesh_io.py` |
| `MeshSmoothNormals` | `comfy_extras/nodes_mesh_postprocess.py` |

Verify after install:

```bash
python -c "import torch; print(torch.__version__, torch.backends.mps.is_available())"
# expected: 2.14.1 True
```

### 1.2 Place the models

The workflow references these filenames exactly. Put them at these paths —
directory names must match, or ComfyUI's loaders will fail to find them.

| Path | Size (installed) |
|---|---|
| `models/diffusion_models/pixal3d_multiview_int8_convrot.safetensors` | 5.2 GB |
| `models/clip_vision/dino_v3_L_naf_fp32.safetensors` | 1.1 GB |
| `models/vae/trellis_2_shape_vae_bf16.safetensors` | 1.0 GB |
| `models/vae/trellis_2_texture_vae_bf16.safetensors` | 905 MB |
| `models/background_removal/birefnet.safetensors` | 424 MB |

Roughly 8.6 GB total. The INT8 "convrot" diffusion checkpoint is deliberate: it
halves memory use and is substantially faster than the BF16 checkpoint on this
class of machine. The texture VAE is only needed by the full PBR (slowest)
workflow variant; the fast mesh variants use the shape VAE.

### 1.3 Start ComfyUI

Use a startup script so the Metal optimizations are always applied:

```bash
#!/bin/bash
# ComfyUI/start_comfyui.sh
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
source .venv/bin/activate

export PYTORCH_ENABLE_MPS_FALLBACK=1      # run unported ops on CPU instead of crashing
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0  # let PyTorch use the full unified-memory ceiling

python main.py --preview-method auto "$@"
```

```bash
chmod +x start_comfyui.sh
./start_comfyui.sh
```

Why the two variables matter:

- `PYTORCH_ENABLE_MPS_FALLBACK=1` — some ops in this pipeline have no Metal
  kernel yet. Without the fallback, generation aborts; with it, those ops run on
  CPU and are slower but correct.
- `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0` — raises the allocator ceiling so the
  model fits in unified memory. Leave it unset if you share the machine with
  memory-hungry applications and would rather fail than swap.

The server is ready at **http://127.0.0.1:8188**. Confirm from another terminal:

```bash
curl -s http://127.0.0.1:8188/system_stats | head -c 200
```

Stopping it is just Ctrl-C in that terminal. Aida keeps working (jobs fail with
a clear "Cannot reach ComfyUI" error) while ComfyUI is down.

### 1.4 Open the Pixal3D workflow

In the ComfyUI web UI, either:

- **Templates → 3D Model → "Pixal3D: Multi Views to 3D"**, or
Aida ships exactly two API-format presets in `backend/aida/scan/assets/`:

| Preset | Workflow asset | Trade-off |
|---|---|---|
| `fast` | `pixal3d_fast_api.json` | 12 structure + 14 shape + 8 refinement steps, 1024 geometry, no textures |
| `full` (default) | `pixal3d_full_api.json` | 12 structure + 20 shape + 12 refinement + 12 texture steps; 1024 geometry; 2048 PBR, normal and AO maps |

Both consume up to four real views and use the same calibrated metric-fit gates.
The full preset keeps a 200,000-face mesh with UVs and embedded textures in GLB;
STL carries geometry only. These are generative reconstructions, so higher
sampling/detail does not guarantee physical accuracy.

Select with `aida scan run --workflow fast` (or `full`), the setup page's
**Mesh quality** selector, multipart `workflow` on `/api/v1/scans`, or JSON
`"workflow":"fast"` on `/api/v1/acquisitions`. Custom API graphs use
`--workflow-path` or `AIDA_PIXAL3D_WORKFLOW`.

Raw ChArUco photos use the built-in SAM 3.1 segmenter with object boxes
automatically located from the print. No matching background reference is needed.
Install `sam3.1_multiplex_fp16.safetensors` from
[Comfy-Org](https://huggingface.co/Comfy-Org/sam3.1) in `models/checkpoints/`.
Install `aida_scan` with `uv run python examples/install_comfy_scan.py --comfy-root "PATH_TO_COMFYUI"`.
Restart ComfyUI with `--whitelist-custom-nodes aida_scan` if custom nodes are disabled.
Refresh and reopen `aida_fast.json`. Upload FRONT and leave unused side views at `(none)`;
both presets support 1–4 images and automatically remove the board without manual boxes.
BiRefNet remains the fallback for ordinary photos without a detected board.

### 1.5 Smoke-test in the UI

1. Select the `LoadImage` node (id `364`, the front view) and upload a photo.
2. Leave the other `LoadImage` nodes empty for a front-only test.
3. Click **Queue**.
4. Watch the terminal for stage progress; when it finishes, the GLB is written to
   `ComfyUI/output/3d/ComfyUI_0000N.glb` and shown in the 3D preview node.

Photographing for best results (pixel-aligned back-projection needs consistency):

- Square framing, 1024×1024 recommended; the object should fill roughly 90% of
  the frame in every view.
- Transparent PNG or clean background. The workflow runs BiRefNet background
  removal, but a clean source still helps.
- Front / left (90°) / back (180°) / right (270°) relative to the turntable's
  rotation direction. Fewer views work; each one you add must be a true orbit
  view of the same object at the same distance.

## Part 2 — Aida side

### 2.1 Install

```bash
cd <this repo>
uv sync --locked
```

The scan dependencies (OpenCV, trimesh, matplotlib, reportlab, FastAPI) are part
of the main environment, so no extra is needed.

### 2.2 Print the calibration board

```bash
uv run aida scan board --kind turntable --format letter --out-dir boards
```

Print the PDF at **100% / "Actual Size"** — never "Fit to Page" — then measure
the 100 mm calibration ruler with calipers. If it is not exactly 100.0 mm, the
print scale is wrong and every dimension downstream will be wrong with it. Cut
along the dashed 8-inch circle and tape it flat to the turntable; the object
sits on top, and enough board corners must stay visible around the base.

The rectangular board alternatives (`--kind rectangular`) use a 10×13 grid
(US Letter) or 10×14 grid (A4).

### 2.3 Run a scan

Command line:

```bash
uv run aida scan run \
  --front front.jpg \
  --left left.jpg --back back.jpg --right right.jpg \
  --height-mm 75 \
  --out artifacts/scan1
```

`--left/--back/--right` are optional. Everything after `--out` is written into
that directory; the command prints the stage it is on and ends with the bounding
box.

HTTP API (the same pipeline, for other tools):

```bash
uv run aida-api                      # http://127.0.0.1:8000, docs at /docs
curl -s http://127.0.0.1:8000/health
```

```bash
# submit -> 202 with a job id
curl -s -X POST http://127.0.0.1:8000/api/v1/scans \
  -F "front_image=@front.jpg" -F "left_image=@left.jpg" \
  -F "known_height_mm=75"

# poll until status is succeeded/failed
curl -s http://127.0.0.1:8000/api/v1/scans/<job_id>

# download artifacts
curl -sO http://127.0.0.1:8000/api/v1/scans/<job_id>/files/metric_model.stl
```

Jobs run one at a time in the background (generation is GPU-bound), so a
long-running job never blocks the API. Artifacts are stored under
`artifacts/scans/<job_id>/`; set `AIDA_SCAN_STORAGE` to relocate them.

### 2.4 What you get

| Artifact | Purpose |
|---|---|
| `metric_model.glb` | 1:1 mesh, millimeters, base on Z=0 |
| `metric_model.stl` | same geometry, for `aida --mesh` |
| `dimensions.json` | bounding box, coordinate frame, volume, ChArUco stats |
| `blueprints.png` | top/front/right/isometric views with mm rulers and callouts |
| `agent_prompt.txt` | ready-to-paste prompt for the build123d CAD agent |
| `front_raw.png` (and other `*_raw.png`) | the input photos as ComfyUI received them |

Hand the result to the CAD agent:

```bash
uv run aida --mesh artifacts/scan1/metric_model.stl --units mm
```

## How it works

### Stage 1 — ChArUco calibration (photo → metric ground plane)

`backend/aida/scan/charuco.py` detects the board's ArUco markers and ChArUco
corners in the front photo, then solves the camera pose (`solvePnP`) relative to
the board plane, which is defined as Z=0. Because every square is a known
20.0 mm, adjacent detected corners also give the ground-plane resolution in
pixels per millimeter.

This anchors the *photo* to real-world scale and tells you how far the camera is
from the turntable. It is recorded in `dimensions.json` under
`charuco_calibration` (`px_per_mm_ground`, `camera_distance_mm`,
`num_corners_detected`). Detection failure is not fatal — the scan continues but
the metadata records the error, and you lose the sanity check on capture quality.

### Stage 2 — Pixal3D (photos → mesh)

Aida uploads each photo to ComfyUI (`/upload/image`), wires the uploaded names
into the workflow's `LoadImage` nodes, queues the prompt (`/prompt`), polls
`/history/<id>`, and fetches the resulting GLB from `/view`. Output files are
pulled over HTTP rather than read from ComfyUI's output directory, so ComfyUI may
run on another machine. The GLB is unitless — Pixal3D normalizes mesh scale.

### Stage 3 — Metric scaling (unitless mesh → millimeters)

`backend/aida/scan/scaling.py` applies one uniform multiplier chosen by the
first available anchor:

| Anchor | Multiplier |
|---|---|
| `--scale-factor` | used as given |
| `--height-mm` | target height ÷ mesh Z extent |
| `--base-dia-mm` | target diameter ÷ mean of X and Y extents |

An anchor is **required**. ChArUco tells you how many pixels the board spans,
but nothing yet maps a mesh vertex to a photo pixel (that would need object
footprint segmentation), so the pipeline cannot invent a mesh scale. The earlier
prototype silently multiplied by 50 in that case, which produced confidently
wrong dimensions; this version fails fast instead. Measure the object's height or
base diameter with calipers — or provide an explicit factor if you calibrated
against a known reference.

After scaling, the mesh is re-centered: footprint centered on X/Y, bottom at
Z=0, so CAD sketches start on the ground plane.

### Stage 4 — CAD handoff

`blueprints.py` renders top, front, right and isometric views with dimension
arrows and green Z=0 ground lines. Together with `dimensions.json` and
`agent_prompt.txt`, that is what the Claude + build123d-mcp agent is meant to
read before modeling. The agent's own exports live in a separate session
(`uv run aida ...`), so scan artifacts stay untouched.

## Multiple input views

The API and CLI accept one to four images in fixed orbit order: front (0°),
left (90°), back (180°), right (270°). A front image is required.

The bundled API workflow starts with the front path. `wire_workflow` adds a
separate LoadImage, background-removal and crop path for each supplied optional
view, connecting it to Pixal3DMultiViewConditioning. Missing views are omitted.
Supplying an image that cannot reach the mesh output now fails explicitly,
rather than uploading it and silently ignoring it.

Custom API-format exports can still be selected with `AIDA_PIXAL3D_WORKFLOW`.
The default node mappings are front `364`, left `377`, back `380`, right `383`,
and mesh output `285`; programmatic callers can override these in `ComfyConfig`.
The automatic preparation expansion recognizes the bundled front path. Other
custom exports must already connect their supplied image nodes to the output.

## Automatic calibrated capture and metric scaling

For USB-webcam capture without an object dimension/caliper anchor, use
`/setup.html` and `POST /api/v1/acquisitions`. See
[automatic scanner setup](AUTOMATIC_SCAN.md).

This workflow calibrates the actual webcam,
captures all four cardinal views without a separate agent, isolates the
object, and fits the generated mesh's uniform scale and pose against calibrated
outer silhouettes. It records fit errors, estimated scale uncertainty and the
applied transforms. The CAD agent receives an already metric mesh and is told
not to apply the scale again. Poor or ambiguous fits fail instead of claiming
an unverified millimeter scale.

The uploaded-photo `/api/v1/scans` and `aida scan run` interfaces above retain
their explicit-anchor contract because arbitrary photos lack the measured
camera poses required by automatic registration.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Cannot reach ComfyUI at http://127.0.0.1:8188` | ComfyUI is not running or is on another port. Start it, or set `COMFY_HOST=host:port`. |
| Job sits in `generating_mesh` then fails after 300 s | Generation exceeded the client timeout. Use a faster workflow variant, or raise `ComfyConfig.timeout` for programmatic use. |
| `No scale anchor provided` | Pass `--height-mm`, `--base-dia-mm`, or `--scale-factor`. The job fails before generating on purpose. |
| `Only N ChArUco corners detected` | Board not framed, too small, blurred, or wrong board type. Inspect with `uv run aida scan inspect --image photo.jpg --board turntable --out annotated.jpg` and look at the annotated output. |
| `Workflow template not found` | Bad `--workflow-path` / `AIDA_PIXAL3D_WORKFLOW` path. |
| ComfyUI reports missing models | Filenames and subdirectories must match §1.2 exactly. |
| Generation is very slow or the process is killed | Expect first-run shader compilation to be slower. Close other memory-heavy apps, use the INT8 checkpoint and a faster variant, and keep the watermark variable from §1.3. |
| `Address already in use` | ComfyUI: pass `--port`. Aida: `AIDA_API_PORT=8001 uv run aida-api`. |
| Dimensions look implausible | Measured anchor is wrong, board printed at the wrong scale, or the photo's object base is not on the board plane. Re-verify the 100 mm ruler and the caliper reading. |

## File map

| Path | Role |
|---|---|
| `backend/aida/scan/comfy.py` | ComfyUI client: upload, queue, poll, download |
| `backend/aida/scan/charuco.py` | board detection, pose, photo inspection |
| `backend/aida/scan/scaling.py` | metric scaling, metadata, STL export |
| `backend/aida/scan/blueprints.py` | orthographic blueprint rendering |
| `backend/aida/scan/boards.py` | printable calibration PDFs |
| `backend/aida/scan/pipeline.py` | stage orchestration shared by CLI and API |
| `backend/aida/scan/cli.py` | `aida scan run/scale/blueprint/board/inspect` |
| `backend/aida/api/` | job queue and HTTP endpoints |
| `backend/aida/scan/assets/pixal3d_{fast,full}_api.json` | two API-format Pixal3D presets |
