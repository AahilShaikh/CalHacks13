"""Closed-loop behavior against a deterministic, imperfect simulated turntable."""
import time
from dataclasses import replace

import pytest

from aida.hardware.control import (
    ControlConfig, PositionController, TurntableError, angle_error,
)


class SimulatedTable:
    def __init__(self, angle=0, speed=90, gain=1):
        self.angle = angle
        self.speed = speed
        self.gain = gain
        self.pulses = []
        self.stops = 0
        self.reads = 0

    def pulse(self, pulse_us, duration_s, deadline):
        self.pulses.append((pulse_us, duration_s))
        self.angle = (self.angle + (pulse_us - 1500) / 100 *
                      duration_s * self.speed * self.gain) % 360

    def stop(self, deadline):
        self.stops += 1

    def read_angle(self, deadline):
        self.reads += 1
        return self.angle


@pytest.fixture()
def no_settling(monkeypatch):
    monkeypatch.setattr('aida.hardware.control.time.sleep', lambda _: None)


def test_corrects_imperfect_speed_instead_of_trusting_timing(no_settling):
    table = SimulatedTable(gain=1.4)
    result = PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert abs(result.error_degrees) <= 1
    assert len(table.pulses) > 1
    assert table.stops == 1
    assert table.reads >= len(table.pulses) + 3
    assert all(duration <= 0.25 for _, duration in table.pulses)


@pytest.mark.parametrize(('start', 'target', 'sign'), [(355, 5, 1), (5, 355, -1)])
def test_shortest_path_across_zero(start, target, sign, no_settling):
    table = SimulatedTable(start)
    result = PositionController(table, table).rotate(target, 1, time.monotonic() + 2)
    assert abs(angle_error(target, result.measured_angle)) <= 1
    assert (table.pulses[0][0] > 1500) == (sign > 0)


def test_relative_target_uses_current_measurement(no_settling):
    table = SimulatedTable(350)
    result = PositionController(table, table).rotate(25, 1, time.monotonic() + 2, relative=True)
    assert result.target_angle == 15


def test_already_aligned_still_stops_and_checks_three_frames(no_settling):
    table = SimulatedTable(90)
    result = PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert result.pulses == 0
    assert table.reads == 3
    assert table.stops == 1


def test_within_tolerance_readings_must_also_stabilize(no_settling):
    table = SimulatedTable(90)
    values = iter([90.8, 89.4, 90.5, 90.2, 90.1, 90.0])
    reads = []
    def measure(_):
        value = next(values)
        reads.append(value)
        return value
    table.read_angle = measure
    result = PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert len(reads) == 5
    assert result.pulses == 0


def test_stationary_angle_must_stay_within_tolerance(no_settling):
    table = SimulatedTable(90)
    values = iter([90, 90, 95, 92, 90, 90, 90])
    table.read_angle = lambda _: next(values)
    result = PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert result.pulses > 0


def test_stall_stops_after_bounded_attempts(no_settling, monkeypatch):
    ticks = iter(i / 1000 for i in range(10000))
    monkeypatch.setattr('aida.hardware.control.time.monotonic', lambda: next(ticks))
    table = SimulatedTable(gain=0)
    with pytest.raises(TurntableError) as error:
        PositionController(table, table).rotate(90, 1, 1.0)
    assert error.value.code == 'stalled'
    # Pulse strength steps up 25 us at a time, capped 300 us from neutral (1500 us), then stalls.
    widths = [width for width, _ in table.pulses]
    assert max(widths) == 1800 and widths == sorted(widths)
    assert len(table.pulses) == 2 * 8 + 5
    assert table.stops == 2


def test_lost_feedback_aborts_without_another_pulse(no_settling):
    table = SimulatedTable()
    def fail(_):
        raise TurntableError('feedback_lost', 'Board hidden')
    table.read_angle = fail
    with pytest.raises(TurntableError, match='Board hidden'):
        PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert table.pulses == []


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_rejects_nonfinite_feedback(value, no_settling):
    table = SimulatedTable()
    table.read_angle = lambda _: value
    with pytest.raises(TurntableError, match='finite'):
        PositionController(table, table).rotate(90, 1, time.monotonic() + 2)


def test_rejects_invalid_control_configuration():
    with pytest.raises(ValueError):
        replace(ControlConfig(), max_pulse_s=1)
    with pytest.raises(ValueError):
        replace(ControlConfig(), positive_deg_per_s=float('nan'))


def test_discovers_reversed_direction_with_small_probe(no_settling):
    table = SimulatedTable(gain=-1)
    events = []
    controller = PositionController(table, table, trace=events.append)
    result = controller.rotate(90, 1, time.monotonic() + 2)
    assert abs(result.error_degrees) <= 1
    assert table.pulses[0][1] <= controller.config.probe_s
    assert controller.profile()['positive_pulse_us'] < 1500
    assert sum(event['event'] == 'polarity_corrected' for event in events) == 1
    # Learned polarity and speeds remain in effect for the next view.
    controller.rotate(180, 1, time.monotonic() + 2)
    assert sum(event['event'] == 'polarity_corrected' for event in events) == 1


def test_deadband_and_asymmetric_realistic_servo(no_settling):
    table = SimulatedTable()
    def pulse(pulse_us, duration, deadline):
        table.pulses.append((pulse_us, duration))
        offset = pulse_us - 1500
        # Fine pulses initially lie in the deadband; opposite directions differ in speed.
        if abs(offset) > 70:
            rate = (offset - 70) * .9 if offset > 0 else (offset + 70) * .5
            table.angle = (table.angle + rate * max(0, duration - .015)) % 360
    table.pulse = pulse
    events = []
    controller = PositionController(table, table, trace=events.append)
    for angle in (90, 180, 270, 0, 355):
        result = controller.rotate(angle, .5, time.monotonic() + 2)
        assert abs(result.error_degrees) <= .5
    assert any(e['event'] == 'deadband_adjusted' for e in events)


def test_transient_feedback_loss_retries_while_stopped(no_settling):
    table = SimulatedTable(90)
    original = table.read_angle
    calls = []
    def read(deadline):
        calls.append(True)
        if len(calls) == 2:
            raise TurntableError('feedback_lost', 'Temporary occlusion')
        return original(deadline)
    table.read_angle = read
    result = PositionController(table, table).rotate(90, 1, time.monotonic() + 2)
    assert result.pulses == 0
    assert len(calls) == 4


def test_expired_deadline_stops_without_driving(no_settling):
    table = SimulatedTable()
    with pytest.raises(TurntableError) as error:
        PositionController(table, table).rotate(90, 1, time.monotonic() - 1)
    assert error.value.code == 'timeout'
    assert not table.pulses
