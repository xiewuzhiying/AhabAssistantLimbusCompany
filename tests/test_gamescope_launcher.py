from module.game_and_screen.gamescope_launcher import GamescopeLauncher


def test_gamescope_launcher_resolves_real_paths():
    """验证在真实机器上 GamescopeLauncher 能够准确解析 Steam 游戏与运行环境"""
    launcher = GamescopeLauncher()
    resolved = launcher.resolve_paths()
    assert resolved is True, "应当成功解析 Steam 游戏与 Proton 路径"

    assert launcher._steam_root is not None
    assert launcher._steam_root.is_dir()

    assert launcher._game_exe is not None
    assert launcher._game_exe.is_file()
    assert launcher._game_exe.name == "LimbusCompany.exe"

    assert launcher._compat_data is not None
    assert launcher._compat_data.is_dir()

    assert launcher._proton_bin is not None
    assert launcher._proton_bin.is_file()

    assert launcher._slr_entrypoint is not None
    assert launcher._slr_entrypoint.is_file()

    cmd = launcher.build_command()
    assert cmd[0] == "gamescope"
    assert "--backend" in cmd
    assert "headless" in cmd
    assert "waitforexitandrun" in cmd
    assert "-nosound" in cmd
    assert str(launcher._game_exe) in cmd

    env = launcher.build_env()
    assert "WINEDLLOVERRIDES" in env
    assert "winepulse.drv=d;winealsa.drv=d" in env["WINEDLLOVERRIDES"]
    assert env["SteamAppId"] == "1973530"
    assert env["STEAM_COMPAT_DATA_PATH"] == str(launcher._compat_data)


def test_gamescope_launcher_override_via_env(tmp_path):
    """验证用户通过环境变量显式配置路径时优先覆盖自动探测"""
    fake_steam = tmp_path / "custom_steam"
    fake_steam.mkdir()
    fake_game_dir = tmp_path / "custom_game"
    fake_game_dir.mkdir()
    fake_game_exe = fake_game_dir / "LimbusCompany.exe"
    fake_game_exe.touch()
    fake_compat = tmp_path / "custom_compat"
    (fake_compat/'pfx').mkdir(parents=True)
    (fake_compat/'pfx/user.reg').write_text('registry')
    fake_proton = tmp_path / "custom_proton"
    fake_proton.touch()
    fake_slr = tmp_path / "custom_slr"
    fake_slr.touch()

    launcher = GamescopeLauncher()
    # 清空可能残留的状态
    launcher._steam_root = None
    launcher._game_exe = None
    launcher._proton_bin = None
    launcher._slr_entrypoint = None

    env = {
        "AALC_STEAM_ROOT": str(fake_steam),
        "AALC_GAME_PATH": str(fake_game_exe),
        "AALC_COMPAT_DATA_PATH": str(fake_compat),
        "AALC_PROTON_PATH": str(fake_proton),
        "AALC_SLR_ENTRYPOINT": str(fake_slr),
    }

    from unittest.mock import patch

    with patch.dict("os.environ", env):
        ok = launcher.resolve_paths()
        assert ok is True
        assert launcher._steam_root == fake_steam.resolve()
        assert launcher._game_exe == fake_game_exe.resolve()
        assert launcher._compat_data == fake_compat.resolve()
        assert launcher._proton_bin == fake_proton.resolve()
        assert launcher._slr_entrypoint == fake_slr.resolve()


def test_gamescope_launcher_override_via_config(tmp_path):
    """验证用户通过 cfg 配置显式指定路径时优先覆盖自动探测"""
    fake_steam = tmp_path / "cfg_steam"
    fake_steam.mkdir()
    fake_game_dir = tmp_path / "cfg_game"
    fake_game_dir.mkdir()
    fake_game_exe = fake_game_dir / "LimbusCompany.exe"
    fake_game_exe.touch()
    fake_proton = tmp_path / "cfg_proton"
    fake_proton.touch()
    fake_slr = tmp_path / "cfg_slr"
    fake_slr.touch()

    config_map = {
        "steam_root": str(fake_steam),
        "custom_game_path": str(fake_game_exe),
        "proton_path": str(fake_proton),
        "slr_entrypoint": str(fake_slr),
    }

    from unittest.mock import patch

    launcher = GamescopeLauncher()
    launcher._steam_root = None
    launcher._game_exe = None
    launcher._proton_bin = None
    launcher._slr_entrypoint = None

    with (
        patch("os.environ", {}),
        patch(
            "module.game_and_screen.gamescope_launcher.cfg.get_value",
            side_effect=lambda k, d="": config_map.get(k, d),
        ),
    ):
        ok = launcher.resolve_paths()
        assert ok is True
        assert launcher._steam_root == fake_steam.resolve()
        assert launcher._game_exe == fake_game_exe.resolve()
        assert launcher._proton_bin == fake_proton.resolve()
        assert launcher._slr_entrypoint == fake_slr.resolve()



def test_runtime_detection_without_steam_root():
    launcher = GamescopeLauncher()
    assert launcher.locate_proton_and_runtime(None, []) == (None, None)


def test_failed_resolution_discards_old_paths(tmp_path, monkeypatch):
    from unittest.mock import patch
    launcher = GamescopeLauncher()
    old_exe = tmp_path / "LimbusCompany.exe"
    old_exe.touch()
    launcher._game_exe = old_exe
    with patch.dict("os.environ", {}, clear=True), patch.object(launcher, "find_steam_root", return_value=None), patch("module.game_and_screen.gamescope_launcher.cfg") as config:
        config.get_value.return_value = ""
        config.game_path = ""
        assert launcher.resolve_paths() is False
        assert launcher._game_exe is None


def test_prefix_override_with_autodetected_game(tmp_path, monkeypatch):
    launcher = GamescopeLauncher()
    prefix = tmp_path / 'new-prefix'
    (prefix/'pfx').mkdir(parents=True)
    (prefix/'pfx/user.reg').write_text('registry')
    monkeypatch.setenv('AALC_COMPAT_DATA_PATH', str(prefix))
    assert launcher.resolve_paths()
    assert launcher.build_env()['STEAM_COMPAT_DATA_PATH'] == str(prefix)
    assert prefix.is_dir()


def test_graphics_api_only_changes_automation_command(monkeypatch):
    launcher = GamescopeLauncher()
    assert launcher.resolve_paths()
    monkeypatch.setenv('AALC_GRAPHICS_API', 'd3d11')
    assert launcher.build_command()[-1] == '-force-d3d11'
    monkeypatch.setenv('AALC_GRAPHICS_API', 'auto')
    assert not any(arg.startswith('-force-d3d') for arg in launcher.build_command())
