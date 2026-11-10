"""Offscreen Qt lifecycle tests, run by test_task_lifecycle in a fresh process."""
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from lifecycle_helpers import load_source
from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope='module')
def qt_app():
    app = QApplication.instance() or QApplication([])
    previous = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.setQuitOnLastWindowClosed(previous)


def make_window(monkeypatch, qt_app, confirm=True):
    from app.shutdown import ShutdownCleanup

    cleanup_entered, cleanup_release = threading.Event(), threading.Event()

    def cleanup(self):
        cleanup_entered.set()
        cleanup_release.wait(3)

    monkeypatch.setattr(ShutdownCleanup, 'run', cleanup)
    ns = dict(sys=sys, QThread=QThread, QWidget=QWidget, cfg=Mock(),
              QApplication=Mock(), MessageBoxConfirm=Mock())
    ns['MessageBoxConfirm'].return_value.exec.return_value = confirm
    cls = load_source('app/my_app.py', ns, class_name='MainWindow', base=QWidget,
                      methods={'closeEvent', 'request_exit', '_has_running_script',
                               '_emit_scheduled_start', '_schedule_next_queued_start',
                               '_start_next_queued_command', 'command_start'})
    window = cls()
    window._closing = False
    window._completion_exit_requested = False
    window._shutdown_cleanup = None
    window._pending_start_commands = [['aalc', 'start']]
    window._scheduled_start_pending = True
    window._scheduled_start_command = ['aalc', 'start']
    window._close_timer = QTimer(window)
    window._close_timer.setInterval(100)
    window._set_completion_actions_suspended = Mock()
    window.farming_interface = Mock()
    window.farming_interface.interface_left.my_script = None
    window.tools_interface = Mock(tools={}, _starting_tools=set())
    window.setting_interface = Mock()
    window.application = ns['QApplication'].instance()
    return window, ns['MessageBoxConfirm'], cleanup_entered, cleanup_release


def test_close_waits_for_tool_and_cleanup_without_repeating_confirmation(monkeypatch, qt_app):
    window, confirm, cleanup_entered, cleanup_release = make_window(monkeypatch, qt_app)
    worker_release = threading.Event()

    class Worker(QThread):
        def run(self):
            worker_release.wait(3)

    worker = Worker()
    ui_ns = dict()
    tool_cls = load_source('tasks/tools/production_module.py', ui_ns,
                           class_name='ProductionModule', methods={'closeEvent'}, base=QWidget)
    tool = tool_cls()
    tool.worker = worker
    tool.stop_production = worker.requestInterruption
    window.tools_interface.tools['production'] = SimpleNamespace(initialized=True, w=tool)
    worker.start()
    try:
        for _ in range(2):
            event = QCloseEvent()
            window.closeEvent(event)
            assert not event.isAccepted()
        window.application.quit.assert_not_called()
        window.application.setQuitOnLastWindowClosed.assert_called_once_with(False)
        assert worker.isInterruptionRequested()
        assert cleanup_entered.wait(3)
        assert confirm.call_count == 1
        assert window._closing
        assert not window._pending_start_commands
        # Incoming or already queued starts are inert once closing is latched.
        window.command_start(['aalc', 'start'])
        window._emit_scheduled_start()
        window._start_next_queued_command()
        assert not window._pending_start_commands
        worker_release.set()
        assert worker.wait(3000)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()  # final cleanup still running
        cleanup_release.set()
        assert window._shutdown_cleanup.wait(3000)
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        window.application.quit.assert_called_once()
    finally:
        worker_release.set()
        cleanup_release.set()
        assert worker.wait(3000)
        if window._shutdown_cleanup:
            assert window._shutdown_cleanup.wait(3000)
        window._close_timer.stop()
        tool.deleteLater()
        window.deleteLater()


def test_reject_exit_does_not_cancel_workers_or_clear_queue(monkeypatch, qt_app):
    window, confirm, _, _ = make_window(monkeypatch, qt_app, confirm=False)
    window.farming_interface.interface_left.my_script = Mock()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window._closing
    assert window._pending_start_commands
    window.farming_interface.interface_left.stop_script.assert_not_called()
    assert window._shutdown_cleanup is None
    window.deleteLater()



@pytest.mark.parametrize('kind', ['preview', 'copy'])
def test_exit_waits_for_native_background_thread(monkeypatch, qt_app, kind):
    from app.linux_setup import FrameReader, ProfileCopy

    if not sys.platform.startswith('linux'):
        pytest.skip('native Linux background workers')
    window, confirm, cleanup_entered, cleanup_release = make_window(monkeypatch, qt_app)
    release = threading.Event()
    started = threading.Event()
    cls = FrameReader if kind == 'preview' else ProfileCopy

    def run(self):
        started.set()
        release.wait(3)

    monkeypatch.setattr(cls, 'run', run)
    worker = cls(window) if kind == 'preview' else cls('source', 'destination', window)
    worker.start()
    try:
        assert started.wait(3)
        window._completion_exit_requested = True
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert worker.isInterruptionRequested()
        confirm.assert_not_called()
        assert cleanup_entered.wait(3)
        cleanup_release.set()
        assert window._shutdown_cleanup.wait(3000)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        release.set()
        assert worker.wait(3000)
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
    finally:
        release.set()
        cleanup_release.set()
        assert worker.wait(3000)
        if window._shutdown_cleanup:
            assert window._shutdown_cleanup.wait(3000)
        window._close_timer.stop()
        window.deleteLater()


def test_close_waits_for_queued_tool_creation(monkeypatch, qt_app):
    window, confirm, cleanup_entered, cleanup_release = make_window(monkeypatch, qt_app)
    manager = SimpleNamespace(initialized=False, w=None)
    window.tools_interface.tools['production'] = manager
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert cleanup_entered.wait(3)
        cleanup_release.set()
        assert window._shutdown_cleanup.wait(3000)
        manager.w = QWidget()
        manager.w.show()
        manager.initialized = True
        window.tools_interface._starting_tools.add('production')
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert manager.w.isVisible()  # registration is still in a nested event loop
        window.tools_interface._starting_tools.clear()
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        assert not manager.w.isVisible()
        assert confirm.call_count == 1
    finally:
        cleanup_release.set()
        if window._shutdown_cleanup:
            assert window._shutdown_cleanup.wait(3000)
        window._close_timer.stop()
        if manager.w:
            manager.w.deleteLater()
        window.deleteLater()
