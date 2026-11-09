"""基于 python-xlib / EWMH 的窗口管理实现，接口与 Windows 版 `Handle` 保持一致。

适用于 X11 会话或 XWayland（游戏经 Steam/Proton 运行时默认以 X11 窗口出现）。
不支持的能力（如 Win32 分层窗口的透明/鼠标穿透）以 no-op 方式降级。
"""

import functools
import os
import threading
import time
from pathlib import Path

import psutil
from Xlib import XK, X
from Xlib import display as xdisplay
from Xlib.error import ConnectionClosedError, DisplayConnectionError, XError
from Xlib.ext import randr, xtest
from Xlib.protocol import event as xevent

from module.game_and_screen.screen import Handle
from module.logger import log
from module.x11_connection import enable_abstract_socket_fallback

enable_abstract_socket_fallback()

# WM_STATE 状态码
NormalState = 1
IconicState = 3

_MOTIF_WM_HINTS = "_MOTIF_WM_HINTS"
# Motif hints: MWM_HINTS_DECORATIONS
MWM_HINTS_DECORATIONS = 0x2

_XLIB_LOCK = threading.RLock()
"""python-xlib 的 Display 非线程安全。

业务线程与重试监控线程会并发使用同一条 X 连接（截图/取坐标/点击），
请求交叉会导致应答流错乱（RuntimeError: Request reply to unknown request），
所有 X 操作必须经此锁串行化。可重入，同线程嵌套调用不受影响。
"""


def _serialized(fn):
    """把整个方法置入 X 连接互斥区，保证请求-应答成对完成。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _XLIB_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def reset_serialization_lock() -> None:
    """更换互斥锁。

    仅应在脚本线程被 terminate() 硬杀等场景调用：被杀线程若正持有
    _XLIB_LOCK，其计数不会释放，不换锁则后续所有 X 调用永久阻塞。
    """
    global _XLIB_LOCK
    _XLIB_LOCK = threading.RLock()


def _read_proc_environ(pid: int, proc_root: Path = Path("/proc")) -> dict[str, str]:
    """读取 /proc/<pid>/environ 中的环境变量字典。"""
    proc_environ_path = proc_root / str(pid) / "environ"
    if not proc_environ_path.exists():
        return {}
    try:
        content = proc_environ_path.read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return {}

    environ: dict[str, str] = {}
    for item in content.split(b"\x00"):
        if not item:
            continue
        if b"=" in item:
            k, v = item.split(b"=", 1)
            try:
                environ[k.decode("utf-8", errors="replace")] = v.decode("utf-8", errors="replace")
            except Exception:
                pass
    return environ


def get_configured_gamescope_display() -> str | None:
    """获取通过环境变量或配置显式指定的 Gamescope DISPLAY 端口。"""
    for env_var in ("AALC_GAMESCOPE_DISPLAY", "GAMESCOPE_DISPLAY"):
        val = os.environ.get(env_var)
        if val and val.strip():
            return val.strip()
    try:
        from module.config import cfg

        cfg_val = getattr(cfg, "gamescope_display", None)
        if cfg_val and str(cfg_val).strip():
            return str(cfg_val).strip()
    except Exception:
        pass
    return None


def detect_gamescope_displays(
    target_process_names: tuple[str, ...] = ("LimbusCompany.exe", "LimbusCompany"),
    gamescope_process_name: str = "gamescope",
    fallback_ports: tuple[str, ...] = (":1", ":2"),
    proc_root: Path = Path("/proc"),
) -> list[str]:
    """探测 Gamescope 嵌套环境中的 DISPLAY 端口列表。

    优先级:
    1. 显式环境变量 / 配置: AALC_GAMESCOPE_DISPLAY, GAMESCOPE_DISPLAY, cfg.gamescope_display
    2. 运行中游戏进程 (如 LimbusCompany.exe) 的 /proc/<pid>/environ 中的 DISPLAY
    3. gamescope 进程的子进程 /proc/<pid>/environ 中的 DISPLAY
    4. 兜底候选端口 (如 :1, :2)
    """
    candidates: list[str] = []

    # 1. 显式环境变量 / 配置
    cfg_disp = get_configured_gamescope_display()
    if cfg_disp and cfg_disp not in candidates:
        candidates.append(cfg_disp)

    # 2. 检查运行中进程的 /proc/<pid>/environ
    gamescope_pids: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").casefold()
                cmdline = " ".join(proc.info.get("cmdline") or []).casefold()
                pid = proc.info.get("pid")
                if not pid:
                    continue

                # 检查是否为目标游戏进程
                is_game = any(tgt.casefold() in name or tgt.casefold() in cmdline for tgt in target_process_names)
                if is_game:
                    proc_env = _read_proc_environ(pid, proc_root=proc_root)
                    disp = proc_env.get("DISPLAY")
                    if disp and disp not in candidates:
                        candidates.append(disp)

                # 记录 gamescope 进程
                if gamescope_process_name.casefold() in name or gamescope_process_name.casefold() in cmdline:
                    gamescope_pids.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        log.debug(f"遍历进程探测 Gamescope 失败: {e}")

    # 3. 检查 gamescope 进程及其子进程
    for gpid in gamescope_pids:
        try:
            gproc = psutil.Process(gpid)
            for child in gproc.children(recursive=True):
                try:
                    cenv = _read_proc_environ(child.pid, proc_root=proc_root)
                    cdisp = cenv.get("DISPLAY")
                    if cdisp and cdisp not in candidates:
                        candidates.append(cdisp)
                except Exception:
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # 4. 兜底端口探测（即使未找到 gamescope 进程名也进行兜底）
    for fb in fallback_ports:
        if fb not in candidates:
            candidates.append(fb)

    return candidates


def _net_wm_state_atoms(disp):
    return [
        disp.intern_atom("_NET_WM_STATE", True),
        disp.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT", True),
        disp.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ", True),
        disp.intern_atom("_NET_WM_STATE_HIDDEN", True),
        disp.intern_atom("_NET_WM_STATE_ABOVE", True),
        disp.intern_atom("_NET_WM_STATE_FULLSCREEN", True),
    ]


class X11Handle(Handle):
    """提供统一的获取窗口信息的接口（X11 实现）

    说明:
        - `hwnd` 在 X11 下是 X 窗口 XID，保持同名属性以复用上层逻辑。
        - `rect(client=True)` 返回游戏窗口自身几何在屏幕上的投影；
          `rect(client=False)` 额外包含由 `_NET_FRAME_EXTENTS` 报告的窗口装饰边框。
        - `width/height/monitor_size/mouse_pos_to_client_mouse/client_to_window/
          bring_window_into_view` 等纯几何方法直接继承自 `Handle` 基类。
    """

    _disp: xdisplay.Display | None = None
    _display_name: str | None = None
    _is_gamescope: bool = False

    def __init__(self, display_name: str | None = None):
        super().__init__()
        self._hwnd: int = 0
        self._transparent = False
        self._display_name = display_name
        self._is_gamescope = False
        if self._display_name is not None:
            self._is_gamescope = self._display_name in detect_gamescope_displays()
        self._disp = None
        try:
            self._disp = xdisplay.Display(self._display_name)
            if self._display_name is None and self._disp is not None:
                try:
                    self._display_name = self._disp.get_display_name()
                except Exception:
                    pass
        except Exception as e:
            log.error(f"无法连接 X 显示服务器（请确认 X11/XWayland 会话）: {e}")
        self._enum_windows_list: list[str] = []
        self._content_offset: int = 0

    @property
    def display_name(self) -> str | None:
        """当前连接的 X11 Display 名称 (例如 ':0', ':1')"""
        if self._display_name is not None:
            return self._display_name
        if self._disp is not None:
            try:
                return self._disp.get_display_name()
            except Exception:
                pass
        return os.environ.get("DISPLAY")

    @property
    def is_gamescope(self) -> bool:
        """是否处于 Gamescope 嵌套环境中"""
        if self._is_gamescope:
            return True
        if os.environ.get("GAMESCOPE_WAYLAND_DISPLAY") or os.environ.get("GAMESCOPE_DISPLAY"):
            return True
        return False

    def _switch_display(self, new_display_name: str) -> bool:
        """切换到新的 X11 Display 连接"""
        try:
            new_disp = xdisplay.Display(new_display_name)
        except Exception as e:
            log.debug(f"无法连接到 X 显示服务 {new_display_name}: {e}")
            return False

        if self._disp is not None:
            try:
                self._disp.close()
            except Exception:
                pass
        self._disp = new_disp
        self._display_name = new_display_name
        return True

    def _invalidate_connection(self) -> None:
        """丢弃失效连接及其窗口状态，保留目标 DISPLAY 和隔离模式用于重连。"""
        old_display = self._disp
        self._disp = None
        self._hwnd = 0
        self._content_offset = 0
        if old_display is not None:
            try:
                old_display.close()
            except Exception:
                pass

    def close(self) -> None:
        """关闭当前 X11 Display 连接并重置状态。"""
        with _XLIB_LOCK:
            self._invalidate_connection()
            self._is_gamescope = False

    # ------------------------------------------------------------------ 基础
    @property
    def display(self) -> xdisplay.Display:
        """暴露底层 X11 Display 连接"""
        return self._dpy

    @property
    def window(self):
        """暴露当前目标 X11 客户端窗口对象"""
        return self._window_obj()

    @property
    def drawable(self):
        """暴露当前目标 X11 可绘制对象 (Drawable)"""
        return self._window_obj()

    @property
    def lock(self) -> threading.RLock:
        """暴露用于 Xlib 请求序列化的重入锁"""
        return _XLIB_LOCK

    @property
    def content_offset(self) -> int:
        """游戏画布相对于窗口客户区顶部的 Y 偏移量（像素）"""
        return getattr(self, "_content_offset", 0)

    @content_offset.setter
    def content_offset(self, value: int) -> None:
        self._content_offset = max(0, int(value))

    @_serialized
    def get_geometry(self):
        """获取当前客户端窗口的几何信息"""
        if self.hwnd == 0:
            raise RuntimeError("未找到游戏窗口句柄")
        return self._window_obj().get_geometry()

    @property
    def _dpy(self) -> xdisplay.Display:
        if self._disp is None:
            self._disp = xdisplay.Display(self._display_name)
        return self._disp

    @property
    def _root(self):
        return self._dpy.screen().root

    def _window_obj(self, wid: int | None = None):
        """按 XID 取窗口对象，窗口已销毁时抛出 XError"""
        wid = self._hwnd if wid is None else wid
        return self._dpy.create_resource_object("window", wid)

    def _get_atom_value(self, win, atom_name: str, dpy: xdisplay.Display | None = None):
        d = dpy if dpy is not None else self._dpy
        try:
            atom = d.intern_atom(atom_name, True)
            if not atom:
                return None
            prop = win.get_full_property(atom, X.AnyPropertyType)
            if prop is None or prop.value is None or len(prop.value) == 0:
                return None
            return prop.value
        except Exception:
            return None

    # ------------------------------------------------------------------ 查找
    def _try_display(
        self,
        disp_name: str | None,
        title: str = "LimbusCompany",
        class_name: str = "UnityWndClass",
        is_gamescope: bool | None = None,
    ) -> int:
        """尝试在指定 DISPLAY 上查找游戏窗口并建立连接。

        - disp_name 为 None 时，复用当前 Display 连接；
        - 若指定了 disp_name 且不同于当前 Display，则新建连接；
        - 匹配成功时更新 self._hwnd, self._display_name, self._disp 及 self._is_gamescope 并返回 wid；
        - 未匹配或连接失败时返回 0。
        """
        # 1. 复用当前连接
        if disp_name is None or (disp_name == self._display_name and self._disp is not None):
            candidates = self._safe_enum_windows()
            wid = self._match_game_window(candidates, title, class_name)
            if wid:
                self._hwnd = wid
                if is_gamescope is not None:
                    self._is_gamescope = is_gamescope
                self._log_found_window(candidates)
                return wid
            # 枚举期间连接可能断开。只有连接失效时才重新连接同一 DISPLAY；
            # 活连接中暂时没有窗口（如游戏加载中）不应触发重连。
            if self._disp is not None or disp_name is None:
                return 0

        # 2. 建立新连接探测
        try:
            probe_disp = xdisplay.Display(disp_name)
        except Exception as e:
            log.debug(f"无法连接到显示服务 {disp_name}: {e}")
            return 0

        try:
            candidates = self._safe_enum_windows(probe_disp)
            wid = self._match_game_window(candidates, title, class_name)
            if wid:
                if self._disp is not None:
                    try:
                        self._disp.close()
                    except Exception:
                        pass
                self._disp = probe_disp
                self._display_name = disp_name
                self._hwnd = wid
                if is_gamescope is not None:
                    self._is_gamescope = is_gamescope
                else:
                    self._is_gamescope = disp_name in detect_gamescope_displays()
                self._log_found_window(candidates)
                return wid
            else:
                try:
                    probe_disp.close()
                except Exception:
                    pass
                return 0
        except Exception as e:
            log.debug(f"在显示服务 {disp_name} 枚举窗口失败: {e}")
            try:
                probe_disp.close()
            except Exception:
                pass
            return 0

    @_serialized
    def init_handle(
        self,
        title: str = "LimbusCompany",
        class_name: str = "UnityWndClass",
        display_name: str | None = None,
    ) -> int:
        """获取窗口句柄。Linux 下只接受精确的游戏标题或明确的游戏类名。

        支持显式指定 display_name、配置优先探测与自适应探测 Gamescope 嵌套环境。
        """
        self._hwnd = 0
        self._enum_windows_list.clear()

        # 1. 显式指定 Display
        if display_name:
            is_gs = display_name in detect_gamescope_displays()
            wid = self._try_display(display_name, title, class_name, is_gamescope=is_gs)
            if wid:
                return wid
            log.error(f"在指定的 X 显示服务 ({display_name}) 中未能获取到游戏窗口", stacklevel=3)
            return 0

        from module.game_and_screen.gamescope_launcher import GamescopeLauncher
        owned_display = GamescopeLauncher().display_name()
        if owned_display:
            wid = self._try_display(owned_display, title, class_name, is_gamescope=True)
            if wid:
                return wid

        self._is_gamescope = True
        log.error("未能获取到隔离游戏窗口", stacklevel=3)
        return 0

    def _safe_enum_windows(self, dpy: xdisplay.Display | None = None) -> list[dict]:
        try:
            if dpy is None:
                return self._enum_windows()
            return self._enum_windows(dpy=dpy)
        except Exception as e:
            if isinstance(e, (ConnectionClosedError, DisplayConnectionError, ConnectionError, OSError, RuntimeError)):
                if dpy is None or dpy is self._disp:
                    self._invalidate_connection()
            log.error(f"枚举窗口时发生错误: {e}")
            return []

    def _match_game_window(
        self,
        candidates: list[dict],
        title: str = "LimbusCompany",
        class_name: str = "UnityWndClass",
    ) -> int:
        """从候选窗口列表中筛选匹配的游戏客户窗口。"""
        title_l = self._normalize_window_name(title)
        class_l = (class_name or "").casefold()
        matched: list[dict] = []

        # 1. 优先按归一化标题匹配
        for info in candidates:
            if title_l and self._normalize_window_name(info.get("title", "")) == title_l:
                matched.append(info)

        # 2. 若标题未匹配，在无标题窗口中按 class_name 或 "limbus" 兜底
        if not matched:
            for info in candidates:
                cls = info.get("cls", "").casefold()
                if not info.get("title", "").strip():
                    if (class_l and class_l in cls) or "limbus" in cls:
                        matched.append(info)

        if not matched:
            return 0

        # 在匹配到的候选窗口中挑选最优客户窗口：
        # 1. 优先有效几何尺寸窗口（> 1x1，过滤 Wine 内部 dummy 窗口）
        # 2. 优先匹配指定 class_name 的窗口
        # 3. 同尺寸下优先更深层级的真实 Client Window（相对于框架窗口）
        # 4. 几何面积优先
        def _score(c: dict) -> tuple[int, int, int, int]:
            w = c.get("width", 1920)
            h = c.get("height", 1080)
            has_valid_size = 1 if (w > 1 and h > 1) else 0
            has_class = 1 if (class_l and class_l in c.get("cls", "").casefold()) else 0
            depth = c.get("depth", 0)
            area = w * h
            return (has_valid_size, has_class, depth, area)

        best = max(matched, key=_score)
        return best["wid"]

    def _log_found_window(self, candidates: list[dict]) -> None:
        info = next((c for c in candidates if c["wid"] == self._hwnd), None)
        log.debug(
            f"找到游戏窗口: wid={self._hwnd}, class={info['cls'] if info else '?'}, title={info['title'] if info else '?'}",
            stacklevel=3,
        )

    @staticmethod
    def _normalize_window_name(value: str) -> str:
        """归一化 X11 窗口标题，兼容游戏标题中的空格和大小写差异。"""
        return "".join(char for char in (value or "").casefold() if char.isalnum())

    def _enum_windows(self, dpy: xdisplay.Display | None = None) -> list[dict]:
        """遍历窗口树收集窗口信息。

        X11 会话下 KWin 会把受管客户窗口重新父化到框架窗口之下（客户窗口
        是根窗口的“孙子”），因此必须递归遍历，只看根窗口直接子层会漏掉
        全部受管窗口。结果按深度降序排列：同名的框架与客户窗口中，
        更深的是真实客户窗口（几何不含装饰）。
        """
        d = dpy if dpy is not None else self._dpy
        results: list[dict] = []
        seen: set[int] = set()
        root = d.screen().root
        pending: list[tuple[object, int]] = [(root, 0)]
        while pending:
            parent, depth = pending.pop(0)
            if depth > 4:
                continue
            try:
                children = parent.query_tree().children
            except XError:
                continue
            for win in children:
                if win.id in seen:
                    continue
                seen.add(win.id)
                pending.append((win, depth + 1))
                try:
                    cls = ""
                    wm_class = win.get_wm_class()
                    if wm_class:
                        cls = f"{wm_class[0]}.{wm_class[1]}"
                    title = self._window_title(win, dpy=d)
                    if not title and not cls:
                        continue
                    try:
                        geo = win.get_geometry()
                        w, h = geo.width, geo.height
                    except XError:
                        w, h = 0, 0
                except XError:
                    continue
                results.append(
                    {
                        "wid": win.id,
                        "cls": cls,
                        "title": title or "",
                        "depth": depth,
                        "width": w,
                        "height": h,
                    }
                )
                if title:
                    self._enum_windows_list.append(f"wid: {win.id}, class_name: {cls}, window_text: {title}")
        results.sort(key=lambda r: -r["depth"])
        return results

    def _window_title(self, win, dpy: xdisplay.Display | None = None) -> str:
        value = self._get_atom_value(win, "_NET_WM_NAME", dpy=dpy)
        if value:
            try:
                return value.decode("utf-8", errors="replace")
            except Exception:
                pass
        try:
            name = win.get_wm_name()
            if isinstance(name, bytes):
                return name.decode("utf-8", errors="replace")
            return name or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ 状态
    @property
    @_serialized
    def hwnd(self) -> int:
        """获取窗口句柄"""
        if self._hwnd == 0:
            if self._is_simulator_mode():
                log.debug("模拟器模式下无法获取窗口句柄", stacklevel=3)
            else:
                self.init_handle()
        else:
            try:
                self._window_obj(self._hwnd).get_attributes()
            except (XError, RuntimeError, ConnectionClosedError, DisplayConnectionError, ConnectionError, OSError) as e:
                # RuntimeError：X 连接应答流错乱，需重建连接后重新查找窗口
                log.warning(
                    f"窗口句柄无效或 X 连接异常（{type(e).__name__}），重新获取, 当前句柄为 {self._hwnd}",
                    stacklevel=3,
                )
                self._invalidate_connection()
                self.init_handle()
                if self._hwnd:
                    log.info(f"重新获取窗口句柄成功, 新句柄为 {self._hwnd}", stacklevel=3)
        return self._hwnd

    @staticmethod
    def _is_simulator_mode() -> bool:
        try:
            from module.config import cfg

            return bool(cfg.config.simulator)
        except Exception:
            return False

    @property
    def isTransparent(self) -> bool:
        """判断窗口是否透明（X11 不支持，仅返回内部标记）"""
        return self._transparent

    @property
    @_serialized
    def pid(self) -> int:
        """游戏窗口所属进程 PID（用于结束游戏进程）"""
        if self.hwnd == 0:
            return 0
        try:
            value = self._get_atom_value(self._window_obj(), "_NET_WM_PID")
            return int(value[0]) if value else 0
        except Exception:
            return 0

    @property
    @_serialized
    def isMinimized(self) -> bool:
        """判断窗口是否最小化"""
        if self.hwnd == 0:
            return False
        try:
            state = self._window_obj().get_wm_state()
            if state and "state" in state:
                return state["state"] == IconicState
        except Exception:
            pass
        return False

    @property
    @_serialized
    def isActive(self) -> bool:
        """判断窗口是否为活动窗口"""
        if self.hwnd == 0:
            return False
        try:
            value = self._get_atom_value(self._root, "_NET_ACTIVE_WINDOW")
            return value and int(value[0]) == self._hwnd
        except Exception:
            return False

    # ------------------------------------------------------------------ 几何
    def _frame_extents(self, win) -> tuple[int, int, int, int]:
        """窗口装饰边框 (left, right, top, bottom)"""
        if self.is_gamescope:
            return 0, 0, 0, 0
        value = self._get_atom_value(win, "_NET_FRAME_EXTENTS")
        if value and len(value) >= 4:
            return int(value[0]), int(value[1]), int(value[2]), int(value[3])
        return 0, 0, 0, 0

    @_serialized
    def rect(self, client: bool = False) -> tuple[int, int, int, int]:
        """获取窗口位置和大小

        Parameters
        ---
        client: bool
            是否获取客户区大小，默认为`False`
        """
        if self.hwnd == 0:
            return (0, 0, 0, 0)
        try:
            win = self._window_obj()
            geo = win.get_geometry()
            pos_x, pos_y = self._root_position(win)
            if client:
                # 返回内容区（扣除外部由 ScreenShot 探测并同步的 content_offset）在屏幕上的投影；
                # 底边保持客户区真实底边，高度即 geo.height - offset
                offset = self.content_offset
                return (pos_x, pos_y + offset, pos_x + geo.width, pos_y + geo.height)
            left, right, top, bottom = self._frame_extents(win)
            return (
                pos_x - left,
                pos_y - top,
                pos_x + geo.width + right,
                pos_y + geo.height + bottom,
            )
        except XError as e:
            log.debug(f"获取窗口几何失败: {e}")
            return (0, 0, 0, 0)

    def _root_position(self, win) -> tuple[int, int]:
        """计算窗口原点在根窗口坐标系下的位置。

        通过父子层级几何偏移累加实现：部分合成器（如 KWin 的 XWayland 缩放）下
        TranslateCoordinates 应答不可靠，逐级累加更稳定。
        """
        x = y = 0
        current = win
        while True:
            geo = current.get_geometry()
            x += geo.x
            y += geo.y
            parent = current.query_tree().parent
            if parent is None or parent.id == self._root.id:
                break
            current = parent
        return x, y

    @property
    @_serialized
    def monitor_info(self) -> dict:
        """获取窗口所在显示器的信息"""
        if self.hwnd == 0:
            return {
                "Monitor": (0, 0, 0, 0),
                "Work": (0, 0, 0, 0),
                "Flags": 0,
                "Device": "Unknown",
            }
        monitors = self._enumerate_monitors()
        rect = self.rect(False)
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        target = None
        for mon in monitors:
            if mon[0] <= cx < mon[2] and mon[1] <= cy < mon[3]:
                target = mon
                break
        if target is None and monitors:
            target = monitors[0]
        if target is None:
            target = (0, 0, self._root.get_geometry().width, self._root.get_geometry().height)
        return {
            "Monitor": target,
            "Work": target,
            "Flags": 0,
            "Device": f"Monitor {monitors.index(target) + 1}" if target in monitors else "Unknown",
        }

    def _enumerate_monitors(self) -> list[tuple[int, int, int, int]]:
        monitors: list[tuple[int, int, int, int]] = []
        try:
            res = randr.get_screen_resources(self._root)
            for crtc in res.crtcs:
                try:
                    info = randr.get_crtc_info(self._root, crtc, res.config_timestamp)
                except XError:
                    continue
                if info.width > 0 and info.height > 0:
                    mon = (info.x, info.y, info.x + info.width, info.y + info.height)
                    if mon not in monitors:
                        monitors.append(mon)
        except Exception as e:
            log.debug(f"RandR 枚举显示器失败，退回根窗口尺寸: {e}")
        if not monitors:
            geo = self._root.get_geometry()
            monitors = [(0, 0, geo.width, geo.height)]
        return monitors

    # ------------------------------------------------------------------ 操作
    def _send_net_wm_state(self, action: int, atoms: list) -> None:
        state_atom = self._dpy.intern_atom("_NET_WM_STATE", True)
        data = [action, atoms[0], atoms[1] if len(atoms) > 1 else 0, 0, 0]
        # 注意：event_mask 只能传给 send_event；放进 ClientMessage 构造参数会
        # 被 rq.Event.__init__ 当作待序列化字段而抛 TypeError（to_binary() ...）
        ev = xevent.ClientMessage(
            window=self._hwnd,
            client_type=state_atom,
            data=(32, data),
        )
        self._root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self._dpy.flush()

    def _send_net_active(self) -> None:
        active_atom = self._dpy.intern_atom("_NET_ACTIVE_WINDOW", True)
        ev = xevent.ClientMessage(
            window=self._hwnd,
            client_type=active_atom,
            data=(32, [1, int(time.time() * 1000) & 0xFFFFFFFF, 0, 0, 0]),
        )
        self._root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self._dpy.flush()

    @_serialized
    def setForeground(self) -> None:
        """将窗口设为前台窗口"""
        if self.hwnd == 0:
            return
        try:
            self._send_net_active()
            self._window_obj().configure(stack_mode=X.Above)
            self._dpy.flush()
        except Exception as e:
            log.debug(f"窗口置前失败: {e}")

    @_serialized
    def setMaximized(self, value: bool = True) -> None:
        """最大化窗口"""
        if self.hwnd == 0:
            return
        state = _net_wm_state_atoms(self._dpy)
        vert, horz = state[1], state[2]
        self._send_net_wm_state(1 if value else 0, [vert, horz])

    @_serialized
    def switchFullScreenMode(self) -> bool:
        """切换全屏模式 (模拟 Alt+Enter，与 Windows 版行为一致)"""
        if self.hwnd == 0:
            return False
        if self.isMinimized:
            self.restore()
            time.sleep(0.5)
        try:
            # 确保窗口聚焦，XTEST 注入的按键才会送达游戏
            self.setForeground()
            time.sleep(0.2)
            alt_kc = self._dpy.keysym_to_keycode(XK.string_to_keysym("Alt_L"))
            enter_kc = self._dpy.keysym_to_keycode(XK.string_to_keysym("Return"))
            xtest.fake_input(self._dpy, X.KeyPress, alt_kc)
            self._dpy.flush()
            time.sleep(0.05)
            xtest.fake_input(self._dpy, X.KeyPress, enter_kc)
            self._dpy.flush()
            time.sleep(0.05)
            xtest.fake_input(self._dpy, X.KeyRelease, enter_kc)
            self._dpy.flush()
            xtest.fake_input(self._dpy, X.KeyRelease, alt_kc)
            self._dpy.flush()
            return True
        except Exception as e:
            log.error(f"切换全屏模式失败: {e}")
            return False

    @_serialized
    def restore(self, activate: bool = False) -> None:
        """恢复窗口（取消最小化）"""
        if self.hwnd == 0:
            return
        try:
            if self.isMinimized:
                if activate:
                    self._send_net_active()
                else:
                    # EWMH 无“仅取消最小化不聚焦”的标准方式，退而发送状态移除 HIDDEN
                    hidden = _net_wm_state_atoms(self._dpy)[3]
                    self._send_net_wm_state(0, [hidden])
                    self._window_obj().configure(stack_mode=X.Above)
                    self._dpy.flush()
                time.sleep(0.1)
        except Exception as e:
            log.debug(f"恢复窗口失败: {e}")

    @_serialized
    def set_window_pos(self, x: int, y: int) -> None:
        """将窗口移动到屏幕坐标 (x, y)"""
        if self.hwnd == 0:
            return
        x = int(x)
        y = int(y)
        try:
            win = self._window_obj()
            geo = win.get_geometry()
            pos_x, pos_y = self._root_position(win)
            parent_x = pos_x - geo.x
            parent_y = pos_y - geo.y
            win.configure(x=x - parent_x, y=y - parent_y)
            self._dpy.flush()
        except XError as e:
            log.debug(f"移动窗口失败: {e}")

    @_serialized
    def set_window_size(self, width: int, height: int) -> None:
        """设置窗口（外框）大小"""
        if self.hwnd == 0:
            return
        try:
            self._window_obj().configure(width=int(width), height=int(height))
            self._dpy.flush()
        except XError as e:
            log.debug(f"设置窗口大小失败: {e}")

    @_serialized
    def set_topmost(self, topmost: bool) -> None:
        """设置窗口是否始终置顶"""
        if self.hwnd == 0:
            return
        above = _net_wm_state_atoms(self._dpy)[4]
        self._send_net_wm_state(1 if topmost else 0, [above])

    @_serialized
    def set_decorated(self, keep_caption: bool = True) -> None:
        """去除窗口装饰（边框/标题栏/最大化按钮），keep_caption 决定是否保留标题栏。

        通过设置 _MOTIF_WM_HINTS 实现，主流窗口管理器（Mutter/KWin/XFWM）均支持。
        """
        if self.hwnd == 0:
            return
        try:
            win = self._window_obj()
            # MWM_HINTS_DECORATIONS 生效时 decorations=1(MWM_DECOR_ALL) 保留装饰, 0 全部去除
            decorations = 1 if keep_caption else 0
            hints = (MWM_HINTS_DECORATIONS, 0, decorations, 0, 0)
            prop = self._dpy.intern_atom(_MOTIF_WM_HINTS, False)
            from Xlib import Xatom

            win.change_property(prop, Xatom.CARDINAL, 32, hints)
            self._dpy.flush()
        except Exception as e:
            log.debug(f"设置窗口装饰失败: {e}")

    def set_window_transparent(self, transparent: bool = True) -> None:
        """设置窗口透明（X11 下无 Win32 分层窗口等价物，仅记录状态）"""
        if self.hwnd == 0:
            return
        if not self._transparent and transparent:
            log.debug("Linux 下不支持窗口透明/鼠标穿透，已跳过")
        self._transparent = transparent
