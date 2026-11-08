import subprocess
import time
from unittest.mock import MagicMock, patch

from module.game_and_screen.gamescope_launcher import GamescopeLauncher


def test_launcher_terminate_idempotent():
    """验证 terminate 在进程未运行时安全幂等"""
    launcher = GamescopeLauncher()
    launcher._process = None
    launcher._log_file = None
    # 应当不抛出任何异常
    launcher.terminate()
    assert launcher.is_running() is False


def test_launcher_terminate_kills_process_group():
    """验证 terminate 能够向整个进程组发送终止信号并清理句柄"""
    launcher = GamescopeLauncher()
    # 启动一个真实的短生命周期 sleep 进程模拟运行中的 Gamescope
    proc = subprocess.Popen(
        ["sleep", "30"],
        start_new_session=True,
    )
    launcher._process = proc
    launcher._pgid = proc.pid
    assert launcher.is_running() is True

    launcher.terminate()
    assert launcher.is_running() is False
    assert launcher._process is None
    # 确认系统级进程已消亡
    time.sleep(0.1)
    assert proc.poll() is not None


def test_game_kill_game_terminates_gamescope_on_linux():
    """验证在 Linux 下 Game.kill_game 联动终止 Gamescope 沙箱"""
    with (
        patch("module.game_and_screen.game.IS_LINUX", True),
        patch(
            "module.game_and_screen.gamescope_launcher.GamescopeLauncher.terminate"
        ) as mock_terminate,
        patch("module.platform_compat.kill_process_by_name") as mock_kill_name,
    ):
        from module.game_and_screen.game import Game

        game = Game(logger=MagicMock())
        game.kill_game()

        mock_terminate.assert_called_once()
        mock_kill_name.assert_not_called()


def test_system_action_exit_game_delegates_to_game_process():
    """验证 _action_exit_game 委托给 game_process.kill_game"""
    with (
        patch("module.system_actions.cfg.get_value", return_value=False),
        patch(
            "module.game_and_screen.game_process.kill_game"
        ) as mock_kill_game,
    ):
        from module.system_actions import _action_exit_game

        _action_exit_game()
        mock_kill_game.assert_called_once()


def test_farming_interface_stop_script_delegates_to_game_process():
    """验证在 Linux 下 farming_interface.stop_script 委托给 game_process.kill_game"""
    with (
        patch("app.farming_interface.IS_WINDOWS", False),
        patch(
            "module.game_and_screen.game_process.kill_game"
        ) as mock_kill_game,
    ):
        from app.farming_interface import FarmingInterfaceLeft

        instance = MagicMock(spec=FarmingInterfaceLeft)
        instance.my_script = MagicMock()
        instance.my_script.isRunning.return_value = True

        FarmingInterfaceLeft.stop_script(instance)

        instance.my_script.terminate.assert_called_once()
        mock_kill_game.assert_called_once()


def test_my_app_close_event_delegates_to_game_process():
    """验证在 Linux 下 AALC 主窗口退出时委托给 game_process.kill_game"""
    with (
        patch("module.platform_compat.IS_WINDOWS", False),
        patch(
            "module.game_and_screen.game_process.kill_game"
        ) as mock_kill_game,
    ):
        from app.my_app import MainWindow

        instance = MagicMock(spec=MainWindow)
        instance.x.return_value = 100
        instance.y.return_value = 100
        instance.farming_interface = MagicMock()
        instance.farming_interface.interface_left.my_script = None
        instance.tools_interface = MagicMock()
        instance.tools_interface.tools = {}

        mock_event = MagicMock()
        with patch.object(
            MainWindow.__bases__[0], "closeEvent", return_value=None
        ):
            MainWindow.closeEvent(instance, mock_event)

        mock_kill_game.assert_called_once()


def test_cleanup_never_unlinks_unowned_x11_socket(tmp_path):
    from pathlib import Path
    socket = tmp_path / "X1"
    socket.touch()
    with patch.object(Path, "unlink") as unlink:
        assert GamescopeLauncher.cleanup_stale_x11_sockets(":1") == []
        unlink.assert_not_called()


def test_terminate_cleans_group_after_leader_exits():
    import os
    import signal
    launcher = GamescopeLauncher()
    proc = subprocess.Popen(["sh", "-c", "sleep 30 &"], start_new_session=True)
    proc.wait(timeout=2)
    launcher._process = proc
    launcher._pgid = proc.pid
    with patch("module.game_and_screen.gamescope_launcher.os.killpg", wraps=os.killpg) as killpg:
        launcher.terminate()
        assert any(call.args == (proc.pid, signal.SIGTERM) for call in killpg.call_args_list)
    assert launcher._pgid is None


def test_terminate_without_owned_process_does_not_kill_game():
    launcher = GamescopeLauncher()
    launcher._process = None
    launcher._pgid = None
    with patch("module.platform_compat.kill_process_by_name") as kill_name:
        launcher.terminate()
        kill_name.assert_not_called()
