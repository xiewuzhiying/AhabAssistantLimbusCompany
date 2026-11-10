"""Cooperative interruption at automation I/O and bounded waits."""
import time

from PySide6.QtCore import QThread

from module.my_error.my_error import userStopError


def checkpoint() -> None:
    if QThread.currentThread().isInterruptionRequested():
        raise userStopError('CANCELLED: task cancellation requested')


def sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(.1, remaining))
