"""Claude integration; CAD and artifact services stay runtime-independent."""
import dataclasses
import inspect
import os

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    TextBlock, ToolUseBlock, create_sdk_mcp_server, tool,
)

from .cad import EXPOSED, text_of
from .mesh_tools import (COMPARE_SCHEMA, DESCRIPTIONS, FRAME_SCHEMA, SERIES_SCHEMA, SLICE_SCHEMA,
                         VIEW_SCHEMA)
from .model_tools import SCHEMAS as MODEL_SCHEMAS, model_tools

MESH_TOOLS = {'view_mesh', 'slice_mesh', 'slice_series', 'compare_to_mesh', 'mesh_frame'}
MODEL_TOOLS = set(MODEL_SCHEMAS)
DEFAULT_MAX_TURNS = 100


def resolve_max_turns(value=None):
    """Turn budget per request: explicit value, then AIDA_MAX_TURNS, then the default."""
    raw = value if value is not None else os.getenv('AIDA_MAX_TURNS') or DEFAULT_MAX_TURNS
    try:
        turns = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'max turns must be a whole number, not {raw!r}') from None
    if not 1 <= turns <= 1000:
        raise ValueError('max turns must be between 1 and 1000')
    return turns


async def _emit(on_event, event):
    """Deliver one streamed event to the optional UI callback."""
    if on_event is None:
        return
    result = on_event(event)
    if inspect.isawaitable(result):
        await result


def make_tools(cad, artifacts, on_event=None, workspace=None):
    tools = []
    for name in sorted(EXPOSED):
        definition = cad.schemas[name]

        async def handler(args, name=name):
            try:
                result = await cad.call(name, args)
                # Agent SDK handlers use is_error; MCP wire results use isError.
                payload = {**result, 'is_error': result.get('isError', False)}
                await _emit(on_event, {'type': 'tool_result', 'name': name,
                                       'ok': not payload['is_error'],
                                       'text': text_of(result)[:4000]})
                return payload
            except Exception as exc:
                artifacts.log('tool_error', {'name': name, 'error': str(exc)})
                await _emit(on_event, {'type': 'tool_result', 'name': name,
                                       'ok': False, 'text': str(exc)[:4000]})
                return {'is_error': True, 'content': [{'type': 'text', 'text': str(exc)}]}

        tools.append(tool(name, definition.description or name, definition.input_schema)(handler))

    @tool('view_image', 'See a rendered PNG from this session. Use after render_view.', {'path': str})
    async def view_image(args):
        try:
            result = artifacts.image(args['path'])
            artifacts.log('image_viewed', {'path': args['path']})
            await _emit(on_event, {'type': 'tool_result', 'name': 'view_image',
                                   'ok': True, 'text': ''})
            return result
        except Exception as exc:
            await _emit(on_event, {'type': 'tool_result', 'name': 'view_image',
                                   'ok': False, 'text': str(exc)[:4000]})
            return {'is_error': True, 'content': [{'type': 'text', 'text': str(exc)}]}

    tools.append(view_image)

    mesh = cad.mesh
    for name, schema, method in (('view_mesh', VIEW_SCHEMA, mesh.view),
                                 ('slice_mesh', SLICE_SCHEMA, mesh.slice),
                                 ('slice_series', SERIES_SCHEMA, mesh.series),
                                 ('compare_to_mesh', COMPARE_SCHEMA, mesh.compare),
                                 ('mesh_frame', FRAME_SCHEMA, mesh.frame)):
        async def mesh_handler(args, method=method, name=name):
            result = await method(args)
            payload = {**result, 'is_error': result.get('isError', False)}
            await _emit(on_event, {'type': 'tool_result', 'name': name,
                                   'ok': not payload['is_error'],
                                   'text': text_of(result)[:4000]})
            return payload
        tools.append(tool(name, DESCRIPTIONS[name], schema)(mesh_handler))

    workspace, table = model_tools(cad, artifacts, workspace)
    for name, description, schema, operation in table:
        async def model_handler(args, operation=operation, name=name):
            result = await operation(args)
            payload = {**result, 'is_error': result.get('isError', False)}
            await _emit(on_event, {'type': 'tool_result', 'name': name,
                                   'ok': not payload['is_error'],
                                   'text': text_of(result)[:4000]})
            # The whole workspace after every model call (failed ones roll back, so this
            # is still the truth); the UI's model panel renders it directly.
            await _emit(on_event, {'type': 'workspace_state', **workspace.snapshot()})
            return payload
        tools.append(tool(name, description, schema)(model_handler))
    return tools


def workspace_brief(workspace):
    """One line per part for the system prompt."""
    if workspace is None or not workspace.parts:
        return 'The workspace has no parts yet.'
    lines = []
    for name, part in workspace.parts.items():
        mesh = f'reference mesh ref_{name}' if part.reference else 'no reference mesh'
        lines.append(f'- {name}: {mesh}' + (f' ({part.description})' if part.description else ''))
    return '\n'.join(lines)


def options_for(cad, artifacts, units=None, max_turns=None, on_event=None, workspace=None):
    from .workspace import Workspace

    workspace = workspace or Workspace.load(cad, artifacts)
    server = create_sdk_mcp_server(name='aida', version='0.1.0',
                                   tools=make_tools(cad, artifacts, on_event, workspace))
    names = [f'mcp__aida__{name}' for name in sorted(EXPOSED | MESH_TOOLS | MODEL_TOOLS | {'view_image'})]
    prompt = f'''You are Aida, a CAD assistant. Use your CAD tools to inspect meshes and build CAD.
Input units: {units or "unknown; do not claim physical dimensions"}. Never rescale implicitly.

The session is a workspace of parts plus one assembly that places them. Parts now:
{workspace_brief(workspace)}
Each part is its own parametric model, built as part_<name>. A part's reference mesh (if any)
is registered as ref_<name>; preserve it. Mesh tools and model_* tools take part (default: the
active part, which is the first one until you change it with workspace_set_active).
workspace_status lists everything.

Inspecting a reference mesh:
- mesh_frame first: it says whether the mesh is aligned with the world axes and lists flat-face
  directions with their plane levels (e.g. Z=3), which are exact heights for extrusions.
- view_mesh renders it with exact framing and returns the image directly. Start with whole-part
  views, then zoom/pan with fit_box (or center plus view_width) onto features you need to see.
  Slice it (slice_axis/slice_normal plus slice_at) to see inside; the cut face is orange.
- slice_series along each axis finds where the cross-section changes: those positions bound
  extrusions, steps, pockets and holes.
- slice_mesh gives exact section loops with fitted primitives, max_deviation and a build123d
  snippet in the right plane. Take dimensions from these numbers, not from reading images.
  Slice between transitions, never exactly on a flat face.
- mesh_holes finds drilled holes (axis, diameter, depth, through) on a ref_<name> object.
- render_view renders generated CAD; render a reference mesh with view_mesh instead.

Building a part, always through its parametric model:
- Record every dimension, angle and count with model_set_params first, with source (measured /
  estimated / assumed) and evidence naming the tool result or design rule; feature code may not
  hard-code numbers. If you round a measured value to a nominal one, keep the raw number in
  measured.
- Add features one at a time with model_add_feature: code runs inside build_<part>(), reads
  P.name and updates part. Order them like a machinist would: base body from the dominant
  section, then cuts, holes, then fillets and chamfers. Each change rebuilds the part from its
  whole script; a part with a reference mesh is checked against it and the trend says better,
  worse, same or mixed. If a change makes it worse, model_undo it and rethink. Use
  model_edit_feature to fix a feature.
- For a tilted mesh, model the part axis-aligned and set placement from mesh_frame.
- Do not show part_<name> or assembly from execute; use execute only for scratch experiments
  under other names, then move what works into a model feature.
- Look closer where the model check reports worst regions: compare_to_mesh gives the heatmap;
  slice_mesh with compare_to overlays the CAD section on the mesh section with per-loop gaps;
  view_mesh with compare_to overlays the CAD in 3D. Fix the worst region first. Stop when the
  verdict is within_threshold or the remaining deviation is explained (scan noise, features you
  chose to simplify), then call model_report and summarise it.
- Fits with large max_deviation are guesses; say so.

Designing new parts and assemblies:
- workspace_add_part creates a part with no reference mesh (a plate, a case). Its parameters are
  design intent: source assumed (or measured when they come from another part), evidence naming
  the rule or the part they fit.
- Anything that differs between copies of a part (a keycap's width in key units, a length)
  must be a parameter of that part, so the assembly can override it per instance.
- The assembly is parametric too: assembly_set_params for pitches, counts, offsets and lists
  (such as the key widths of each row), then assembly_add_feature with layout code that calls
  place(part, location, name=None, **overrides) - for example
  for i, w in enumerate(P.row1): place('keycap', Pos(x, P.row1_y, P.cap_z), width_u=w)
  where x accumulates P.pitch * w. Each distinct part + overrides is built once and copied.
  The assembly rebuilds whenever a part it places changes.
- Make parts fit: derive mating dimensions from the other part's parameters (a plate cutout from
  the switch size, a case from the plate outline plus a wall) and check a few representative
  pairs with assembly_check. Model each part in its own natural frame and position it only in
  the assembly.
- Keyboard conventions, when relevant: 1u key pitch 19.05 mm; common widths 1, 1.25, 1.5, 1.75,
  2, 2.25, 2.75 and 6.25u; MX switch plate cutouts 14 x 14 mm in a 1.5 mm plate; a 60% layout
  has 5 rows of 15u total width.

- After render_view returns a PNG path or [SEND: path], call view_image to inspect it.
  Do not claim to have seen an image before you have it.
Use from build123d import * when necessary. Validate before exporting and export only when
requested; export the assembly object for the whole design. Save exports inside
{artifacts.root}; use relative filenames such as keyboard.step.
Tool errors are actionable feedback; correct ordinary code errors, but stop and tell the user
to restart if the CAD session times out.
A geometric approximation is not proof of accurate reconstruction. Explain uncertainty.
Only the supplied CAD and image tools are available. Do not attempt shell or filesystem access.
'''
    return ClaudeAgentOptions(
        model=os.getenv('ANTHROPIC_MODEL', 'claude-opus-5-5'),
        system_prompt=prompt, tools=[], allowed_tools=names,
        mcp_servers={'aida': server}, setting_sources=[], strict_mcp_config=True,
        permission_mode='dontAsk',
        max_turns=resolve_max_turns(max_turns), cwd=str(artifacts.root),
        env={'MCP_TOOL_TIMEOUT': '180000',
             'CLAUDE_CONFIG_DIR': str(artifacts.root / 'claude-runtime')},
    )


async def run_turn(client, prompt, artifacts, on_event=None):
    """Run one conversational turn. With ``on_event`` the turn streams
    ``agent_text``/``tool_call``/``tool_result``/``turn_end`` events to the UI
    instead of printing to stdout."""
    artifacts.log('user', prompt)
    await client.query(prompt)
    final = None
    async for message in client.receive_response():
        artifacts.log('agent', dataclasses.asdict(message) if dataclasses.is_dataclass(message) else str(message))
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    if on_event is None:
                        print(block.text, flush=True)
                    else:
                        await _emit(on_event, {'type': 'agent_text', 'text': block.text})
                elif isinstance(block, ToolUseBlock):
                    if on_event is None:
                        print(f'  [agent tool] {block.name}', flush=True)
                    else:
                        name = block.name.removeprefix('mcp__aida__')
                        await _emit(on_event, {'type': 'tool_call', 'name': name,
                                               'args': block.input})
        elif isinstance(message, ResultMessage):
            final = message
            summary = {'turns': message.num_turns, 'cost_usd': message.total_cost_usd,
                       'duration_s': round(message.duration_ms / 1000, 1), 'subtype': message.subtype}
            artifacts.log('turn_summary', summary)
            if on_event is not None:
                await _emit(on_event, {'type': 'turn_end', 'is_error': message.is_error,
                                       'subtype': message.subtype, **summary})
                continue
            cost = f', ${message.total_cost_usd:.2f}' if message.total_cost_usd is not None else ''
            print(f'  [done] {message.num_turns} agent turns, {summary["duration_s"]}s{cost}', flush=True)
            if message.subtype == 'error_max_turns':
                print('Agent reached its turn limit before finishing. Ask it to continue, or restart with '
                      f'a higher --max-turns. Artifacts preserved at {artifacts.root}')
            elif message.is_error:
                print(f'Agent stopped: {message.subtype}. Artifacts preserved at {artifacts.root}')
    return final


async def chat(cad, artifacts, units=None, initial_prompt=None, max_turns=None):
    async with ClaudeSDKClient(options=options_for(cad, artifacts, units, max_turns)) as client:
        if initial_prompt:
            result = await run_turn(client, initial_prompt, artifacts)
            if result is None or result.is_error:
                raise RuntimeError('Agent request did not complete successfully; inspect the session logs')
            return
        print('Aida ready. Enter a request; /quit exits. Ctrl-C stops the session.')
        while True:
            try:
                # No work runs while waiting for input; synchronous input keeps Ctrl-C clean.
                prompt = input('\naida> ').strip()
            except EOFError:
                break
            if prompt in {'/quit', '/exit'}:
                break
            if prompt:
                await run_turn(client, prompt, artifacts)
                if cad.broken:
                    break
