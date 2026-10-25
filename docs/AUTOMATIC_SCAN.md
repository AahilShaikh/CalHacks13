# USB webcam → automatic metric mesh → CAD

Open **http://127.0.0.1:8000/setup.html** after starting `uv run aida-api`.
If another Aida server is already using that port, the updated server can run
with `AIDA_API_PORT=8001 uv run aida-api`; then use port 8001 for all URLs below.
Use one active controller for the turntable.

The scanner uses a **USB webcam**, requested at **1280 × 720**, through macOS
AVFoundation. Preview, calibration, turntable feedback and object capture all
share one selected USB device. Built-in and Continuity cameras are rejected;
missing USB hardware never silently falls back to the laptop camera.

On `/setup.html`, click **Refresh camera devices**, choose the USB webcam by
name, then **Use selected USB camera**. Its stable device ID is saved in
`artifacts/turntable/hardware.json`, so camera index changes after reconnecting
do not redirect capture to another camera. With one USB webcam connected it is
automatically selected; save it explicitly to preserve that identity.
Allow camera access when macOS requests it. Loading the setup page lists devices
but does not capture a photo or move the motor. macOS discovery uses a small
local AVFoundation helper compiled with Xcode Command Line Tools.

## Configure

```dotenv
ANTHROPIC_API_KEY=your_key
AIDA_TURNTABLE_PORT=/dev/cu.usbmodem101
AIDA_TURNTABLE_NEUTRAL_US=1450
COMFY_HOST=http://127.0.0.1:8188
```

You can select and save the detected USB port directly on `/setup.html`, without
editing `.env` or restarting the server. It persists in `artifacts/turntable/hardware.json`.
An explicit `AIDA_TURNTABLE_PORT` overrides the saved selection. Alternatively,
use the actual port from `uv run python -m serial.tools.list_ports`.
Camera settings are also saved by the page. Optional `AIDA_TURNTABLE_CAMERA_ID`
or `AIDA_TURNTABLE_CAMERA` environment overrides take precedence; remove an old
built-in-camera index override before using the USB selector. Index numbers are
not stable and must not be assumed to be 0 or 1.
The existing Claude authentication environment also works where supported by
the Agent SDK. Only the main Aida conversation incurs Claude API costs.
The acquisition workflow itself runs fixed captures, local ComfyUI and metric
registration without another model call.

Firmware is `firmware/turntable/turntable.ino`, Mega pin 9, startup neutral
1450 µs. Upload it, confirm stopped neutral, and close Serial Monitor before API
capture. See [turntable wiring and tuning](TURNTABLE.md).

ComfyUI is installed separately at
`/Users/linweizhi/Documents/CalHacks Project/ComfyUI`:

```bash
cd "/Users/linweizhi/Documents/CalHacks Project/ComfyUI"
./start_comfyui.sh --listen 127.0.0.1 --port 8188 --disable-all-custom-nodes --whitelist-custom-nodes aida_scan
```

All required model weights are already installed on this machine. Aida uses
ComfyUI core generation/SAM nodes plus the bundled `aida_scan` extension for
automatic object isolation and optional image inputs. Both servers remain local.

## Exact first-run steps

1. Plug the USB webcam and Mega into the Mac. Keep the servo's external 5 V
   supply and shared-ground wiring as described in the turntable guide. Upload
   the sketch with neutral **1450 µs**, then close Arduino Serial Monitor.
2. Print the supplied circular ChArUco PDF at **100% / Actual Size**. Do not use
   Fit to Page. The disc is 8 inches (203.2 mm); squares must remain 20 mm and
   markers 15 mm, dictionary `DICT_5X5_100`. Check the printed ruler or a square
   with a ruler; this checks the reference print, not an object measurement.
3. Keep the paper flat on a rigid backing. Use thin double-sided tape on its
   back or small perimeter tabs. Avoid glossy tape across codes or checkerboard
   corners, bubbles, folds, stretching, and raised foam tape. Center the board
   over the turntable rotation axis. If already fixed to the disc, the whole
   disc must be held flat and tilted for calibration with the servo unpowered.
4. Mount the USB camera so it sees the whole disc and the object from an oblique
   angle above the table. Use even, diffuse lighting and a static background.
   Set focus at the working distance; keep focus, zoom and resolution fixed.
5. Open `/setup.html`. Click **Refresh camera devices**, select the USB webcam
   by name, and click **Use selected USB camera**. Click **Refresh camera preview**
   and verify it shows the USB camera's view of the table.
6. Click **Reset samples** if any samples came from the built-in camera or a
   different setup. Hold the flat board in front of the USB camera near the
   working distance. Take **12–20 samples**, tilting left/right and up/down and
   varying position across the image. Pause so each image is sharp. Keep the
   camera fixed. Flat table rotation alone is insufficient. Click **Solve calibration**.
7. Mount the board flat and centered on the turntable again, reconnect servo
   power if removed, and leave the table **empty**. Click **Refresh USB ports**,
   choose the Mega's port, then **Save Arduino port**.
8. Restart the server after mounting the board. **Startup zero** is established
   automatically from its current orientation, using STOP at 1450 µs and stationary
   camera checks; the server does not drive to a home position. Check that startup
   zero is `ready`. If the server started before the hardware was ready, click
   **Retry startup zero** after correcting the reported problem.
9. Optionally capture an empty-table reference for diagnostics. Object-only
   SAM segmentation does not require it; keep camera calibration and board mounting fixed.
10. Place a small opaque, matte object at the center. Leave at least **eight
   visible ChArUco corners spanning 60 mm in both board directions** around it
   in every view. Keep hands out of the scene during capture.
11. Open an Aida workspace in millimeters. Ask Aida to scan the object, or paste
    the workspace ID from `/w/<id>` into the setup page and click **Start scan**
    while Aida is idle. The scanner always captures all four cardinal views.
12. Confirm the job reaches `mesh_attached`. Inspect the saved photos, masks,
    dimensions and `metric_model.stl`, then ask Aida to build editable CAD.
    Additional details can be photographed with Aida's `capture_angle(angle)` tool.
    Only the main Aida conversation needs Claude authentication; scanning itself
    no longer starts a capture agent or another CAD conversation.

### Clear tape on the board

Clear tape can hold the cutout, but gloss can obscure codes/corners with glare,
especially while the board is tilted or rotating. Prefer attaching it from the
back or using small tabs in unprinted margins. Do not coat the printed face.
The board must remain flat and rigidly attached without slipping. Tape placed
under the paper should not create local bumps. After mounting, check preview
at several table orientations for bright reflections. Once lighting and mounting
are settled, capture a new empty-table reference. Flatness, accurate dimensions
and diffuse lighting are also emphasized in
[Calib.io's calibration guidance](https://calib.io/blogs/knowledge-base/calibration-best-practices).

Calibration is saved under `artifacts/turntable/camera.json`; its error report
and setup photographs are alongside it. Calibration images are separate from
the four-image limit for each object. No object dimension/caliper anchor is
required by the automatic workflow.

Calibration from the built-in camera cannot be reused for the USB camera.
Click **Reset samples** and collect a new USB-camera calibration. Recalibrate
after changing camera, resolution or lens settings. Startup observes a fresh
zero on every server launch. Capture a new empty reference after moving the USB
camera, changing lighting or the background, or remounting the board. A reference
can survive a zero reset/server restart when the physical scene and calibration
are unchanged; it is bound to camera calibration, not the logical angle origin. A calibration change invalidates the
old zero reference. Do not reuse a zero reference from another camera.

## Automatic workflow

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/acquisitions \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"YOUR_OPEN_WORKSPACE_ID","workflow":"full"}'
```

The response is 202 with a `job_id` and `status_url`. Individual moves remain
synchronous internally; the complete scan runs in the background because mesh
inference may take minutes.

Aida's main CAD agent exposes two tools:

- `scan_object()` captures front, left, back and right, reconstructs with Pixal3D,
  and fits metric scale using the calibrated board poses. It adds new reference
  parts to the current workspace and returns dimensions, fit evidence, artifact
  URLs and the blueprint image. Existing parts are preserved.
- `capture_angle(angle)` rotates to an absolute angle modulo 360, verifies the
  position and returns the actual photograph as image content. The photo is saved
  in the workspace's References. It does not regenerate the mesh.

There is no separate capture agent. The camera and serial port remain reserved
through the four captures and are released before GPU inference. Hardware failures
stop the sequence; there are no automatic retries of an entire failed scan.
Shutdown attempts a confirmed STOP even on failure; firmware locally bounds every burst.

Open a workspace first. The HTTP acquisition endpoint accepts its `session_id`
while it is idle; when Aida is working, ask it to scan through its own tool instead.
Calibration, ready zero, and ComfyUI must be available. A workspace with existing
parts must already use millimeters. Scans do not automatically build parametric CAD
or create commits; Aida continues modeling and uses `commit_changes` explicitly.

For ChArUco photos, Aida detects the printed corners and projects the known
board pattern into the photo. Pattern disagreement locates a loose object box;
ComfyUI's built-in **SAM 3.1** uses that box to segment the object on the original
photo. This replaces BiRefNet for board photos, because BiRefNet treats the board
and object together as foreground. The final SAM silhouette controls the black
background composite sent to Pixal3D and is returned at original resolution for
metric fitting. All views use one common crop size; calibrated captures also
use the crop field of view derived from measured camera intrinsics, preserving
the relative size of the front and side views. `*_conditioned.png` records
the exact model input and `framing.json` records crop/FOV evidence.
The surrounding background may change; **no empty-table photo
is required**. An optional empty reference is retained for diagnostics only.

Inspect `object_prompts.json`, `*_foreground.png`, and `*_object.png` if separation
fails. Final masks come from SAM, not from the approximate pattern-difference
region. Photos without detected ChArUco use BiRefNet as before. The automatic
locator assumes one substantial object on the print; very large, camouflaged,
transparent or poorly lit subjects may fail object localization and need improved lighting or framing.

Choose `workflow: "full"` (default) for 1024 geometry refinement plus 2048 PBR
color, metallic, roughness, normal and ambient-occlusion maps embedded in GLB.
Choose `workflow: "fast"` for 1024 refined geometry with fewer sampling steps and
no texture stage. Both use all supplied views and the same metric quality gates.
The setup page exposes both choices. CLI: `--workflow fast|full`; uploaded scans:
multipart `workflow=fast|full`. STL cannot carry textures.

In addition to Pixal3D and its VAEs/DINO weights, place the official
[Comfy-Org SAM 3.1 checkpoint](https://huggingface.co/Comfy-Org/sam3.1)
`sam3.1_multiplex_fp16.safetensors` in `ComfyUI/models/checkpoints/` (about 1.6 GiB).
The editor workflows use the repository's `aida_scan` extension to run automatic
ChArUco object localization, SAM segmentation, and shared framing inside ComfyUI.
This is required for the direct editor path as well as the API templates.
Install it once:

```bash
uv run python examples/install_comfy_scan.py --comfy-root "$HOME/Documents/CalHacks Project/ComfyUI"
```

Restart ComfyUI; if using `--disable-all-custom-nodes`, also pass
`--whitelist-custom-nodes aida_scan`. Refresh the browser and **reopen**
`aida_fast.json`: an already-open BiRefNet graph does not update when its file changes.

Both `aida_fast.json` and `aida_full.json` accept **1, 2, 3 or 4** photos. Upload
FRONT, then select any additional LEFT/BACK/RIGHT photos. Leave unused side
inputs at **(none)**. No dummy images, manual disconnections or object boxes are
needed. Every selected view passes through **Aida: Object Only** before Pixal3D.
The preview shows exactly the black-background object images used for generation.
A board-sized mask is rejected rather than generating a turntable.

Metric registration fits a single uniform scale, orientation and ground-plane
position against **calibrated multi-view convex outer silhouettes**. It uses
actual webcam intrinsics, distortion coefficients and each photo's metric board
pose. The default quality gates require support RMS below 2.5% of each image
silhouette diagonal, no materially different near-equivalent scale solutions,
and estimated relative scale standard deviation below 3.5%. A front/back pair
alone is rejected; at least one side baseline is required.

These are model-fit checks, not proof of physical accuracy. Printed board size,
camera calibration error, mask/shadow errors and generated shape distortion can
all produce systematic errors beyond the reported fit uncertainty. Concavities,
hidden surfaces and internal details are still generative. Difficult reflective,
transparent or poorly separated objects may fail. A failed fit does not hand a
guessed metric mesh to CAD.

## Results and CAD handoff

Poll `GET /api/v1/acquisitions/{job_id}`. It returns status, stage, error, file
URLs, dimensions, created part names, the existing `session_id`, and `viewer_url`.

Files under `artifacts/cad/{session_id}/scans/{job_id}/` include:

- `front.png`, `left.png`, `back.png`, `right.png`, with `*_pose.json` angle/board evidence;
- `empty_table.png`, `empty_table.json`, `camera.json`;
- `object_prompts.json`, `*_foreground.png`, `*_object.png`, and ComfyUI `*_mask.png`;
- `motion.jsonl`, containing each verified angle, drive pulse, retry and result;
- `unscaled_model.glb`, `metric_model.glb`, `metric_model.stl`;
- `dimensions.json`, `blueprints.png`, `agent_prompt.txt`, `workflow.json`.

`dimensions.json` records the applied millimeters-per-raw-unit multiplier,
raw-to-board and raw-to-CAD transforms, per-view fit errors and uncertainty.
The metric mesh is centered at its base footprint with Z=0 at the ground.

The current workspace receives the **already-scaled mesh with `units=mm`**.
Original photos, blueprints, calibration, and fit evidence remain in its artifact
directory. `succeeded`/`mesh_attached` means the scan was added successfully, not
that editable CAD is finished. The main Aida tool returns part names and explicitly
warns against applying the scale multiplier again.

`POST /api/v1/acquisitions/{job_id}/cancel` requests cancellation after the
current operation. It prevents subsequent motion and workspace attachment; it is not an
emergency stop and does not globally interrupt somebody else's ComfyUI queue.
An in-flight inference can continue until it completes or times out.

## API setup calls

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/api/v1/camera/devices`, `/api/v1/camera/selection` | List USB cameras / save stable selected device |
| GET | `/api/v1/camera/frame.jpg` | Fresh camera preview, no motor access |
| GET | `/api/v1/camera/calibration` | Camera index, sample count, measured calibration |
| POST | `/api/v1/camera/calibration/samples` | Capture one varied board pose |
| POST | `/api/v1/camera/calibration/solve` | Solve camera intrinsics from at least 10 samples |
| DELETE | `/api/v1/camera/calibration/samples` | Clear setup samples, retain solved calibration |
| GET | `/api/v1/turntable/status` | Startup zero status and empty-reference readiness |
| POST | `/api/v1/turntable/initialize` | Retry automatic zero after making hardware ready |
| POST | `/api/v1/turntable/reference` | Capture an explicitly empty table without resetting zero |
| POST | `/api/v1/turntable/zero` | Optional manual zero override |
| GET/POST | `/api/v1/turntable/configuration` | List detected USB ports / save selected Arduino port |
| POST | `/api/v1/acquisitions` | Four-view capture → metric mesh → existing workspace |
| GET | `/api/v1/acquisitions/{id}` | Poll workflow and artifact URLs |

The existing uploaded-image `/api/v1/scans` route keeps its manual-anchor
contract. Automatic scaling is provided by `/api/v1/acquisitions` with measured
webcam poses; uploading arbitrary uncalibrated photos cannot establish metric scale.

## Verification

Tests cover camera calibration from synthetic perspective images; metric scale
recovery without a supplied object dimension, including a rotated raw mesh;
foreground separation and rotating-board compensation; capture budgets and
failure handling; original-coordinate ComfyUI mask download; and HTTP-to-CAD
handoff using simulated hardware and a stub CAD runner. They do not establish
accuracy on the assembled rig. A real scan still requires the physical setup
and calibration above.
