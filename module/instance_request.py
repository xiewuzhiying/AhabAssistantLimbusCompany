"""Acknowledge profile requests without applying abandoned queued requests."""
import threading
import time
from collections.abc import Callable


class NativeRequest:
    def __init__(self, args: list[str], timeout: float = 10):
        self.args = args
        self.deadline = time.monotonic() + timeout
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.state = 'queued'
        self.ok = False

    def execute(self, action: Callable[[], bool]) -> None:
        with self.lock:
            if self.state != 'queued' or time.monotonic() >= self.deadline:
                self.state = 'cancelled'
                self.done.set()
                return
            self.state = 'applying'
        try:
            self.ok = action()
        finally:
            with self.lock:
                self.state = 'finished'
                self.done.set()

    def reply(self, timeout: float = 10) -> bytes:
        self.done.wait(timeout)
        with self.lock:
            if self.state == 'queued':
                self.state = 'cancelled'
                self.done.set()
            if self.state == 'applying':
                # An operation already in progress cannot be cancelled safely.
                return b'PENDING'
            return b'OK' if self.ok else b'REJECTED'
