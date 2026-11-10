"""Bounded, cancellable transport for a read-only preview capture process."""
import os
import selectors
import struct
import subprocess
import sys
import time
from pathlib import Path


class PreviewStream:
    def __init__(self, display):
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'scripts.linux.preview_frame', '--display', display, '--stream'],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def _read(self, size, deadline, interrupted):
        data = bytearray()
        while len(data) < size:
            if interrupted():
                raise InterruptedError('Preview closed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Preview capture stalled')
            if not self.selector.select(min(.05, remaining)):
                continue
            chunk = os.read(self.process.stdout.fileno(), size - len(data))
            if not chunk:
                raise EOFError('Preview capture exited')
            data.extend(chunk)
        return bytes(data)

    def read_frame(self, interrupted=lambda: False, timeout=5):
        deadline = time.monotonic() + timeout
        self.process.stdin.write(b'f')
        width, height = struct.unpack('!II', self._read(8, deadline, interrupted))
        if not (0 < width <= 960 and 0 < height <= 540):
            raise ValueError('Invalid preview frame size')
        return width, height, self._read(width * height * 3, deadline, interrupted)

    def close(self):
        # Kill only our capture helper: it may be blocked inside Xlib.
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
