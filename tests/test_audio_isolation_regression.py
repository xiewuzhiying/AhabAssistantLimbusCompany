from module.game_and_screen.gamescope_launcher import GamescopeLauncher


def test_audio_isolation_command_and_env_contract():
    """回归测试：保证多层物理静音契约不被单点依赖（如未透传的 PULSE_SINK）破坏。

    防御点：
    1. Unity 命令行必须包含 -nosound 参数；
    2. WINEDLLOVERRIDES 必须禁用 winepulse.drv 和 winealsa.drv，在 Wine 层阻断音频；
    3. 防止由于 pressure-vessel 容器重写环境变量导致的声音泄漏到物理设备。
    """
    launcher = GamescopeLauncher()
    launcher.resolve_paths()

    cmd = launcher.build_command()
    assert "-nosound" in cmd, "命令中必须显式包含 -nosound，以在 Unity 引擎层阻断音频初始化"

    env = launcher.build_env()
    assert "WINEDLLOVERRIDES" in env, "环境变量中必须包含 WINEDLLOVERRIDES"
    assert "winepulse.drv=d" in env["WINEDLLOVERRIDES"], "必须禁用 winepulse.drv"
    assert "winealsa.drv=d" in env["WINEDLLOVERRIDES"], "必须禁用 winealsa.drv"
