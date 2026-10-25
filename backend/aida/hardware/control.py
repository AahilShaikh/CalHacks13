"""Hardware-independent, blocking timed-pulse position controller.

The drive stops each pulse on the microcontroller. Position is always obtained
from the feedback adapter, never inferred from elapsed motor running time.
"""
import math
import time
from dataclasses import dataclass
from typing import Callable, Protocol


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
    probe_s: float = 0.06
    max_step_degrees: float = 12.0
    stall_samples: int = 5
    neutral_us: float | None = None  # servo stop pulse; defaults to the coarse-pulse midpoint

    def __post_init__(self):
        rates = (self.positive_deg_per_s, self.negative_deg_per_s,
                 self.fine_positive_deg_per_s, self.fine_negative_deg_per_s)
        if any(not math.isfinite(x) or x <= 0 for x in rates):
            raise ValueError('Drive rates must be positive finite degrees per second.')
        if not (0 < self.min_pulse_s <= self.max_pulse_s <= 0.5):
            raise ValueError('Pulse durations must satisfy 0 < min <= max <= 0.5 seconds.')
        if not (0 < self.pulse_gain <= 1) or not (0 < self.settle_s <= 5):
            raise ValueError('Pulse gain must be in (0, 1], settling time in (0, 5].')
        if not self.min_pulse_s <= self.probe_s <= self.max_pulse_s:
            raise ValueError('Probe duration must lie within pulse limits.')
        if not 0 < self.max_step_degrees <= 30 or self.stall_samples < 3:
            raise ValueError('Invalid step or stall limit.')
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
                 config: ControlConfig | None = None, trace: Callable | None = None):
        self.drive = drive
        self.feedback = feedback
        self.config = config or ControlConfig()
        self.trace = trace or (lambda event: None)
        c = self.config
        # Retain learned timing and polarity across all views in a scan.
        self.pulses = {(True, False): c.positive_pulse_us, (False, False): c.negative_pulse_us,
                       (True, True): c.fine_positive_pulse_us, (False, True): c.fine_negative_pulse_us}
        self.rates = {(True, False): c.positive_deg_per_s, (False, False): c.negative_deg_per_s,
                      (True, True): c.fine_positive_deg_per_s, (False, True): c.fine_negative_deg_per_s}
        self.learned = set()
        # Midpoint of the coarse pulses; unchanged by polarity swaps and torque increases.
        self.neutral_us = (c.neutral_us if c.neutral_us is not None
                           else (c.positive_pulse_us + c.negative_pulse_us) / 2)
        self.direction_verified = False

    def _emit(self, event, **data):
        self.trace({'event': event, 'time': time.time(), **data})

    def _settle(self, deadline: float, delay: float | None = None):
        delay = self.config.settle_s if delay is None else delay
        if remaining(deadline) <= delay:
            raise TurntableError('timeout', 'Timed out waiting for the turntable to settle.', 504)
        time.sleep(delay)

    def _measure(self, deadline: float) -> float:
        # The servo is stopped while retrying an intermittently occluded board.
        for attempt in range(3):
            remaining(deadline)
            try:
                angle = self.feedback.read_angle(deadline)
                remaining(deadline)
                if not math.isfinite(angle):
                    raise TurntableError('feedback_invalid', 'Angle feedback was not finite.')
                return angle % 360.0
            except TurntableError as exc:
                if exc.code not in ('feedback_lost', 'feedback_invalid') or attempt == 2:
                    raise
                self._emit('feedback_retry', code=exc.code, attempt=attempt + 1)
                self._settle(deadline, 0.1)

    def profile(self) -> dict:
        result = {}
        for positive, direction in ((True, 'positive'), (False, 'negative')):
            for fine, prefix in ((False, ''), (True, 'fine_')):
                result[f'{prefix}{direction}_pulse_us'] = self.pulses[positive, fine]
                result[f'{prefix}{direction}_deg_per_s'] = self.rates[positive, fine]
        return result

    def rotate(self, angle: float, tolerance: float, deadline: float,
               relative: bool = False) -> RotationResult:
        if not math.isfinite(angle) or not math.isfinite(tolerance) or not 0 < tolerance <= 10:
            raise ValueError('Angle must be finite and tolerance must be in (0, 10].')
        self._emit('start', requested_angle=angle, tolerance=tolerance, relative=relative)
        try:
            result = self._rotate(angle, tolerance, deadline, relative)
        except Exception as exc:
            # A caller can use this controller independently of hardware_session.
            try:
                self.drive.stop(time.monotonic() + 0.5)
            except Exception as stop_exc:
                self._emit('error', code='stop_unconfirmed', message=str(stop_exc))
                raise TurntableError('stop_unconfirmed', f'Could not confirm STOP: {stop_exc}') from exc
            self._emit('error', code=getattr(exc, 'code', 'motion_failed'), message=str(exc))
            raise
        self._emit('succeeded', target=result.target_angle, measured=result.measured_angle,
                   error=result.error_degrees, pulses=result.pulses)
        return result

    def _rotate(self, angle, tolerance, deadline, relative):
        c = self.config
        self.drive.stop(deadline)
        self._settle(deadline)
        measured = self._measure(deadline)
        target = (measured + angle if relative else angle) % 360.0
        pulses = 0
        recent_errors = []
        stalled = 0
        no_motion = {}
        while True:
            remaining(deadline)
            error = angle_error(target, measured)
            self._emit('measurement', target=target, measured=measured, error=error)
            if abs(error) <= tolerance:
                recent_errors.append(error)
                recent_errors = recent_errors[-c.stable_samples:]
                if (len(recent_errors) >= c.stable_samples and
                        max(recent_errors) - min(recent_errors) <= tolerance / 2):
                    return RotationResult(target, measured, error, pulses)
                self._settle(deadline)
                measured = self._measure(deadline)
                continue
            recent_errors.clear()
            positive = error > 0
            fine = abs(error) <= c.fine_threshold_degrees
            key = (positive, fine)
            speed = self.rates[key]
            duration = max(c.min_pulse_s, min(c.max_pulse_s,
                c.pulse_gain * min(abs(error), c.max_step_degrees) / speed))
            if key not in self.learned:
                # Never discover an unknown direction/speed with a large move.
                duration = min(duration, c.probe_s * 2 ** min(no_motion.get(key, 0), 2))
            if remaining(deadline) <= duration + c.settle_s:
                raise TurntableError('timeout', 'Insufficient time for another drive pulse.', 504)
            pulse = self.pulses[key]
            self._emit('pulse', pulse_us=pulse, duration_s=duration, fine=fine,
                       positive=positive, estimated_deg_per_s=speed)
            self.drive.pulse(pulse, duration, deadline)
            pulses += 1
            before = measured
            self._settle(deadline)
            measured = self._measure(deadline)
            travel = angle_error(measured, before)
            # A single bounded pulse cannot rotate the table through a quadrant.
            if abs(travel) > 45:
                raise TurntableError('feedback_invalid', 'Implausible angle jump after a bounded pulse.')
            threshold = max(0.12, min(0.3, tolerance * 0.15))
            directed_travel = travel * (1 if positive else -1)
            if directed_travel < -threshold:
                if self.direction_verified:
                    raise TurntableError('direction_mismatch',
                                         'Observed motor direction changed during the move; check board tracking and mechanics.')
                # Camera coordinates and linkage determine polarity, not the wire colour.
                self.pulses = {(not p, f): value for (p, f), value in self.pulses.items()}
                self.rates = {(not p, f): value for (p, f), value in self.rates.items()}
                self.learned = {(not p, f) for p, f in self.learned}
                key = (not positive, fine)
                directed_travel = abs(travel)
                self._emit('polarity_corrected', profile=self.profile())
            if directed_travel > threshold:
                self.direction_verified = True
                observed_rate = directed_travel / duration
                self.rates[key] = (0.3 * self.rates[key] + 0.7 * observed_rate
                                   if key in self.learned else observed_rate)
                self.learned.add(key)
                no_motion[key] = 0
                stalled = 0
            else:
                stalled += 1
                no_motion[key] = no_motion.get(key, 0) + 1
                if fine and no_motion[key] >= 2:
                    # Escape the FS90R deadband in small steps toward the coarse pulse.
                    coarse = self.pulses[positive, False]
                    step = max(-10, min(10, coarse - pulse))
                    if step:
                        self.pulses[key] += step
                        self.learned.discard(key)
                        no_motion[key] = 0
                        self._emit('deadband_adjusted', pulse_us=self.pulses[key], positive=positive)
                elif not fine and no_motion[key] >= 2:
                    # Push through mechanical friction with a stronger (faster) pulse, bounded
                    # to 300 us from the neutral midpoint; once capped, the stall check applies.
                    away = 1 if pulse > self.neutral_us else -1
                    limit = self.neutral_us + away * 300
                    stronger = int(max(1000, min(2000, pulse + away * 25)))
                    if (stronger - limit) * away <= 0 and stronger != pulse:
                        self.pulses[key] = stronger
                        no_motion[key] = 0
                        stalled = 0
                        self._emit('torque_increased', pulse_us=stronger, positive=positive)
                if stalled >= c.stall_samples:
                    raise TurntableError('stalled', 'No measurable turntable movement after repeated bounded pulses. '
                                         'Check servo power, linkage and neutral/deadband calibration.')
            self._emit('travel', before=before, after=measured, travel_degrees=travel,
                       duration_s=duration, profile=self.profile())
