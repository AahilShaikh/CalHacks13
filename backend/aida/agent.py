"""Claude integration; CAD and artifact services stay runtime-independent."""
import dataclasses
import inspect
import os

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    TextBlock, ToolUseBlock, create_sdk_mcp_server, tool,
)

from .cad import EXPOSED, text_of
from .mesh_tools import DESCRIPTIONS, SERIES_SCHEMA, SLICE_SCHEMA, VIEW_SCHEMA

MESH_TOOLS = {'view_mesh', 'slice_mesh', 'slice_series'}


async def _emit(on_event, event):
    """Deliver one streamed event to the optional UI callback."""
    if on_event is None:
        return
    result = on_event(event)
    if inspect.isawaitable(result):
        await result


def make_tools(cad, artifacts, on_event=None):
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
                                 ('slice_series', SERIES_SCHEMA, mesh.series)):
        async def mesh_handler(args, method=method, name=name):
            result = await method(args)
            payload = {**result, 'is_error': result.get('isError', False)}
            await _emit(on_event, {'type': 'tool_result', 'name': name,
                                   'ok': not payload['is_error'],
                                   'text': text_of(result)[:4000]})
            return payload
        tools.append(tool(name, DESCRIPTIONS[name], schema)(mesh_handler))
    return tools


def options_for(cad, artifacts, units=None, on_event=None):
    server = create_sdk_mcp_server(name='aida', version='0.1.0',
                                   tools=make_tools(cad, artifacts, on_event))
    names = [f'mcp__aida__{name}' for name in sorted(EXPOSED | MESH_TOOLS | {'view_image'})]
    prompt = f'''You are Aida, a CAD assistant. Use your CAD tools to inspect meshes and build CAD.
The STL is registered as reference_mesh. Preserve it; create generated CAD under a different name.
Input units: {units or "unknown; do not claim physical dimensions"}. Never rescale implicitly.

Inspecting the reference mesh:
- view_mesh renders it with exact framing and returns the image directly. Start with whole-part
  views, then zoom/pan with fit_box (or center plus view_width) onto features you need to see.
  Slice it (slice_axis/slice_normal plus slice_at) to see inside; the cut face is orange.
- slice_series along each axis finds where the cross-section changes: those positions bound
  extrusions, steps, pockets and holes.
- slice_mesh gives exact section loops with fitted primitives, max_deviation and a build123d
  snippet in the right plane. Take dimensions from these numbers, not from reading images.
  Slice between transitions, never exactly on a flat face.
- mesh_holes finds drilled holes (axis, diameter, depth, through) on the mesh.
- render_view still renders generated CAD; render reference_mesh with view_mesh instead.

Building CAD from the mesh:
- Keep one script with a PARAMS dict of named dimensions and rebuild the whole part from it on
  each execute. Build feature by feature: base extrusion from the dominant section, then cuts,
  holes and fillets, each traced to a slice or series result.
- Check each step with measure on the generated CAD and compare its size and sections with
  the reference. Fits with large max_deviation are guesses; say so.
- After render_view returns a PNG path or [SEND: path], call view_image to inspect it.
  Do not claim to have seen an image before you have it.
Use from build123d import * when necessary. Validate before exporting and export only when
requested. Save exports inside {artifacts.root}; use relative filenames such as model.step.
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
        max_turns=30, cwd=str(artifacts.root),
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
            if on_event is not None:
                await _emit(on_event, {'type': 'turn_end', 'is_error': message.is_error,
                                       'subtype': message.subtype})
            elif message.is_error:
                print(f'Agent stopped: {message.subtype}. Artifacts preserved at {artifacts.root}')
    return final


async def chat(cad, artifacts, units=None, initial_prompt=None):
    async with ClaudeSDKClient(options=options_for(cad, artifacts, units)) as client:
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
