"""Cancellation must unwind business retries and release their locks."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from lifecycle_helpers import load_source
from PySide6.QtCore import QThread

from module.cancellation import checkpoint, sleep
from module.my_error.my_error import userStopError


def test_mirror_cancel_unwinds_without_retry_or_error_log():
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Mirror:
        def __init__(self, *args):
            pass

        def run(self):
            calls.append(1)
            entered.set()
            assert release.wait(3)
            checkpoint()
            return False

    team = SimpleNamespace(fixed_team_use=False)
    cfg = SimpleNamespace(set_mirror_count=1, infinite_dungeons=False, save_rewards=False,
                          hard_mirror=False, auto_hard_mirror=False, teams_active_queue=[1],
                          config=SimpleNamespace(teams={'1': team}),
                          normalize_and_sync_team_state=Mock(), get_value=lambda key: [True])
    ns = dict(cfg=cfg, log=Mock(), Mirror=Mirror, mediator=Mock(),
              checkpoint=checkpoint, userStopError=userStopError)
    load_source('tasks/base/script_task_scheme.py', ns, functions={'Mirror_task', 'onetime_mir_process'})

    class Worker(QThread):
        cancelled = False

        def run(self):
            try:
                ns['Mirror_task']()
            except userStopError:
                self.cancelled = True

    worker = Worker()
    worker.start()
    try:
        assert entered.wait(3)
        worker.requestInterruption()
    finally:
        release.set()
        assert worker.wait(3000)
    assert worker.cancelled
    assert calls == [1]
    ns['log'].exception.assert_not_called()


def test_screenshot_cancel_during_failed_capture_does_not_retry():
    entered, release = threading.Event(), threading.Event()

    def capture(gray):
        entered.set()
        assert release.wait(3)
        raise OSError('display stopped')

    ns = dict(checkpoint=checkpoint, sleep=sleep, userStopError=userStopError,
              time=time, cfg=SimpleNamespace(screenshot_interval=.001),
              ScreenShot=SimpleNamespace(take_screenshot=Mock(side_effect=capture)), log=Mock())
    cls = load_source('module/automation/automation.py', ns, class_name='Automation',
                      methods={'take_screenshot'}, base=object)
    auto = cls()
    auto.last_screenshot_time = 0
    auto._screenshot_lock = threading.RLock()

    class Worker(QThread):
        cancelled = False

        def run(self):
            try:
                auto.take_screenshot()
            except userStopError:
                self.cancelled = True

    worker = Worker()
    worker.start()
    try:
        assert entered.wait(3)
        worker.requestInterruption()
    finally:
        release.set()
        assert worker.wait(3000)
    assert worker.cancelled
    ns['ScreenShot'].take_screenshot.assert_called_once()





def test_cancel_interrupts_hdr_acknowledgement_wait(monkeypatch):
    import sys

    import pytest

    monkeypatch.setitem(sys.modules, 'win32api', SimpleNamespace(MonitorFromWindow=lambda *args: 1))
    monkeypatch.setitem(sys.modules, 'win32con', SimpleNamespace(MONITOR_DEFAULTTONEAREST=2))
    entered = threading.Event()
    ns = dict(IS_WINDOWS=True, cfg=SimpleNamespace(simulator=False, get_value=lambda *args: True),
              screen=SimpleNamespace(handle=SimpleNamespace(hwnd=1)), log=Mock(),
              get_monitor_hdr_info=lambda *args: SimpleNamespace(hdr_enabled=True),
              Event=threading.Event, checkpoint=checkpoint, mediator=Mock())
    load_source('tasks/base/script_task_scheme.py', ns, functions={'_warn_if_game_monitor_hdr_enabled'})
    ns['mediator'].hdr_warning.emit.side_effect = lambda event: entered.set()

    class Worker(QThread):
        cancelled = False
        error = None

        def run(self):
            try:
                ns['_warn_if_game_monitor_hdr_enabled']()
            except userStopError:
                self.cancelled = True
            except Exception as exc:
                self.error = exc

    worker = Worker()
    worker.start()
    try:
        if not entered.wait(2):
            pytest.fail(f'warning did not enter wait: {worker.error}')
        worker.requestInterruption()
    finally:
        worker.requestInterruption()
        assert worker.wait(3000)
    assert worker.cancelled


def test_stop_during_monitor_join_skips_completion_actions():
    from datetime import datetime
    from time import time

    entered, release = threading.Event(), threading.Event()

    def join_monitor():
        entered.set()
        assert release.wait(3)

    cfg = SimpleNamespace(skip_enkephalin=False, simulator=True, resonate_with_Ahab=False,
                          daily_task=False, get_reward=False, buy_enkephalin=False,
                          mirror=False, set_reduce_miscontact=False)
    auto = Mock()
    auto.click_element.return_value = False
    completion = Mock(return_value=True)
    ns = dict(time=time, datetime=datetime, cfg=cfg, auto=auto, log=Mock(),
              init_game=Mock(), _warn_if_game_monitor_hdr_enabled=Mock(), path_manager=Mock(),
              retry_monitor=Mock(), checkpoint=checkpoint, IS_LINUX=True, IS_WINDOWS=False,
              get_after_completion_config=Mock(return_value=([], 'none')),
              execute_after_completion=completion, send_toast=Mock(), TemplateToast=Mock(),
              QT_TRANSLATE_NOOP=lambda *args: args[-1])
    ns['retry_monitor'].stop.side_effect = join_monitor
    load_source('tasks/base/script_task_scheme.py', ns, functions={'script_task'})

    class Worker(QThread):
        cancelled = False

        def run(self):
            try:
                ns['script_task']()
            except userStopError:
                self.cancelled = True

    worker = Worker()
    worker.start()
    try:
        assert entered.wait(3)
        worker.requestInterruption()
    finally:
        release.set()
        assert worker.wait(3000)
    assert worker.cancelled
    completion.assert_not_called()
