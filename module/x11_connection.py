"""Xlib connection support without application or configuration imports."""
import socket

import Xlib.support.unix_connect as _xlib_unix_connect

# 猴子补丁：为 python-xlib 增加 Linux 抽象套接字（abstract unix domain socket）自动回退能力。
# Gamescope / Xwayland 在容器或非 FHS 环境下可能仅监听抽象套接字（@/tmp/.X11-unix/Xn），
# 磁盘上的文件套接字不可达时通过抽象套接字建立连接。
# This private helper is defined conditionally on Unix in python-xlib.
_orig_get_unix_socket = getattr(_xlib_unix_connect, "_get_unix_socket")


def _abstract_fallback_get_unix_socket(address):
    try:
        return _orig_get_unix_socket(address)
    except Exception:
        if isinstance(address, str) and address.startswith("/tmp/.X11-unix/X"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            try:
                s.connect("\0" + address)
                s.settimeout(None)
                return s
            except Exception:
                s.close()
                raise
        raise


def enable_abstract_socket_fallback():
    setattr(_xlib_unix_connect, "_get_unix_socket", _abstract_fallback_get_unix_socket)

