"""Measured profiles must stay bound to the hardware that produced them."""
import json
from dataclasses import replace

from aida.hardware.control import PositionController
from aida.hardware.motion import MotionTrace, load_profile, save_profile
from aida.turntable import HardwareSettings


def test_profile_round_trip_and_hardware_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'test-port')
    monkeypatch.setenv('AIDA_TURNTABLE_STORAGE', str(tmp_path))
    settings = HardwareSettings.from_env()
    config = replace(settings.control, positive_pulse_us=1300, negative_pulse_us=1600,
                     fine_positive_pulse_us=1390, fine_negative_pulse_us=1470,
                     positive_deg_per_s=27, negative_deg_per_s=36)
    controller = PositionController(None, None, config)
    save_profile(settings, controller)
    assert HardwareSettings.from_env().control == config
    for port, camera, neutral in [('other-port', settings.camera_id, 1450),
                                  (settings.port, 'other-camera', 1450),
                                  (settings.port, settings.camera_id, 1500)]:
        assert load_profile(tmp_path, port, camera, neutral) == {}
    monkeypatch.setenv('AIDA_TURNTABLE_POSITIVE_DPS', '29')
    assert HardwareSettings.from_env().control.positive_deg_per_s == 29


def test_motion_trace_groups_moves_and_keeps_failure_evidence(tmp_path):
    path = tmp_path / 'motion.jsonl'
    trace = MotionTrace(path)
    for event in ('start', 'pulse', 'succeeded', 'start', 'error'):
        trace({'event': event})
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len({r['move_id'] for r in rows}) == 2
    assert rows[0]['move_id'] == rows[2]['move_id']
    assert rows[-1]['event'] == 'error'
