"""Monitor ownership survives blocked I/O and stop/join timeouts."""
import threading
import time
from unittest.mock import Mock

from lifecycle_helpers import load_source

from module.cancellation import checkpoint


def make_monitor():
    ns = dict(threading=threading, time=time, auto=Mock(), log=Mock(), checkpoint=checkpoint)
    cls = load_source('tasks/base/retry_monitor.py', ns, class_name='RetryMonitor')
    monitor = cls(poll_interval=.001, click_cooldown=0)
    monitor._load_templates = lambda: (object(),)
    return monitor, ns['auto'], ns['log']


def test_monitor_timed_out_stop_retains_run_until_exit():
    monitor, auto, log = make_monitor()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def capture(**kwargs):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return object()

    auto.take_monitor_screenshot.side_effect = capture
    monitor._find_retry_button = Mock(return_value=(20, 30))
    monitor.start()
    old = monitor._thread
    try:
        assert entered.wait(3)
        assert not monitor.stop(timeout=.01)
        assert monitor.is_running()
        monitor.start()
        assert monitor._thread is old
        assert monitor._stop_event.is_set()
    finally:
        release.set()
        assert monitor.stop(timeout=3)
    assert len(calls) == 1
    auto.monitor_mouse_click.assert_not_called()
    log.exception.assert_not_called()
    assert not monitor.is_running()
    monitor.start()
    assert monitor._thread is not old
    assert monitor.stop(timeout=3)


def test_monitor_cannot_click_after_waiting_for_input_lock():
    cls = load_source('module/automation/automation.py', {}, class_name='Automation',
                      methods={'monitor_mouse_click'}, base=object)
    auto = cls()
    auto._input_lock = threading.Lock()
    auto.input_handler = Mock()
    cancelled = threading.Event()
    entered = threading.Event()
    result = []

    def click():
        entered.set()
        result.append(auto.monitor_mouse_click(1, 2, cancelled=cancelled.is_set))

    auto._input_lock.acquire()
    worker = threading.Thread(target=click)
    worker.start()
    try:
        assert entered.wait(3)
        cancelled.set()
    finally:
        auto._input_lock.release()
        worker.join(3)
    assert not worker.is_alive()
    assert result == [False]
    auto.input_handler.mouse_click.assert_not_called()


