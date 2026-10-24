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
the Agent SDK. Agent capture and CAD calls can incur API costs. The first
hardware validation can use `four_views` with `start_cad:false`, avoiding both
Claude stages while still running local ComfyUI and metric registration.

Firmware is `firmware/turntable/turntable.ino`, Mega pin 9, startup neutral
1450 µs. Upload it, confirm stopped neutral, and close Serial Monitor before API
capture. See [turntable wiring and tuning](TURNTABLE.md).

ComfyUI is installed separately at
`/Users/linweizhi/Documents/CalHacks Project/ComfyUI`:

```bash
cd "/Users/linweizhi/Documents/CalHacks Project/ComfyUI"
./start_comfyui.sh --listen 127.0.0.1 --port 8188 --disable-all-custom-nodes
```

All required model weights are already installed on this machine. Aida uses
ComfyUI core nodes, with no custom nodes required. Both servers should remain
bound to localhost.

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
8. Click **Set zero and save empty-table reference**. The servo must stop still
   at 1450 µs. Do not move the camera, change lighting, remount the board or
   change focus after saving this reference.
9. Place a small opaque, matte object at the center. Leave at least **eight
   visible ChArUco corners spanning 60 mm in both board directions** around it
   in every view. Keep hands out of the scene during capture.
10. For the first check, choose **Four views (no capture-agent call)** and uncheck
    **Start CAD agent after fitting**, then click **Start scan**. Confirm that the
    job reaches `metric_mesh_ready` and inspect photos, masks, dimensions and
    `metric_model.stl`. Failure messages identify missing corners or a bad scale fit.
11. Once that works, choose **Agent chooses 2–4 views**, check **Start CAD agent
    after fitting**, enter the modeling request, and click **Start scan**. Open
    the live CAD viewer when its link appears. Both agent stages need configured
    Claude authentication.

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
after changing camera, resolution or lens settings. Re-zero and
capture a new empty reference after moving the USB camera, changing lighting or
the background, or remounting the board. A calibration change invalidates the
old zero reference. Do not reuse a zero reference from another camera.

## Automatic workflow

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/acquisitions \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Reconstruct this object as editable CAD.","policy":"agent","start_cad":true}'
```

The response is 202 with a `job_id` and `status_url`. Individual moves remain
synchronous internally; the complete scan runs in the background because mesh
inference may take minutes.

The capture agent has only two tools:

- `capture_view`: front, left, back or right. Each unique view is saved once,
  after the table has stopped and repeated angle checks pass. Tool calls are
  serialized even if the model requests them in parallel.
- `reconstruct`: requires front plus at least one side. Generates the mesh and
  tries metric registration. If the fit is ambiguous, the agent may request
  another unused cardinal view and try again, up to **four object views** total.

The camera and serial port remain reserved across agent calls, avoiding board
resets between photos and preventing unrelated moves from changing the object
scan. A hardware/camera/Comfy failure disables further agent motion. Shutdown
attempts a confirmed STOP even on failure; firmware locally bounds every burst.

The empty-table photo is warped using each measured board pose to compensate
for the rotating print. Foreground differencing isolates the object. Original
photos remain unchanged; derived object images and masks preserve the original
pixel coordinates. ComfyUI receives isolated object images and returns both
the generated mesh and original-coordinate foreground masks.

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
URLs, dimensions and (after successful handoff) `cad_session_id` and `viewer_url`.

Files under `artifacts/acquisitions/{job_id}/` include:

- `front.png` and any additional views, with `*_pose.json` angle/board evidence;
- `empty_table.png`, `empty_table.json`, `camera.json`;
- `*_foreground.png`, `*_object.png`, and ComfyUI `*_mask.png`;
- `unscaled_model.glb`, `metric_model.glb`, `metric_model.stl`;
- `dimensions.json`, `blueprints.png`, `agent_prompt.txt`, capture-agent log.

`dimensions.json` records the applied millimeters-per-raw-unit multiplier,
raw-to-board and raw-to-CAD transforms, per-view fit errors and uncertainty.
The metric mesh is centered at its base footprint with Z=0 at the ground.

CAD receives that **already-scaled mesh with `units=mm`**, the original reference
photos and blueprints, the full dimensions/calibration evidence in its prompt,
and copies of the metadata files. Its prompt explicitly forbids applying the
scale multiplier a second time. `succeeded`/`cad_started` means capture, metric
registration and CAD handoff succeeded; follow the linked CAD session for its
own completion, errors and generated geometry.

`POST /api/v1/acquisitions/{job_id}/cancel` requests cancellation after the
current operation. It prevents subsequent motion and CAD handoff; it is not an
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
| POST | `/api/v1/turntable/zero` | Use `{"empty_table_reference":true}` during setup |
| GET/POST | `/api/v1/turntable/configuration` | List detected USB ports / save selected Arduino port |
| POST | `/api/v1/acquisitions` | Start bounded capture → metric mesh → optional CAD |
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
