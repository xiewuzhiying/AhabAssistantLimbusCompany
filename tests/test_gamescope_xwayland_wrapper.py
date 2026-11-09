import os
from pathlib import Path

import pytest

from module.game_and_screen.gamescope_launcher import GamescopeLauncher


@pytest.fixture
def xwayland_bin(tmp_path, monkeypatch):
    binary = tmp_path / "Xwayland"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.delenv("XWAYLAND_PATH", raising=False)
    monkeypatch.delenv("AALC_XWAYLAND_PATH", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    return binary


def test_gamescope_launcher_resolves_xwayland_bin(xwayland_bin):
    launcher = GamescopeLauncher()
    resolved = launcher._resolve_xwayland_bin()
    assert resolved == str(xwayland_bin)
    assert Path(resolved).is_file()
    assert os.access(resolved, os.X_OK)


def test_gamescope_launcher_creates_and_cleans_wrapper(xwayland_bin):
    launcher = GamescopeLauncher()
    wrapper_path = launcher._setup_xwayland_wrapper()
    assert wrapper_path is not None
    assert Path(wrapper_path).is_file()
    assert os.access(wrapper_path, os.X_OK)

    # 验证 wrapper 内容
    content = Path(wrapper_path).read_text(encoding="utf-8")
    assert "unset LIBEI_SOCKET" in content
    assert launcher._resolve_xwayland_bin() in content

    # 验证 build_env 包含 WLR_XWAYLAND
    env = launcher.build_env()
    assert env.get("WLR_XWAYLAND") == str(wrapper_path)

    # 验证清理逻辑
    launcher._cleanup_xwayland_wrapper()
    assert not Path(wrapper_path).exists()


def test_resolve_xwayland_env_override(tmp_path, monkeypatch):
    custom_xwayland = tmp_path / "custom-Xwayland"
    custom_xwayland.touch(mode=0o755)
    monkeypatch.setenv("XWAYLAND_PATH", str(custom_xwayland))

    launcher = GamescopeLauncher()
    resolved = launcher._resolve_xwayland_bin()
    assert resolved == str(custom_xwayland)


def test_resolve_xwayland_does_not_spawn_nix_store_subprocess(xwayland_bin):
    """验证 _resolve_xwayland_bin 纯靠内部扫描与路径探测，不调用外部 nix-store 进程"""
    from unittest.mock import patch

    launcher = GamescopeLauncher()
    with patch("subprocess.run") as mock_run:
        resolved = launcher._resolve_xwayland_bin()
        assert resolved == str(xwayland_bin)
        mock_run.assert_not_called()

