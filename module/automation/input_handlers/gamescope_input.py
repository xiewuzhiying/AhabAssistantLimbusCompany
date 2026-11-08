"""基于 XTest 的 Gamescope 嵌套会话纯后台隔离输入驱动。

本模块通过 python-xlib 与 XTest 扩展向 Gamescope 嵌套显示服务发送虚拟事件。
由于 Gamescope 创建了完全隔离的虚拟 X11 屏幕，所有在此连接上发出的 fake_input
仅作用于游戏本身，绝不移动宿主机物理光标，亦不干扰宿主键盘输入。
"""

from __future__ import annotations

import math
import random
import time
from contextlib import nullcontext
from typing import Any

from Xlib import XK, X
from Xlib.ext import xtest

from module.config import cfg
from module.logger import log

from . import AbstractInput
from .scroll_swipe import build_windows_scroll_swipe_plan

_KEY_MAPPING: dict[str, int] = {
    "enter": XK.XK_Return,
    "return": XK.XK_Return,
    "esc": XK.XK_Escape,
    "escape": XK.XK_Escape,
    "space": XK.XK_space,
    "tab": XK.XK_Tab,
    "backspace": XK.XK_BackSpace,
    "shift": XK.XK_Shift_L,
    "ctrl": XK.XK_Control_L,
    "alt": XK.XK_Alt_L,
    "up": XK.XK_Up,
    "down": XK.XK_Down,
    "left": XK.XK_Left,
    "right": XK.XK_Right,
    "home": XK.XK_Home,
    "end": XK.XK_End,
    "pageup": XK.XK_Page_Up,
    "pagedown": XK.XK_Page_Down,
    "delete": XK.XK_Delete,
    "insert": XK.XK_Insert,
}


def ease_out_cubic(t: float) -> float:
    """三次缓动（Ease-Out Cubic）：t 范围 [0, 1]，初始速度大，平滑减速至终点。"""
    clamped_t = max(0.0, min(1.0, float(t)))
    return 1.0 - math.pow(1.0 - clamped_t, 3)


class GamescopeXTestInput(AbstractInput):
    """基于 XTest 的 Gamescope 无侵入隔离后台输入驱动。

    核心特性:
    - 仅向 Gamescope 嵌套 X11 Display 派发 XTest 事件，彻底与宿主机输入解耦。
    - 所有鼠标坐标自动叠加 handle.content_offset 与窗口基准。
    - 严格通过 handle.lock 进行 Xlib 请求串行化保护。
    """

    def __init__(self, handle: Any | None = None) -> None:
        super().__init__()
        self._handle = handle

    @property
    def is_gamescope(self) -> bool:
        """指示当前驱动是否运行在 Gamescope 嵌套隔离会话中。"""
        return True

    @property
    def handle(self) -> Any:
        if self._handle is not None:
            return self._handle
        from module.game_and_screen import screen

        return screen.handle

    @property
    def display(self) -> Any:
        handle = self.handle
        disp = getattr(handle, "display", None)
        if disp is not None:
            return disp
        raise RuntimeError("未连接到有效的 X11 Display")

    @property
    def lock(self) -> Any:
        handle = self.handle
        lock = getattr(handle, "lock", None)
        return lock if lock is not None else nullcontext()

    def _window_is_ready(self) -> bool:
        """检查游戏窗口是否已就绪。窗口重建或冷启动期间跳过输入。"""
        try:
            handle = self.handle
            if hasattr(handle, "hwnd") and handle.hwnd == 0:
                return False
            try:
                rect = handle.rect(False)
            except TypeError:
                rect = handle.rect()
            if not rect or len(rect) < 4:
                return False
            left, top, right, bottom = rect[0], rect[1], rect[2], rect[3]
            return right > left and bottom > top
        except Exception as e:
            log.debug(f"读取游戏窗口几何失败: {e}")
            return False

    def pos_offset(self, *args) -> tuple[int, int]:
        """根据窗口基准坐标及 handle.content_offset 自动计算最终目标坐标。

        支持参数形式:
        - pos_offset(x, y)
        - pos_offset((x, y))
        """
        try:
            if len(args) == 2:
                x, y = int(args[0]), int(args[1])
            elif len(args) == 1 and hasattr(args[0], "__getitem__") and not isinstance(args[0], (str, bytes)) and len(args[0]) >= 2:
                x, y = int(args[0][0]), int(args[0][1])
            else:
                raise ValueError("pos_offset 接受两个数值参数或一个包含至少两个数值的序列")
        except (TypeError, ValueError) as e:
            raise ValueError(f"pos_offset 参数无效: {e}") from e

        handle = self.handle
        offset = getattr(handle, "content_offset", 0) or 0
        try:
            rect = handle.rect(False)
        except TypeError:
            rect = handle.rect()
        except Exception as e:
            log.debug(f"获取窗口几何失败，坐标基准默认为 (0, 0): {e}")
            rect = (0, 0, 0, 0)

        real_x = int(x + rect[0])
        real_y = int(y + rect[1] + offset)
        return real_x, real_y

    def get_mouse_position(self) -> tuple[int, int]:
        """获取 Gamescope 虚拟会话中的当前指针位置。"""
        try:
            with self.lock:
                p = self.display.screen().root.query_pointer()
                self.display.sync()
                return int(p.root_x), int(p.root_y)
        except Exception as e:
            log.debug(f"获取 Gamescope 虚拟指针位置失败，返回 (0, 0): {e}")
            return (0, 0)

    def _send_fake_input(self, event_type: int, detail: int = 0, x: int = 0, y: int = 0) -> bool:
        """线程安全地向 Gamescope X11 会话发送 XTest 伪输入事件。"""
        try:
            with self.lock:
                dpy = self.display
                if not hasattr(dpy, "xtest_fake_input") and hasattr(dpy, "has_extension"):
                    try:
                        dpy.has_extension("XTEST")
                    except Exception:
                        pass
                xtest.fake_input(dpy, event_type, detail=detail, x=int(x), y=int(y))
                dpy.flush()
            return True
        except Exception as e:
            log.debug(f"发送 Gamescope XTest 事件失败 (type={event_type}, detail={detail}, x={x}, y={y}): {e}")
            return False

    def _send_motion(self, x: int, y: int) -> bool:
        return self._send_fake_input(X.MotionNotify, detail=0, x=x, y=y)

    def _send_button(self, button: int = 1, press: bool = True) -> bool:
        event_type = X.ButtonPress if press else X.ButtonRelease
        return self._send_fake_input(event_type, detail=button)

    def mouse_move(self, *args, **kwargs) -> None:
        """将 Gamescope 虚拟光标移动到指定绝对坐标。"""
        if len(args) == 2:
            x, y = int(args[0]), int(args[1])
        elif len(args) == 1 and hasattr(args[0], "__getitem__") and len(args[0]) >= 2:
            x, y = int(args[0][0]), int(args[0][1])
        elif "coordinate" in kwargs:
            coord = kwargs["coordinate"]
            x, y = int(coord[0]), int(coord[1])
        elif len(args) == 1:
            x, y = int(args[0]), int(args[0])
        else:
            x, y = 1, 1

        self._send_motion(int(x), int(y))
        self.wait_pause()

    def mouse_down(self, x: int | None = None, y: int | None = None, button: int = 1) -> None:
        """在 Gamescope 虚拟会话中按下鼠标按键。提供坐标时自动叠加 content_offset。"""
        if x is not None and y is not None:
            real_x, real_y = self.pos_offset(x, y)
            self._send_motion(real_x, real_y)
            time.sleep(0.02)
        self._send_button(button, press=True)

    def mouse_up(self, x: int | None = None, y: int | None = None, button: int = 1) -> None:
        """在 Gamescope 虚拟会话中释放鼠标按键。提供坐标时自动叠加 content_offset。"""
        if x is not None and y is not None:
            real_x, real_y = self.pos_offset(x, y)
            self._send_motion(real_x, real_y)
            time.sleep(0.02)
        self._send_button(button, press=False)

    def mouse_click(
        self,
        x: int,
        y: int,
        times: int = 1,
        move_back: bool = False,
        button: int = 1,
    ) -> bool:
        """在 Gamescope 虚拟会话中执行点击。自动叠加 handle.content_offset。"""
        if not self._window_is_ready():
            log.debug("游戏窗口尚未就绪，跳过鼠标点击")
            return False

        current_pos = self.get_mouse_position() if move_back else None
        real_x, real_y = self.pos_offset(x, y)

        log.debug(f"Gamescope 点击位置: ({x}, {y}) -> 虚拟显示 ({real_x}, {real_y})", stacklevel=2)

        for _ in range(times):
            self._send_motion(real_x, real_y)
            time.sleep(0.02)
            self._send_button(button, press=True)
            # ADR 0001 §4: Unity UGUI 跨帧采样需保持按住 100~150ms
            time.sleep(0.12)
            self._send_button(button, press=False)
            time.sleep(0.03)

        if move_back and current_pos is not None:
            self.mouse_move(*current_pos)

        self.wait_pause()
        return True

    def mouse_drag(self, *args, **kwargs) -> None:
        """在 Gamescope 虚拟会话中执行鼠标拖拽。

        支持两种参数格式:
        1. mouse_drag(start, end, drag_time=0.1, move_back=False)
           其中 start=(x1, y1), end=(x2, y2)
        2. mouse_drag(x, y, drag_time=0.1, dx=0, dy=0, move_back=True)
        """
        if (
            len(args) >= 2
            and hasattr(args[0], "__getitem__")
            and not isinstance(args[0], (str, bytes))
            and hasattr(args[1], "__getitem__")
            and not isinstance(args[1], (str, bytes))
        ):
            start_x, start_y = int(args[0][0]), int(args[0][1])
            end_x, end_y = int(args[1][0]), int(args[1][1])
            drag_time = kwargs.get("drag_time", args[2] if len(args) > 2 else 0.1)
            move_back = kwargs.get("move_back", args[3] if len(args) > 3 else False)
        elif len(args) >= 2:
            start_x, start_y = int(args[0]), int(args[1])
            drag_time = kwargs.get("drag_time", args[2] if len(args) > 2 else 0.1)
            dx = kwargs.get("dx", args[3] if len(args) > 3 else 0)
            dy = kwargs.get("dy", args[4] if len(args) > 4 else 0)
            move_back = kwargs.get("move_back", args[5] if len(args) > 5 else True)
            end_x, end_y = int(start_x + dx), int(start_y + dy)
        else:
            raise ValueError("mouse_drag 参数格式不正确")

        current_pos = self.get_mouse_position() if move_back else None

        real_start_x, real_start_y = self.pos_offset(start_x, start_y)
        real_end_x, real_end_y = self.pos_offset(end_x, end_y)

        self._send_motion(real_start_x, real_start_y)
        time.sleep(0.02)
        self._send_button(1, press=True)
        time.sleep(0.02)

        steps = max(5, int(drag_time / 0.02))
        step_duration = drag_time / steps if steps > 0 else 0
        for i in range(1, steps + 1):
            t = i / steps
            progress = ease_out_cubic(t)
            cur_x = int(real_start_x + (real_end_x - real_start_x) * progress)
            cur_y = int(real_start_y + (real_end_y - real_start_y) * progress)
            self._send_motion(cur_x, cur_y)
            if step_duration > 0:
                time.sleep(step_duration)

        # 对齐 Windows (input.py:136) 与模拟器契约：释放前保持静止以彻底消除速度向量，
        # 避免触发 Unity 滚动惯性 (Fling/Inertia Scrolling) 导致迷宫地图与视口对齐漂移
        settle = kwargs.get("settle_time", max(0.45, drag_time * 0.3))
        time.sleep(settle)
        self._send_button(1, press=False)
        time.sleep(0.02)

        if move_back and current_pos is not None:
            self.mouse_move(*current_pos)

        self.wait_pause()

    def mouse_drag_down(self, x: int, y: int, reverse: int = 1, move_back: bool = True) -> None:
        """沿 Y 轴按窗口缩放比例拖拽指定距离。"""
        scale = getattr(cfg, "set_win_size", 1080) / 1080
        dy = int(300 * scale * reverse)
        self.mouse_drag(x, y, drag_time=0.4, dx=0, dy=dy, move_back=move_back)

    def mouse_drag_link(self, position: list, drag_time: float = 0.1, move_back: bool = False) -> None:
        """沿一系列目标坐标连续拖拽（如连线技能选择）。"""
        if not position:
            return
        current_pos = self.get_mouse_position() if move_back else None

        first_x, first_y = self.pos_offset(*position[0])
        self._send_motion(first_x, first_y)
        time.sleep(0.05)
        self._send_button(1, press=True)
        time.sleep(0.05)

        previous_x, previous_y = first_x, first_y
        steps = max(1, math.ceil(drag_time * 60))
        step_duration = max(0.0, drag_time) / steps
        for pt in position[1:]:
            nx, ny = self.pos_offset(*pt)
            # The gear template anchor sits above its interactive area. Unity
            # must sample the path into the gear before reaching a skill slot;
            # teleporting between waypoints opens the portrait's E.G.O menu.
            for step in range(1, steps + 1):
                self._send_motion(
                    round(previous_x + (nx - previous_x) * step / steps),
                    round(previous_y + (ny - previous_y) * step / steps),
                )
                time.sleep(step_duration)
            previous_x, previous_y = nx, ny

        time.sleep(0.05)
        self._send_button(1, press=False)
        time.sleep(0.02)

        if move_back and current_pos is not None:
            self.mouse_move(*current_pos)

    def mouse_swipe_for_scroll(
        self,
        x: int,
        y: int,
        duration: float = 0.3,
        dx: int = 0,
        dy: int = 0,
        move_back: bool = True,
    ) -> None:
        """列表滚动手势实现，避开长按判定区并平滑释放。"""
        current_pos = self.get_mouse_position() if move_back else None

        raw_plan, settle_duration = build_windows_scroll_swipe_plan(x, y, dx, dy, duration)
        plan = [(self.pos_offset(*point), move_dur) for point, move_dur in raw_plan]

        start_pt = plan[0][0]
        self._send_motion(start_pt[0], start_pt[1])
        time.sleep(0.02)
        self._send_button(1, press=True)
        time.sleep(0.02)

        previous_x, previous_y = start_pt
        for point, move_dur in plan[1:]:
            # Match the duration-based motion used by the desktop adapters.
            # Teleporting to the endpoint then sleeping lets a frame-sampled
            # UI begin its drag at that endpoint, losing the page displacement.
            steps = max(1, math.ceil(move_dur * 60))
            step_duration = max(0.0, move_dur) / steps
            for step in range(1, steps + 1):
                self._send_motion(
                    round(previous_x + (point[0] - previous_x) * step / steps),
                    round(previous_y + (point[1] - previous_y) * step / steps),
                )
                time.sleep(step_duration)
            previous_x, previous_y = point

        if settle_duration:
            time.sleep(settle_duration)

        self._send_button(1, press=False)
        time.sleep(0.02)

        if move_back and current_pos is not None:
            self.mouse_move(*current_pos)

    def mouse_scroll(self, direction: int = -3) -> bool:
        """鼠标滚轮滚动。正数表示向上拉近(Button4)，负数表示向下拉远(Button5)。"""
        if direction <= 0:
            log.debug("Gamescope 虚拟鼠标滚轮滚动，远离界面", stacklevel=2)
        else:
            log.debug("Gamescope 虚拟鼠标滚轮滚动，拉近界面", stacklevel=2)

        button = 4 if direction > 0 else 5
        count = abs(direction) or 1
        for _ in range(count):
            self._send_button(button, press=True)
            time.sleep(0.02)
            self._send_button(button, press=False)
            time.sleep(0.03)
        return True

    def mouse_click_blank(self, coordinate=(1, 1), times: int = 1, move_back: bool = False) -> bool:
        """点击空白区域。"""
        if not self._window_is_ready():
            log.debug("游戏窗口尚未就绪，跳过空白点击")
            return False

        x = coordinate[0] + random.randint(0, 10)
        y = coordinate[1] + random.randint(0, 10)
        return self.mouse_click(x, y, times=times, move_back=move_back)

    def mouse_to_blank(self, coordinate=(1, 1), move_back: bool = False) -> None:
        """移动虚拟指针到空白区域，避免遮挡。"""
        current_pos = self.get_mouse_position() if move_back else None
        x, y = self.pos_offset(*coordinate)
        self._send_motion(x, y)
        if move_back and current_pos is not None:
            self.mouse_move(*current_pos)
        self.wait_pause()

    # ---------------------------------------------------------------- 键盘接口
    def _resolve_keycode(self, key: str) -> int:
        """将按键名或字符转换为 Gamescope X11 Display 上的有效 keycode。"""
        try:
            with self.lock:
                dpy = self.display
                k_lower = key.lower()
                keysym = _KEY_MAPPING.get(k_lower)
                if keysym is None:
                    if len(key) == 1:
                        keysym = ord(key)
                    else:
                        keysym = XK.string_to_keysym(key)

                if not keysym:
                    log.warning(f"无法识别按键名称: {key}")
                    return 0

                keycode = dpy.keysym_to_keycode(keysym)
                if not keycode:
                    log.warning(f"按键 {key} (keysym={keysym}) 未能映射到有效 keycode")
                    return 0
                return keycode
        except Exception as e:
            log.debug(f"解析按键 {key} keycode 异常: {e}")
            return 0

    def key_down(self, key: str) -> None:
        """在 Gamescope 虚拟会话中按下键盘按键。"""
        code = self._resolve_keycode(key)
        if code:
            self._send_fake_input(X.KeyPress, detail=code)

    def key_up(self, key: str) -> None:
        """在 Gamescope 虚拟会话中释放键盘按键。"""
        code = self._resolve_keycode(key)
        if code:
            self._send_fake_input(X.KeyRelease, detail=code)

    def key_press(self, key: str) -> None:
        """在 Gamescope 虚拟会话中轻触按键（按下并抬起）。"""
        code = self._resolve_keycode(key)
        if code:
            self._send_fake_input(X.KeyPress, detail=code)
            time.sleep(0.05)
            self._send_fake_input(X.KeyRelease, detail=code)

    def input_text(self, text: str) -> None:
        """在 Gamescope 虚拟会话中逐字符输入文本。"""
        if not text:
            log.warning("未提供要输入的文本")
            return
        for char in text:
            self.key_press(char)
            time.sleep(0.02)
