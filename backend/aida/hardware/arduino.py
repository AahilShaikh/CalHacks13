"""Request-correlated serial transport; importing this module opens no devices."""
import math
import time
import uuid

from .control import TurntableError, remaining


class ArduinoDrive:
    def __init__(self, port: str, neutral_us: int = 1500, baud: int = 115200,
                 reset_wait_s: float = 2.0):
        self.port = port
        self.neutral_us = neutral_us
        self.baud = baud
        self.reset_wait_s = reset_wait_s
        self.connection = None
        self.firmware_info = None
        if not port or not 1000 <= neutral_us <= 2000:
            raise ValueError('A serial port and neutral pulse in [1000, 2000] are required.')

    def connect(self, deadline: float):
        try:
            import serial

            remaining(deadline)
            self.connection = serial.Serial(
                self.port, self.baud, timeout=0.1, write_timeout=0.2, exclusive=True)
            # Opening an Uno-style serial port resets the board; wait for its bootloader.
            if remaining(deadline) <= self.reset_wait_s:
                raise TurntableError('timeout', 'Insufficient time for Arduino startup.', 504)
            time.sleep(self.reset_wait_s)
            self.connection.reset_input_buffer()
            self._command('PING', '', 'PING', deadline, retry_interval_s=0.5)
            self.stop(deadline)
            try:
                info = self._command('INFO', '', 'INFO', deadline).split()
            except TurntableError as exc:
                if exc.code != 'firmware_error':
                    raise
                raise TurntableError('firmware_update_required',
                                     'Upload firmware/turntable/turntable.ino; firmware INFO is required.') from exc
            if len(info) != 5 or info[1] != '2' or any(not value.isdecimal() for value in info[2:]):
                raise TurntableError('firmware_update_required', f'Unsupported turntable firmware INFO response: {" ".join(info)}. Upload the repository turntable sketch; firmware V2 is required.')
            self.firmware_info = {'version': int(info[1]), 'startup_neutral_us': int(info[2]),
                                  'pin': int(info[3]), 'max_pulse_ms': int(info[4])}
            if self.firmware_info['startup_neutral_us'] != self.neutral_us:
                raise TurntableError('neutral_mismatch',
                                     'Firmware startup neutral differs from the configured stop pulse; '
                                     'update STARTUP_NEUTRAL_US and re-upload to avoid movement on USB reset.')
        except TurntableError:
            raise
        except Exception as exc:
            raise TurntableError('serial_unavailable', f'Cannot connect to Arduino: {exc}') from exc

    def _command(self, command: str, arguments: str, expected: str, deadline: float,
                 retry_interval_s: float | None = None):
        if self.connection is None:
            raise TurntableError('serial_unavailable', 'Arduino is not connected.')
        token = uuid.uuid4().hex[:12]
        payload = f'{command} {token} {arguments}'.strip().encode('ascii') + b'\n'
        last_response = None
        try:
            write_timeout = min(0.2, remaining(deadline))
            if getattr(self.connection, 'write_timeout', write_timeout) != write_timeout:
                self.connection.write_timeout = write_timeout
            if self.connection.write(payload) != len(payload):
                raise TurntableError('serial_error', 'Incomplete command sent to Arduino.')
            pending = b''
            next_retry = time.monotonic() + retry_interval_s if retry_interval_s else None
            while True:
                if next_retry is not None and time.monotonic() >= next_retry:
                    # Only connect() requests retries, for the non-mutating PING handshake.
                    remaining(deadline)
                    if self.connection.write(payload) != len(payload):
                        raise TurntableError('serial_error', 'Incomplete PING sent to Arduino.')
                    next_retry = time.monotonic() + retry_interval_s
                read_timeout = min(0.1, remaining(deadline))
                # pySerial reconfigures the Windows port even when assigning the same
                # timeout. Avoid repeatedly resetting its DTR/RTS and UART state.
                if getattr(self.connection, 'timeout', read_timeout) != read_timeout:
                    self.connection.timeout = read_timeout
                pending += self.connection.readline(128)
                if len(pending) > 256:
                    raise TurntableError('serial_error', 'Oversized Arduino response.')
                if not pending.endswith(b'\n'):
                    continue
                response = pending.decode('ascii', errors='replace').strip()
                last_response = response
                pending = b''
                prefix = f'OK {token} '
                if response.startswith(prefix):
                    body = response[len(prefix):]
                    if body == expected or (expected == 'INFO' and body.startswith('INFO ')):
                        return body
                if response.startswith(f'ERR {token} '):
                    version = f' (firmware V{self.firmware_info["version"]})' if self.firmware_info else ''
                    request = f'{command} {arguments}'.strip()
                    hint = (' The installed firmware rejected the DRIVE format or its arguments; '
                            'check its command handler against firmware/turntable/turntable.ino.'
                            if command == 'DRIVE' and response.endswith('BAD_COMMAND') else '')
                    raise TurntableError('firmware_error',
                        f'Arduino rejected {request}{version}: {response}.' + hint)
                # Ignore startup banners and late replies to previous commands.
        except TurntableError as exc:
            if exc.code == 'timeout':
                observed = f' Last response: {last_response}' if last_response else ' No response was received.'
                raise TurntableError('timeout', f'Arduino {command} acknowledgement timed out on {self.port}.'
                                     + observed, 504) from exc
            raise
        except Exception as exc:
            raise TurntableError('serial_error', f'Arduino communication failed: {exc}') from exc

    def pulse(self, pulse_us: int, duration_s: float, deadline: float):
        duration_ms = math.ceil(duration_s * 1000)
        if not 1000 <= pulse_us <= 2000 or not 1 <= duration_ms <= 500:
            raise ValueError('Drive pulse outside firmware limits.')
        self._command('DRIVE', f'{pulse_us} {duration_ms} {self.neutral_us}', 'DONE', deadline)

    def stop(self, deadline: float):
        self._command('STOP', str(self.neutral_us), 'STOP', deadline)

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
