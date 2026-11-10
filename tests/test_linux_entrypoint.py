"""Run the source launcher against tool stubs on the current Linux distribution."""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux shell entry point")
ENTRYPOINT = Path(__file__).resolve().parents[1] / "scripts/linux/aalc"


@pytest.mark.parametrize("available", [("nix", "uv"), ("nix",), ("uv",), ()])
def test_entrypoint_selects_available_environment(tmp_path, available):
    for name in ("bash", "dirname"):
        (tmp_path / name).symlink_to(shutil.which(name))
    for name in available:
        tool = tmp_path / name
        tool.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "{name}" "$@"\n', encoding="utf-8")
        tool.chmod(0o755)

    result = subprocess.run(
        [str(ENTRYPOINT), "doctor", "--json"], cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path)}, capture_output=True, text=True, timeout=5,
    )
    if not available:
        assert result.returncode != 0
        assert "Install uv" in result.stderr
        return

    nixos = platform.freedesktop_os_release().get("ID") == "nixos"
    selected = "nix" if "nix" in available and (nixos or "uv" not in available) else "uv"
    prefix = ["nix", "develop", "--command"] if selected == "nix" else ["uv", "run", "--frozen"]
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == prefix + ["python", "scripts/linux/cli.py", "doctor", "--json"]
