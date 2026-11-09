from unittest.mock import Mock, patch

from module.automation import screenshot as screenshot_module
from module.automation.screenshot import ScreenShot
from module.game_and_screen import x11_handle
from module.config.config_typing import ConfigModel
from ruamel.yaml import YAML
from pathlib import Path


def test_gamescope_capture_failure_never_reads_host(monkeypatch):
    monkeypatch.setattr(screenshot_module, 'IS_WINDOWS', False)
    monkeypatch.setattr(screenshot_module, 'cfg', Mock(simulator=False, background_click=True))
    monkeypatch.setattr(screenshot_module.screen, 'handle', Mock(is_gamescope=True))
    with patch.object(ScreenShot, '_linux_window_is_ready', return_value=True), patch.object(ScreenShot, 'take_screenshot_x11', side_effect=RuntimeError('display lost')), patch.object(ScreenShot, 'take_screenshot_mss') as mss, patch.object(ScreenShot, 'take_screenshot_pyautogui') as host:
        assert ScreenShot.take_screenshot() is None
        mss.assert_not_called()
        host.assert_not_called()


def test_configured_display_failure_never_probes_host():
    handle = object.__new__(x11_handle.X11Handle)
    handle._is_gamescope = False
    handle._enum_windows_list = []
    with patch('module.game_and_screen.gamescope_launcher.GamescopeLauncher.display_name', return_value=':87'), patch.object(handle, '_try_display', return_value=0) as probe:
        assert handle.init_handle() == 0
        assert [call.args[0] for call in probe.call_args_list] == [':87']
        assert handle.is_gamescope


def test_launcher_config_round_trip():
    values = YAML(typ='safe').load(Path('assets/config/config.example.yaml'))
    overrides = dict(steam_root='/tmp/steam', custom_game_path='/tmp/game.exe', proton_path='/tmp/proton', slr_entrypoint='/tmp/runtime', compat_data_path='/tmp/prefix', gamescope_display=':87')
    values.update(overrides)
    restored = ConfigModel.model_validate(ConfigModel.model_validate(values).model_dump())
    assert {key: getattr(restored, key) for key in overrides} == overrides


def test_linux_game_cleanup_does_not_kill_by_name():
    from module.game_and_screen import game
    instance = object.__new__(game.Game)
    instance.log = Mock()
    with patch.object(game, 'IS_LINUX', True), patch('module.game_and_screen.gamescope_launcher.GamescopeLauncher') as launcher, patch('module.platform_compat.kill_process_by_name') as kill:
        instance.kill_game()
        launcher.return_value.terminate.assert_called_once()
        kill.assert_not_called()


def test_missing_isolated_display_never_probes_host():
    handle = object.__new__(x11_handle.X11Handle)
    handle._is_gamescope = False
    handle._enum_windows_list = []
    with patch.object(x11_handle, 'get_configured_gamescope_display', return_value=None), patch.object(x11_handle, 'detect_gamescope_displays', return_value=[]), patch.object(handle, '_try_display') as probe:
        assert handle.init_handle() == 0
        probe.assert_not_called()
