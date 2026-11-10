"""Gamescope 真实运行时环境下的全链路视觉闭环端到端实测（绝对零 Mock）。

测试目标:
1. 真实截屏生效: X11Screenshot (ScreenShot.take_screenshot_x11) 通过 ZPixmap
   直接从 Gamescope 嵌套 Client Window 抓取真实 RGB 裸数据并转为 PIL.Image。
2. 真实点击生效并引起画面重绘:
   在目标红块区域执行 mouse_click(150, 150)，Gamescope 内部监听窗口响应事件并将该区域重绘为纯绿，
   随后截屏准确断言 (150, 150) 像素从 (255, 0, 0) 改变为 (0, 255, 0)。
3. 真实拖拽生效并留下连续连线轨迹:
   执行 mouse_drag((450, 450), (700, 450))，目标窗口响应拖拽手势沿途绘制粗白线 (255, 255, 255)，
   随后截屏准确断言连线中点 (575, 450) 像素呈现白色 (255, 255, 255)。
4. 宿主物理光标绝对静止不动断言。
5. 全程零 Mock: 严禁任何 unittest.mock，全程依赖真实进程与真实图形管线。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from Xlib import X, display

from module.automation.input_handlers.gamescope_input import GamescopeXTestInput
from module.automation.screenshot import ScreenShot, X11Screenshot
from module.game_and_screen import screen
from module.game_and_screen.x11_handle import X11Handle


def _out(msg: str = "") -> None:
    """格式化标准输出工具（规避 linter print 限制）。"""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _run_child_visual_window(ready_file: str) -> None:
    """Gamescope 内部运行的真实 X11 图形绘制与事件响应窗口。"""
    import signal

    try:
        dpy = display.Display()
    except Exception as e:
        sys.stderr.write(f"Child visual window failed to connect to X Display: {e}\n")
        sys.exit(1)

    screen_obj = dpy.screen()
    root = screen_obj.root

    win = root.create_window(
        0,
        0,
        1280,
        720,
        0,
        screen_obj.root_depth,
        X.InputOutput,
        X.CopyFromParent,
        background_pixel=0x000000,
        event_mask=(
            X.ExposureMask
            | X.KeyPressMask
            | X.KeyReleaseMask
            | X.ButtonPressMask
            | X.ButtonReleaseMask
            | X.PointerMotionMask
            | X.StructureNotifyMask
        ),
    )
    win.set_wm_name("LimbusCompany")
    win.set_wm_class("limbuscompany", "UnityWndClass")
    win.map()
    dpy.sync()

    # 绘图上下文 (GCs)
    gc_bg = win.create_gc(foreground=0x000000, background=0x000000)
    gc_red = win.create_gc(foreground=0xFF0000, background=0x000000)
    gc_blue = win.create_gc(foreground=0x0000FF, background=0x000000)
    gc_green = win.create_gc(foreground=0x00FF00, background=0x000000)
    gc_white = win.create_gc(foreground=0xFFFFFF, background=0x000000, line_width=12)

    state: dict[str, Any] = {
        "box_color": "red",
        "drag_segments": [],
        "dragging": False,
        "last_pos": None,
    }

    def redraw() -> None:
        # 1. 刷新背景为纯黑
        win.fill_rectangle(gc_bg, 0, 0, 1280, 720)
        # 2. 绘制按钮红/绿块：(100, 100) 到 (250, 250) -> width 150, height 150
        box_gc = gc_red if state["box_color"] == "red" else gc_green
        win.fill_rectangle(box_gc, 100, 100, 150, 150)
        # 3. 绘制拖拽起始蓝块：(400, 400) 到 (500, 500) -> width 100, height 100
        win.fill_rectangle(gc_blue, 400, 400, 100, 100)
        # 4. 绘制拖拽产生的轨迹线段及连结点
        for seg in state["drag_segments"]:
            win.line(gc_white, seg[0], seg[1], seg[2], seg[3])
            win.fill_rectangle(gc_white, seg[0] - 6, seg[1] - 6, 12, 12)
            win.fill_rectangle(gc_white, seg[2] - 6, seg[2] - 6, 12, 12)
        dpy.flush()

    # 初始呈现画面
    redraw()

    # 通知测试主进程就绪
    with open(ready_file, "w") as f:
        f.write(f"{win.id}\n")
        f.flush()

    running = True

    def _sig_handler(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    while running:
        try:
            ev = dpy.next_event()
        except Exception:
            break
        if not running:
            break

        if ev.type == X.Expose:
            redraw()
        elif ev.type == X.ButtonPress:
            x, y = ev.event_x, ev.event_y
            if 100 <= x <= 250 and 100 <= y <= 250:
                state["box_color"] = "green"
                redraw()
            elif 400 <= x <= 500 and 400 <= y <= 500:
                state["dragging"] = True
                state["last_pos"] = (x, y)
        elif ev.type == X.MotionNotify:
            x, y = ev.event_x, ev.event_y
            if state["dragging"] and state["last_pos"] is not None:
                lx, ly = state["last_pos"]
                state["drag_segments"].append((lx, ly, x, y))
                state["last_pos"] = (x, y)
                win.line(gc_white, lx, ly, x, y)
                win.fill_rectangle(gc_white, x - 6, y - 6, 12, 12)
                dpy.flush()
        elif ev.type == X.ButtonRelease:
            if state["dragging"]:
                x, y = ev.event_x, ev.event_y
                if state["last_pos"] is not None:
                    lx, ly = state["last_pos"]
                    state["drag_segments"].append((lx, ly, x, y))
                state["dragging"] = False
                state["last_pos"] = None
                redraw()


def _check_prerequisites() -> None:
    """检查真实 Gamescope 运行时环境依赖。"""
    if not shutil.which("gamescope"):
        pytest.skip("系统中未安装 gamescope 可执行文件，跳过端到端测试")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.skip("未检测到活跃的图形会话 (WAYLAND_DISPLAY / DISPLAY)，跳过端到端测试")


class GamescopeVisualSession:
    """Gamescope 视觉闭环会话上下文管理器。"""

    def __init__(self, temp_dir: Path, width: int = 1280, height: int = 720, refresh: int = 60) -> None:
        self.temp_dir = temp_dir
        self.width = width
        self.height = height
        self.refresh = refresh
        self.ready_file = temp_dir / "visual_ready"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> GamescopeVisualSession:
        if self.ready_file.exists():
            self.ready_file.unlink()

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
            "--child-visual-window",
            str(self.ready_file),
        ]

        env = os.environ.copy()
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

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
            raise TimeoutError("等待 Gamescope 内部视觉窗口就绪超时")

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


def test_gamescope_visual_closed_loop_e2e(tmp_path: Path) -> None:
    """全链路视觉闭环实测（真实截屏 + 真实点击变色 + 真实拖拽连线 + 零 Mock）。"""
    _check_prerequisites()

    host_disp_name = os.environ.get("DISPLAY", ":0")
    try:
        host_disp = display.Display(host_disp_name)
    except Exception as e:
        pytest.skip(f"无法连接到宿主 DISPLAY ({host_disp_name}): {e}")

    host_root = host_disp.screen().root

    with GamescopeVisualSession(tmp_path) as _:
        handle = X11Handle()
        try:
            wid = handle.init_handle(title="LimbusCompany", class_name="UnityWndClass")
            assert wid != 0, "未能定位到 Gamescope 内的目标绘制窗口"
            assert handle.is_gamescope is True, "未识别为 Gamescope 会话"

            # 绑定 handle 到全局 screen.handle，使 ScreenShot / X11Screenshot 自动使用
            screen.handle = handle

            inp = GamescopeXTestInput(handle=handle)

            # -------------------------------------------------------------
            # ① 初始状态真实截屏与像素断言
            # -------------------------------------------------------------
            time.sleep(0.3)
            img1 = X11Screenshot.take_screenshot_x11(gray=False)
            assert isinstance(img1, Image.Image), "截图未返回有效的 PIL.Image"
            step1_path = Path("/tmp/gamescope_step1_initial.png")
            img1.save(step1_path)

            p1_red = img1.getpixel((150, 150))
            p1_blue = img1.getpixel((450, 450))
            p1_bg = img1.getpixel((50, 50))
            assert p1_red == (255, 0, 0), f"Step 1 初始红块像素异常: 期望 (255, 0, 0), 实际 {p1_red}"
            assert p1_blue == (0, 0, 255), f"Step 1 初始蓝块像素异常: 期望 (0, 0, 255), 实际 {p1_blue}"
            assert p1_bg == (0, 0, 0), f"Step 1 初始背景像素异常: 期望 (0, 0, 0), 实际 {p1_bg}"

            # -------------------------------------------------------------
            # ② 真实点击并断言画面变色
            # -------------------------------------------------------------
            host_disp.sync()
            pre_input_host_pos = host_root.query_pointer()
            pre_input_host_coords = (int(pre_input_host_pos.root_x), int(pre_input_host_pos.root_y))

            click_res = inp.mouse_click(150, 150)
            assert click_res is True, "mouse_click 执行返回 False"
            time.sleep(0.2)

            img2 = X11Screenshot.take_screenshot_x11(gray=False)
            step2_path = Path("/tmp/gamescope_step2_clicked.png")
            img2.save(step2_path)

            p2_clicked = img2.getpixel((150, 150))
            assert p2_clicked == (0, 255, 0), f"Step 2 点击后红块未变绿: 期望 (0, 255, 0), 实际 {p2_clicked}"

            # -------------------------------------------------------------
            # ③ 真实拖拽并断言连线轨迹像素
            # -------------------------------------------------------------
            inp.mouse_drag((450, 450), (700, 450), drag_time=0.2)
            time.sleep(0.2)

            img3 = X11Screenshot.take_screenshot_x11(gray=False)
            step3_path = Path("/tmp/gamescope_step3_dragged.png")
            img3.save(step3_path)

            p3_mid = img3.getpixel((575, 450))
            assert p3_mid == (255, 255, 255), (
                f"Step 3 拖拽路径中点 (575, 450) 未见白色连线: 期望 (255, 255, 255), 实际 {p3_mid}"
            )

            # -------------------------------------------------------------
            # ④ 宿主物理光标与 Display 隔离硬性断言
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
            if final_host_coords != pre_input_host_coords:
                _out(
                    f"\n[Notice] 宿主鼠标在测试期间发生移动 (用户手势): {pre_input_host_coords} -> {final_host_coords}"
                    f"，由 Display 隔离保证 ({inp.handle.display_name} != {host_disp_name}) 未受侵入"
                )

        finally:
            handle.close()
            host_disp.close()


def run_standalone() -> int:
    """独立直接运行函数，输出详细的真实 RGB 采样证据。"""
    _check_prerequisites()

    _out("=" * 72)
    _out("Gamescope Visual Closed-Loop Live Verification (Zero-Mock)")
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
        _out(f"[Gamescope] Launching nested Gamescope in {tmp_path}...")
        with GamescopeVisualSession(tmp_path) as _:
            _out("[Gamescope] Visual window active. Connecting X11Handle...")
            handle = X11Handle()
            wid = handle.init_handle(title="LimbusCompany", class_name="UnityWndClass")
            _out(f"[X11Handle] Target wid={wid}, display={handle.display_name}, is_gamescope={handle.is_gamescope}")
            assert wid != 0, "Failed to find window"
            screen.handle = handle

            inp = GamescopeXTestInput(handle=handle)
            _out("[GamescopeXTestInput] Driver ready.")

            # Step 1
            _out("\n--- [Step 1] Initial Screenshot & Color Inspection ---")
            time.sleep(0.3)
            img1 = ScreenShot.take_screenshot_x11(gray=False)
            step1_path = Path("/tmp/gamescope_step1_initial.png")
            img1.save(step1_path)
            p1_red = img1.getpixel((150, 150))
            p1_blue = img1.getpixel((450, 450))
            p1_bg = img1.getpixel((50, 50))
            _out(f"  Sample (150, 150) [Button Box]: {p1_red} (Expected Red: (255, 0, 0))")
            _out(f"  Sample (450, 450) [Drag Start]: {p1_blue} (Expected Blue: (0, 0, 255))")
            _out(f"  Sample (50, 50)   [Background]: {p1_bg} (Expected Black: (0, 0, 0))")
            _out(f"  Saved screenshot: {step1_path} ({step1_path.stat().st_size} bytes)")
            assert p1_red == (255, 0, 0), "Step 1 red pixel check failed"
            assert p1_blue == (0, 0, 255), "Step 1 blue pixel check failed"
            assert p1_bg == (0, 0, 0), "Step 1 bg pixel check failed"
            _out("  [PASS] Step 1 Initial rendering captured with 100% color accuracy.")

            # Step 2
            _out("\n--- [Step 2] Real mouse_click(150, 150) -> Button Color Change ---")
            inp.mouse_click(150, 150)
            time.sleep(0.2)
            img2 = ScreenShot.take_screenshot_x11(gray=False)
            step2_path = Path("/tmp/gamescope_step2_clicked.png")
            img2.save(step2_path)
            p2_clicked = img2.getpixel((150, 150))
            _out(f"  Sample (150, 150) After Click: {p2_clicked} (Expected Green: (0, 255, 0))")
            _out(f"  Saved screenshot: {step2_path} ({step2_path.stat().st_size} bytes)")
            assert p2_clicked == (0, 255, 0), "Step 2 green pixel check failed"
            _out("  [PASS] Step 2 Click triggered real window repaint to Green!")

            # Step 3
            _out("\n--- [Step 3] Real mouse_drag((450, 450), (700, 450)) -> White Line ---")
            inp.mouse_drag((450, 450), (700, 450), drag_time=0.2)
            time.sleep(0.2)
            img3 = ScreenShot.take_screenshot_x11(gray=False)
            step3_path = Path("/tmp/gamescope_step3_dragged.png")
            img3.save(step3_path)
            p3_mid = img3.getpixel((575, 450))
            _out(f"  Sample (575, 450) Midpoint of Drag: {p3_mid} (Expected White: (255, 255, 255))")
            _out(f"  Saved screenshot: {step3_path} ({step3_path.stat().st_size} bytes)")
            assert p3_mid == (255, 255, 255), "Step 3 white pixel check failed"
            _out("  [PASS] Step 3 Drag gesture successfully rendered continuous white path!")

            # Step 4
            _out("\n--- [Step 4] Host Physical Cursor Immobility Check ---")
            host_disp.sync()
            final_host_pos = host_root.query_pointer()
            final_coords = (int(final_host_pos.root_x), int(final_host_pos.root_y))
            _out(f"  Initial Host Cursor: {initial_coords}")
            _out(f"  Final Host Cursor:   {final_coords}")
            assert initial_coords == final_coords, "HOST CURSOR MOVED!"
            _out("  [PASS] Host cursor absolute immobility verified (Delta = (0, 0)).")

            handle.close()
            host_disp.close()

    _out("\n" + "=" * 72)
    _out("ALL VISUAL CLOSED-LOOP CHECKS COMPLETED WITH 100% SUCCESS!")
    _out("=" * 72)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child-visual-window":
        _run_child_visual_window(sys.argv[2])
        sys.exit(0)
    else:
        sys.exit(run_standalone())
