"""Persistent native Linux policy and read-only diagnostics, without Qt imports."""
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import psutil


def settings_path() -> Path:
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'aalc/native-linux.json'


def default_profile() -> Path:
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'aalc/compatdata/1973530'


def load_settings() -> dict[str, str]:
    path = settings_path()
    if not path.exists():
        return {'profile': str(default_profile()), 'graphics_api': 'd3d11'}
    data = json.loads(path.read_text())
    if data.get('graphics_api') not in ('auto', 'd3d11', 'd3d12') or not isinstance(data.get('profile'), str):
        raise ValueError('Invalid native Linux settings')
    return {'profile': data['profile'], 'graphics_api': data['graphics_api']}


def save_settings(profile: str, graphics_api: str) -> None:
    if graphics_api not in ('auto', 'd3d11', 'd3d12'):
        raise ValueError('Unsupported graphics API')
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump({'profile': str(Path(profile).expanduser().resolve()), 'graphics_api': graphics_api}, output)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_profile(prefix: Path) -> Path:
    prefix = prefix.expanduser().resolve()
    if 'steamapps' in prefix.parts and 'compatdata' in prefix.parts:
        raise ValueError('Select a private prefix outside Steam steamapps/compatdata')
    if (prefix/'pfx').is_symlink() or (prefix/'pfx/user.reg').is_symlink():
        raise ValueError('Private prefix must not link to another prefix')
    if not (prefix/'pfx/user.reg').is_file():
        raise ValueError('Initialize the game once in Steam, exit it, then copy or import its prefix')
    return prefix


def report(status_only: bool = False) -> dict[str, Any]:
    checks = []
    def add(code, ok, action, phase='environment'):
        checks.append({'code': code, 'ok': bool(ok), 'phase': phase,
                       'action': None if ok else action, 'retryable': code in ('STEAM_NOT_RUNNING', 'USER_MANAGER_UNAVAILABLE')})
    add('UNSUPPORTED_PLATFORM', platform.system() == 'Linux' and platform.machine() in ('x86_64', 'AMD64'), 'USE_SUPPORTED_LINUX')
    if not status_only:
        for binary in ('gamescope', 'systemd-run', 'systemctl'):
            add('MISSING_'+binary.upper().replace('-', '_'), shutil.which(binary), 'INSTALL_DEPENDENCIES')
        for package in ('PySide6', 'Xlib', 'cv2', 'onnxruntime'):
            add('MISSING_'+package.upper(), importlib.util.find_spec(package) is not None, 'INSTALL_PYTHON_DEPENDENCIES')
        try:
            result = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True, timeout=3)
            add('USER_MANAGER_UNAVAILABLE', result.returncode == 0, 'START_USER_SESSION')
        except (OSError, subprocess.TimeoutExpired):
            add('USER_MANAGER_UNAVAILABLE', False, 'START_USER_SESSION')
        add('STEAM_NOT_RUNNING', any(p.info['name'] == 'steam' for p in psutil.process_iter(['name'])), 'START_STEAM_AND_RETRY')
    try:
        settings = load_settings()
        check_profile(Path(settings['profile']))
        add('PROFILE_NOT_READY', True, 'OPEN_SETUP', 'profile')
    except (OSError, ValueError, KeyError):
        add('PROFILE_NOT_READY', False, 'OPEN_SETUP', 'profile')
    if not status_only and checks[-1]['ok']:
        # Import resolution only after the lightweight checks; capture incidental library stdout.
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                from module.game_and_screen.gamescope_launcher import GamescopeLauncher
                resolved = GamescopeLauncher().resolve_paths()
            add('GAME_RUNTIME_NOT_READY', resolved, 'SELECT_GAME_AND_PROTON', 'runtime')
        except Exception:
            add('GAME_RUNTIME_NOT_READY', False, 'SELECT_GAME_AND_PROTON', 'runtime')
    units = []
    try:
        result = subprocess.run(['systemctl', '--user', 'list-units', 'aalc-game-*.service', '--no-legend', '--plain'], capture_output=True, text=True, timeout=3)
        add('SESSION_QUERY_FAILED', result.returncode == 0, 'CHECK_USER_MANAGER', 'session')
        units = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        add('SESSION_QUERY_FAILED', False, 'CHECK_USER_MANAGER', 'session')
    return {'schema_version': 1, 'ok': all(c['ok'] for c in checks),
            'state': 'running' if units else 'idle', 'checks': checks,
            'sessions': units, 'log': 'logs/gamescope.log'}
