import os
import subprocess
import time

import pytest
import Xlib.display
from Xlib import XK, X
from Xlib.ext import xtest

from module.game_and_screen.gamescope_launcher import GamescopeLauncher


@pytest.mark.live
def test_gamescope_xwayland_native_xtest_under_rapid_input():
    """验证在 GamescopeLauncher 注入 WLR_XWAYLAND 隔离 LIBEI 后，
    连续注入键盘与鼠标 XTest 事件不会触发 EIS 崩溃，且 XTest 原生正常派发。"""
    launcher = GamescopeLauncher()
    env = launcher.build_env()
    assert "WLR_XWAYLAND" in env, "环境必须包含 WLR_XWAYLAND 包装器"
    wrapper_path = env["WLR_XWAYLAND"]
    assert os.path.isfile(wrapper_path)

    log_path = "/tmp/test_ei_regression.log"
    with open(log_path, "w") as f:
        f.write("")

    proc = subprocess.Popen(
        [
            "gamescope",
            "--backend", "headless",
            "-W", "1280",
            "-H", "720",
            "-r", "60",
            "--",
            "sleep", "15",
        ],
        env=env,
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    try:
        # 等待 X11 Display :1 就绪
        display_num = ":1"
        d = None
        start = time.time()
        while time.time() - start < 5.0:
            try:
                d = Xlib.display.Display(display_num)
                break
            except Exception:
                time.sleep(0.1)

        assert d is not None, "Gamescope Display :1 应当在 5 秒内就绪"

        # 创建接收事件的 X11 测试窗口
        screen = d.screen()
        window = screen.root.create_window(
            50, 50, 200, 200, 0,
            screen.root_depth,
            X.InputOutput,
            X.CopyFromParent,
            event_mask=X.KeyPressMask | X.KeyReleaseMask | X.ButtonPressMask | X.ButtonReleaseMask | X.PointerMotionMask,
        )
        window.map()
        d.sync()
        time.sleep(0.1)

        keys = [XK.string_to_keysym("a"), XK.string_to_keysym("d"), XK.string_to_keysym("Return")]
        keycodes = [d.keysym_to_keycode(k) for k in keys if k]

        received_events = 0
        # 连续注入 30 轮简单键盘模式与鼠标事件
        for cycle in range(30):
            for kc in keycodes:
                xtest.fake_input(d, X.KeyPress, kc)
                d.sync()
                xtest.fake_input(d, X.KeyRelease, kc)
                d.sync()

            xtest.fake_input(d, X.MotionNotify, x=100 + cycle, y=100 + cycle)
            d.sync()
            xtest.fake_input(d, X.ButtonPress, 1)
            d.sync()
            xtest.fake_input(d, X.ButtonRelease, 1)
            d.sync()

            while d.pending_events():
                d.next_event()
                received_events += 1

            assert proc.poll() is None, f"Gamescope 在第 {cycle} 轮输入时崩溃闪退"

        assert received_events > 0, "测试窗口必须真实接收到 X11 原生 XTest 事件"

        # 检查日志中绝对不能包含崩溃关键字
        with open(log_path, "r") as f:
            log_content = f.read()
        assert "Unhandled libei event" not in log_content
        assert "Broken pipe" not in log_content

    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except Exception:
            pass
        launcher._cleanup_xwayland_wrapper()
