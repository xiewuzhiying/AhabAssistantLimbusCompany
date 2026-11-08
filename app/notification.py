from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Q_ARG,
    Q_RETURN_ARG,
    SIGNAL,
    QCoreApplication,
    QMetaObject,
    QObject,
    Slot,
)

try:
    from PySide6.QtDBus import (
        QDBusConnection,
        QDBusInterface,
        QDBusMessage,
    )
    _QTDBUS_AVAILABLE = True
except ImportError:
    QDBusConnection = None  # type: ignore[assignment, misc]
    QDBusInterface = None  # type: ignore[assignment, misc]
    QDBusMessage = None  # type: ignore[assignment, misc]
    _QTDBUS_AVAILABLE = False

from app import mediator
from module.config import cfg
from module.logger import log
from module.platform_compat import IS_WINDOWS

APPID = "AALC_Notification"
APPNAME = "AALC"
ICONPATH = "assets/logo/my_icon.png"


class TemplateToast(Enum):
    NoneTemplate = 0
    TestTemplate = 1
    NormalTemplate = 2


def normalize_actions(
    actions: list[tuple[str, str]] | dict[str, str] | list[str] | None,
) -> list[tuple[str, str]]:
    """Normalize notification actions into a list of (key, label) tuples."""
    if not actions:
        return []
    if isinstance(actions, dict):
        return [(str(k), str(v)) for k, v in actions.items()]
    if isinstance(actions, list):
        result: list[tuple[str, str]] = []
        i = 0
        while i < len(actions):
            item = actions[i]
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                result.append((str(item[0]), str(item[1])))
                i += 1
            elif isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                result.append((k, v))
                i += 1
            elif isinstance(item, str) and i + 1 < len(actions) and isinstance(actions[i + 1], str):
                result.append((item, actions[i + 1]))
                i += 2
            else:
                result.append((str(item), str(item)))
                i += 1
        return result
    return []


class BaseNotificationBackend:
    """Abstract base class for platform-specific notification backends."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def send_notification(
        self,
        title: str,
        msg: str | list[str],
        actions: list[tuple[str, str]] | dict[str, str] | list[str] | None = None,
        on_activated: Callable[[str], None] | None = None,
        app_name: str = APPNAME,
        app_id: str = APPID,
        icon_path: str = ICONPATH,
        template: TemplateToast = TemplateToast.NoneTemplate,
        timeout: int = 5000,
        **kwargs: Any,
    ) -> bool:
        """Send a notification to the desktop environment."""
        raise NotImplementedError

    def unregister(self, app_id: str = APPID) -> None:
        """Unregister platform-specific application registration (if any)."""
        pass


class WindowsNotificationBackend(BaseNotificationBackend):
    """Windows notification backend utilizing WinRT / windows_toasts."""

    def __init__(self) -> None:
        self._toaster = None
        self._registered = False

    def _ensure_registered(self, app_id: str) -> None:
        if self._registered or not IS_WINDOWS:
            return
        try:
            from winreg import (
                HKEY_CURRENT_USER,
                KEY_READ,
                KEY_SET_VALUE,
                REG_SZ,
                CloseKey,
                CreateKey,
                OpenKey,
                QueryValueEx,
                SetValueEx,
            )

            a_key = OpenKey(
                HKEY_CURRENT_USER,
                r"Software\Classes\AppUserModelId",
                0,
                KEY_SET_VALUE | KEY_READ,
            )
            try:
                QueryValueEx(a_key, app_id)
            except OSError:
                app_key = CreateKey(a_key, app_id)
                SetValueEx(app_key, "DisplayName", 0, REG_SZ, APPNAME)
                icon_file = Path(ICONPATH).resolve()
                if icon_file.exists():
                    SetValueEx(app_key, "IconUri", 0, REG_SZ, str(icon_file))
                CloseKey(app_key)
            CloseKey(a_key)
            self._registered = True
        except Exception as e:
            log.warning(f"Failed to register Windows AppUserModelId: {e}")

    def send_notification(
        self,
        title: str,
        msg: str | list[str],
        actions: list[tuple[str, str]] | dict[str, str] | list[str] | None = None,
        on_activated: Callable[[str], None] | None = None,
        app_name: str = APPNAME,
        app_id: str = APPID,
        icon_path: str = ICONPATH,
        template: TemplateToast = TemplateToast.NoneTemplate,
        timeout: int = 5000,
        **kwargs: Any,
    ) -> bool:
        if not IS_WINDOWS:
            log.warning("WindowsNotificationBackend invoked on non-Windows platform")
            return False

        try:
            import windows_toasts
            from windows_toasts import (
                InteractableWindowsToaster,
                Toast,
                ToastAudio,
                ToastButton,
                ToastImage,
            )
        except ImportError:
            log.warning("windows_toasts is not installed; notification skipped.")
            return False

        self._ensure_registered(app_id)
        if self._toaster is None:
            self._toaster = InteractableWindowsToaster(app_name, app_id)

        try:
            toast = Toast()

            if template == TemplateToast.NormalTemplate:
                title = QCoreApplication.translate("WindowsToast", title)
                if isinstance(msg, str):
                    body_lines = [QCoreApplication.translate("WindowsToast", msg)]
                else:
                    body_lines = [QCoreApplication.translate("WindowsToast", line) for line in msg]
                toast.text_fields = [title] + body_lines

                btn_confirm = ToastButton(
                    QCoreApplication.translate("WindowsToast", "知道了"),
                    arguments="confirm",
                )
                btn_close = ToastButton(
                    QCoreApplication.translate("WindowsToast", "关闭 AALC"),
                    arguments="close",
                )
                toast.AddAction(btn_confirm)
                toast.AddAction(btn_close)
            elif template == TemplateToast.TestTemplate:
                toast.text_fields = [title, msg] if isinstance(msg, str) else [title] + msg
                toast.AddAction(ToastButton("都是你的错", arguments="button1"))
                toast.AddAction(ToastButton("以实玛丽", arguments="button2"))
                toast.AddAction(ToastButton("伊莎玛拉", arguments="button3"))
                toast.AddAction(ToastButton("亚哈", arguments="hide"))
            else:
                toast.text_fields = [title, msg] if isinstance(msg, str) else [title] + msg
                norm_actions = normalize_actions(actions)
                for k, v in norm_actions:
                    toast.AddAction(ToastButton(v, arguments=k))

            def _on_activated_handler(activated_event_args: Any) -> None:
                arg = getattr(activated_event_args, "arguments", "")
                if on_activated is not None:
                    try:
                        on_activated(arg)
                    except Exception as ex:
                        log.warning(f"Error in Windows notification callback: {ex}")
                if arg == "close":
                    mediator.kill_signal.emit()

            toast.on_activated = _on_activated_handler

            resolved_icon = Path(icon_path).resolve()
            if resolved_icon.exists():
                toast.AddImage(ToastImage(str(resolved_icon)))

            if cfg.resonate_with_Ahab:
                from random import randint
                toast.audio = ToastAudio(
                    getattr(
                        windows_toasts.AudioSource,
                        f"Alarm{randint(1, 10)}",
                        windows_toasts.AudioSource.Default,
                    )
                )

            self._toaster.show_toast(toast)
            return True
        except Exception as e:
            log.warning(f"Failed to send Windows notification: {e}")
            return False

    def unregister(self, app_id: str = APPID) -> None:
        if not IS_WINDOWS:
            return
        try:
            from winreg import (
                HKEY_CURRENT_USER,
                KEY_ALL_ACCESS,
                CloseKey,
                DeleteKey,
                OpenKey,
            )
            a_key = OpenKey(HKEY_CURRENT_USER, r"Software\Classes\AppUserModelId", 0, KEY_ALL_ACCESS)
            DeleteKey(a_key, app_id)
            CloseKey(a_key)
        except OSError:
            pass
        except Exception as e:
            log.warning(f"Error during Windows notification unregister: {e}")


class DBusNotificationBackend(BaseNotificationBackend, QObject):
    """Linux Freedesktop notification backend communicating via PySide6.QtDBus."""

    def __init__(
        self,
        bus: Any | None = None,
        iface: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus if bus is not None else (QDBusConnection.sessionBus() if _QTDBUS_AVAILABLE else None)
        self._iface = iface
        self._capabilities: set[str] | None = None
        self._active_callbacks: dict[int, Callable[[str], None]] = {}
        self._signals_connected = False

    def _init_interface(self) -> Any | None:
        if self._iface is not None:
            if not self._signals_connected and getattr(self._iface, "isValid", lambda: True)():
                self._connect_signals()
            return self._iface

        if not _QTDBUS_AVAILABLE or self._bus is None:
            return None

        try:
            if not self._bus.isConnected():
                return None
            self._iface = QDBusInterface(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                self._bus,
                self,
            )
            if self._iface.isValid() and not self._signals_connected:
                self._connect_signals()
            return self._iface
        except Exception as e:
            log.debug(f"Failed to initialize QDBusInterface: {e}")
            return None

    def _connect_signals(self) -> None:
        if self._iface is None or not isinstance(self._iface, QObject):
            return
        try:
            QObject.connect(
                self._iface,
                SIGNAL("ActionInvoked(uint,QString)"),
                self._on_action_invoked,
            )
            QObject.connect(
                self._iface,
                SIGNAL("NotificationClosed(uint,uint)"),
                self._on_notification_closed,
            )
            self._signals_connected = True
        except Exception as e:
            log.warning(f"Failed to connect D-Bus notification signals: {e}")

    @Slot(int, str)
    def _on_action_invoked(self, nid: int, action_key: str) -> None:
        log.debug(f"Notification action invoked: nid={nid}, key={action_key}")
        callback = self._active_callbacks.get(nid)
        if callback:
            try:
                callback(action_key)
            except Exception as e:
                log.warning(f"Error in notification callback: {e}")
        if action_key == "close":
            mediator.kill_signal.emit()

    @Slot(int, int)
    def _on_notification_closed(self, nid: int, reason: int) -> None:
        log.debug(f"Notification closed: nid={nid}, reason={reason}")
        self._active_callbacks.pop(nid, None)

    def get_capabilities(self) -> set[str]:
        if self._capabilities is not None:
            return self._capabilities

        iface = self._init_interface()
        if iface is None or not getattr(iface, "isValid", lambda: True)():
            self._capabilities = set()
            return self._capabilities

        try:
            reply = iface.call("GetCapabilities")
            if (
                _QTDBUS_AVAILABLE
                and hasattr(reply, "type")
                and reply.type() == QDBusMessage.MessageType.ReplyMessage
                and reply.arguments()
            ):
                caps = reply.arguments()[0]
                self._capabilities = set(caps) if isinstance(caps, (list, tuple)) else set()
            elif isinstance(reply, (list, tuple)):
                self._capabilities = set(reply)
            else:
                error_msg = getattr(reply, "errorMessage", lambda: "Unknown error")()
                log.debug(f"GetCapabilities returned non-reply message: {error_msg}")
                self._capabilities = set()
        except Exception as e:
            log.warning(f"Error calling GetCapabilities on D-Bus daemon: {e}")
            self._capabilities = set()

        return self._capabilities

    def supports_actions(self) -> bool:
        return "actions" in self.get_capabilities()

    def _notify(
        self,
        app_name: str,
        replaces_id: int,
        icon: str,
        title: str,
        body: str,
        actions: list[str],
        hints: dict[str, Any],
        timeout: int,
    ) -> int:
        if self._iface is None:
            return 0
        res = QMetaObject.invokeMethod(
            self._iface,
            "Notify",
            Q_RETURN_ARG("uint"),
            Q_ARG("QString", app_name),
            Q_ARG("uint", replaces_id),
            Q_ARG("QString", icon),
            Q_ARG("QString", title),
            Q_ARG("QString", body),
            Q_ARG("QStringList", actions),
            Q_ARG("QVariantMap", hints or {}),
            Q_ARG("int", timeout),
        )
        return int(res) if res is not None else 0

    def send_notification(
        self,
        title: str,
        msg: str | list[str],
        actions: list[tuple[str, str]] | dict[str, str] | list[str] | None = None,
        on_activated: Callable[[str], None] | None = None,
        app_name: str = APPNAME,
        app_id: str = APPID,
        icon_path: str = ICONPATH,
        template: TemplateToast = TemplateToast.NoneTemplate,
        timeout: int = 5000,
        replaces_id: int = 0,
        hints: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> bool:
        # Format body string
        if template == TemplateToast.NormalTemplate:
            title = QCoreApplication.translate("WindowsToast", title)
            if isinstance(msg, str):
                body = QCoreApplication.translate("WindowsToast", msg)
            else:
                body = "\n".join(QCoreApplication.translate("WindowsToast", line) for line in msg)
            norm_actions = [
                ("confirm", QCoreApplication.translate("WindowsToast", "知道了")),
                ("close", QCoreApplication.translate("WindowsToast", "关闭 AALC")),
            ]
        elif template == TemplateToast.TestTemplate:
            body = msg if isinstance(msg, str) else "\n".join(msg)
            norm_actions = [
                ("button1", "都是你的错"),
                ("button2", "以实玛丽"),
                ("button3", "伊莎玛拉"),
                ("hide", "亚哈"),
            ]
        else:
            body = msg if isinstance(msg, str) else "\n".join(msg)
            norm_actions = normalize_actions(actions)

        # Resolve icon path
        resolved_icon = ""
        if icon_path:
            p = Path(icon_path).resolve()
            resolved_icon = str(p) if p.exists() else icon_path

        # Capability detection: smooth fallback if server does not support actions
        if not self.supports_actions():
            norm_actions = []

        # Flatten actions to key, label, key, label...
        dbus_actions: list[str] = []
        for k, v in norm_actions:
            dbus_actions.extend([k, v])

        iface = self._init_interface()
        if iface is None or not getattr(iface, "isValid", lambda: True)():
            log.debug("D-Bus interface invalid or unavailable; falling back to notify-send")
            return self._fallback_notify_send(
                title=title,
                body=body,
                icon_path=resolved_icon,
                actions=norm_actions,
                on_activated=on_activated,
                app_name=app_name,
            )

        try:
            nid = self._notify(
                app_name=app_name,
                replaces_id=replaces_id,
                icon=resolved_icon,
                title=title,
                body=body,
                actions=dbus_actions,
                hints=hints or {},
                timeout=timeout,
            )
            if not nid:
                log.warning("D-Bus Notify returned 0 or invalid id; falling back to notify-send")
                return self._fallback_notify_send(
                    title=title,
                    body=body,
                    icon_path=resolved_icon,
                    actions=norm_actions,
                    on_activated=on_activated,
                    app_name=app_name,
                )

            if on_activated is not None or any(k == "close" for k, _ in norm_actions):
                self._active_callbacks[nid] = on_activated  # type: ignore[assignment]
            log.debug(f"Sent D-Bus notification (id={nid})")
            return True
        except Exception as e:
            log.warning(f"Error calling D-Bus Notify: {e}; falling back to notify-send")
            return self._fallback_notify_send(
                title=title,
                body=body,
                icon_path=resolved_icon,
                actions=norm_actions,
                on_activated=on_activated,
                app_name=app_name,
            )

    def _fallback_notify_send(
        self,
        title: str,
        body: str,
        icon_path: str = "",
        actions: list[tuple[str, str]] | None = None,
        on_activated: Callable[[str], None] | None = None,
        app_name: str = APPNAME,
    ) -> bool:
        if shutil.which("notify-send") is None:
            log.warning(
                f"Notification service unavailable (neither D-Bus nor notify-send). "
                f"Title: {title}, Message: {body}"
            )
            return False

        cmd = ["notify-send", "-a", app_name]
        if icon_path and Path(icon_path).exists():
            cmd += ["-i", icon_path]
        if actions:
            for k, v in actions:
                cmd += ["--action", f"{k}={v}"]

        has_actions = bool(actions and (on_activated or any(k == "close" for k, _ in actions)))
        if has_actions:
            def _wait_and_handle() -> None:
                try:
                    wait_cmd = cmd + ["--wait", title, body]
                    res = subprocess.run(wait_cmd, capture_output=True, text=True, timeout=300)
                    action_key = res.stdout.strip()
                    if action_key:
                        log.debug(f"notify-send action triggered: {action_key}")
                        if on_activated:
                            try:
                                on_activated(action_key)
                            except Exception as ex:
                                log.debug(f"notify-send callback failed: {ex}")
                        if action_key == "close":
                            mediator.kill_signal.emit()
                except Exception as ex:
                    log.debug(f"notify-send action listener finished/failed: {ex}")

            threading.Thread(target=_wait_and_handle, daemon=True).start()
            return True

        try:
            cmd += [title, body]
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True
        except Exception as e:
            log.warning(f"Failed to execute notify-send: {e}")
            return False


_CURRENT_BACKEND: BaseNotificationBackend | None = None


def get_notification_backend(force_refresh: bool = False) -> BaseNotificationBackend:
    """Get the active notification backend for the current platform."""
    global _CURRENT_BACKEND
    if _CURRENT_BACKEND is not None and not force_refresh:
        return _CURRENT_BACKEND

    if IS_WINDOWS:
        _CURRENT_BACKEND = WindowsNotificationBackend()
    else:
        _CURRENT_BACKEND = DBusNotificationBackend()

    return _CURRENT_BACKEND


def set_notification_backend(backend: BaseNotificationBackend | None) -> None:
    """Set or override the active notification backend (primarily for testing)."""
    global _CURRENT_BACKEND
    _CURRENT_BACKEND = backend


def send_notification(
    title: str,
    msg: str | list[str],
    actions: list[tuple[str, str]] | dict[str, str] | list[str] | None = None,
    on_activated: Callable[[str], None] | None = None,
    app_name: str = APPNAME,
    app_id: str = APPID,
    icon_path: str = ICONPATH,
    template: TemplateToast = TemplateToast.NoneTemplate,
    timeout: int = 5000,
    **kwargs: Any,
) -> bool:
    """
    Send a desktop notification across platforms.

    On Linux, uses native PySide6.QtDBus with org.freedesktop.Notifications,
    falling back to notify-send when necessary.
    On Windows, uses WinRT Toast notifications.
    """
    backend = get_notification_backend()
    return backend.send_notification(
        title=title,
        msg=msg,
        actions=actions,
        on_activated=on_activated,
        app_name=app_name,
        app_id=app_id,
        icon_path=icon_path,
        template=template,
        timeout=timeout,
        **kwargs,
    )


def send_toast(
    title: str,
    msg: str | list[str],
    template: TemplateToast = TemplateToast.NoneTemplate,
    on_activated: Callable[[str], None] | None = None,
    actions: list[tuple[str, str]] | dict[str, str] | list[str] | None = None,
    **kwargs: Any,
) -> bool:
    """Backward-compatible alias for send_notification."""
    return send_notification(
        title=title,
        msg=msg,
        actions=actions,
        on_activated=on_activated,
        template=template,
        **kwargs,
    )


def unregister_toast(app_id: str = APPID) -> None:
    """Unregister desktop notification application registrations."""
    backend = get_notification_backend()
    backend.unregister(app_id)
