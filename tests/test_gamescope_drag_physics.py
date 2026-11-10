from unittest.mock import MagicMock, patch

import pytest

from module.automation.input_handlers.gamescope_input import (
    GamescopeXTestInput,
    ease_out_cubic,
)


def test_ease_out_cubic_math():
    """验证三次缓动曲线数学特性：起点0，终点1，中间前快后慢"""
    assert ease_out_cubic(0.0) == 0.0
    assert ease_out_cubic(1.0) == 1.0
    # 中间点 t=0.5 时进度显著大于 0.5（产生自然初速度）
    half_val = ease_out_cubic(0.5)
    assert half_val == pytest.approx(0.875)


def test_gamescope_drag_trajectory_ease_out():
    """验证 GamescopeXTestInput 拖拽采用平滑缓动而非简单线性等分"""
    mock_handle = MagicMock()
    mock_handle.display = MagicMock()
    mock_handle.lock = MagicMock()
    mock_handle.rect.return_value = (0, 0, 1920, 1080)
    mock_handle.content_offset = 0

    input_handler = GamescopeXTestInput(handle=mock_handle)
    input_handler._display = MagicMock()
    input_handler.pos_offset = lambda x, y: (x, y)
    input_handler.get_mouse_position = lambda: (0, 0)
    input_handler.wait_pause = MagicMock()

    motions = []
    input_handler._send_motion = MagicMock(side_effect=lambda x, y: motions.append((x, y)))
    input_handler._send_button = MagicMock()

    # 模拟一个从 (100, 100) 到 (200, 300) 的拖动
    with patch("time.sleep") as mock_sleep:
        input_handler.mouse_drag(100, 100, drag_time=1.0, dx=100, dy=200, move_back=False)

    assert len(motions) > 5
    # 第一个点为起点
    assert motions[0] == (100, 100)
    # 最后一个移动点为目标终点
    assert motions[-1] == (200, 300)

    # 检查中间点呈现 Ease-Out 特征：前半段移动距离明显大于后半段
    mid_idx = len(motions) // 2
    mid_x, mid_y = motions[mid_idx]
    assert mid_x > 150, f"X 中间点 {mid_x} 应大于线性中点 150"
    assert mid_y > 200, f"Y 中间点 {mid_y} 应大于线性中点 200"

    # 验证松开按钮前有充足的静止停顿（对齐 Windows 的 0.5s / max(0.45, drag_time*0.3)），
    # 彻底抹杀线速度以防 Unity 抛甩惯性造成地图定位漂移
    sleep_calls = [c[0][0] for c in mock_sleep.call_args_list if c[0]]
    # 倒数第 2 次 sleep 为释放按键前的 settle 停顿
    settle_sleep = sleep_calls[-2]
    assert settle_sleep >= 0.45, f"释放前停顿不足导致 Unity 惯性抛甩: {settle_sleep}"
