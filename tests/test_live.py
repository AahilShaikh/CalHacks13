"""Opt-in paid test: AIDA_LIVE_TEST=1 uv run pytest -m live -s."""
import os

import pytest

from aida.artifacts import Artifacts
from aida.cad import open_cad, text_of

pytestmark = [pytest.mark.live, pytest.mark.skipif(
    os.getenv('AIDA_LIVE_TEST') != '1' or not os.getenv('ANTHROPIC_API_KEY'),
    reason='Requires AIDA_LIVE_TEST=1 and ANTHROPIC_API_KEY; uses API credits')]


async def test_claude_vision_and_followup(tmp_path):
    from build123d import Box, Cylinder, Pos, export_stl
    from claude_agent_sdk import ClaudeSDKClient
    from aida.agent import options_for, run_turn
    source = tmp_path / 'reference.stl'
    export_stl(Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10), str(source))
    artifacts = Artifacts(tmp_path / 'sessions')
    async with open_cad(artifacts) as cad:
        await cad.startup(artifacts.copy_mesh(source))
        async with ClaudeSDKClient(options=options_for(cad, artifacts, 'mm')) as client:
            first = await run_turn(client,
                'Inspect reference_mesh from top and isometric views using view_image. Describe its visible features. '
                'Then build generated_cad as a 60 x 40 x 6 mm plate with two 5 mm through holes 40 mm apart. '
                'Render and inspect it. Remember the project label amber.', artifacts)
            assert first and not first.is_error
            second = await run_turn(client,
                'Recall our project label. Change the plate thickness to 8 mm, preserving its other dimensions '
                'and holes. Validate and export plate.step and plate.stl.', artifacts)
            assert second and not second.is_error
        assert (artifacts.root / 'plate.step').exists()
        assert (artifacts.root / 'plate.stl').exists()
        assert 'image_viewed' in (artifacts.root / 'events.jsonl').read_text()
        assert 'generated_cad' in text_of(await cad.call('session_state', {}))
