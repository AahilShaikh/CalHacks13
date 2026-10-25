"""Serial acknowledgement matching, timeout and hardware cleanup paths."""
import time
from types import SimpleNamespace

import pytest

from aida.hardware.arduino import ArduinoDrive
from aida.hardware.control import TurntableError
from aida.turntable import HardwareSettings, hardware_session


class SerialConnection:
    def __init__(self, neutral=1500):
        self.replies = []
        self.sent = []
        self.closed = False
        self.neutral = neutral

    def write(self, payload):
        command, token, *arguments = payload.decode().split()
        self.sent.append((command, arguments))
        expected = 'DONE' if command == 'DRIVE' else command
        if command == 'INFO':
            expected = f'INFO 2 {self.neutral} 9 500'
        self.replies.extend([b'READY AIDA_TURNTABLE_V1\n', b'OK stale DONE\n',
                             f'OK {token} {expected}\n'.encode()])
        return len(payload)

    def readline(self, size):
        return self.replies.pop(0) if self.replies else b''

    def reset_input_buffer(self):
        self.replies.clear()

    def close(self):
        self.closed = True


def test_serial_protocol_waits_for_matching_done(monkeypatch):
    serial = SerialConnection()
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', reset_wait_s=0)
    end = time.monotonic() + 3
    drive.connect(end)
    drive.pulse(1600, 0.1, end)
    drive.stop(end)
    drive.close()
    assert serial.sent == [('PING', []), ('STOP', ['1500']), ('INFO', []),
                           ('DRIVE', ['1600', '100', '1500']),
                           ('STOP', ['1500'])]
    assert serial.closed


@pytest.mark.parametrize('neutral', [1450, 1500])
def test_default_drive_commands_follow_configured_neutral(monkeypatch, neutral):
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'fake')
    monkeypatch.setenv('AIDA_TURNTABLE_NEUTRAL_US', str(neutral))
    for name in ('POSITIVE_US', 'NEGATIVE_US', 'FINE_POSITIVE_US', 'FINE_NEGATIVE_US'):
        monkeypatch.delenv(f'AIDA_TURNTABLE_{name}', raising=False)
    settings = HardwareSettings.from_env()
    assert settings.neutral_us == neutral
    assert settings.control.positive_pulse_us == neutral + 100
    assert settings.control.negative_pulse_us == neutral - 100
    assert settings.control.fine_positive_pulse_us == neutral + 60
    assert settings.control.fine_negative_pulse_us == neutral - 60


def test_missing_ack_times_out():
    serial = SerialConnection()
    serial.write = lambda payload: len(payload)
    drive = ArduinoDrive('fake')
    drive.connection = serial
    with pytest.raises(TurntableError) as error:
        drive.stop(time.monotonic() + 0.01)
    assert error.value.code == 'timeout'


def test_firmware_rejection_is_an_error():
    serial = SerialConnection()
    def reject(payload):
        token = payload.decode().split()[1]
        serial.replies = [f'ERR {token} BUSY\n'.encode()]
        return len(payload)
    serial.write = reject
    drive = ArduinoDrive('fake')
    drive.connection = serial
    with pytest.raises(TurntableError) as error:
        drive.pulse(1600, 0.1, time.monotonic() + 3)
    assert error.value.code == 'firmware_error'


def test_firmware_neutral_must_match_host(monkeypatch):
    serial = SerialConnection(neutral=1500)
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', neutral_us=1450, reset_wait_s=0)
    with pytest.raises(TurntableError) as error:
        drive.connect(time.monotonic() + 2)
    assert error.value.code == 'neutral_mismatch'
    assert ('STOP', ['1450']) in serial.sent
    assert not any(command == 'DRIVE' for command, _ in serial.sent)


def test_partial_serial_acknowledgements_are_reassembled(monkeypatch):
    serial = SerialConnection()
    original_read = serial.readline
    remainder = []
    def split_read(size):
        if remainder:
            return remainder.pop()
        line = original_read(size)
        if line:
            remainder.append(line[4:])
            return line[:4]
        return line
    serial.readline = split_read
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', reset_wait_s=0)
    drive.connect(time.monotonic() + 2)
    drive.pulse(1600, .1, time.monotonic() + 2)
    assert drive.firmware_info['startup_neutral_us'] == 1500


def test_old_firmware_is_stopped_before_requiring_update(monkeypatch):
    serial = SerialConnection()
    original = serial.write
    def old_firmware(payload):
        if payload.startswith(b'INFO '):
            token = payload.decode().split()[1]
            serial.replies.append(f'ERR {token} BAD_COMMAND\n'.encode())
            return len(payload)
        return original(payload)
    serial.write = old_firmware
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', reset_wait_s=0)
    with pytest.raises(TurntableError) as error:
        drive.connect(time.monotonic() + 2)
    assert error.value.code == 'firmware_update_required'
    assert serial.sent[-1] == ('STOP', ['1500'])


@pytest.mark.parametrize('stop_fails', [False, True])
def test_cleanup_on_feedback_error(tmp_path, monkeypatch, stop_fails):
    serial = SerialConnection()
    drive = ArduinoDrive('fake')
    drive.connection = serial
    drive.connect = lambda _: None
    stops = []
    def stop(deadline):
        stops.append(deadline)
        if stop_fails:
            raise TurntableError('serial_error', 'USB disconnected')
    drive.stop = stop
    monkeypatch.setattr('aida.turntable.ArduinoDrive', lambda *args: drive)
    closed = []
    monkeypatch.setattr('aida.hardware.vision.Webcam', lambda *_: SimpleNamespace(
        start=lambda: None, close=lambda: closed.append(True)))
    monkeypatch.setattr('aida.hardware.vision.CharucoFeedback', lambda *args: None)
    monkeypatch.setenv('AIDA_TURNTABLE_PORT', 'fake')
    expected = 'stop_unconfirmed' if stop_fails else 'feedback_lost'
    with pytest.raises(TurntableError) as error:
        with hardware_session(HardwareSettings.from_env(), time.monotonic() + 3, False):
            raise TurntableError('feedback_lost', 'Board occluded')
    assert error.value.code == expected
    assert len(stops) == 1
    assert serial.closed
    assert closed == [True]


def test_bootloader_can_drop_first_ping_without_losing_startup(monkeypatch):
    serial = SerialConnection()
    write = serial.write
    sent_pings = []
    def booting(payload):
        if payload.startswith(b'PING '):
            sent_pings.append(payload)
            if len(sent_pings) == 1:
                return len(payload)  # Bootloader swallowed the first command.
        return write(payload)
    serial.write = booting
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', reset_wait_s=0)
    drive.connect(time.monotonic() + 3)
    assert len(sent_pings) == 2 and sent_pings[0] == sent_pings[1]
    assert not any(command == 'DRIVE' for command, _ in serial.sent)


def test_experimental_v3_firmware_is_rejected_before_motion(monkeypatch):
    serial = SerialConnection(neutral=1450)
    write = serial.write
    def v3(payload):
        if payload.startswith(b'INFO '):
            token = payload.decode().split()[1]
            serial.replies.append(f'OK {token} INFO 3 1450 9 15000\n'.encode())
            return len(payload)
        return write(payload)
    serial.write = v3
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    drive = ArduinoDrive('fake', neutral_us=1450, reset_wait_s=0)
    with pytest.raises(TurntableError) as error:
        drive.connect(time.monotonic() + 2)
    assert error.value.code == 'firmware_update_required'
    assert 'firmware V2 is required' in str(error.value)
    assert not any(command == 'DRIVE' for command, _ in serial.sent)


def test_serial_timeout_names_the_unacknowledged_command():
    serial = SerialConnection()
    serial.write = lambda data: len(data)
    drive = ArduinoDrive('COM14')
    drive.connection = serial
    with pytest.raises(TurntableError, match='Arduino STOP acknowledgement timed out on COM14'):
        drive.stop(time.monotonic() + .01)


def test_handshake_does_not_reconfigure_unchanged_windows_serial_timeouts(monkeypatch):
    class Connection(SerialConnection):
        @property
        def timeout(self): return .1
        @timeout.setter
        def timeout(self, value): raise AssertionError('Unnecessary port reconfiguration')
        @property
        def write_timeout(self): return .2
        @write_timeout.setter
        def write_timeout(self, value): raise AssertionError('Unnecessary port reconfiguration')
    serial = Connection()
    monkeypatch.setattr('serial.Serial', lambda *args, **kwargs: serial)
    ArduinoDrive('fake', reset_wait_s=0).connect(time.monotonic() + 2)


def test_rejected_drive_reports_wire_arguments_and_firmware_without_retry():
    serial = SerialConnection(neutral=1450)
    writes = []
    def reject(payload):
        writes.append(payload)
        token = payload.decode().split()[1]
        serial.replies = [f'ERR {token} BAD_COMMAND\n'.encode()]
        return len(payload)
    serial.write = reject
    drive = ArduinoDrive('COM14', neutral_us=1450)
    drive.connection = serial
    drive.firmware_info = {'version': 2, 'startup_neutral_us': 1450, 'pin':9, 'max_pulse_ms':500}
    with pytest.raises(TurntableError) as error:
        drive.pulse(1390, .06, time.monotonic()+1)
    message = str(error.value)
    assert 'Arduino rejected DRIVE 1390 60 1450' in message
    assert 'firmware V2' in message and 'BAD_COMMAND' in message
    assert len(writes) == 1
