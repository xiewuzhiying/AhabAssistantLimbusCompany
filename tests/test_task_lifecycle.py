"""Run production lifecycle methods with real threads and isolated I/O collaborators.

AST loading keeps global configuration, OCR models and live game input out of
these tests. Only construction is substituted; the exercised methods are unmodified.
"""
import ast
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from lifecycle_helpers import load_source
from PySide6.QtCore import QMutex, QThread


def make_farming(worker, window):
    ns = dict(log=Mock(), mediator=Mock(), auto=Mock(), IS_WINDOWS=True)
    cls = load_source('app/farming_interface.py', ns, class_name='FarmingInterfaceLeft', base=object,
                      methods={'start_and_stop_tasks', 'stop_script', '_on_script_finished', 'stop_AALC'})
    ui = cls()
    ui.my_script = worker
    ui.window = lambda: window
    ui.parent = Mock()
    ui.link_start_button = Mock()
    ui.link_start_button.get_text.return_value = 'S t o p !'
    ui.pause_resume_button = Mock()
    ui._enable_setting = Mock()
    ui.reset_pause_resume_button = Mock()
    ui.after_completion_selector = Mock()
    return ui


def test_stop_signals_once_and_keeps_settings_disabled_until_finished():
    worker = Mock()
    worker.isRunning.return_value = True
    worker.isInterruptionRequested.side_effect = [False, True]
    worker.exit_requested = False
    ui = make_farming(worker, SimpleNamespace(_closing=False))
    ui.start_and_stop_tasks()
    ui.start_and_stop_tasks()
    worker.request_stop.assert_called_once()
    ui._enable_setting.assert_not_called()
    ui.link_start_button.setEnabled.assert_called_once_with(False)
    ui._on_script_finished()
    ui._enable_setting.assert_called_once()
    ui.link_start_button.setEnabled.assert_called_with(True)


def test_completion_exit_uses_window_shutdown_before_reenabling_settings():
    worker = SimpleNamespace(exit_requested=True)
    window = SimpleNamespace(_closing=False)
    window.request_exit = Mock(side_effect=lambda: setattr(window, '_closing', True))
    ui = make_farming(worker, window)
    ui._on_script_finished()
    window.request_exit.assert_called_once()
    ui._enable_setting.assert_not_called()


def test_script_finished_follows_keep_awake_and_all_cleanup():
    cleanup_entered, cleanup_release = threading.Event(), threading.Event()
    calls = []

    def kill_game():
        calls.append('kill')
        cleanup_entered.set()
        assert cleanup_release.wait(3)

    ns = dict(QThread=QThread, QMutex=QMutex, Lock=threading.Lock, IS_LINUX=True,
              cfg=SimpleNamespace(simulator=False, get_value=lambda *args: True), log=Mock(),
              retry_monitor=Mock(), auto=Mock(), game_process=SimpleNamespace(kill_game=kill_game),
              script_task=lambda: 0, mediator=Mock(),
              apply_power_keep_awake=lambda value: calls.append(('awake', value)))
    # Exception classes normally imported by the worker module.
    tree = ast.parse(Path('tasks/base/script_task_scheme.py').read_text())
    imports = next(n for n in tree.body if isinstance(n, ast.ImportFrom)
                   and n.module == 'module.my_error.my_error')
    import module.my_error.my_error as errors
    ns.update({alias.name: getattr(errors, alias.name) for alias in imports.names})
    cls = load_source('tasks/base/script_task_scheme.py', ns, class_name='my_script_task')
    worker = cls()
    worker.msleep = lambda _: None
    worker.start()
    try:
        assert cleanup_entered.wait(3)
        assert worker.isRunning()
        assert calls == [('awake', True), ('awake', False), 'kill']
        ns['mediator'].kill_signal.emit.assert_not_called()
    finally:
        cleanup_release.set()
        assert worker.wait(3000)
    assert worker.exit_requested
    ns['retry_monitor'].stop.assert_called_once_with(timeout=None)
    assert worker.mutex.tryLock()
    worker.mutex.unlock()


def test_window_lifecycle_in_separate_qt_process():
    # Other tests create QCoreApplication. Qt cannot upgrade it to QApplication.
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'tests/_task_lifecycle_gui.py'],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_stop_returns_before_session_reap_and_joins_it_before_finished(monkeypatch):
    import module.my_error.my_error as errors
    from module.cancellation import sleep
    from module.game_and_screen import gamescope_launcher

    entered, reaping, release = threading.Event(), threading.Event(), threading.Event()
    terminators = []

    def terminate_async():
        def reap():
            reaping.set()
            release.wait(3)
        thread = threading.Thread(target=reap)
        thread.start()
        terminators.append(thread)
        return thread

    launcher = SimpleNamespace(terminate_async=Mock(side_effect=terminate_async))
    monkeypatch.setattr(gamescope_launcher, 'GamescopeLauncher', lambda: launcher)
    cfg = SimpleNamespace(simulator=False)
    ns = dict(QThread=QThread, QMutex=QMutex, Lock=threading.Lock, IS_LINUX=True,
              cfg=cfg, log=Mock(), retry_monitor=Mock(), auto=Mock(), game_process=Mock())
    ns.update({name: value for name, value in vars(errors).items() if isinstance(value, type)})
    cls = load_source('tasks/base/script_task_scheme.py', ns, class_name='my_script_task')
    worker = cls()

    def work():
        entered.set()
        sleep(30)

    worker._run = work
    worker.start()
    try:
        assert entered.wait(3)
        cfg.simulator = True  # Backend ownership must not follow mutable settings.
        worker.request_stop()
        worker.request_stop()
        assert reaping.wait(3)
        assert worker.isRunning()
        assert not worker.wait(50)
        launcher.terminate_async.assert_called_once()
        ns['game_process'].kill_game.assert_not_called()
    finally:
        release.set()
        assert worker.wait(3000)
        for thread in terminators:
            thread.join(3)
            assert not thread.is_alive()
    ns['game_process'].kill_game.assert_called_once()
    ns['retry_monitor'].stop.assert_called_once_with(timeout=None)
    ns['log'].exception.assert_not_called()


def test_tools_registration_reentry_is_owned_until_signals_are_connected():
    ns = dict()
    cls = load_source('app/tools_interface.py', ns, class_name='ToolsInterface', base=object,
                      methods={'_tool_start'})
    ui = cls()
    ui._starting_tools = set()
    ui.window = lambda: SimpleNamespace(_closing=False)
    card = Mock()
    calls = []

    def open_tool(name, button):
        calls.append(name)
        assert name in ui._starting_tools
        # Simulate a nested event loop delivering the same Run action.
        ui._tool_start(name, button)

    ui._open_tool = open_tool
    ui._tool_start('production', card)
    assert calls == ['production']
    assert ui._starting_tools == set()
