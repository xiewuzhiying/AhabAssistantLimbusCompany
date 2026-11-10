import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from time import sleep
from typing import Callable, Optional

from module.cancellation import checkpoint
from module.config import cfg
from module.game_and_screen.managed_session import ManagedSession
from module.logger import log
from module.native_linux import load_settings
from utils.singletonmeta import SingletonMeta

LIMBUS_APP_ID = "1973530"


class GamescopeLauncher(metaclass=SingletonMeta):
    """Linux 平台专属 Gamescope 启动器。

    负责自动解析 Steam 配置定位 Limbus Company、Proton 与容器运行时，
    以 Gamescope headless 模式拉起游戏，实现无头静音与输入隔离。
    """

    def __init__(self):
        self._lifecycle_lock = threading.RLock()
        self._pgid: Optional[int] = None
        self._session: ManagedSession | None = None
        self._cleanup_failed = False
        self._process: Optional[subprocess.Popen] = None
        self._log_file = None
        self._steam_root: Optional[Path] = None
        self._game_dir: Optional[Path] = None
        self._game_exe: Optional[Path] = None
        self._compat_data: Optional[Path] = None
        self._proton_bin: Optional[Path] = None
        self._slr_entrypoint: Optional[Path] = None
        self._xwayland_wrapper_path: Optional[Path] = None

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._process

    def owned_pids(self) -> list[int]:
        return self._session.member_pids() if self._session else []

    def cleanup_pending(self) -> bool:
        return self._cleanup_failed or (self._session is not None and not self.is_running())

    def display_name(self) -> str | None:
        """Resolve only a display belonging to this launch's cgroup."""
        import psutil
        for pid in self.owned_pids():
            try:
                process = psutil.Process(pid)
                if process.name().lower() == 'xwayland':
                    for argument in process.cmdline()[1:]:
                        if re.fullmatch(r':\d+', argument) and argument != os.environ.get('DISPLAY'):
                            return argument
                if 'limbus' in process.name().lower():
                    value = process.environ().get('DISPLAY')
                    if value and value != os.environ.get('DISPLAY'):
                        return value
            except psutil.Error:
                pass
        return None

    def is_running(self) -> bool:
        """检查 Gamescope 子进程是否存活"""
        if self._process is None:
            return False
        return self._process.poll() is None

    def terminate(self) -> None:
        """Stop only the process group created by this launcher, even after its leader exits."""
        with self._lifecycle_lock:
            if self._session is not None:
                self._cleanup_failed = True
                self._session.stop()
                self._session = None
                self._cleanup_failed = False
            pgid = self._pgid
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if self._process is not None:
                            self._process.poll()
                        try:
                            os.killpg(pgid, 0)
                        except ProcessLookupError:
                            break
                        sleep(0.05)
                    else:
                        os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    log.warning(f"Cannot terminate owned Gamescope group {pgid}: {exc}")
                    return  # Retain ownership so cleanup can be retried.
            if self._process is not None:
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    log.warning("Gamescope did not exit after group termination")
                    return
            self._process = None
            self._pgid = None
            if self._log_file:
                self._log_file.close()
                self._log_file = None
            self._cleanup_xwayland_wrapper()

    def terminate_async(self, callback: Optional[Callable[[], None]] = None) -> threading.Thread:
        """在独立的守护工作线程中异步执行终止，避免阻塞 Qt UI 事件循环"""
        def _worker():
            try:
                self.terminate()
            finally:
                if callback:
                    try:
                        callback()
                    except Exception as e:
                        log.warning(f"终止回调执行失败: {e}")

        t = threading.Thread(target=_worker, name="GamescopeTerminator", daemon=True)
        t.start()
        return t

    def find_steam_root(self) -> Optional[Path]:
        """定位 Steam 根安装目录"""
        env_steam = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH")
        candidates = []
        if env_steam:
            candidates.append(Path(env_steam))
        candidates.extend([
            Path.home() / ".local/share/Steam",
            Path.home() / ".steam/root",
            Path.home() / ".steam/steam",
        ])
        for p in candidates:
            if p.is_dir() and (p / "steamapps").is_dir():
                return p.resolve()
        return None

    def get_library_folders(self, steam_root: Path) -> list[Path]:
        """解析 libraryfolders.vdf 获取所有 Steam 库路径"""
        vdf_file = steam_root / "steamapps/libraryfolders.vdf"
        libraries = [steam_root]
        if not vdf_file.is_file():
            return libraries

        try:
            content = vdf_file.read_text(encoding="utf-8", errors="ignore")
            # 匹配 "path" "..."
            paths = re.findall(r'"path"\s+"([^"]+)"', content)
            for p_str in paths:
                p = Path(p_str)
                if p.is_dir() and p not in libraries:
                    libraries.append(p)
        except Exception as e:
            log.warning(f"解析 libraryfolders.vdf 失败: {e}")

        return libraries

    def locate_game(self, libraries: list[Path]) -> tuple[Optional[Path], Optional[Path]]:
        """在所有 Steam 库中定位 Limbus Company 游戏目录与 compatdata 目录"""
        for lib in libraries:
            manifest = lib / f"steamapps/appmanifest_{LIMBUS_APP_ID}.acf"
            if manifest.is_file():
                try:
                    content = manifest.read_text(encoding="utf-8", errors="ignore")
                    m = re.search(r'"installdir"\s+"([^"]+)"', content)
                    install_dir_name = m.group(1) if m else "Limbus Company"
                    game_dir = lib / f"steamapps/common/{install_dir_name}"
                    game_exe = game_dir / "LimbusCompany.exe"
                    compat_data = lib / f"steamapps/compatdata/{LIMBUS_APP_ID}"
                    if game_exe.is_file():
                        return game_exe, compat_data
                except Exception as e:
                    log.warning(f"读取清单文件 {manifest} 失败: {e}")

        # 兜底直接扫描常见路径
        for lib in libraries:
            game_exe = lib / "steamapps/common/Limbus Company/LimbusCompany.exe"
            if game_exe.is_file():
                compat_data = lib / f"steamapps/compatdata/{LIMBUS_APP_ID}"
                return game_exe, compat_data

        return None, None

    def locate_proton_and_runtime(
        self, steam_root: Optional[Path], libraries: list[Path]
    ) -> tuple[Optional[Path], Optional[Path]]:
        """定位配置的 Proton 运行程序与 Steam Linux Runtime 容器入口"""
        config_vdf = steam_root / "config/config.vdf" if steam_root else None
        tool_name = None

        if config_vdf is not None and config_vdf.is_file():
            try:
                content = config_vdf.read_text(encoding="utf-8", errors="ignore")
                # 寻找 CompatToolMapping 中的 1973530 配置
                # 结构通常为 "1973530" { ... "name" "..." ... }
                mapping_match = re.search(
                    r'"1973530"\s*\{[^}]*?"name"\s+"([^"]+)"', content, re.DOTALL
                )
                if mapping_match:
                    tool_name = mapping_match.group(1)
                else:
                    # 寻找全局默认工具 "0" { ... "name" "..." ... }
                    default_match = re.search(
                        r'"0"\s*\{[^}]*?"name"\s+"([^"]+)"', content, re.DOTALL
                    )
                    if default_match:
                        tool_name = default_match.group(1)
            except Exception as e:
                log.warning(f"解析 config.vdf 失败: {e}")

        proton_dir = None
        # 1. 查找指定工具目录
        if tool_name:
            candidates = ([steam_root / f"compatibilitytools.d/{tool_name}"]
                          if steam_root else [])
            for lib in libraries:
                candidates.append(lib / f"steamapps/common/{tool_name}")
                candidates.append(lib / f"compatibilitytools.d/{tool_name}")

            for c in candidates:
                if c.is_dir() and (c / "proton").is_file():
                    proton_dir = c
                    break

        # 2. 兜底扫描任何可用的 Proton
        if not proton_dir:
            for lib in libraries:
                common = lib / "steamapps/common"
                if common.is_dir():
                    for entry in sorted(common.iterdir(), reverse=True):
                        if (
                            entry.is_dir()
                            and entry.name.startswith("Proton")
                            and (entry / "proton").is_file()
                        ):
                            proton_dir = entry
                            break
                if proton_dir:
                    break

        proton_bin = proton_dir / "proton" if proton_dir else None

        # 查找 Steam Linux Runtime 容器
        slr_entrypoint = None
        slr_appid = "1628350"  # 默认 sniper
        if proton_dir and (proton_dir / "toolmanifest.vdf").is_file():
            try:
                t_content = (proton_dir / "toolmanifest.vdf").read_text(
                    encoding="utf-8", errors="ignore"
                )
                m = re.search(r'"require_tool_appid"\s+"([^"]+)"', t_content)
                if m:
                    slr_appid = m.group(1)
            except Exception:
                pass

        for lib in libraries:
            # 常见 sniper 容器目录
            possible_slrs = [
                lib / "steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point",
                lib / f"steamapps/common/SteamLinuxRuntime_{slr_appid}/_v2-entry-point",
                lib / "steamapps/common/Steam Linux Runtime 3.0 (sniper)/_v2-entry-point",
            ]
            for slr in possible_slrs:
                if slr.is_file() and os.access(slr, os.X_OK):
                    slr_entrypoint = slr
                    break
            if slr_entrypoint:
                break

        return proton_bin, slr_entrypoint

    def ensure_steam_appid_file(self, game_dir: Path) -> None:
        """确保游戏根目录下存在 steam_appid.txt 以避免 SteamAPI 自重启"""
        appid_file = game_dir / "steam_appid.txt"
        try:
            if not appid_file.exists() or appid_file.read_text().strip() != LIMBUS_APP_ID:
                appid_file.write_text(LIMBUS_APP_ID)
                log.info(f"已创建/更新 {appid_file}")
        except Exception as e:
            log.warning(f"写入 steam_appid.txt 失败: {e}")

    def resolve_paths(self) -> bool:
        """解析所有依赖路径。

        优先支持用户配置覆盖与环境变量覆盖，未提供时平滑回退至自动探测。
        """
        # Resolve each launch afresh; a removed override must not reuse stale paths.
        self._steam_root = self._game_dir = self._game_exe = None
        self._compat_data = self._proton_bin = self._slr_entrypoint = None
        # 1. 解析 Steam 根目录
        custom_steam = os.environ.get("AALC_STEAM_ROOT") or cfg.get_value(
            "steam_root", ""
        )
        if custom_steam and Path(custom_steam).is_dir():
            self._steam_root = Path(custom_steam).resolve()
            log.info(f"使用自定义 Steam 根目录: {self._steam_root}")
        else:
            self._steam_root = self.find_steam_root()

        libraries = (
            self.get_library_folders(self._steam_root) if self._steam_root else []
        )

        # 2. 解析游戏可执行文件与 Compatdata 目录
        custom_game = (
            os.environ.get("AALC_GAME_PATH")
            or os.environ.get("AALC_GAME_DIR")
            or cfg.get_value("custom_game_path", "")
            or getattr(cfg, "game_path", "")
        )
        if custom_game:
            p = Path(custom_game)
            if p.is_file() and p.name == "LimbusCompany.exe":
                self._game_exe = p.resolve()
            elif p.is_dir() and (p / "LimbusCompany.exe").is_file():
                self._game_exe = (p / "LimbusCompany.exe").resolve()
            elif p.is_file():
                self._game_exe = p.resolve()

        if self._game_exe and self._game_exe.is_file():
            self._game_dir = self._game_exe.parent
            if (
                self._game_dir.parent.parent / "compatdata/1973530"
            ).is_dir():
                self._compat_data = (
                    self._game_dir.parent.parent / "compatdata/1973530"
                ).resolve()
            elif libraries:
                _, det_compat = self.locate_game(libraries)
                self._compat_data = det_compat
        elif libraries:
            self._game_exe, self._compat_data = self.locate_game(libraries)
            if self._game_exe:
                self._game_dir = self._game_exe.parent

        if not self._game_exe or not self._game_exe.is_file():
            log.error("未找到 Limbus Company 游戏文件 (LimbusCompany.exe)")
            return False

        from module.native_linux import check_profile, load_settings
        try:
            self._compat_data = check_profile(Path(os.environ.get("AALC_COMPAT_DATA_PATH") or load_settings()['profile']))
            from module.game_and_screen.automation_profile import require_stopped
            require_stopped(self._compat_data)
        except (OSError, ValueError, RuntimeError) as exc:
            log.error(f"PROFILE_NOT_READY: {exc}")
            return False

        # 3. 解析 Proton 兼容层与 SLR 容器
        custom_proton = os.environ.get("AALC_PROTON_PATH") or cfg.get_value(
            "proton_path", ""
        )
        if custom_proton:
            p_proton = Path(custom_proton)
            if p_proton.is_file():
                self._proton_bin = p_proton.resolve()
            elif p_proton.is_dir() and (p_proton / "proton").is_file():
                self._proton_bin = (p_proton / "proton").resolve()

        custom_slr = os.environ.get("AALC_SLR_ENTRYPOINT") or cfg.get_value(
            "slr_entrypoint", ""
        )
        if custom_slr and Path(custom_slr).is_file():
            self._slr_entrypoint = Path(custom_slr).resolve()

        # 未被用户覆盖的部分，回退到自动探测
        if not self._proton_bin or not self._slr_entrypoint:
            det_proton, det_slr = self.locate_proton_and_runtime(
                self._steam_root, libraries
            )
            if not self._proton_bin:
                self._proton_bin = det_proton
            if not self._slr_entrypoint:
                self._slr_entrypoint = det_slr

        if not self._proton_bin or not self._proton_bin.is_file():
            log.error("未找到可用的 Proton 兼容层")
            return False

        log.info(f"Steam 根目录: {self._steam_root}")
        log.info(f"游戏文件: {self._game_exe}")
        log.info(f"Compatdata 路径: {self._compat_data}")
        log.info(f"Proton 路径: {self._proton_bin}")
        log.info(f"SteamLinuxRuntime: {self._slr_entrypoint}")
        return True

    def build_command(self) -> list[str]:
        """构建 Gamescope 启动命令行"""
        height = int(getattr(cfg, "set_win_size", 1080) or 1080)
        width = int(height * 16 / 9)

        cmd = [
            "gamescope",
            "--backend",
            "headless",
            "-W",
            str(width),
            "-H",
            str(height),
            "-r",
            "60",
            "--",
        ]

        is_nixos = Path("/run/current-system").exists() or bool(
            shutil.which("nixos-version")
        )
        steam_run_bin = shutil.which("steam-run")

        # 若找到 SLR 容器入口，通过容器执行以彻底规避 NixOS 非 FHS 约束
        if self._slr_entrypoint and self._slr_entrypoint.is_file():
            if is_nixos and steam_run_bin:
                cmd.extend([
                    steam_run_bin,
                    str(self._slr_entrypoint),
                    "--verb=waitforexitandrun",
                    "--",
                ])
            else:
                cmd.extend([
                    str(self._slr_entrypoint),
                    "--verb=waitforexitandrun",
                    "--",
                ])
        elif is_nixos and steam_run_bin:
            # 若无 SLR 容器但处于 NixOS，退回使用 steam-run
            cmd.extend([steam_run_bin])

        cmd.extend([
            str(self._proton_bin),
            "waitforexitandrun",
            str(self._game_exe),
            "-nosound",
        ])
        graphics_api = os.environ.get("AALC_GRAPHICS_API") or load_settings()["graphics_api"]
        if graphics_api not in ("auto", "d3d11", "d3d12"):
            raise ValueError(f"Unsupported graphics API: {graphics_api}")
        if graphics_api != "auto":
            cmd.append(f"-force-{graphics_api}")
        return cmd

    def build_env(self) -> dict[str, str]:
        """构建注入的环境变量字典 (实现音频驱动阻断与 Steamworks 凭据传递)"""
        env = os.environ.copy()
        # 禁用 Wine 底层音频驱动 (即使进入 pressure-vessel 容器依然生效)
        existing_overrides = env.get("WINEDLLOVERRIDES", "")
        audio_overrides = "winepulse.drv=d;winealsa.drv=d"
        if existing_overrides:
            env["WINEDLLOVERRIDES"] = f"{existing_overrides};{audio_overrides}"
        else:
            env["WINEDLLOVERRIDES"] = audio_overrides

        env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(self._steam_root)
        if self._compat_data:
            env["STEAM_COMPAT_DATA_PATH"] = str(self._compat_data)
        env["SteamAppId"] = LIMBUS_APP_ID
        env["SteamGameId"] = LIMBUS_APP_ID

        wrapper_path = self._setup_xwayland_wrapper()
        if wrapper_path:
            env["WLR_XWAYLAND"] = wrapper_path

        return env

    def _resolve_xwayland_bin(self) -> Optional[str]:
        """定位系统的真实 Xwayland 可执行文件路径"""
        # 1. 显式环境变量覆盖
        env_override = os.environ.get("XWAYLAND_PATH") or os.environ.get("AALC_XWAYLAND_PATH")
        if env_override and Path(env_override).is_file() and os.access(env_override, os.X_OK):
            return env_override

        # 2. PATH 环境变量查找
        candidate = shutil.which("Xwayland")
        if candidate:
            return candidate

        # 3. 常见系统标准路径 (FHS & NixOS 系统 profile)
        for standard_path in [
            "/run/current-system/sw/bin/Xwayland",
            "/usr/bin/Xwayland",
            "/usr/lib/xorg/Xwayland",
            "/usr/libexec/Xwayland",
        ]:
            if Path(standard_path).is_file() and os.access(standard_path, os.X_OK):
                return standard_path

        # 4. 二进制字节快速扫描 (NixOS gamescope 二进制通常直接嵌入其依赖的 Xwayland 完整路径)
        # 纯内存正则检索，绝不在启动关键路径阻塞执行外部 nix-store 查询进程
        gamescope_bin = shutil.which("gamescope")
        if gamescope_bin:
            try:
                real_gs = Path(os.path.realpath(gamescope_bin))
                for candidate_file in [real_gs, real_gs.parent / f".{real_gs.name}-wrapped"]:
                    if candidate_file.is_file():
                        content = candidate_file.read_bytes()
                        m = re.search(rb"(/nix/store/[a-z0-9]+-xwayland-[^/\0\s\"']+/bin/Xwayland)", content)
                        if m:
                            xw_path = Path(m.group(1).decode("ascii"))
                            if xw_path.is_file() and os.access(xw_path, os.X_OK):
                                return str(xw_path)
            except Exception:
                pass

        return None

    def _setup_xwayland_wrapper(self) -> Optional[str]:
        """创建隔离 LIBEI 的 Xwayland 包装脚本以规避 Gamescope EIS 协议漏洞崩溃"""
        if self._xwayland_wrapper_path and self._xwayland_wrapper_path.is_file():
            return str(self._xwayland_wrapper_path)

        xwayland_bin = self._resolve_xwayland_bin()
        if not xwayland_bin:
            log.warning("未找到系统的 Xwayland 二进制文件，跳过 EIS 规避包装器创建")
            return None

        try:
            wrapper_dir = Path(tempfile.mkdtemp(prefix="aalc-xwayland-"))
            wrapper_path = wrapper_dir / "xwayland_no_ei.sh"

            script_content = (
                "#!/bin/sh\n"
                "# AALC: 隔离 Gamescope 缺陷 EIS，促使 Xwayland 回退到原生 XTest\n"
                "unset LIBEI_SOCKET\n"
                f'exec {shlex.quote(xwayland_bin)} "$@"\n'
            )
            wrapper_path.write_text(script_content, encoding="utf-8")
            wrapper_path.chmod(0o755)

            self._xwayland_wrapper_path = wrapper_path
            return str(wrapper_path)
        except Exception as e:
            log.warning(f"创建 Xwayland EIS 规避包装器失败: {e}")
            return None

    def _cleanup_xwayland_wrapper(self) -> None:
        """清理临时 Xwayland 包装脚本"""
        if self._xwayland_wrapper_path:
            try:
                if self._xwayland_wrapper_path.exists():
                    self._xwayland_wrapper_path.unlink()
                    self._xwayland_wrapper_path.parent.rmdir()
            except Exception as e:
                log.warning(f"清理 Xwayland 包装脚本失败: {e}")
            finally:
                self._xwayland_wrapper_path = None

    @staticmethod
    def cleanup_stale_x11_sockets(display_name: Optional[str] = None) -> list[str]:
        """Leave shared X11 sockets to their server: failed connects do not prove ownership."""
        return []

    def launch(self) -> bool:
        with self._lifecycle_lock:
            checkpoint()
            return self._launch_locked()

    def _launch_locked(self) -> bool:
        """执行 Gamescope 模式启动"""
        if self._cleanup_failed:
            self.terminate()
            if self._cleanup_failed:
                return False
        if self.is_running():
            log.info("Gamescope 实例已在运行中")
            return True

        if self._process is not None or self._pgid is not None or self._session is not None:
            self.terminate()
            if self._process is not None or self._pgid is not None or self._session is not None:
                return False

        if not self.resolve_paths():
            return False

        assert self._game_dir is not None
        self.ensure_steam_appid_file(self._game_dir)

        try:
            cmd = self.build_command()
            env = self.build_env()
            log.info(f"启动 Gamescope 命令行: {' '.join(cmd)}")
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = (log_dir / "gamescope.log").open("a", encoding="utf-8")

            self._session = ManagedSession()
            self._process = self._session.start(cmd, env, self._game_dir, self._log_file)
            log.info(f"Gamescope 服务客户端已创建，PID: {self._process.pid}")
            # 等待 2 秒检测子进程是否立即崩溃
            sleep(2)
            if not self.is_running():
                log.error(f"Gamescope 启动后异常退出，退出码: {self._process.poll()}")
                self.terminate()
                return False
            return True
        except Exception as e:
            log.error(f"启动 Gamescope 异常: {e}")
            self.terminate()
            return False
