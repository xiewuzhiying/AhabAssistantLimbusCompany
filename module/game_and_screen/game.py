import os
from time import sleep, time

import psutil

from module.config import cfg
from module.platform_compat import IS_LINUX, IS_WINDOWS, open_uri
from utils.singletonmeta import SingletonMeta


class Game(metaclass=SingletonMeta):
    def __init__(self, logger):
        self.game_path = cfg.game_path
        self.game_url = "steam://rungameid/1973530"
        self.log = logger
        self.process_name = cfg.game_process_name
        self.game_path_exists = True
        self.last_check_time = None
        self.check_in_short_time = 0

    def check_game_alive(self):
        if IS_LINUX and not cfg.simulator:
            from module.game_and_screen.gamescope_launcher import GamescopeLauncher

            return GamescopeLauncher().is_running()
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                # 获取进程的可执行文件名（如 "notepad.exe"）
                proc_name = proc.info["name"] or ""
                # 精确匹配进程名（区分大小写，取决于系统）
                if self.process_name in proc_name:
                    self.log.debug(f"游戏已启动：{self.process_name}，进程ID：{proc.pid}")
                    return True
                # Linux/Proton 下首次启动要先初始化 wine 前缀，游戏进程出现前
                # Steam 启动链已存在（命令行含游戏 exe 路径），视同游戏在启动中，
                # 避免此期间反复触发 steam 启动命令
                if IS_LINUX and proc.info["cmdline"]:
                    if any(self.process_name in (c or "") for c in proc.info["cmdline"]):
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # 忽略已终止、无权限或僵尸进程
                continue
        return False

    def start_game(self) -> bool:
        """启动游戏"""
        if self.check_game_alive():
            if self.last_check_time is None or time() - self.last_check_time > 60:
                self.last_check_time = time()
                self.check_in_short_time = 0
            else:
                self.check_in_short_time += 1
            if self.check_in_short_time > 5:
                from tasks.base.retry import kill_game

                kill_game()
                self.check_in_short_time = 0
            else:
                return True

        if IS_LINUX:
            try:
                from module.game_and_screen.gamescope_launcher import GamescopeLauncher

                launcher = GamescopeLauncher()
                if launcher.launch():
                    self.log.info("已通过 Gamescope 模式启动游戏")
                    return True
                else:
                    self.log.error("Gamescope 模式启动游戏失败")
                    return False
            except Exception as e:
                self.log.error(f"Gamescope 模式启动游戏异常: {e}")
                return False

        if not os.path.exists(self.game_path):
            self.log.error(f"游戏路径不存在：{self.game_path}，使用steam命令启动...")
            self.game_path_exists = False

        try:
            # 调用系统协议处理器打开 steam:// 链接（触发 Steam 启动游戏）
            open_uri(self.game_url)
            self.log.info("使用steam命令启动游戏")
            sleep(5)
            # Linux 下直接启动 exe 需要 Proton，只能依赖 Steam，跳过路径兜底
            if not self.check_game_alive() and self.game_path_exists and IS_WINDOWS:
                from module.platform_compat import open_path

                open_path(self.game_path)
                self.log.info(f"游戏启动：{self.game_path}")
            return True
        except Exception as e:
            self.log.error(f"启动游戏时发生错误：{e}")
        return False

    def kill_game(self) -> None:
        """关闭游戏进程。跨平台生命周期门面方法。

        Linux 模式下联动 GamescopeLauncher 回收独立沙箱及其子进程组；
        Linux 仅终止托管沙箱，Windows 保留句柄与进程名清理。
        """
        if IS_LINUX:
            try:
                from module.game_and_screen.gamescope_launcher import (
                    GamescopeLauncher,
                )

                GamescopeLauncher().terminate()
            except Exception as e:
                self.log.warning(f"终止 Gamescope 沙箱异常: {e}")
            return

        from module.platform_compat import kill_pid, kill_process_by_name

        try:
            from module.game_and_screen.screen import screen

            pid = getattr(screen.handle, "pid", 0)
            if pid and kill_pid(pid):
                self.log.info("已退出游戏（PID）")
                return
        except Exception:
            pass

        if kill_process_by_name(self.process_name):
            self.log.info("已退出游戏（进程名）")
