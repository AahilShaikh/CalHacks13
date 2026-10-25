"""Hardware-independent, blocking timed-pulse position controller.

The drive stops each pulse on the microcontroller. Position is always obtained
from the feedback adapter, never inferred from elapsed motor running time.
"""
import math
import time
from dataclasses import dataclass
from typing import Protocol


class TurntableError(RuntimeError):
    def __init__(self, code: str, message: str, http_status: int = 503):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TurntableError('timeout', 'Turntable operation timed out.', 504)
    return value


def angle_error(target: float, measured: float) -> float:
    """Signed shortest displacement, in [-180, 180)."""
    return (target - measured + 180.0) % 360.0 - 180.0


class TimedDrive(Protocol):
    def pulse(self, pulse_us: int, duration_s: float, deadline: float) -> None: ...
    def stop(self, deadline: float) -> None: ...


class AngleFeedback(Protocol):
    def read_angle(self, deadline: float) -> float: ...


@dataclass(frozen=True)
class ControlConfig:
    positive_pulse_us: int = 1600
    negative_pulse_us: int = 1400
    positive_deg_per_s: float = 90.0
    negative_deg_per_s: float = 90.0
    fine_positive_pulse_us: int = 1560
    fine_negative_pulse_us: int = 1440
    fine_positive_deg_per_s: float = 30.0
    fine_negative_deg_per_s: float = 30.0
    fine_threshold_degrees: float = 8.0
    min_pulse_s: float = 0.02
    max_pulse_s: float = 0.25
    pulse_gain: float = 0.6
    settle_s: float = 0.25
    stable_samples: int = 3

    def __post_init__(self):
        rates = (self.positive_deg_per_s, self.negative_deg_per_s,
                 self.fine_positive_deg_per_s, self.fine_negative_deg_per_s)
        if any(not math.isfinite(x) or x <= 0 for x in rates):
            raise ValueError('Drive rates must be positive finite degrees per second.')
        if not (0 < self.min_pulse_s <= self.max_pulse_s <= 0.5):
            raise ValueError('Pulse durations must satisfy 0 < min <= max <= 0.5 seconds.')
        if not (0 < self.pulse_gain <= 1) or not (0 < self.settle_s <= 5):
            raise ValueError('Pulse gain must be in (0, 1], settling time in (0, 5].')
        if self.stable_samples < 2:
            raise ValueError('At least two stable feedback samples are required.')
        if not math.isfinite(self.fine_threshold_degrees) or not 0 < self.fine_threshold_degrees <= 180:
            raise ValueError('Fine threshold must be in (0, 180] degrees.')
        if any(not 1000 <= x <= 2000 for x in
               (self.positive_pulse_us, self.negative_pulse_us,
                self.fine_positive_pulse_us, self.fine_negative_pulse_us)):
            raise ValueError('Servo pulses must be between 1000 and 2000 microseconds.')


@dataclass(frozen=True)
class RotationResult:
    target_angle: float
    measured_angle: float
    error_degrees: float
    pulses: int


class PositionController:
    def __init__(self, drive: TimedDrive, feedback: AngleFeedback,
                 config: ControlConfig | None = None):
        self.drive = drive
        self.feedback = feedback
        self.config = config or ControlConfig()

    def _settle(self, deadline: float):
        delay = self.config.settle_s
        if remaining(deadline) <= delay:
            raise TurntableError('timeout', 'Timed out waiting for the turntable to settle.', 504)
        time.sleep(delay)

    def _measure(self, deadline: float) -> float:
        remaining(deadline)
        angle = self.feedback.read_angle(deadline)
        remaining(deadline)
        if not math.isfinite(angle):
            raise TurntableError('feedback_invalid', 'Angle feedback was not finite.')
        return angle % 360.0

    def rotate(self, angle: float, tolerance: float, deadline: float,
               relative: bool = False) -> RotationResult:
        if not math.isfinite(angle) or not math.isfinite(tolerance) or not 0 < tolerance <= 10:
            raise ValueError('Angle must be finite and tolerance must be in (0, 10].')
        # A STOP acknowledgement is required even for an already-aligned target.
        self.drive.stop(deadline)
        self._settle(deadline)
        measured = self._measure(deadline)
        target = (measured + angle if relative else angle) % 360.0
        stable = pulses = 0
        recent_errors = []
        rates = {(True, False): self.config.positive_deg_per_s,
                 (False, False): self.config.negative_deg_per_s,
                 (True, True): self.config.fine_positive_deg_per_s,
                 (False, True): self.config.fine_negative_deg_per_s}
        while True:
            error = angle_error(target, measured)
            if abs(error) <= tolerance:
                recent_errors.append(error)
                recent_errors = recent_errors[-self.config.stable_samples:]
                stable = len(recent_errors)
                if (stable >= self.config.stable_samples and
                        max(recent_errors) - min(recent_errors) <= tolerance / 2):
                    return RotationResult(target, measured, error, pulses)
            else:
                stable = 0
                recent_errors.clear()
                positive = error > 0
                fine = abs(error) <= self.config.fine_threshold_degrees
                speed = rates[positive, fine]
                duration = max(self.config.min_pulse_s, min(
                    self.config.max_pulse_s, self.config.pulse_gain * abs(error) / speed))
                if remaining(deadline) <= duration + self.config.settle_s:
                    raise TurntableError('timeout', 'Insufficient time for another drive pulse.', 504)
                if fine:
                    pulse = (self.config.fine_positive_pulse_us if positive else
                             self.config.fine_negative_pulse_us)
                else:
                    pulse = self.config.positive_pulse_us if positive else self.config.negative_pulse_us
                self.drive.pulse(pulse, duration, deadline)
                pulses += 1
                before_pulse = measured
            # All measurements happen with the drive stopped, including final checks.
            self._settle(deadline)
            measured = self._measure(deadline)
            if stable == 0:
                displacement = angle_error(measured, before_pulse) * (1 if positive else -1)
                if displacement < -max(0.5, tolerance):
                    raise TurntableError('direction_mismatch',
                                         'Drive direction is reversed. Swap POSITIVE_US and NEGATIVE_US.')
                # Adapt timing to measured travel, including coast/load effects. Tiny
                # changes are dominated by camera noise and should not retune speed.
                if displacement > max(0.5, tolerance):
                    observed_rate = displacement / duration
                    rates[positive, fine] = 0.5 * speed + 0.5 * observed_rate
