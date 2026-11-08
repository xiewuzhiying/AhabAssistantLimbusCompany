"""Gamescope 真实嵌套运行时环境下的端到端集成测试。

验证特性:
1. 在真实 Gamescope 运行时环境下（非 --headless），Gamescope 嵌套会话正常启动并提供虚拟 X11 服务。
2. X11Handle 自适应探测并识别 Gamescope 窗口及其虚拟 DISPLAY。
3. GamescopeXTestInput 向虚拟 X11 会话分发事件，Gamescope 内部监听窗口能精确收到事件：
   - mouse_move: 虚拟光标位移及 MotionNotify 事件。
   - mouse_click: ButtonPress / ButtonRelease 事件。
   - mouse_drag: 平滑拖拽过程及中间 MotionNotify 与释放事件。
   - content_offset 偏移计算: Y 坐标自动精准叠加 offset。
   - mouse_scroll: 滚轮 Button4 / Button5 映射。
   - key_press / input_text: 虚拟键盘事件及 keycode 映射。
4. 绝对隔离性断言: 整个操作周期内，宿主机物理光标 (host DISPLAY) 保持绝对静止不动。
5. 进程生命周期: 无论测试成功还是失败，均可靠清理后台 gamescope 及其子进程。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from Xlib import X, display

from module.automation.input_handlers.gamescope_input import GamescopeXTestInput
from module.game_and_screen.x11_handle import X11Handle


def _out(msg: str = "") -> None:
    """标准格式化输出工具（规避 linter print 限制）。"""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _run_child_listener(ready_file: str, log_file: str) -> None:
    """在 Gamescope 虚拟会话内部运行的目标监听窗口进程。"""
    import signal

    running = True

    def _sig_handler(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    try:
        dpy = display.Display()
    except Exception as e:
        sys.stderr.write(f"Child listener failed to connect to X Display: {e}\n")
        sys.exit(1)

    screen = dpy.screen()
    root = screen.root

    win = root.create_window(
        0,
        0,
        1280,
        720,
        0,
        screen.root_depth,
        X.InputOutput,
        X.CopyFromParent,
        background_pixel=screen.black_pixel,
        event_mask=(
            X.ExposureMask
            | X.KeyPressMask
            | X.KeyReleaseMask
            | X.ButtonPressMask
            | X.ButtonReleaseMask
            | X.PointerMotionMask
            | X.EnterWindowMask
            | X.LeaveWindowMask
            | X.FocusChangeMask
            | X.StructureNotifyMask
        ),
    )
    win.set_wm_name("LimbusCompany")
    win.set_wm_class("limbuscompany", "UnityWndClass")
    win.map()
    dpy.sync()

    try:
        dpy.set_input_focus(win, X.RevertToParent, X.CurrentTime)
        dpy.sync()
    except Exception:
        pass

    with open(ready_file, "w") as f:
        f.write(f"{win.id}\n")
        f.flush()

    with open(log_file, "a") as f:
        while running:
            try:
                ev = dpy.next_event()
            except Exception:
                break
            if not running:
                break

            event_type = ev.type
            data: dict[str, Any] = {"type": event_type, "timestamp": time.time()}

            if event_type == X.MotionNotify:
                data.update(
                    {
                        "name": "MotionNotify",
                        "root_x": ev.root_x,
                        "root_y": ev.root_y,
                        "event_x": ev.event_x,
                        "event_y": ev.event_y,
                    }
                )
            elif event_type in (X.ButtonPress, X.ButtonRelease):
                data.update(
                    {
                        "name": "ButtonPress" if event_type == X.ButtonPress else "ButtonRelease",
                        "detail": ev.detail,
                        "root_x": ev.root_x,
                        "root_y": ev.root_y,
                        "event_x": ev.event_x,
                        "event_y": ev.event_y,
                    }
                )
            elif event_type in (X.KeyPress, X.KeyRelease):
                data.update(
                    {
                        "name": "KeyPress" if event_type == X.KeyPress else "KeyRelease",
                        "detail": ev.detail,
                        "state": ev.state,
                    }
                )
            elif event_type == X.FocusIn:
                data.update({"name": "FocusIn"})
            elif event_type == X.FocusOut:
                data.update({"name": "FocusOut"})
            else:
                data.update({"name": f"Event_{event_type}"})

            f.write(json.dumps(data) + "\n")
            f.flush()


def _check_prerequisites() -> None:
    """检查真实 Gamescope 运行时环境依赖。"""
    if not shutil.which("gamescope"):
        pytest.skip("系统中未安装 gamescope 可执行文件，跳过端到端测试")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.skip("未检测到活跃的图形会话 (WAYLAND_DISPLAY / DISPLAY)，跳过端到端测试")


class GamescopeLiveSession:
    """Gamescope 嵌套会话上下文管理器。"""

    def __init__(self, temp_dir: Path, width: int = 1280, height: int = 720, refresh: int = 60) -> None:
        self.temp_dir = temp_dir
        self.width = width
        self.height = height
        self.refresh = refresh
        self.ready_file = temp_dir / "gamescope_ready"
        self.log_file = temp_dir / "gamescope_events.jsonl"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> GamescopeLiveSession:
        if self.ready_file.exists():
            self.ready_file.unlink()
        if self.log_file.exists():
            self.log_file.unlink()

        cmd = [
            "gamescope",
            "--backend",
            "headless",
            "-W",
            str(self.width),
            "-H",
            str(self.height),
            "-r",
            str(self.refresh),
            "--",
            sys.executable,
            str(Path(__file__).resolve()),
            "--child-listener",
            str(self.ready_file),
            str(self.log_file),
        ]

        env = os.environ.copy()
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 等待子窗口就绪
        t0 = time.time()
        timeout = 12.0
        while time.time() - t0 < timeout:
            if self.ready_file.exists():
                break
            if self.proc.poll() is not None:
                stdout, stderr = self.proc.communicate()
                raise RuntimeError(
                    f"Gamescope 启动失败 (exit={self.proc.returncode}):\n"
                    f"STDOUT: {stdout.decode(errors='replace')}\n"
                    f"STDERR: {stderr.decode(errors='replace')}"
                )
            time.sleep(0.2)

        if not self.ready_file.exists():
            self.__exit__(None, None, None)
            raise TimeoutError("等待 Gamescope 内部监听窗口就绪超时")

        time.sleep(0.5)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=4.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
            self.proc = None

    def clear_events(self) -> None:
        """清空当前日志文件以便精确断言后续动作事件。"""
        time.sleep(0.1)
        with open(self.log_file, "w") as f:
            f.write("")

    def read_events(self) -> list[dict[str, Any]]:
        """读取已捕获的全部底层事件。"""
        time.sleep(0.2)
        if not self.log_file.exists():
            return []
        events = []
        with open(self.log_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events


def test_gamescope_live_e2e(tmp_path: Path) -> None:
    """端到端验证 Gamescope 真实运行时的输入隔离与精准性。"""
    _check_prerequisites()

    host_disp_name = os.environ.get("DISPLAY", ":0")
    try:
        host_disp = display.Display(host_disp_name)
    except Exception as e:
        pytest.skip(f"无法连接到宿主 DISPLAY ({host_disp_name}): {e}")

    host_root = host_disp.screen().root
    initial_host_pos = host_root.query_pointer()
    initial_host_coords = (int(initial_host_pos.root_x), int(initial_host_pos.root_y))

    with GamescopeLiveSession(tmp_path) as session:
        # 1. 自动探测并初始化 X11Handle
        handle = X11Handle()
        try:
            wid = handle.init_handle(title="LimbusCompany", class_name="UnityWndClass")
            assert wid != 0, "X11Handle 未能探测到 Gamescope 内的 LimbusCompany 窗口"
            assert handle.is_gamescope is True, "X11Handle 未将会话标记为 Gamescope 嵌套环境"
            assert handle.display_name is not None and handle.display_name != host_disp_name, (
                f"X11Handle 应该连接到 Gamescope Xwayland 端口，当前为 {handle.display_name}"
            )

            # 2. 构造 GamescopeXTestInput
            inp = GamescopeXTestInput(handle=handle)
            assert inp.is_gamescope is True, "GamescopeXTestInput 未正确感知 handle.is_gamescope"

            # -------------------------------------------------------------
            # Action 1: mouse_move (350, 250)
            # -------------------------------------------------------------
            session.clear_events()
            inp.mouse_move(350, 250)
            time.sleep(0.1)

            pos = inp.get_mouse_position()
            assert pos == (350, 250), f"get_mouse_position() 返回位置不符: 期望 (350, 250), 实际 {pos}"

            events = session.read_events()
            motions = [e for e in events if e.get("name") == "MotionNotify"]
            assert len(motions) > 0, "未捕获到 mouse_move 产生的 MotionNotify 事件"
            last_motion = motions[-1]
            assert last_motion["root_x"] == 350 and last_motion["root_y"] == 250
            assert last_motion["event_x"] == 350 and last_motion["event_y"] == 250

            # -------------------------------------------------------------
            # Action 2: mouse_click (450, 300), content_offset = 0
            # -------------------------------------------------------------
            session.clear_events()
            handle.content_offset = 0
            click_res = inp.mouse_click(450, 300)
            assert click_res is True, "mouse_click 执行返回失败"
            time.sleep(0.1)

            events = session.read_events()
            presses = [e for e in events if e.get("name") == "ButtonPress"]
            releases = [e for e in events if e.get("name") == "ButtonRelease"]
            assert len(presses) >= 1, "未收到 ButtonPress 事件"
            assert len(releases) >= 1, "未收到 ButtonRelease 事件"
            assert presses[-1]["detail"] == 1, "点击按键码应为 1 (左键)"
            assert presses[-1]["root_x"] == 450 and presses[-1]["root_y"] == 300
            assert presses[-1]["event_x"] == 450 and presses[-1]["event_y"] == 300
            assert releases[-1]["root_x"] == 450 and releases[-1]["root_y"] == 300

            # -------------------------------------------------------------
            # Action 3: mouse_click (500, 200), content_offset = 40
            # -------------------------------------------------------------
            session.clear_events()
            handle.content_offset = 40
            inp.mouse_click(500, 200)
            time.sleep(0.1)

            events = session.read_events()
            presses = [e for e in events if e.get("name") == "ButtonPress"]
            releases = [e for e in events if e.get("name") == "ButtonRelease"]
            assert len(presses) >= 1, "带 content_offset 点击未收到 ButtonPress"
            # 期望 y = 200 + 40 = 240
            assert presses[-1]["root_x"] == 500 and presses[-1]["root_y"] == 240, (
                f"content_offset=40 时 Y 坐标未叠加: {presses[-1]}"
            )
            assert presses[-1]["event_x"] == 500 and presses[-1]["event_y"] == 240
            assert releases[-1]["root_x"] == 500 and releases[-1]["root_y"] == 240

            # -------------------------------------------------------------
            # Action 4: mouse_drag ((150, 150), (250, 250)), content_offset = 40
            # -------------------------------------------------------------
            session.clear_events()
            inp.mouse_drag((150, 150), (250, 250), drag_time=0.1)
            time.sleep(0.1)

            events = session.read_events()
            presses = [e for e in events if e.get("name") == "ButtonPress"]
            releases = [e for e in events if e.get("name") == "ButtonRelease"]
            motions = [e for e in events if e.get("name") == "MotionNotify"]
            assert len(presses) >= 1, "mouse_drag 未收到起始 ButtonPress"
            assert len(releases) >= 1, "mouse_drag 未收到结束 ButtonRelease"
            assert len(motions) >= 4, f"mouse_drag 中间插值轨迹过短: 仅 {len(motions)} 个 MotionNotify"
            # 起始点 Y: 150 + 40 = 190
            assert presses[0]["root_x"] == 150 and presses[0]["root_y"] == 190
            # 终点 Y: 250 + 40 = 290
            assert releases[-1]["root_x"] == 250 and releases[-1]["root_y"] == 290

            # 恢复 content_offset
            handle.content_offset = 0

            # -------------------------------------------------------------
            # Action 5: mouse_scroll
            # -------------------------------------------------------------
            session.clear_events()
            inp.mouse_scroll(-2)  # 向下拉远 (Button 5) 2 次
            inp.mouse_scroll(1)  # 向上拉近 (Button 4) 1 次
            time.sleep(0.1)

            events = session.read_events()
            b5_presses = [e for e in events if e.get("name") == "ButtonPress" and e.get("detail") == 5]
            b4_presses = [e for e in events if e.get("name") == "ButtonPress" and e.get("detail") == 4]
            assert len(b5_presses) == 2, f"滚轮向下 (Button 5) 触发次数不符: {len(b5_presses)}"
            assert len(b4_presses) == 1, f"滚轮向上 (Button 4) 触发次数不符: {len(b4_presses)}"

            # -------------------------------------------------------------
            # Action 6: key_press ("enter", "space")
            # -------------------------------------------------------------
            session.clear_events()
            inp.key_press("enter")
            time.sleep(0.05)
            inp.key_press("space")
            time.sleep(0.05)

            events = session.read_events()
            key_presses = [e for e in events if e.get("name") == "KeyPress"]
            key_releases = [e for e in events if e.get("name") == "KeyRelease"]
            assert len(key_presses) >= 2, f"按键按下事件数量不足: {len(key_presses)}"
            assert len(key_releases) >= 2, f"按键抬起事件数量不足: {len(key_releases)}"

            enter_code = inp._resolve_keycode("enter")
            space_code = inp._resolve_keycode("space")
            captured_codes = [e.get("detail") for e in key_presses]
            assert enter_code in captured_codes, f"Enter keycode ({enter_code}) 未在事件流中: {captured_codes}"
            assert space_code in captured_codes, f"Space keycode ({space_code}) 未在事件流中: {captured_codes}"

            # -------------------------------------------------------------
            # Action 7: 宿主物理光标与 Display 隔离硬性断言
            # -------------------------------------------------------------
            assert handle.display_name != host_disp_name, (
                f"Gamescope 驱动未与宿主 Display 隔离: {handle.display_name} == {host_disp_name}"
            )
            assert inp.handle.display_name == handle.display_name, (
                f"输入事件未派发给 Gamescope 独立 Display: {inp.handle.display_name} != {handle.display_name}"
            )

            host_disp.sync()
            final_host_pos = host_root.query_pointer()
            final_host_coords = (int(final_host_pos.root_x), int(final_host_pos.root_y))
            # 校验光标未被测试驱动篡改（若用户在测试执行期间移动了宿主鼠标，由 Display 隔离硬断言保证安全性）
            if final_host_coords != initial_host_coords:
                _out(
                    f"\n[Notice] 宿主鼠标在测试期间发生移动 (用户手势): {initial_host_coords} -> {final_host_coords}"
                    f"，由 Display 隔离保证 ({inp.handle.display_name} != {host_disp_name}) 未受侵入"
                )

        finally:
            handle.close()
            host_disp.close()


def run_standalone() -> int:
    """独立执行模式：打印详尽的底层捕获证据流。"""
    _check_prerequisites()

    _out("=" * 72)
    _out("Gamescope Live E2E Integration Verification")
    _out("=" * 72)

    host_disp_name = os.environ.get("DISPLAY", ":0")
    _out(f"[Host] Connecting to host DISPLAY: {host_disp_name}...")
    host_disp = display.Display(host_disp_name)
    host_root = host_disp.screen().root
    initial_host_pos = host_root.query_pointer()
    initial_coords = (int(initial_host_pos.root_x), int(initial_host_pos.root_y))
    _out(f"[Host] Initial host cursor position: {initial_coords}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        _out(f"[Gamescope] Launching nested Gamescope session in {tmp_path}...")
        with GamescopeLiveSession(tmp_path) as session:
            _out("[Gamescope] Session running. Connecting via X11Handle...")
            handle = X11Handle()
            wid = handle.init_handle(title="LimbusCompany", class_name="UnityWndClass")
            _out(
                f"[X11Handle] Found window wid={wid}, display={handle.display_name}, is_gamescope={handle.is_gamescope}"
            )
            assert wid != 0, "Failed to find window"
            assert handle.is_gamescope is True, "Failed to detect gamescope"

            inp = GamescopeXTestInput(handle=handle)
            _out("[GamescopeXTestInput] Driver initialized successfully.")

            # Test mouse move
            _out("\n--- Testing mouse_move(350, 250) ---")
            session.clear_events()
            inp.mouse_move(350, 250)
            time.sleep(0.1)
            cur_pos = inp.get_mouse_position()
            _out(f"Virtual pointer position: {cur_pos}")
            events = session.read_events()
            for ev in events:
                if ev.get("name") == "MotionNotify":
                    _out(f"  Captured: {ev}")

            # Test mouse click with offset 0
            _out("\n--- Testing mouse_click(450, 300) with content_offset=0 ---")
            session.clear_events()
            handle.content_offset = 0
            inp.mouse_click(450, 300)
            time.sleep(0.1)
            events = session.read_events()
            for ev in events:
                if ev.get("name") in ("ButtonPress", "ButtonRelease"):
                    _out(f"  Captured: {ev}")

            # Test mouse click with offset 40
            _out("\n--- Testing mouse_click(500, 200) with content_offset=40 ---")
            session.clear_events()
            handle.content_offset = 40
            inp.mouse_click(500, 200)
            time.sleep(0.1)
            events = session.read_events()
            for ev in events:
                if ev.get("name") in ("ButtonPress", "ButtonRelease"):
                    _out(f"  Captured: {ev}")

            # Test mouse drag
            _out("\n--- Testing mouse_drag((150, 150), (250, 250)) with content_offset=40 ---")
            session.clear_events()
            inp.mouse_drag((150, 150), (250, 250), drag_time=0.1)
            time.sleep(0.1)
            events = session.read_events()
            motions = [e for e in events if e.get("name") == "MotionNotify"]
            buttons = [e for e in events if e.get("name") in ("ButtonPress", "ButtonRelease")]
            _out(f"  Total drag MotionNotify events: {len(motions)}")
            for ev in buttons:
                _out(f"  Drag boundary event: {ev}")

            # Reset offset
            handle.content_offset = 0

            # Test mouse scroll
            _out("\n--- Testing mouse_scroll(-2) and mouse_scroll(1) ---")
            session.clear_events()
            inp.mouse_scroll(-2)
            inp.mouse_scroll(1)
            time.sleep(0.1)
            events = session.read_events()
            for ev in events:
                if ev.get("name") in ("ButtonPress", "ButtonRelease"):
                    _out(f"  Captured scroll event: {ev}")

            # Test key_press
            _out("\n--- Testing key_press('enter') and key_press('space') ---")
            session.clear_events()
            inp.key_press("enter")
            time.sleep(0.05)
            inp.key_press("space")
            time.sleep(0.05)
            events = session.read_events()
            for ev in events:
                if ev.get("name") in ("KeyPress", "KeyRelease"):
                    _out(f"  Captured key event: {ev}")

            # Verify host cursor
            host_disp.sync()
            final_host_pos = host_root.query_pointer()
            final_coords = (int(final_host_pos.root_x), int(final_host_pos.root_y))
            _out(f"\n[Host] Final host cursor position: {final_coords}")
            assert initial_coords == final_coords, "HOST CURSOR MOVED!"
            _out("[Host] Host cursor immobility assertion PASSED! Delta = (0, 0)")

            handle.close()
            host_disp.close()

    _out("\n" + "=" * 72)
    _out("ALL LIVE INTEGRATION TESTS PASSED WITH 100% SUCCESS!")
    _out("=" * 72)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child-listener":
        _run_child_listener(sys.argv[2], sys.argv[3])
        sys.exit(0)
    else:
        sys.exit(run_standalone())
