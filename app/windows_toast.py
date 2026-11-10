"""Backward-compatibility module forwarding to app.notification."""

from __future__ import annotations

from app.notification import (
    APPID,
    APPNAME,
    ICONPATH,
    TemplateToast,
    get_notification_backend,
    normalize_actions,
    send_notification,
    send_toast,
    unregister_toast,
)

__all__ = [
    "APPID",
    "APPNAME",
    "ICONPATH",
    "TemplateToast",
    "get_notification_backend",
    "normalize_actions",
    "send_notification",
    "send_toast",
    "unregister_toast",
]

if __name__ == "__main__":
    send_toast(
        "运行结束",
        ["第一行", "第二行", "第三行"],
        template=TemplateToast.TestTemplate,
    )
