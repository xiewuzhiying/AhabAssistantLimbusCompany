"""Failure storms must not turn the soak recorder into an image-writing loop."""

import logging
import json
import os
import subprocess
import sys
from unittest.mock import Mock

from scripts.diagnostics.run_mirror_soak import IncidentLog


def test_repeated_errors_preserve_messages_but_bound_expensive_evidence():
    elapsed = 0.0
    event = Mock()
    snapshot = Mock()
    handler = IncidentLog(event, snapshot, clock=lambda: elapsed)
    record = logging.LogRecord("soak-test", logging.ERROR, __file__, 1, "closed X11 connection", (), None)
    for _ in range(1000):
        handler.handle(record)
    assert event.call_count == 1000
    assert snapshot.call_count == 1
    elapsed = 10.0
    handler.handle(record)
    assert snapshot.call_count == 2


def test_recorder_exits_when_worker_ignores_stop(tmp_path):
    # Exercise the complete Qt supervisor in a child process. The fake worker
    # deliberately ignores cancellation; no game or X11 connection is used.
    code = r'''
import logging, sys, time
from types import ModuleType, SimpleNamespace
from PySide6.QtCore import QObject, QThread, Signal
from scripts.diagnostics.run_mirror_soak import main

def stub(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module

class Mediator(QObject):
    mirror_signal = Signal(int, int)

class Worker(QThread):
    def run(self):
        time.sleep(60)

cfg = SimpleNamespace(normalize_and_sync_team_state=lambda **kw: None,
    teams_active_queue=[1], mirror=True, set_mirror_count=5, daily_task=False,
    get_reward=False, buy_enkephalin=False, infinite_dungeons=False,
    config=SimpleNamespace(model_dump=lambda: {}))
stub('module')
stub('module.config', cfg=cfg)
stub('module.logger', log=logging.getLogger('soak-test'))
stub('app', mediator=Mediator())
stub('module.automation', auto=SimpleNamespace(init_input=lambda: None, input_handler=SimpleNamespace()))
stub('module.automation.screenshot', ScreenShot=type('ScreenShot', (), {'take_screenshot': staticmethod(lambda gray=True: None)}))
stub('module.game_and_screen', screen=SimpleNamespace(handle=SimpleNamespace()))
stub('module.game_and_screen.gamescope_launcher', GamescopeLauncher=lambda: SimpleNamespace(process=None, _compat_data=None, owned_pids=lambda: [], terminate=lambda: None))
stub('module.my_error')
stub('module.my_error.my_error', userStopError=type('UserStopError', (Exception,), {}))
stub('tasks')
scheme = stub('tasks.base.script_task_scheme', my_script_task=Worker, onetime_mir_process=lambda *a: True)
stub('tasks.base', script_task_scheme=scheme)
stub('tasks.base.retry_monitor', retry_monitor=SimpleNamespace(stop=lambda: None))
import psutil
psutil.process_iter = lambda *a: []
sys.exit(main())
'''
    result = subprocess.run(
        [sys.executable, "-c", code, "--output", str(tmp_path / "run"), "--hours", "0"],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=18,
    )
    assert result.returncode == 1, result.stderr
    summary = json.loads((tmp_path / "run/summary.json").read_text())
    assert summary["reason"] == "time-limit"
    assert summary["worker_shutdown_timeout"] is True
    assert summary["success"] is False
