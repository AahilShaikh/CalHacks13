"""Claude integration; CAD and artifact services stay runtime-independent."""
import dataclasses
import os

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
    TextBlock, ToolUseBlock, create_sdk_mcp_server, tool,
)

from .cad import EXPOSED


def make_tools(cad, artifacts):
    tools = []
    for name in sorted(EXPOSED):
        definition = cad.schemas[name]

        async def handler(args, name=name):
            try:
                result = await cad.call(name, args)
                # Agent SDK handlers use is_error; MCP wire results use isError.
                return {**result, 'is_error': result.get('isError', False)}
            except Exception as exc:
                artifacts.log('tool_error', {'name': name, 'error': str(exc)})
                return {'is_error': True, 'content': [{'type': 'text', 'text': str(exc)}]}

        tools.append(tool(name, definition.description or name, definition.input_schema)(handler))

    @tool('view_image', 'See a rendered PNG from this session. Use after render_view.', {'path': str})
    async def view_image(args):
        try:
            result = artifacts.image(args['path'])
            artifacts.log('image_viewed', {'path': args['path']})
            return result
        except Exception as exc:
            return {'is_error': True, 'content': [{'type': 'text', 'text': str(exc)}]}

    tools.append(view_image)
    return tools


def options_for(cad, artifacts, units=None):
    server = create_sdk_mcp_server(name='aida', version='0.1.0', tools=make_tools(cad, artifacts))
    names = [f'mcp__aida__{name}' for name in sorted(EXPOSED | {'view_image'})]
    prompt = f'''You are Aida, a CAD assistant. Use your CAD tools to inspect meshes and build CAD.
The STL is registered as reference_mesh. Preserve it; create generated CAD under a different name.
Input units: {units or "unknown; do not claim physical dimensions"}. Never rescale implicitly.
Inspect reference_mesh from multiple views before describing its geometry. Specify objects explicitly
when rendering so the reference and generated CAD do not unintentionally obscure each other.
After render_view returns a PNG path or [SEND: path], call view_image to actually inspect it.
Do not claim to have seen an image before view_image succeeds. Use front, side, top, iso and
azimuth/elevation to inspect geometry. Build incrementally using execute and named parameters.
Use from build123d import * when necessary. Use measure and validate to verify generated CAD.
Validate before exporting and export only when requested. Save exports inside {artifacts.root}.
Use relative export filenames such as model.step. Tool errors are actionable feedback; correct
ordinary code errors, but stop and tell the user to restart if the CAD session times out.
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


async def run_turn(client, prompt, artifacts):
    artifacts.log('user', prompt)
    await client.query(prompt)
    final = None
    async for message in client.receive_response():
        artifacts.log('agent', dataclasses.asdict(message) if dataclasses.is_dataclass(message) else str(message))
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text, flush=True)
                elif isinstance(block, ToolUseBlock):
                    print(f'  [agent tool] {block.name}', flush=True)
        elif isinstance(message, ResultMessage):
            final = message
            if message.is_error:
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
