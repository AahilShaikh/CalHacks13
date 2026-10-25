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


def test_stall_times_out(no_settling, monkeypatch):
    ticks = iter(i / 1000 for i in range(10000))
    monkeypatch.setattr('aida.hardware.control.time.monotonic', lambda: next(ticks))
    table = SimulatedTable(gain=0)
    with pytest.raises(TurntableError, match='time') as error:
        PositionController(table, table).rotate(90, 1, 1.0)
    assert error.value.code == 'timeout'
    assert table.pulses


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
