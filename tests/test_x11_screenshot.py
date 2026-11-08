from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest
from PIL import Image
from Xlib import X

from module.automation import screenshot as screenshot_module
from module.automation.screenshot import ScreenShot
from module.config import cfg
from module.game_and_screen.x11_handle import X11Handle


class TestX11HandleSRPDecoupling:
    """验证 X11Handle 职责解耦：仅暴露 Display、Window Drawable 与几何信息，不再包含图像与截图业务。"""

    def test_handle_has_no_screenshot_methods(self):
        assert not hasattr(X11Handle, "capture_window_image"), "capture_window_image 应从 X11Handle 剥离"
        assert not hasattr(X11Handle, "_resolve_content_offset"), "_resolve_content_offset 应从 X11Handle 剥离"
        assert not hasattr(X11Handle, "_heuristic_offset"), "_heuristic_offset 应从 X11Handle 剥离"
        assert not hasattr(X11Handle, "_known_content_offset"), "_known_content_offset 应从 X11Handle 剥离"
        assert not hasattr(X11Handle, "_content_offset_cache"), "_content_offset_cache 应从 X11Handle 彻底剥离"

    def test_handle_module_has_no_image_library_imports(self):
        import module.game_and_screen.x11_handle as x11_mod

        assert not hasattr(x11_mod, "cv2"), "x11_handle 不应依赖 cv2"
        assert not hasattr(x11_mod, "Image"), "x11_handle 不应依赖 PIL.Image"
        assert not hasattr(x11_mod, "np"), "x11_handle 不应依赖 numpy"
        assert not hasattr(x11_mod, "ScreenShot"), "x11_handle 杜绝引用 ScreenShot"

    def test_handle_exposes_display_drawable_geometry_and_lock(self):
        handle = object.__new__(X11Handle)
        mock_disp = Mock()
        mock_win = Mock()
        mock_geo = SimpleNamespace(x=10, y=20, width=1920, height=1080, depth=24)
        mock_win.get_geometry.return_value = mock_geo
        mock_disp.create_resource_object.return_value = mock_win

        handle._hwnd = 99999
        handle._disp = mock_disp
        handle._transparent = False
        handle._content_offset = 0

        # 测试暴露 Display
        assert handle.display is mock_disp

        # 测试暴露 Window / Drawable
        with patch.object(handle, "_window_obj", return_value=mock_win):
            assert handle.window is mock_win
            assert handle.drawable is mock_win

            # 测试暴露 Lock
            assert hasattr(handle.lock, "acquire") and hasattr(handle.lock, "release")

            # 测试暴露 Geometry
            geo = handle.get_geometry()
            assert geo.width == 1920
            assert geo.height == 1080
            assert geo.depth == 24

            # 测试纯几何计算（无标题栏探测耦合）
            with patch.object(handle, "_root_position", return_value=(0, 0)), patch.object(
                handle, "_frame_extents", return_value=(0, 0, 0, 0)
            ):
                assert handle.rect(client=True) == (0, 0, 1920, 1080)
                assert handle.rect(client=False) == (0, 0, 1920, 1080)

                # 当存在外部注入的 content_offset 时
                handle.content_offset = 40
                assert handle.rect(client=True) == (0, 40, 1920, 1080)


class TestGamescopeDirectZPixmapCapture:
    """验证 Gamescope 嵌套客户端窗口直采管线：ZPixmap 零拷贝协议提取与 PIL / OpenCV 格式封装。"""

    @pytest.fixture(autouse=True)
    def setup_mock_environment(self, monkeypatch):
        monkeypatch.setattr(screenshot_module, "IS_WINDOWS", False)
        ScreenShot._content_offset_cache.clear()

        self.width = 1920
        self.height = 1080
        self.depth = 24

        # 生成确定性测试像素：B=210, G=120, R=30, X=255
        self.raw_bgra = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        self.raw_bgra[:, :, 0] = 210  # B
        self.raw_bgra[:, :, 1] = 120  # G
        self.raw_bgra[:, :, 2] = 30   # R
        self.raw_bgra[:, :, 3] = 255  # X
        self.raw_bytes = self.raw_bgra.tobytes()

        self.mock_ximage = SimpleNamespace(data=self.raw_bytes, depth=self.depth)
        self.mock_geo = SimpleNamespace(
            x=0, y=0, width=self.width, height=self.height, depth=self.depth
        )

        self.mock_win = Mock()
        self.mock_win.get_geometry.return_value = self.mock_geo
        self.mock_win.get_image.return_value = self.mock_ximage

        self.mock_root = Mock()
        self.mock_root.get_image.side_effect = AssertionError("严禁触碰根窗口，会引发 Gamescope BadMatch 异常！")

        self.mock_disp = Mock()
        self.mock_disp.screen.return_value.root = self.mock_root

        self.mock_lock = threading.RLock()

        self.mock_handle = SimpleNamespace(
            hwnd=123456,
            display=self.mock_disp,
            window=self.mock_win,
            drawable=self.mock_win,
            lock=self.mock_lock,
            content_offset=0,
            rect=Mock(return_value=(0, 0, self.width, self.height)),
            bring_window_into_view=Mock(),
        )

        monkeypatch.setattr(screenshot_module.screen, "handle", self.mock_handle)

    def test_direct_client_capture_avoids_root_window_badmatch(self):
        """Gamescope 直采核心规范：必须直接调用 Client Window 的 get_image，避开根窗口 BadMatch。"""
        img = ScreenShot.take_screenshot_x11(gray=False)

        assert img is not None
        # 验证是在客户端窗口上调用了 X.ZPixmap
        self.mock_win.get_image.assert_called_once_with(0, 0, self.width, self.height, X.ZPixmap, 0xFFFFFFFF)
        # 验证未调用根窗口
        self.mock_root.get_image.assert_not_called()

    def test_take_screenshot_x11_pil_rgb_and_gray(self):
        """验证 PIL 输出格式转换：RGB 与 L 模式。"""
        # 1. 彩色图测试 (gray=False)
        img_rgb = ScreenShot.take_screenshot_x11(gray=False)
        assert isinstance(img_rgb, Image.Image)
        assert img_rgb.size == (self.width, self.height)
        assert img_rgb.mode == "RGB"
        # 检查首像素色彩 (R=30, G=120, B=210)
        assert img_rgb.getpixel((0, 0)) == (30, 120, 210)

        # 2. 灰度图测试 (gray=True)
        img_gray = ScreenShot.take_screenshot_x11(gray=True)
        assert isinstance(img_gray, Image.Image)
        assert img_gray.size == (self.width, self.height)
        assert img_gray.mode == "L"

    def test_take_screenshot_x11_is_readonly_and_no_side_effects(self):
        """截图管线应保持只读，严禁在 take_screenshot_x11 中修改 handle 状态。"""
        self.mock_handle.content_offset = 0
        _ = ScreenShot.take_screenshot_x11(gray=False)
        assert self.mock_handle.content_offset == 0, "截图不应具有修改 handle.content_offset 的副作用"

    def test_lock_used_as_context_manager(self):
        """验证 handle.lock 通过上下文管理器协议 (with lock:) 安全加锁释放。"""
        mock_lock = MagicMock()
        self.mock_handle.lock = mock_lock
        _ = ScreenShot.take_screenshot_x11(gray=False)
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()

    def test_gamescope_zero_overhead_alignment(self, monkeypatch):
        """在 Gamescope 下窗口高度恰等于目标分辨率，无需做多余的模板匹配探测。"""
        monkeypatch.setattr(cfg, "set_win_size", 1080)

        mock_resolve = Mock(wraps=ScreenShot._resolve_content_offset)
        monkeypatch.setattr(ScreenShot, "_resolve_content_offset", mock_resolve)

        img = ScreenShot.take_screenshot_x11(gray=False)
        assert img.size == (1920, 1080)
        assert ScreenShot.get_content_offset(1920, 1080) == 0

    def test_alignment_with_titlebar_offset_cropping(self, monkeypatch):
        """当遇到非 Gamescope 或 Wine 自绘标题栏窗口（高度超额），收敛至 ScreenShot 自动裁剪并同步 handle 偏移。"""
        extra_h = 50
        actual_h = 1080 + extra_h
        monkeypatch.setattr(cfg, "set_win_size", 1080)

        bgra = np.zeros((actual_h, self.width, 4), dtype=np.uint8)
        self.mock_ximage.data = bgra.tobytes()
        self.mock_geo.height = actual_h

        # 模拟探测出 50 像素自绘标题栏
        monkeypatch.setattr(ScreenShot, "_heuristic_offset", lambda h: 50)
        monkeypatch.setattr(
            ScreenShot,
            "_resolve_content_offset",
            Mock(side_effect=lambda w, h, img=None: 50),
        )

        img = ScreenShot.take_screenshot_x11(gray=False)
        # 裁剪后应该严格等于 1080 高度
        assert img.size == (1920, 1080)
        # 验证 handle 未被篡改
        assert self.mock_handle.content_offset == 0

    def test_unsupported_depth_raises_value_error(self):
        """遇到不支持的颜色深度（如 8-bit 色盘）时明确抛出 ValueError。"""
        self.mock_geo.depth = 8
        with pytest.raises(ValueError, match="不支持的窗口颜色深度"):
            ScreenShot.take_screenshot_x11()

    def test_missing_window_raises_runtime_error(self):
        """无窗口句柄时抛出 RuntimeError。"""
        self.mock_handle.hwnd = 0
        with pytest.raises(RuntimeError, match="未找到游戏窗口句柄"):
            ScreenShot.take_screenshot_x11()

    def test_invalid_geometry_raises_value_error(self):
        """窗口尺寸异常（<=0）时抛出 ValueError。"""
        self.mock_geo.width = 0
        with pytest.raises(ValueError, match="无效的窗口几何尺寸"):
            ScreenShot.take_screenshot_x11()

    def test_thread_safety_under_xlib_lock(self):
        """多线程并发采集测试，验证通过 handle.lock 进行互斥，无死锁且正常执行。"""
        errors = []
        results = []

        def worker():
            try:
                for _ in range(5):
                    im = ScreenShot.take_screenshot_x11(gray=True)
                    results.append(im.size)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发截图出现错误: {errors}"
        assert len(results) == 20

    def test_conversion_latency_under_5ms(self):
        """单帧零拷贝/低开销转换基准测试：要求 1080p 裸像素封装耗时在 5ms 以内。"""
        # PIL 转换耗时
        pil_times = []
        for _ in range(30):
            t0 = time.perf_counter()
            _ = ScreenShot.take_screenshot_x11(gray=False)
            t1 = time.perf_counter()
            pil_times.append((t1 - t0) * 1000)
        avg_pil_time = np.mean(pil_times)
        assert avg_pil_time < 5.0, f"PIL 单帧封装耗时超标: {avg_pil_time:.3f} ms (应 < 5ms)"

    def test_depth_16_is_unsupported(self):
        """验证 16 位颜色深度超范围并抛出 ValueError。"""
        self.mock_geo.depth = 16
        with pytest.raises(ValueError, match="不支持的窗口颜色深度: 16"):
            ScreenShot.take_screenshot_x11()

    def test_linux_frame_is_usable(self):
        """验证 _linux_frame_is_usable 针对 PIL.Image 空帧及有效帧的判定"""
        assert not ScreenShot._linux_frame_is_usable(None)

        # 尺寸为 0 的 PIL
        zero_pil = Image.new("RGB", (0, 0))
        assert not ScreenShot._linux_frame_is_usable(zero_pil)

        # 全黑 PIL
        black_pil = Image.new("RGB", (100, 100), (0, 0, 0))
        assert not ScreenShot._linux_frame_is_usable(black_pil)

        # 有效内容 PIL
        valid_pil = Image.new("RGB", (100, 100), (10, 20, 30))
        assert ScreenShot._linux_frame_is_usable(valid_pil)

    def test_take_screenshot_pipeline_linux_integration(self, monkeypatch):
        """验证 ScreenShot.take_screenshot 主流程在 Linux 下优先调用 take_screenshot_x11 并在可用时返回"""
        monkeypatch.setattr(ScreenShot, "_linux_window_is_ready", lambda: True)

        img = ScreenShot.take_screenshot(gray=False)
        assert img is not None
        assert isinstance(img, Image.Image)

        # 当 X11 返回未就绪的空帧时，应记录日志并返回 None，不交给下游
        black_img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        monkeypatch.setattr(ScreenShot, "take_screenshot_x11", lambda gray: black_img)
        assert ScreenShot.take_screenshot(gray=False) is None

        # 当 X11 抛出异常时，回退到 take_screenshot_mss
        fallback_img = Image.new("RGB", (self.width, self.height), (100, 100, 100))
        monkeypatch.setattr(
            ScreenShot,
            "take_screenshot_x11",
            Mock(side_effect=RuntimeError("X11 error")),
        )
        monkeypatch.setattr(ScreenShot, "take_screenshot_mss", lambda gray: fallback_img)
        res = ScreenShot.take_screenshot(gray=False)
        assert res == fallback_img
