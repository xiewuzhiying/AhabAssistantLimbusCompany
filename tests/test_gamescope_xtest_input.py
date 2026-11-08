"""Gamescope XTest 后台隔离输入驱动单元测试。

验证特性:
1. 依赖 Xlib.ext.xtest，线程安全地向 Gamescope 嵌套 X11 Display 发送 fake_input。
2. 坐标自动叠加 handle.content_offset 与窗口基准。
3. 提供完整的鼠标与键盘 API (mouse_click, mouse_down, mouse_up, mouse_move,
   mouse_drag, mouse_swipe_for_scroll, mouse_scroll, key_press, input_text 等)。
4. 窗口未就绪或异常时的容错与安全退出。
5. 纯后台虚拟会话隔离，绝不抢占宿主物理光标或调用 /dev/uinput、KWin DBus JS。
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from Xlib import XK, X

from module.automation.input_handlers.gamescope_input import (
    GamescopeXTestInput,
)
from module.automation.input_handlers.linux_input import LinuxInput


class FakeRoot:
    def __init__(self, root_x: int = 42, root_y: int = 84) -> None:
        self.root_x = root_x
        self.root_y = root_y

    def query_pointer(self):
        return SimpleNamespace(root_x=self.root_x, root_y=self.root_y)


class FakeScreen:
    def __init__(self, root: FakeRoot) -> None:
        self.root = root


class FakeDisplay:
    def __init__(self, root_x: int = 42, root_y: int = 84) -> None:
        self._screen = FakeScreen(FakeRoot(root_x, root_y))
        self.flushed = False
        self.synced = False
        self.keysym_map = {
            XK.XK_Return: 36,
            XK.XK_Escape: 9,
            XK.XK_space: 65,
            ord("a"): 38,
            ord("b"): 56,
        }

    def screen(self):
        return self._screen

    def flush(self):
        self.flushed = True

    def sync(self):
        self.synced = True

    def keysym_to_keycode(self, keysym: int) -> int:
        return self.keysym_map.get(keysym, 0)


class MockX11Handle:
    def __init__(
        self,
        rect: tuple[int, int, int, int] = (0, 0, 1920, 1080),
        content_offset: int = 40,
        is_gamescope: bool = True,
        hwnd: int = 12345,
    ) -> None:
        self._rect = rect
        self.content_offset = content_offset
        self.is_gamescope = is_gamescope
        self.hwnd = hwnd
        self.lock = threading.RLock()
        self.display = FakeDisplay()

    def rect(self, _client: bool = False) -> tuple[int, int, int, int]:
        return self._rect


@pytest.fixture
def mock_handle() -> MockX11Handle:
    return MockX11Handle(rect=(0, 0, 1920, 1080), content_offset=40)


@pytest.fixture
def xtest_driver(mock_handle: MockX11Handle) -> GamescopeXTestInput:
    driver = GamescopeXTestInput(handle=mock_handle)
    driver.wait_pause = lambda: None
    return driver


# ====================================================================
# 1. 构造与句柄解析
# ====================================================================


def test_driver_custom_handle_and_properties(mock_handle: MockX11Handle) -> None:
    driver = GamescopeXTestInput(handle=mock_handle)
    assert driver.handle is mock_handle
    assert driver.display is mock_handle.display
    assert driver.lock is mock_handle.lock


def test_driver_default_handle_from_screen(monkeypatch, mock_handle: MockX11Handle) -> None:
    with patch("module.game_and_screen.screen") as mock_screen:
        mock_screen.handle = mock_handle
        driver = GamescopeXTestInput()
        assert driver.handle is mock_handle
        assert driver.display is mock_handle.display


def test_driver_missing_display_raises_runtime_error() -> None:
    empty_handle = SimpleNamespace(display=None, lock=nullcontext())
    driver = GamescopeXTestInput(handle=empty_handle)
    with pytest.raises(RuntimeError, match="未连接到有效的 X11 Display"):
        _ = driver.display


# ====================================================================
# 2. 坐标计算与 content_offset 叠加
# ====================================================================


def test_pos_offset_with_content_offset(mock_handle: MockX11Handle) -> None:
    mock_handle.content_offset = 40
    mock_handle._rect = (0, 0, 1920, 1080)
    driver = GamescopeXTestInput(handle=mock_handle)

    rx, ry = driver.pos_offset(100, 200)
    assert rx == 100
    assert ry == 240

    # 支持元组输入
    assert driver.pos_offset((100, 200)) == (100, 240)


def test_pos_offset_with_window_origin_and_content_offset(mock_handle: MockX11Handle) -> None:
    mock_handle.content_offset = 50
    mock_handle._rect = (10, 20, 1930, 1100)
    driver = GamescopeXTestInput(handle=mock_handle)

    rx, ry = driver.pos_offset(100, 200)
    assert rx == 110  # 100 + 10
    assert ry == 270  # 200 + 20 + 50


def test_pos_offset_invalid_args(xtest_driver: GamescopeXTestInput) -> None:
    with pytest.raises(ValueError, match="pos_offset"):
        xtest_driver.pos_offset()

    with pytest.raises(ValueError, match="pos_offset"):
        xtest_driver.pos_offset(1)

    with pytest.raises(ValueError, match="pos_offset"):
        xtest_driver.pos_offset("invalid")


# ====================================================================
# 3. 窗口就绪与冷启动保护
# ====================================================================


def test_window_is_ready_checks(mock_handle: MockX11Handle) -> None:
    driver = GamescopeXTestInput(handle=mock_handle)
    assert driver._window_is_ready() is True

    # 句柄 hwnd 为 0
    mock_handle.hwnd = 0
    assert driver._window_is_ready() is False
    mock_handle.hwnd = 12345

    # 零几何尺寸
    mock_handle._rect = (0, 0, 0, 0)
    assert driver._window_is_ready() is False

    # 异常抛出
    mock_handle.rect = Mock(side_effect=RuntimeError("X11 error"))
    assert driver._window_is_ready() is False


def test_mouse_click_skipped_when_window_not_ready(mock_handle: MockX11Handle) -> None:
    mock_handle._rect = (0, 0, 0, 0)
    driver = GamescopeXTestInput(handle=mock_handle)
    driver.wait_pause = lambda: None

    with patch("Xlib.ext.xtest.fake_input") as mock_fake:
        assert driver.mouse_click(100, 200) is False
        mock_fake.assert_not_called()


def test_mouse_click_blank_skipped_when_window_not_ready(mock_handle: MockX11Handle) -> None:
    mock_handle._rect = (0, 0, 0, 0)
    driver = GamescopeXTestInput(handle=mock_handle)
    driver.wait_pause = lambda: None

    with patch("Xlib.ext.xtest.fake_input") as mock_fake:
        assert driver.mouse_click_blank() is False
        mock_fake.assert_not_called()


# ====================================================================
# 4. 指针查询
# ====================================================================


def test_get_mouse_position(xtest_driver: GamescopeXTestInput) -> None:
    pos = xtest_driver.get_mouse_position()
    assert pos == (42, 84)


def test_get_mouse_position_exception_fallback(xtest_driver: GamescopeXTestInput) -> None:
    xtest_driver.display.screen().root.query_pointer = Mock(side_effect=RuntimeError("XQueryPointer failed"))
    pos = xtest_driver.get_mouse_position()
    assert pos == (0, 0)


# ====================================================================
# 5. 鼠标点击 (mouse_click)
# ====================================================================


def test_mouse_click_emits_motion_and_button_press_release(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        success = xtest_driver.mouse_click(100, 200, times=1)

    assert success is True
    # 期望收到 MotionNotify 到 (100, 240)，然后 ButtonPress(detail=1)，再 ButtonRelease(detail=1)
    assert len(events) == 3
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 100, "y": 240})
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    assert events[2] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})
    assert xtest_driver.display.flushed is True


def test_mouse_click_hold_duration_satisfies_ugui_sampling(xtest_driver: GamescopeXTestInput) -> None:
    """验证 mouse_click 在按下与释放之间保持 100~150ms (ADR 0001 §4)，保证 Unity UGUI 采样跨帧。"""
    sleep_calls = []
    with patch("Xlib.ext.xtest.fake_input"), patch("time.sleep", side_effect=sleep_calls.append):
        xtest_driver.mouse_click(100, 200, times=1)

    # 包含移动延迟 (0.02s)、按下保持延迟 (0.12s)、释放后冷却 (0.03s)
    assert 0.10 <= sleep_calls[1] <= 0.15


def test_mouse_click_multiple_times(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_click(100, 200, times=3, button=1)

    # 3 次点击：每次 1 motion + 1 press + 1 release = 9 事件
    assert len(events) == 9
    presses = [e for e in events if e[0] == X.ButtonPress]
    releases = [e for e in events if e[0] == X.ButtonRelease]
    assert len(presses) == 3
    assert len(releases) == 3


def test_mouse_click_with_move_back(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_click(100, 200, move_back=True)

    # 初始指针为 (42, 84)，move_back 需在点击完成后移动回 (42, 84)
    assert events[-1] == (X.MotionNotify, {"detail": 0, "x": 42, "y": 84})


# ====================================================================
# 6. 鼠标按下与释放 (mouse_down, mouse_up)
# ====================================================================


def test_mouse_down_and_up_with_coordinates(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_down(50, 60, button=1)
        xtest_driver.mouse_up(50, 60, button=1)

    assert len(events) == 4
    # down: motion + button press
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 50, "y": 100})
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    # up: motion + button release
    assert events[2] == (X.MotionNotify, {"detail": 0, "x": 50, "y": 100})
    assert events[3] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


def test_mouse_down_and_up_without_coordinates(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_down()
        xtest_driver.mouse_up()

    assert len(events) == 2
    assert events[0] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    assert events[1] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


# ====================================================================
# 7. 鼠标移动 (mouse_move)
# ====================================================================


def test_mouse_move_variants(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_move(300, 400)
        xtest_driver.mouse_move((500, 600))
        xtest_driver.mouse_move(coordinate=(700, 800))
        xtest_driver.mouse_move(900)

    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 300, "y": 400})
    assert events[1] == (X.MotionNotify, {"detail": 0, "x": 500, "y": 600})
    assert events[2] == (X.MotionNotify, {"detail": 0, "x": 700, "y": 800})
    assert events[3] == (X.MotionNotify, {"detail": 0, "x": 900, "y": 900})


# ====================================================================
# 8. 鼠标拖拽 (mouse_drag)
# ====================================================================


def test_mouse_drag_tuple_format(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_drag((10, 20), (30, 40), drag_time=0.04)

    # 应该先移动到起始点 (10, 60)，按下按键 1，然后发出插值移动，最后释放按键 1
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 10, "y": 60})
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    # 中间插值移动
    motions = [e for e in events if e[0] == X.MotionNotify]
    assert len(motions) >= 6
    # 终点为 (30, 80)
    assert motions[-1] == (X.MotionNotify, {"detail": 0, "x": 30, "y": 80})
    assert events[-1] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


def test_mouse_drag_dx_dy_format(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_drag(10, 20, drag_time=0.04, dx=40, dy=50, move_back=False)

    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 10, "y": 60})
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    motions = [e for e in events if e[0] == X.MotionNotify]
    # 终点：10+40=50, 20+40(offset)+50=110
    assert motions[-1] == (X.MotionNotify, {"detail": 0, "x": 50, "y": 110})
    assert events[-1] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


def test_mouse_drag_invalid_args(xtest_driver: GamescopeXTestInput) -> None:
    with pytest.raises(ValueError, match="mouse_drag"):
        xtest_driver.mouse_drag("invalid")


def test_mouse_drag_down(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input), \
         patch("module.automation.input_handlers.gamescope_input.cfg") as mock_cfg:
        mock_cfg.set_win_size = 1080
        xtest_driver.mouse_drag_down(100, 200, reverse=1, move_back=False)

    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 100, "y": 240})
    motions = [e for e in events if e[0] == X.MotionNotify]
    # 终点：y = 200 + 300 + 40 = 540
    assert motions[-1] == (X.MotionNotify, {"detail": 0, "x": 100, "y": 540})


# ====================================================================
# 9. 连续路径拖拽 (mouse_drag_link)
# ====================================================================


def test_mouse_drag_link_empty(xtest_driver: GamescopeXTestInput) -> None:
    with patch("Xlib.ext.xtest.fake_input") as mock_fake:
        xtest_driver.mouse_drag_link([])
        mock_fake.assert_not_called()


def test_mouse_drag_link_points(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    pts = [(10, 20), (30, 40), (50, 60)]
    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_drag_link(pts, drag_time=0.01, move_back=False)

    # 1. 移动到第一点 (10, 60)
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 10, "y": 60})
    # 2. 按下
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    # 3. 移动到后续点
    assert events[2] == (X.MotionNotify, {"detail": 0, "x": 30, "y": 80})
    assert events[3] == (X.MotionNotify, {"detail": 0, "x": 50, "y": 100})
    # 4. 释放
    assert events[4] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


def test_mouse_drag_link_crosses_start_gear(xtest_driver: GamescopeXTestInput) -> None:
    """The template anchor is above the hitbox: dragging must cross it.

    These coordinates reproduce the live defense selection failure. Jumping
    straight to the portrait misses the gear and opens the E.G.O menu instead.
    """
    events = []
    points = [(399, 811), (540.375, 998.5), (661.125, 998.5)]

    def record(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record), patch("time.sleep"):
        xtest_driver.mouse_drag_link(points)

    press = next(i for i, (kind, _) in enumerate(events) if kind == X.ButtonPress)
    release = next(i for i, (kind, _) in enumerate(events) if kind == X.ButtonRelease)
    held_motion = [
        (args["x"], args["y"] - 40)
        for kind, args in events[press + 1 : release]
        if kind == X.MotionNotify
    ]
    first_portrait = held_motion.index((540, 998))
    assert any(410 <= x <= 445 and 835 <= y <= 875 for x, y in held_motion[:first_portrait])
    assert held_motion[-1] == (661, 998)


# ====================================================================
# 10. 滚动手势 (mouse_swipe_for_scroll) 与滚轮 (mouse_scroll)
# ====================================================================


def test_mouse_swipe_for_scroll(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_swipe_for_scroll(100, 200, duration=0.1, dx=0, dy=100, move_back=False)

    assert events[0][0] == X.MotionNotify
    assert events[1] == (X.ButtonPress, {"detail": 1, "x": 0, "y": 0})
    assert events[-1] == (X.ButtonRelease, {"detail": 1, "x": 0, "y": 0})


@pytest.mark.parametrize("sample_phase", [0, 0.004, 0.008, 0.012])
@pytest.mark.parametrize("sample_rate", [30, 60])
def test_team_swipe_moves_list_for_frame_sampled_drag(xtest_driver, sample_phase, sample_rate):
    """A frame consumer must see the drag start before the main displacement.

    Scroll controls establish a drag anchor when motion crosses their threshold.
    Multiple XTest jumps within that frame can consume the page displacement as
    the anchor, leaving only the 30-pixel braking tail to scroll the list.
    """
    elapsed = 0.0
    events = []

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    def record(dpy, kind, **kwargs):
        events.append((elapsed, kind, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record), patch("time.sleep", side_effect=advance):
        xtest_driver.mouse_swipe_for_team_scroll(187, 761, dy=-288.75, duration=0.3, move_back=False)

    pointer = (0, 0)
    down = False
    dragging = False
    anchor = previous = None
    moved = 0
    index = 0
    sample_time = sample_phase
    while sample_time <= elapsed:
        while index < len(events) and events[index][0] <= sample_time:
            _, kind, args = events[index]
            if kind == X.MotionNotify:
                pointer = (args["x"], args["y"])
            elif kind == X.ButtonPress:
                down = True
            elif kind == X.ButtonRelease:
                down = False
            index += 1
        if down:
            if anchor is None:
                anchor = pointer
            elif not dragging and abs(pointer[1] - anchor[1]) > 5:
                dragging = True
            elif dragging:
                moved += pointer[1] - previous[1]
            previous = pointer
        else:
            anchor = previous = None
            dragging = False
        sample_time += 1 / sample_rate

    # A nominal 289-pixel page must advance several rows, not only its tail.
    assert moved <= -230, f"{sample_rate} Hz consumer scrolled only {moved}px"


def test_mouse_scroll_up_and_down(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        # 正数向上拉近 (Button 4)
        xtest_driver.mouse_scroll(2)
        # 负数向下拉远 (Button 5)
        xtest_driver.mouse_scroll(-2)

    btn4_press = [e for e in events if e == (X.ButtonPress, {"detail": 4, "x": 0, "y": 0})]
    btn5_press = [e for e in events if e == (X.ButtonPress, {"detail": 5, "x": 0, "y": 0})]
    assert len(btn4_press) == 2
    assert len(btn5_press) == 2


# ====================================================================
# 11. 空白区移动与点击 (mouse_to_blank, mouse_click_blank)
# ====================================================================


def test_mouse_to_blank(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.mouse_to_blank((5, 10))

    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 5, "y": 50})


def test_mouse_click_blank(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input), \
         patch("random.randint", return_value=2):
        res = xtest_driver.mouse_click_blank((1, 1))

    assert res is True
    # (1+2, 1+2) -> (3, 3 + 40 = 43)
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 3, "y": 43})


# ====================================================================
# 12. 键盘事件 (key_press, key_down, key_up, input_text)
# ====================================================================


def test_key_press_named_and_char(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.key_press("enter")
        xtest_driver.key_press("a")

    # enter -> keycode 36
    assert events[0] == (X.KeyPress, {"detail": 36, "x": 0, "y": 0})
    assert events[1] == (X.KeyRelease, {"detail": 36, "x": 0, "y": 0})
    # 'a' -> keycode 38
    assert events[2] == (X.KeyPress, {"detail": 38, "x": 0, "y": 0})
    assert events[3] == (X.KeyRelease, {"detail": 38, "x": 0, "y": 0})


def test_key_down_and_up(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.key_down("esc")
        xtest_driver.key_up("esc")

    assert events[0] == (X.KeyPress, {"detail": 9, "x": 0, "y": 0})
    assert events[1] == (X.KeyRelease, {"detail": 9, "x": 0, "y": 0})


def test_input_text_sequence(xtest_driver: GamescopeXTestInput) -> None:
    events = []

    def record_fake_input(dpy, event_type, **kwargs):
        events.append((event_type, kwargs))

    with patch("Xlib.ext.xtest.fake_input", side_effect=record_fake_input):
        xtest_driver.input_text("ab")

    assert len(events) == 4
    # 'a': 38, 'b': 56
    assert events[0] == (X.KeyPress, {"detail": 38, "x": 0, "y": 0})
    assert events[1] == (X.KeyRelease, {"detail": 38, "x": 0, "y": 0})
    assert events[2] == (X.KeyPress, {"detail": 56, "x": 0, "y": 0})
    assert events[3] == (X.KeyRelease, {"detail": 56, "x": 0, "y": 0})


def test_input_text_empty_does_nothing(xtest_driver: GamescopeXTestInput) -> None:
    with patch("Xlib.ext.xtest.fake_input") as mock_fake:
        xtest_driver.input_text("")
        mock_fake.assert_not_called()


def test_unrecognized_key_handled_safely(xtest_driver: GamescopeXTestInput) -> None:
    with patch("Xlib.ext.xtest.fake_input") as mock_fake:
        xtest_driver.key_press("nonexistent_unknown_super_key")
        mock_fake.assert_not_called()


# ====================================================================
# 13. 异常处理与线程安全验证
# ====================================================================


def test_send_fake_input_handles_x_error(xtest_driver: GamescopeXTestInput) -> None:
    with patch("Xlib.ext.xtest.fake_input", side_effect=RuntimeError("X protocol error")):
        result = xtest_driver._send_fake_input(X.ButtonPress, 1)
        assert result is False


def test_fake_input_serialized_under_lock(xtest_driver: GamescopeXTestInput) -> None:
    lock_state = {"in_lock": False, "held_during_fake_input": False}

    class TrackingLock:
        def __enter__(self):
            lock_state["in_lock"] = True
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            lock_state["in_lock"] = False

    xtest_driver.handle.lock = TrackingLock()

    def fake_call(dpy, event_type, **kwargs):
        if lock_state["in_lock"]:
            lock_state["held_during_fake_input"] = True

    with patch("Xlib.ext.xtest.fake_input", side_effect=fake_call):
        xtest_driver.mouse_down(10, 20)

    assert lock_state["held_during_fake_input"] is True

    # 验证实际 threading.RLock 场景下 _is_owned() 真实持有断言
    real_rlock = threading.RLock()
    xtest_driver.handle.lock = real_rlock
    rlock_owned_during_call = []

    def fake_call_rlock(dpy, event_type, **kwargs):
        rlock_owned_during_call.append(real_rlock._is_owned())

    with patch("Xlib.ext.xtest.fake_input", side_effect=fake_call_rlock):
        xtest_driver.mouse_down(10, 20)

    assert len(rlock_owned_during_call) > 0
    assert all(rlock_owned_during_call)


# ====================================================================
# 14. LinuxInput 自动委托与宿主无侵入性
# ====================================================================


def test_linux_input_delegates_to_gamescope_when_active(monkeypatch) -> None:
    from module.game_and_screen import screen

    mock_handle = MockX11Handle(rect=(0, 0, 1920, 1080), content_offset=40, is_gamescope=True)
    monkeypatch.setattr(screen, "handle", mock_handle)

    handler = object.__new__(LinuxInput)
    handler._gamescope_input = None
    handler.wait_pause = lambda: None

    events = []
    with patch("Xlib.ext.xtest.fake_input", side_effect=lambda d, t, **kw: events.append((t, kw))):
        res = handler.mouse_click(50, 60)

    assert res is True
    # 证明委托给了 GamescopeXTestInput，自动叠加了 content_offset 40 -> y=100
    assert events[0] == (X.MotionNotify, {"detail": 0, "x": 50, "y": 100})


def test_package_exports_gamescope_xtest_input() -> None:
    from module.automation.input_handlers import GamescopeXTestInput as ExportedGamescopeInput

    assert ExportedGamescopeInput is GamescopeXTestInput


def test_automation_init_input_selects_gamescope_xtest_input(monkeypatch) -> None:
    from module.automation.automation import Automation
    from module.config import cfg
    from module.game_and_screen import screen

    mock_handle = MockX11Handle(rect=(0, 0, 1920, 1080), content_offset=40, is_gamescope=True)
    monkeypatch.setattr(screen, "handle", mock_handle)
    monkeypatch.setattr(cfg, "simulator", False)
    monkeypatch.setattr(cfg, "win_input_type", "background")

    auto = object.__new__(Automation)
    auto.input_handler = None
    with patch("module.automation.automation.IS_WINDOWS", False):
        auto.init_input()

    assert isinstance(auto.input_handler, GamescopeXTestInput)


def test_no_host_uinput_or_kwin_dbus_scripts_in_codebase() -> None:
    """严格验证代码库中绝不包含 /dev/uinput 或 KDE KWin DBus 注入。"""
    import inspect

    import module.automation.input_handlers.gamescope_input as gs_mod
    import module.automation.input_handlers.linux_input as lx_mod

    gs_src = inspect.getsource(gs_mod)
    lx_src = inspect.getsource(lx_mod)

    for forbidden in ["/dev/uinput", "UI_SET_EVBIT", "UI_DEV_CREATE", "busctl", "org.kde.KWin", "workspace.cursorPos"]:
        assert forbidden not in gs_src, f"GamescopeXTestInput 包含宿主破坏性代码: {forbidden}"
        assert forbidden not in lx_src, f"LinuxInput 包含宿主破坏性代码: {forbidden}"
