# Aida — agent-assisted design

A local Claude-powered terminal agent that views STL meshes and creates editable
build123d CAD using a persistent MCP server. This milestone provides tools and
vision, not automatic or dimensionally verified mesh-to-CAD reconstruction.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the
repository root in PowerShell:

```powershell
uv sync --locked
uv run python examples/make_fixture.py
uv run aida --mesh examples/asymmetric.stl --units mm --check
```

Python 3.12 is pinned. uv can obtain that interpreter if needed. Run all commands
in your Windows terminal. The CAD stack includes
native OpenCascade and VTK dependencies; `--check` tests their rendering and export
capabilities before spending API credits. If native libraries or a headless
render backend are missing, inspect the printed error and the session's
`cad-stderr.log`. The Claude Agent SDK bundles its Claude Code runtime.

For the agent, open `.env` in the repository root and fill in `ANTHROPIC_API_KEY`.
Aida loads this file automatically when started from the repository root. For a
fresh checkout, copy `.env.example` to `.env` first. Then run:

```powershell
uv run aida --mesh examples/asymmetric.stl --units mm
```

`ANTHROPIC_MODEL` optionally overrides `claude-opus-5-5`; model access depends on
your Anthropic account. Existing shell variables take precedence over `.env`.
The `.env` file is excluded from Git; keep your actual key there.

Example requests:

1. “View the reference mesh from the top and isometric directions. Describe the visible features.”
2. “Create a 60 x 40 x 6 mm plate with two 5 mm through holes 40 mm apart. Name it generated_cad and inspect its render.”
3. “Change the plate thickness to 8 mm, preserve the holes, validate, and export plate.step and plate.stl.”
4. “Reconstruct reference_mesh as parametric CAD, verify it against the mesh, and finish with model_report.”

### Mesh inspection tools

Aida inspects the reference mesh with three local tools (VTK, trimesh and
matplotlib, each run in a subprocess):

- `view_mesh` frames the mesh exactly. To zoom or pan, pass `fit_box` or `center` +
  `view_width`. To orbit, pass `direction`/`view_dir` + `azimuth`/`elevation`.
  `slice_axis`/`slice_normal` + `slice_at` cut the mesh and shade the cut face.
  Images carry a coordinate grid, a scale bar and an axis triad. The returned JSON
  gives model units per pixel, so a pixel position maps back to model coordinates.
- `slice_mesh` returns the exact section loops, outer or hole, and fits a primitive
  to each: circle, rectangle, rounded rectangle, slot, or a line/arc profile. Each
  fit has its maximum deviation and a build123d snippet placed in the right plane.
- `slice_series` slices along an axis and reports the constant-profile ranges and
  the positions where the section changes, refined to the fit tolerance.

The CAD server's `mesh_holes` is exposed too. `render_view` of `reference_mesh`
with `clip_plane`/`clip_at` now renders a sliced view.

Three ways to check generated CAD against the mesh (each tessellates the named
CAD object to a temporary STL first):

- `compare_to_mesh` samples both surfaces and returns:
  - a heatmap of the reference: red where the CAD is missing material, blue where
    it has extra;
  - distance statistics in both directions;
  - bounding-box and volume differences;
  - the worst regions, each with a bounding box to zoom or slice into;
  - a `within_threshold` / `deviates` verdict.
- `slice_mesh` with `compare_to` slices the CAD on the same plane, draws it over
  the mesh section, and matches the loops. It reports each loop's boundary gap,
  centroid offset and size difference, plus any loops missing from or extra in
  the CAD.
- `view_mesh` with `compare_to` draws the CAD in opaque green over a translucent
  reference, and works with slicing too.

`mesh_frame` checks whether the reference is aligned with the world axes. It
lists flat-face plane levels (such as `Z=3`) and, for a tilted mesh, returns a
`Location(...)` placement for the model.

### Parametric model

Aida builds `generated_cad` through a parametric model rather than ad-hoc
`execute` calls:

- `model_set_params` records named dimensions. Each records its source (`measured`,
  `estimated` or `assumed`), its evidence and, when rounded, the raw measured value.
- `model_add_feature` and `model_edit_feature` manage the feature code blocks. Each
  block runs inside `build()`, reads `P.name` and updates `part`.
- Every change regenerates one script and rebuilds `generated_cad` from scratch. It
  then checks the result against the mesh and reports whether the deviation got
  better or worse.
- A failed build is rolled back and names the failing feature. `model_undo` reverts
  any revision.
- `model_status` shows the current state. `model_report` writes `report.md` with the
  verdict, a parameter table with sources, the features and the revision history.

The session folder keeps `model.py` (the current script), `model.json`, and every
revision under `model-history/`.

To try the tools without Claude:
`uv run python -m aida.mesh_probe slice examples/asymmetric.stl --spec '{"axis": "z", "at": 1}' --out section.png`

Use `/quit`, EOF, or Ctrl-C to exit. `--prompt "..."` runs a single paid request.
Each launch starts a new conversation and CAD session; follow-ups within that
launch retain both. A request may use up to 100 agent turns (`--max-turns` or
`AIDA_MAX_TURNS` changes this) and a CAD call 180 seconds. After each request Aida prints
the turns used, the time taken and the API cost. If the turn limit is reached, ask it to
continue.
A timed-out CAD session must be restarted because its state is uncertain.

## Scan pipeline (images to metric mesh)

For a complete walkthrough — installing ComfyUI, placing the Pixal3D models,
capture setup, and how each stage works — see
[docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md).

The Pixal3D/ComfyUI image-to-mesh pipeline and the ChArUco 1:1 calibration
(originally `cad_calibration`) live in `backend/aida/scan/`. The ComfyUI
installation with its models stays external and is reached over HTTP
(`COMFY_HOST`, default `http://127.0.0.1:8188`); nothing assumes its output
directory is on the local filesystem. Start ComfyUI with the Pixal3Dworkflow first (see [docs/PIXAL3D_GUIDE.md](docs/PIXAL3D_GUIDE.md)).

Modules: `scan/comfy.py` (ComfyUI client), `scan/charuco.py` (board detection
and pose), `scan/scaling.py` (metric scaling + CAD metadata),
`scan/blueprints.py` (orthographic blueprints), `scan/boards.py` (printable
calibration PDFs), `scan/pipeline.py` (orchestration), and `aida.api`
(job-based HTTP API).

### HTTP API

```powershell
uv run aida-api                       # http://127.0.0.1:8000
# or: uv run uvicorn aida.api.server:app --port 8000
```

- `GET /health` — service status and ComfyUI reachability.
- `POST /api/v1/scans` — multipart upload: `front_image` (required),
  `left_image`/`back_image`/`right_image` (optional), plus one scale anchor
  (`known_height_mm`, `known_base_dia_mm`, or `scale_factor`). Returns 202 with
  a job id; generation runs in the background.
- `GET /api/v1/scans/{job_id}` — poll `status` (`queued`/`running`/`succeeded`/
  `failed`), `stage`, `cad_specs`, and artifact URLs.
- `GET /api/v1/scans/{job_id}/files/{name}` — download `metric_model.glb`,
  `metric_model.stl`, `blueprints.png`, `dimensions.json`, `agent_prompt.txt`,
  and the saved input photos.

A scale anchor is required: a unitless Pixal3D mesh cannot be sized from the
ChArUco photo alone. Without one the job fails with a clear error instead of
returning wrong millimeter dimensions. Job artifacts are stored under
`artifacts/scans/` (`AIDA_SCAN_STORAGE` overrides).

## Web UI (Aida CAD Studio)

The UI is a React app in `frontend/` (Vite, TypeScript, Tailwind, @react-three/fiber), designed in
Figma first. The design is the "Aida CAD Studio UI" Figma file, and `docs/STAGE_LOG.md` records how
it went from Figma to code. Layout: a 3D viewer showing the reference mesh and the generated CAD,
plus a right panel with the accuracy chart, the agent's tool calls, the model's features and the
prompt box. While the agent inspects the mesh, the viewer mirrors it: slice planes appear where it
cuts, and the camera flies to the exact view each `view_mesh` call rendered ("Following agent";
drag to look around yourself). Clicking a tool card marked 3D replays that step's view.
The Model tab shows the parametric model live (from `model_state` events sent after every
`model_*` call): each parameter with its source (measured / estimated / assumed) and evidence,
features in build order with the deviation trend each one caused, and the revision history.

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173, forwards /api to the API on :8000
npm run build      # writes frontend/dist; `uv run aida-api` then serves it at /
```

Run `uv run aida-api` alongside `npm run dev`. Once `frontend/dist` exists, the API serves the
React app at `/` and the older single-file viewer below at `/legacy`. `GET /api/v1/examples`
lists the sample meshes in `examples/` for the empty state's "Try the example part" button.

## Legacy web viewer (live CAD sessions)

`uv run aida-api` also serves the web viewer at `http://127.0.0.1:8000/` (`/legacy` once the React
app is built). Upload a
reference mesh (`.stl`/`.glb`/`.obj`/`.ply`), optional reference photos, and a
prompt to start a live CAD session. The UI streams agent text, build123d
snippets, tool activity and renders over SSE, and rebuilds its 3D preview after
every successful `execute`: each revision is exported as a binary GLB and the
viewer morphs from the previous mesh to the new one with a particle transition
while the export runs. Follow-up prompts continue in the same session. The
viewer loads Three.js from a CDN; the session needs the same `ANTHROPIC_API_KEY`
as the CLI.

- `POST /api/v1/cad-sessions` — multipart `mesh` + `prompt` (optional `units`,
  `images`), returns 202 with session, `events` and `mesh` URLs.
- `GET /api/v1/cad-sessions/{id}/events` — SSE stream; `?after=N` replays events.
- `GET /api/v1/cad-sessions/{id}/mesh.glb?rev=N` — binary mesh revision.
- `POST /api/v1/cad-sessions/{id}/turns` — follow-up prompt; `/close` ends the
  session.

### Command line

```powershell
uv run aida scan run --front photo.jpg --height-mm 75 --out artifacts/scan1
uv run aida scan scale --mesh raw.glb --height-mm 75 --out-mesh mm.glb --out-json dims.json
uv run aida scan blueprint --mesh mm.glb --out blueprints.png
uv run aida scan board --kind both --format both --out-dir boards
uv run aida scan inspect --image photo.jpg --out annotated.jpg
```

`run` emits the metric GLB, an STL copy (the format `aida --mesh` accepts),
blueprints, `dimensions.json`, and a ready-to-paste `agent_prompt.txt` for the
build123d CAD agent. Print a calibration board from `aida scan board`, print at
100% scale, and verify its 100 mm ruler with calipers before capture.

## Meshes and outputs

Only STL is accepted. STL does not declare physical units: omit `--units` when
unknown, or provide `--units mm` when you know the coordinates are millimeters.
Aida never scales the input automatically. The original input is copied into
`artifacts/<session>/reference.stl` and imported as `reference_mesh`. Generated
CAD should use another name.

Each session saves PNG previews, individual submitted Python snippets, JSONL
conversation/tool logs, and requested exports. Snippets are an ordered execution
record; a later snippet can depend on variables established earlier. STEP exports
preserve solid geometry, not a native CAD application's parametric feature tree.
Image bytes are omitted from logs; images themselves stay in the session folder.

Reference meshes are rendered directly from STL triangles using VTK. The pinned
CAD server routes its renderer through STEP, which loses imported STL
triangulation and produces blank previews. Aida bypasses that path for
`reference_mesh`, while generated CAD uses the server renderer. Render the two
objects separately; overlays and mesh clipping are not part of this milestone.
Blank PNGs are treated as failures.

The agent's `view_image` tool sends real PNG image content to Claude, rather than
only a filename. It accepts files only within the current session, including the
CAD server's private temporary output directory. Built-in shell, filesystem,
web, and delegation tools are disabled. MCP configuration is restricted to Aida's
explicitly registered tools. CAD executes with build123d-mcp's default restrictions;
this local application is not a multi-tenant execution sandbox.

## Tests

```powershell
uv run pytest -q
$env:AIDA_INTEGRATION = '1'
uv run pytest -m integration -s
```

The integration test uses real CAD tools, imports an asymmetric mesh, renders
four views, changes plate thickness in one persistent session, and exports STEP
and STL. It makes no Claude API calls.

The following test **uses paid API credits** and is opt-in:

```powershell
$env:AIDA_LIVE_TEST = '1'
uv run pytest -m live -s
```

It also needs `ANTHROPIC_API_KEY`. It exercises visual inspection, CAD generation,
and a follow-up edit using the same ClaudeSDKClient. Review its saved transcript
and renders to evaluate the quality of the model's visual description.

## Architecture

- `backend/aida/cad.py`: owns one stdio MCP subprocess; discovers schemas,
  serializes calls, gates exports on validation, and records code/artifacts.
- `backend/aida/artifacts.py`: stores session artifacts and validates PNG access.
- `backend/aida/mesh_render.py`: bounded subprocess rendering of original STL triangles.
- `backend/aida/agent.py`: registers the CAD proxy and image tools in the Claude
  Agent SDK; maintains the conversational loop with all built-in tools disabled.

Use `uv add` to change dependencies and commit `pyproject.toml`, `uv.lock`, and
`.python-version`. Do not mix manual pip installs into the managed environment.
Web search/fetch, hardware control, and quantitative comparison are future work.
