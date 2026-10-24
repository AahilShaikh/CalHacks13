# Aida — agent-assisted design

A local Claude-powered terminal agent that views STL meshes and creates editable
build123d CAD using a persistent MCP server. This milestone provides tools and
vision, not automatic or dimensionally verified mesh-to-CAD reconstruction.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the
repository root (on Linux/WSL, add `~/.local/bin` to `PATH` if `uv` is not found):

```bash
uv sync --locked
uv run python examples/make_fixture.py
uv run aida --mesh examples/asymmetric.stl --units mm --check
```

### WSL checkouts on a Windows drive

For a checkout under `/mnt/c`, put the virtual environment on the Linux
filesystem before running the commands above:

```bash
export PATH="$HOME/.local/bin:$PATH"
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/aida-venv"
uv sync --locked
```

Keep those variables set in the terminal where you run Aida. In this environment,
the Windows-mounted virtual environment exceeded the CAD worker's 150-second
health-check timeout; the same lockfile on Linux storage passed the CLI and all
local tests. Native Windows users can use the normal `.venv` setup.

Python 3.12 is pinned. uv can obtain that interpreter if needed. Run all commands
in the same OS environment (for example, all inside WSL). The CAD stack includes
native OpenCascade and VTK dependencies; `--check` tests their rendering and export
capabilities before spending API credits. If native libraries or a headless
render backend are missing, inspect the printed error and the session's
`cad-stderr.log`. The Claude Agent SDK bundles its Claude Code runtime.

For the agent, set your Anthropic API key in the shell environment (do not commit
it), then run:

```bash
export ANTHROPIC_API_KEY='your-key'
uv run aida --mesh examples/asymmetric.stl --units mm
```

PowerShell uses `$env:ANTHROPIC_API_KEY = 'your-key'` instead of `export`.
`ANTHROPIC_MODEL` optionally overrides `claude-opus-5-5`; model access depends on
your Anthropic account. `.env` files are not loaded automatically.

Example requests:

1. “View the reference mesh from the top and isometric directions. Describe the visible features.”
2. “Create a 60 x 40 x 6 mm plate with two 5 mm through holes 40 mm apart. Name it generated_cad and inspect its render.”
3. “Change the plate thickness to 8 mm, preserve the holes, validate, and export plate.step and plate.stl.”

Use `/quit`, EOF, or Ctrl-C to exit. `--prompt "..."` runs a single paid request.
Each launch starts a new conversation and CAD session; follow-ups within that
launch retain both. A turn is limited to 30 SDK turns and a CAD call to 180 seconds.
A timed-out CAD session must be restarted because its state is uncertain.

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

```bash
uv run pytest -q
AIDA_INTEGRATION=1 uv run pytest -m integration -s
```

The integration test uses real CAD tools, imports an asymmetric mesh, renders
four views, changes plate thickness in one persistent session, and exports STEP
and STL. It makes no Claude API calls.

The following test **uses paid API credits** and is opt-in:

```bash
AIDA_LIVE_TEST=1 uv run pytest -m live -s
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
