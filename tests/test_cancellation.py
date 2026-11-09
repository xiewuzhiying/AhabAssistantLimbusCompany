import threading

from PySide6.QtCore import QThread

from module.cancellation import sleep
from module.my_error.my_error import userStopError


def test_cancellation_unwinds_lock_and_cleanup():
    lock = threading.RLock()
    ready = threading.Event()
    cleaned = threading.Event()
    class Worker(QThread):
        def run(self):
            try:
                with lock:
                    ready.set()
                    sleep(30)
            except userStopError:
                pass
            finally:
                cleaned.set()
    worker = Worker()
    worker.start()
    assert ready.wait(2)
    worker.requestInterruption()
    assert worker.wait(2000)
    assert cleaned.is_set()
    assert lock.acquire(timeout=.1)
    lock.release()
