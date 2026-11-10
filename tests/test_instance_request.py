from threading import Event, Thread
from unittest.mock import Mock

from module.instance_request import NativeRequest


def test_expired_queued_request_cannot_later_change_settings():
    request = NativeRequest([], timeout=0)
    action = Mock(return_value=True)
    assert request.reply(timeout=0) == b'REJECTED'
    request.execute(action)
    action.assert_not_called()


def test_applied_request_is_acknowledged():
    request = NativeRequest([])
    request.execute(lambda: True)
    assert request.reply(timeout=0) == b'OK'


def test_in_progress_request_is_unconfirmed_instead_of_rejected():
    started, release = Event(), Event()
    request = NativeRequest([])

    def action():
        started.set()
        release.wait(2)
        return True

    worker = Thread(target=request.execute, args=(action,))
    worker.start()
    try:
        assert started.wait(1)
        assert request.reply(timeout=0) == b'PENDING'
    finally:
        release.set()
        worker.join(2)
    assert request.reply(timeout=0) == b'OK'
