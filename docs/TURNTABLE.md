# Turntable rotation and photo API

For the USB webcam, one-time lens calibration and the complete automatic
image-to-metric-mesh/CAD workflow, see [automatic scanner setup](AUTOMATIC_SCAN.md).

Aida reconstructs photos through Pixal3D/ComfyUI and hands meshes to its CAD
agent. This addition provides hardware capture: an Arduino drives the FS90R in
timed bursts while a USB webcam measures actual angle from a rotating ChArUco
board. Timing estimates never serve as position feedback.

`POST /api/v1/turntable/rotate` blocks until three settled angle readings satisfy
the tolerance, the verified photo and metadata are saved, and final STOP is
acknowledged. It returns 200 or a non-2xx error. FastAPI runs this blocking work
in its thread pool so other routes remain available.

## Wiring and firmware

The board is assumed to be an **Arduino Mega 2560**; confirm the printed model.

| Connection | Destination |
| --- | --- |
| FS90R signal (typically orange) | Mega digital **9** |
| FS90R ground (typically brown) | External supply ground and Mega GND |
| FS90R power (typically red) | Regulated 5 V external servo supply |
| Mega USB and webcam USB | Laptop |

Use a servo supply capable of at least 1 A; power the Mega through USB. The
[manufacturer specification](https://www.pololu.com/file/0J1867/FS90R.pdf)
lists 4.8–6 V operation and 700–800 mA stall current. Join grounds; keep the
external servo supply positive separate from the Mega's USB-powered 5 V rail.

Open `firmware/turntable/turntable.ino` in Arduino IDE, select Mega 2560 and its
USB port, install the **Servo** library if needed, and upload. Close Serial
Monitor before running the API. `SERVO_PIN` is configurable in the sketch.

The sketch boots stopped at the measured neutral, `STARTUP_NEUTRAL_US = 1450`.
If it creeps, recheck the measured neutral or adjust the servo trim. If changing neutral in
software, change both that sketch constant and the environment value. Neutral
is not an active brake or position hold.

Firmware stops each burst locally after at most 500 ms, even if the laptop
disconnects. `DONE` is sent after neutral is applied. This depends on neutral
actually stopping the servo; the microcontroller has no independent motion
sensor.

## Board and camera

Print the existing 10×10 ChArUco turntable board at 100% scale:

```bash
uv run aida scan board --kind turntable --format letter --out-dir artifacts/boards
```

Use 20 mm squares, 15 mm markers, and `DICT_5X5_100`, matching the existing board
generator. Mount it flat and rigidly on the rotating disc. At least six visible
corners must span 40 mm in both board directions at every angle, including
around the object. Keep the webcam, table plane, board size and camera resolution
fixed after zeroing. Use sharp focus and good lighting.

The saved zero homography maps pixels back into the table plane before fitting
rotation, avoiding raw image-angle errors from an oblique camera. No camera
intrinsics are required for this planar method. Lens distortion, table wobble,
board flex and camera movement still limit physical accuracy. Re-zero after
moving the camera; arbitrary camera motion cannot reliably be distinguished
from board rotation.

## Configuration

```bash
uv sync --locked
uv run python -m serial.tools.list_ports
```

Set these in the repository's local `.env` (loaded automatically by `aida-api`):

```dotenv
# macOS example; Windows could be COM3, Linux /dev/ttyACM0
AIDA_TURNTABLE_PORT=/dev/cu.usbmodemYOUR_PORT
AIDA_TURNTABLE_CAMERA=0
AIDA_TURNTABLE_STORAGE=artifacts/turntable
AIDA_TURNTABLE_NEUTRAL_US=1450
# Optional overrides; normally use the saved measured motion profile.
# AIDA_TURNTABLE_POSITIVE_US=1300
# AIDA_TURNTABLE_NEGATIVE_US=1600
# AIDA_TURNTABLE_POSITIVE_DPS=27
# AIDA_TURNTABLE_NEGATIVE_DPS=36
# AIDA_TURNTABLE_FINE_POSITIVE_US=1390
# AIDA_TURNTABLE_FINE_NEGATIVE_US=1470
# AIDA_TURNTABLE_FINE_POSITIVE_DPS=12
# AIDA_TURNTABLE_FINE_NEGATIVE_DPS=12
AIDA_TURNTABLE_MAX_PULSE_S=0.45
AIDA_TURNTABLE_SETTLE_S=0.25
```

Neutral 1450 µs and the commented pulse settings above were measured on this rig.
Successful moves save learned direction, coarse/fine pulse widths and travel rates
in `artifacts/turntable/motion_profile.json`, bound to the serial port, camera ID
and neutral. Explicit environment values override the saved profile. Without a
matching profile, coarse pulses default to neutral ±100 µs and fine pulses to
neutral ±60 µs. The first use of each pulse mode is limited to a 60 ms discovery
pulse (or shorter near the target). Camera feedback determines actual direction;
the controller automatically swaps polarity once if the initial mapping is reversed.
A direction reversal after verification aborts with `direction_mismatch`. Positive
angles rotate the printed board X axis toward its Y axis: clockwise viewed from
the printed side. Recheck calibration if the camera or mechanics move.

Fine commands must lie between neutral and their corresponding coarse command
and still move the loaded table. The FS90R has a neutral deadband: if fine
commands stall, the controller steps them toward the coarse pulse in 10 µs increments.
Repeated pulses with no measurable movement abort with `stalled`. Use a slower drive so the smallest
useful movement is smaller than the target tolerance. An API parameter cannot
guarantee precision the mechanics and camera cannot achieve.

Run **one process/worker per turntable**, with no competing controller or Serial
Monitor:

```bash
uv run aida-api
```

An in-process lock rejects overlapping operations with 409; the serial driver
also requests exclusive access. Importing modules opens no devices. On server startup the configured camera
and Arduino are opened to establish zero: STOP is commanded at neutral, the
mounted board is observed, and stationary checks must pass. No DRIVE command is
issued. Missing hardware is reported by the setup page and `/health`; the HTTP
service remains available for setup and CAD work.

## Automatic startup zero, then rotate and capture

Mount the board flat and fix the camera before starting the server. The current
observed board orientation becomes 0° on each server startup. This defines a
fresh angle origin; it does not physically home or rotate the table. STOP at
neutral and stationary camera checks are required before reporting ready.

`GET /api/v1/turntable/status` reports readiness and initialization errors. If
hardware was missing or unready at startup, correct it and use **Retry startup
zero** on `/setup.html` or `POST /api/v1/turntable/initialize`.

For automatic mesh scanning, leave the table empty and click **Capture empty-table
reference** (or `POST /api/v1/turntable/reference` with `{}`). This separate action
does not change zero, and is never assumed safe from an object-containing startup
photo. Recapture after camera, lighting or mounting changes. A valid empty
reference remains usable across logical zero resets with unchanged calibration.

`POST /api/v1/turntable/zero` remains available for an explicit manual override.
It should not be called between ordinary moves. The zero homography is stored
in `artifacts/turntable/zero.json`.

Rotate to an absolute angle and save a photo:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/v1/turntable/rotate \
  -H 'Content-Type: application/json' \
  -d '{"angle":90,"tolerance_degrees":1,"timeout_s":30}'
```

Illustrative response:

```json
{
  "status": "succeeded",
  "target_angle": 90.0,
  "measured_angle": 89.7,
  "error_degrees": 0.3,
  "pulses": 8,
  "tolerance_degrees": 1.0,
  "feedback": "charuco",
  "capture_url": "/api/v1/turntable/captures/0123456789abcdef0123456789abcdef.jpg",
  "elapsed_s": 7.2
}
```

`GET capture_url` downloads the JPEG. JPEGs and angle metadata live in
`artifacts/turntable/captures/`. The JPEG is the exact frame supplying the final
verified angle while stopped. Failed saves or unconfirmed final STOP produce an
error; failures may leave partial files on disk. Only 200 confirms the operation.

Angles normalize modulo 360 and use the shortest path: absolute 360° equals 0°,
not one full revolution. Negative angles work. Optional `"relative": true`
targets current measured angle plus the supplied angle, still by shortest path.

Default tolerance is 1° with three fresh settled checks, whose measured angle
spread must also be at most half the tolerance. This is a measured
camera criterion, not independently verified physical accuracy. Normal bursts
are capped at 450 ms, slower commands apply within 8° of target, and settling
defaults to 250 ms. Timing estimates adapt after every measured pulse, persist
across views and are saved after successful moves. Each normal pulse targets
at most 9° based on the current rate estimate. Improve image quality, tune fine pulses,
and shorten settling only after checking vibration. Tighten tolerance after
validating physical accuracy on your hardware.

`timeout_s` includes startup (about 2 s for Arduino reset), camera acquisition,
movement and saving. Cleanup can add about 0.7 s for STOP/camera shutdown; OS
device/filesystem operations are not hard real-time guarantees. Give HTTP
clients a timeout longer than `timeout_s + 2`. A client disconnect does not
cancel the controller; it finishes or reaches its own deadline.

## Errors and modules

Errors use `{"detail":{"code":"...","message":"..."}}`, except FastAPI's
standard 422 validation response:

| HTTP | Typical codes |
| --- | --- |
| 409 | `busy`, `zero_required` |
| 422 | Invalid input or unknown fields |
| 503 | `not_configured`, `configuration_error`, `serial_unavailable`, `serial_error`, `firmware_error` |
| 503 | `firmware_update_required`, `neutral_mismatch` |
| 503 | `camera_unavailable`, `feedback_lost`, `feedback_invalid`, `direction_mismatch`, `stalled`, `stop_unconfirmed` |
| 504 | `timeout` |
| 500 | `capture_failed`, `calibration_save_failed` |

Transient board loss is retried up to three times with the motor stopped;
continued loss aborts without another drive pulse. Stalls abort after five
consecutive pulses with no measurable movement. Cleanup attempts STOP on
every opened connection, including on failures. Motion measurements, pulse
widths/durations, rate estimates, retries and failures are saved as JSONL in
`artifacts/turntable/motion/` for standalone moves and `motion.jsonl` in each
acquisition directory. `stop_unconfirmed` means the
final STOP could not be acknowledged and the position is unverified.

- `hardware/control.py`: drive/feedback interfaces, coarse/fine controller,
  shortest-path math, deadline and stable checks. Replace adapters independently.
- `hardware/arduino.py`: pyserial, firmware/neutral verification, correlated command IDs and completion ACKs.
- `hardware/motion.py`: hardware-bound measured profiles and per-move JSONL traces.
- `hardware/vision.py`: fresh webcam frames and planar ChArUco angle feedback.
- `turntable.py`: exclusive hardware lifecycle, photo/metadata persistence, and
  existing async `capture_views()` integration for four scan views.
- `api/turntable.py`: blocking FastAPI routes.
- `firmware/turntable/turntable.ino`: Mega timed-burst firmware.

115200-baud newline-delimited serial protocol:

```text
PING <id>                                    -> OK <id> PING
INFO <id>                                    -> OK <id> INFO 2 1450 9 500
STOP <id> <neutral_us>                        -> OK <id> STOP
DRIVE <id> <pulse_us> <duration_ms> <neutral_us> -> OK <id> DONE (after stop)
invalid/busy                                 -> ERR <id> BAD_COMMAND/BUSY
```

Pulse values: 1000–2000 µs; burst duration: 1–500 ms. The sketch boots with
`READY AIDA_TURNTABLE_V2`. The host requires version 2 and verifies that
firmware startup neutral equals the configured neutral before allowing motion.
Re-upload the sketch when updating from version 1 or changing the neutral.
Command IDs prevent stale responses completing a new operation; the motor timer
is nonblocking.

```bash
uv run pytest tests/test_turntable_control.py tests/test_turntable_vision.py \
  tests/test_turntable_serial.py tests/test_turntable_api.py -q
```

Tests use actual OpenCV on synthetic perspective-projected boards, simulated
motors, serial fakes and HTTP integration. They do not move hardware. Firmware
must be compiled/uploaded and validated on the assembled system.
The development rig was flashed with version 2 and exercised at 0°, 90°, 180°,
270° and back to 0° using the USB camera; all five final readings were within
0.5° of their targets. This verifies camera-reported accuracy, not external metrology.
