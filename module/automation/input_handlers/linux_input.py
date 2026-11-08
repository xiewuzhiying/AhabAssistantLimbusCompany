"""Linux 前台输入实现，接口与 Windows 版 `Input` 一致。

Linux 的 X11 协议没有 Win32 PostMessage 后台消息通道的可靠等价物
（XSendEvent 会被 Unity/Proton 游戏忽略），因此普通桌面 Linux 下统一使用前台输入。
在 Gamescope 嵌套会话下，优先使用无侵入隔离的 GamescopeXTestInput。
"""

from __future__ import annotations

import os
import random
import time
from typing import overload

import pyautogui
import pyperclip
from Xlib import X
from Xlib import display as xdisplay
from Xlib.ext import xtest

from module.config import cfg
from module.logger import log
from utils.singletonmeta import SingletonMeta

from ...game_and_screen import screen
from . import AbstractInput
from .gamescope_input import GamescopeXTestInput
from .scroll_swipe import build_windows_scroll_swipe_plan

pyautogui.FAILSAFE = False


def _use_pyautogui_mouse() -> bool:
    """X11 下沿用稳定的 pyautogui 鼠标路径；Wayland 才使用特殊注入。"""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if not os.environ.get("DISPLAY"):
        return False
    if session_type == "wayland":
        return False
    return session_type == "x11" or not os.environ.get("WAYLAND_DISPLAY")


# ---------------------------------------------------------------- Xlib 注入

_DISPLAY = None


def _dpy() -> xdisplay.Display:
    global _DISPLAY
    if _DISPLAY is None:
        _DISPLAY = xdisplay.Display()
    return _DISPLAY


def _warp(x: int, y: int) -> None:
    """XWarpPointer 定位（仅回退路径使用）"""
    _dpy().screen().root.warp_pointer(int(x), int(y))
    _dpy().flush()


def _button_press(button: int = 1) -> None:
    xtest.fake_input(_dpy(), X.ButtonPress, button)
    _dpy().flush()


def _button_release(button: int = 1) -> None:
    xtest.fake_input(_dpy(), X.ButtonRelease, button)
    _dpy().flush()


def _query_pointer() -> tuple[int, int]:
    p = _dpy().screen().root.query_pointer()
    _dpy().sync()
    return int(p.root_x), int(p.root_y)


def _real_cursor_pos() -> tuple[int, int] | None:
    """读取真实光标位置。"""
    return _query_pointer()


def _xtest_motion(x: int, y: int) -> None:
    """XTEST 仿真移动"""
    xtest.fake_input(_dpy(), X.MotionNotify, x=int(x), y=int(y))
    _dpy().flush()


def _abs_move(x: int, y: int) -> bool:
    """移动光标到屏幕坐标 (x, y)。"""
    try:
        _xtest_motion(x, y)
        time.sleep(0.02)
        cur = _query_pointer()
        if abs(cur[0] - x) <= 5 and abs(cur[1] - y) <= 5:
            return True
        _warp(x, y)
        time.sleep(0.02)
        cur = _query_pointer()
        return abs(cur[0] - x) <= 5 and abs(cur[1] - y) <= 5
    except Exception as e:
        log.debug(f"移动光标失败: {e}")
        return False


def _left_press() -> None:
    _button_press()


def _left_release() -> None:
    time.sleep(0.02)
    _button_release()


def _left_click() -> None:
    _left_press()
    _left_release()


# ---------------------------------------------------------------- 输入类


class LinuxInput(AbstractInput, metaclass=SingletonMeta):
    """基于 Xlib/pyautogui 的输入类。Gamescope 环境下自动委托给 GamescopeXTestInput。"""

    def __init__(self) -> None:
        super().__init__()
        self._gamescope_input: GamescopeXTestInput | None = None

    def _get_gamescope_driver(self) -> GamescopeXTestInput | None:
        if getattr(screen.handle, "is_gamescope", False):
            if self._gamescope_input is None:
                self._gamescope_input = GamescopeXTestInput()
            return self._gamescope_input
        return None

    @staticmethod
    def _window_is_ready() -> bool:
        """窗口重建或冷启动期间不允许把点击坐标退化到屏幕 (1, 1)。"""
        try:
            left, top, right, bottom = screen.handle.rect(True)
        except Exception as e:
            log.debug(f"读取 Linux 游戏窗口区域失败，跳过输入: {e}")
            return False
        return right > left and bottom > top

    @overload
    def pos_offset(self, x: int, y: int) -> tuple[int, int]: ...
    @overload
    def pos_offset(self, pos: tuple[int, int]) -> tuple[int, int]: ...

    def pos_offset(self, *args) -> tuple[int, int]:  # type: ignore
        """根据当前窗口位置偏移点击位置"""
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.pos_offset(*args)
        if len(args) == 2:
            x, y = args
        elif isinstance(args[0], tuple):
            x, y = args[0]
        else:
            raise ValueError("pos_offset 接受两个整数参数或一个包含两个整数的元组")
        real_x, real_y, _, _ = screen.handle.rect(True)
        return x + real_x, y + real_y

    def get_mouse_position(self) -> tuple[int, int]:
        """获取鼠标当前位置（X11 异常时返回 (0, 0)）"""
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.get_mouse_position()
        try:
            if _use_pyautogui_mouse():
                pos = pyautogui.position()
                return int(pos.x), int(pos.y)
            return _query_pointer()
        except Exception:
            log.debug("获取鼠标位置失败，返回 (0, 0)")
            return (0, 0)

    def mouse_click(self, x, y, times=1, move_back=False) -> bool:
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.mouse_click(x, y, times=times, move_back=move_back)

        if not self._window_is_ready():
            log.debug("Linux 游戏窗口尚未就绪，跳过鼠标点击")
            return False
        current_mouse_position = self.get_mouse_position() if move_back else None

        msg = f"点击位置:({x},{y})"
        log.debug(msg, stacklevel=2)
        x, y = self.pos_offset(x, y)
        for _ in range(times):
            if _use_pyautogui_mouse():
                pyautogui.click(x, y)
            else:
                if not _abs_move(x, y):
                    log.debug(f"Linux 鼠标无法移动到目标位置 ({x}, {y})，跳过点击")
                    return False
                time.sleep(0.05)
                _left_click()
                time.sleep(0.05)

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

        self.wait_pause()
        return True

    def mouse_drag_down(self, x, y, reverse=1, move_back=True) -> None:
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_drag_down(x, y, reverse=reverse, move_back=move_back)
            return

        current_mouse_position = self.get_mouse_position() if move_back else None

        scale = cfg.set_win_size / 1080
        x, y = self.pos_offset(x, y)
        if _use_pyautogui_mouse():
            pyautogui.moveTo(x, y)
            pyautogui.mouseDown()
            pyautogui.moveTo(x, y + int(300 * scale * reverse), duration=0.4)
            pyautogui.mouseUp()
        else:
            _warp(x, y)
            time.sleep(0.1)
            _button_press()
            time.sleep(0.1)
            distance = int(300 * scale * reverse)
            steps = 8
            for i in range(1, steps + 1):
                _warp(x, y + int(distance * i / steps))
                time.sleep(0.04)
            time.sleep(0.2)
            _button_release()

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

    def mouse_drag(self, x, y, drag_time=0.1, dx=0, dy=0, move_back=True) -> None:
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_drag(x, y, drag_time=drag_time, dx=dx, dy=dy, move_back=move_back)
            return

        current_mouse_position = self.get_mouse_position() if move_back else None
        x, y = self.pos_offset(x, y)
        if _use_pyautogui_mouse():
            pyautogui.moveTo(x, y)
            pyautogui.mouseDown()
            pyautogui.moveTo(x + dx, y + dy, duration=drag_time)
            if drag_time * 0.3 > 0.5:
                time.sleep(drag_time * 0.3)
            else:
                time.sleep(0.5)
            pyautogui.mouseUp()
        else:
            _abs_move(x, y)
            time.sleep(0.1)
            _left_press()
            time.sleep(0.1)
            _abs_move(x + dx, y + dy)
            if drag_time * 0.3 > 0.5:
                time.sleep(drag_time * 0.3)
            else:
                time.sleep(0.5)
            _left_release()

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

    def mouse_swipe_for_scroll(self, x, y, duration=0.3, dx=0, dy=0, move_back=True) -> None:
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_swipe_for_scroll(x, y, duration=duration, dx=dx, dy=dy, move_back=move_back)
            return

        current_mouse_position = self.get_mouse_position() if move_back else None

        raw_plan, settle_duration = build_windows_scroll_swipe_plan(
            x, y, dx, dy, duration
        )
        plan = [
            (self.pos_offset(*point), move_duration)
            for point, move_duration in raw_plan
        ]
        if _use_pyautogui_mouse():
            pyautogui.moveTo(*plan[0][0])
            pyautogui.mouseDown()
            for point, move_duration in plan[1:]:
                pyautogui.moveTo(*point, duration=move_duration)
            if settle_duration:
                time.sleep(settle_duration)
            pyautogui.mouseUp()
        else:
            _abs_move(*plan[0][0])
            time.sleep(0.1)
            _left_press()
            for point, move_duration in plan[1:]:
                _abs_move(*point)
                time.sleep(move_duration)
            if settle_duration:
                time.sleep(settle_duration)
            _left_release()

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

    def mouse_scroll(self, direction: int = -3) -> bool:
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.mouse_scroll(direction)

        if direction <= 0:
            msg = "鼠标滚动滚轮，远离界面"
        else:
            msg = "鼠标滚动滚轮，拉近界面"
        log.debug(msg, stacklevel=2)
        if _use_pyautogui_mouse():
            pyautogui.scroll(direction)
        else:
            button = 4 if direction > 0 else 5
            for _ in range(abs(direction) or 1):
                _button_press(button)
                _button_release(button)
                time.sleep(0.05)
        return True

    def mouse_click_blank(self, coordinate=(1, 1), times=1, move_back=False) -> bool:
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.mouse_click_blank(coordinate, times=times, move_back=move_back)

        if not self._window_is_ready():
            log.debug("Linux 游戏窗口尚未就绪，跳过空白位置点击")
            return False
        current_mouse_position = self.get_mouse_position() if move_back else None

        msg = "点击（1，1）空白位置"
        log.debug(msg, stacklevel=2)
        x = coordinate[0] + random.randint(0, 10)
        y = coordinate[1] + random.randint(0, 10)
        x, y = self.pos_offset(x, y)
        for _ in range(times):
            if _use_pyautogui_mouse():
                pyautogui.click(x, y)
            else:
                if not _abs_move(x, y):
                    log.debug(f"Linux 鼠标无法移动到空白位置 ({x}, {y})，跳过点击")
                    return False
                time.sleep(0.05)
                _left_click()

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

        self.wait_pause()
        return True

    def mouse_to_blank(self, coordinate=(1, 1), move_back=False) -> None:
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_to_blank(coordinate, move_back=move_back)
            return

        current_mouse_position = self.get_mouse_position() if move_back else None

        msg = "鼠标移动到空白，避免遮挡"
        log.debug(msg, stacklevel=2)
        x, y = self.pos_offset(*coordinate)
        if _use_pyautogui_mouse():
            pyautogui.moveTo(x, y)
        else:
            _abs_move(x, y)

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)
        self.wait_pause()

    def mouse_move(self, coordinate=(1, 1)) -> None:
        """鼠标移动到指定坐标（屏幕绝对坐标）

        Args:
            coordinate (tuple): 坐标元组 (x, y)
        """
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_move(coordinate)
            return

        if _use_pyautogui_mouse():
            pyautogui.moveTo(int(coordinate[0]), int(coordinate[1]))
        else:
            _abs_move(int(coordinate[0]), int(coordinate[1]))
        self.wait_pause()

    def mouse_drag_link(self, position: list, drag_time=0.1, move_back=False) -> None:
        driver = self._get_gamescope_driver()
        if driver is not None:
            driver.mouse_drag_link(position, drag_time=drag_time, move_back=move_back)
            return

        if not position:
            return

        current_mouse_position = self.get_mouse_position() if move_back else None

        x, y = self.pos_offset(position[0][0], position[0][1])
        if _use_pyautogui_mouse():
            pyautogui.moveTo(x, y)
            pyautogui.mouseDown()
            for pos in position:
                x, y = self.pos_offset(pos[0], pos[1])
                pyautogui.moveTo(x, y, duration=drag_time)
            pyautogui.mouseUp()
        else:
            _abs_move(x, y)
            time.sleep(0.1)
            _left_press()
            for pos in position:
                x, y = self.pos_offset(pos[0], pos[1])
                _abs_move(x, y)
                time.sleep(drag_time)
            _left_release()

        if move_back and current_mouse_position:
            self.mouse_move(current_mouse_position)

    def key_press(self, key):
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.key_press(key)
        return pyautogui.press(key)

    def input_text(self, text: str):
        driver = self._get_gamescope_driver()
        if driver is not None:
            return driver.input_text(text)
        if not text:
            log.warning("未提供要粘贴的文本")
            return
        try:
            pyperclip.copy(text)
        except Exception:
            try:
                pyautogui.typewrite(text)
                return
            except Exception:
                log.error("pyautogui 直接输入失败")
                return
        pyautogui.hotkey("ctrl", "v")
