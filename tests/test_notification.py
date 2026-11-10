from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, Mock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusMessage

from app import mediator
from app.notification import (
    APPID,
    APPNAME,
    DBusNotificationBackend,
    TemplateToast,
    WindowsNotificationBackend,
    get_notification_backend,
    normalize_actions,
    send_toast,
)


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_normalize_actions() -> None:
    assert normalize_actions(None) == []
    assert normalize_actions([]) == []

    # Dict
    assert normalize_actions({"confirm": "知道了", "close": "关闭 AALC"}) == [
        ("confirm", "知道了"),
        ("close", "关闭 AALC"),
    ]

    # Tuples / pairs
    assert normalize_actions([("a", "1"), ("b", "2")]) == [("a", "1"), ("b", "2")]

    # Flat pair list
    assert normalize_actions(["k1", "v1", "k2", "v2"]) == [("k1", "v1"), ("k2", "v2")]

    # Key=value list
    assert normalize_actions(["k1=v1", "k2=v2"]) == [("k1", "v1"), ("k2", "v2")]


def test_backend_selection(monkeypatch) -> None:
    from app import notification

    monkeypatch.setattr(notification, "IS_WINDOWS", True)
    backend_win = get_notification_backend(force_refresh=True)
    assert isinstance(backend_win, WindowsNotificationBackend)

    monkeypatch.setattr(notification, "IS_WINDOWS", False)
    backend_linux = get_notification_backend(force_refresh=True)
    assert isinstance(backend_linux, DBusNotificationBackend)


def test_dbus_capability_detection_with_actions(qapp) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    reply_msg = MagicMock()
    reply_msg.type.return_value = QDBusMessage.MessageType.ReplyMessage
    reply_msg.arguments.return_value = [["actions", "body", "icon-static"]]
    mock_iface.call.return_value = reply_msg

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)
    caps = backend.get_capabilities()

    assert "actions" in caps
    assert backend.supports_actions() is True
    mock_iface.call.assert_called_with("GetCapabilities")


def test_dbus_capability_detection_without_actions(qapp) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    reply_msg = MagicMock()
    reply_msg.type.return_value = QDBusMessage.MessageType.ReplyMessage
    reply_msg.arguments.return_value = [["body", "icon-static"]]
    mock_iface.call.return_value = reply_msg

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)
    assert backend.supports_actions() is False


def test_dbus_capability_detection_error_handling(qapp) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    reply_msg = MagicMock()
    reply_msg.type.return_value = QDBusMessage.MessageType.ErrorMessage
    reply_msg.errorMessage.return_value = "Method not supported"
    mock_iface.call.return_value = reply_msg

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)
    assert backend.supports_actions() is False
    assert backend.get_capabilities() == set()


def test_dbus_send_notification_success(qapp, monkeypatch) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)
    backend._capabilities = {"actions", "body"}

    invoked_calls = []

    def fake_notify(app_name, replaces_id, icon, title, body, actions, hints, timeout):
        invoked_calls.append({
            "app_name": app_name,
            "replaces_id": replaces_id,
            "icon": icon,
            "title": title,
            "body": body,
            "actions": actions,
            "hints": hints,
            "timeout": timeout,
        })
        return 42

    monkeypatch.setattr(backend, "_notify", fake_notify)

    on_activated = Mock()
    success = backend.send_notification(
        title="测试标题",
        msg=["第一行", "第二行"],
        actions=[("confirm", "确定"), ("close", "关闭")],
        on_activated=on_activated,
    )

    assert success is True
    assert backend._active_callbacks.get(42) == on_activated
    assert len(invoked_calls) == 1
    call_info = invoked_calls[0]
    assert call_info["app_name"] == APPNAME
    assert call_info["title"] == "测试标题"
    assert "第一行\n第二行" in call_info["body"]
    assert call_info["actions"] == ["confirm", "确定", "close", "关闭"]


def test_dbus_send_notification_degrades_actions_when_unsupported(qapp, monkeypatch) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)
    backend._capabilities = {"body"}  # No actions capability

    invoked_calls = []

    def fake_notify(app_name, replaces_id, icon, title, body, actions, hints, timeout):
        invoked_calls.append(actions)
        return 43

    monkeypatch.setattr(backend, "_notify", fake_notify)

    success = backend.send_notification(
        title="无按钮标题",
        msg="内容",
        actions=[("confirm", "确定")],
    )

    assert success is True
    assert len(invoked_calls) == 1
    # Action list must be empty because daemon does not support actions
    assert invoked_calls[0] == []


def test_dbus_action_invoked_signal_and_close_handling(qapp) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = True

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)

    cb = Mock()
    backend._active_callbacks[100] = cb

    kill_signal_spy = Mock()
    mediator.kill_signal.connect(kill_signal_spy)

    try:
        # Trigger action "confirm"
        backend._on_action_invoked(100, "confirm")
        cb.assert_called_once_with("confirm")
        kill_signal_spy.assert_not_called()

        cb.reset_mock()
        # Trigger action "close"
        backend._on_action_invoked(100, "close")
        cb.assert_called_once_with("close")
        kill_signal_spy.assert_called_once()
    finally:
        mediator.kill_signal.disconnect(kill_signal_spy)


def test_dbus_notification_closed_cleans_up_callbacks(qapp) -> None:
    backend = DBusNotificationBackend(bus=MagicMock(), iface=MagicMock())
    backend._active_callbacks[101] = Mock()

    assert 101 in backend._active_callbacks
    backend._on_notification_closed(101, 2)  # Closed by user
    assert 101 not in backend._active_callbacks


def test_dbus_fallback_to_notify_send_when_iface_invalid(monkeypatch) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = True

    mock_iface = MagicMock()
    mock_iface.isValid.return_value = False

    backend = DBusNotificationBackend(bus=mock_bus, iface=mock_iface)

    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/notify-send" if cmd == "notify-send" else None)
    mock_run = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr("subprocess.run", mock_run)

    success = backend.send_notification("降级标题", "降级内容")
    assert success is True
    mock_run.assert_called_once()
    assert "/usr/bin/notify-send" in mock_run.call_args[0][0] or "notify-send" in mock_run.call_args[0][0]


def test_dbus_graceful_degradation_without_notify_send(monkeypatch) -> None:
    mock_bus = MagicMock()
    mock_bus.isConnected.return_value = False

    backend = DBusNotificationBackend(bus=mock_bus, iface=None)

    # Neither D-Bus nor notify-send
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    # Must return False and not raise unhandled exceptions
    success = backend.send_notification("无法通知", "无任何服务")
    assert success is False


def test_send_toast_and_windows_toast_compatibility(monkeypatch) -> None:
    import app.windows_toast as wt
    from app import notification

    # Ensure windows_toast re-exports match notification
    assert wt.send_toast is notification.send_toast
    assert wt.send_notification is notification.send_notification
    assert wt.TemplateToast is notification.TemplateToast
    assert wt.APPID == APPID
    assert wt.APPNAME == APPNAME

    # Test send_toast calls send_notification
    backend_mock = MagicMock()
    backend_mock.send_notification.return_value = True
    monkeypatch.setattr(notification, "get_notification_backend", lambda **kwargs: backend_mock)

    result = send_toast("测试标题", ["消息1", "消息2"], template=TemplateToast.NormalTemplate)
    assert result is True
    backend_mock.send_notification.assert_called_once()
