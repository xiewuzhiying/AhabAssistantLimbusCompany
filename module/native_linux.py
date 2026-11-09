"""Persistent native Linux policy and read-only diagnostics, without Qt imports."""
import importlib.util
import json
import os
import platform
import re
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
    if not isinstance(data, dict):
        raise ValueError('Invalid native Linux settings object')
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



def runtime_availability() -> dict[str, bool]:
    """Read overrides without importing Config, which creates/updates user files."""
    from ruamel.yaml import YAML

    config_path = Path('config.yaml')
    config = YAML(typ='safe').load(config_path) if config_path.is_file() else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError('Invalid application configuration object')

    def override(environment, key):
        value = os.environ.get(environment) or config.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f'Invalid path setting: {key}')
        return Path(value).expanduser() if value else None

    steam = override('AALC_STEAM_ROOT', 'steam_root')
    candidates = [Path.home()/'.local/share/Steam', Path.home()/'.steam/root', Path.home()/'.steam/steam']
    if os.environ.get('STEAM_COMPAT_CLIENT_INSTALL_PATH'):
        candidates.insert(0, Path(os.environ['STEAM_COMPAT_CLIENT_INSTALL_PATH']))
    if steam is None or not steam.is_dir():
        steam = next((p for p in candidates if (p/'steamapps').is_dir()), None)
    libraries = [steam] if steam else []
    if steam and (steam/'steamapps/libraryfolders.vdf').is_file():
        libraries += [Path(value) for value in re.findall(r'"path"\s+"([^"]+)"', (steam/'steamapps/libraryfolders.vdf').read_text(errors='replace'))]

    game = override('AALC_GAME_PATH', 'custom_game_path')
    if os.environ.get('AALC_GAME_DIR') and not os.environ.get('AALC_GAME_PATH'):
        game = Path(os.environ['AALC_GAME_DIR'])
    if game is None:
        game = override('AALC_GAME_PATH', 'game_path')
    games = [game, game/'LimbusCompany.exe'] if game else []
    for library in libraries:
        manifest = library/'steamapps/appmanifest_1973530.acf'
        name = 'Limbus Company'
        if manifest.is_file():
            match = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(errors='replace'))
            if match:
                name = match[1]
        games.append(library/'steamapps/common'/name/'LimbusCompany.exe')

    proton = override('AALC_PROTON_PATH', 'proton_path')
    protons = [proton, proton/'proton'] if proton else []
    runtime = override('AALC_SLR_ENTRYPOINT', 'slr_entrypoint')
    runtimes = [runtime] if runtime else []
    for library in libraries:
        protons += list((library/'steamapps/common').glob('Proton*/proton'))
        protons += list((library/'compatibilitytools.d').glob('*/proton'))
        runtimes += list((library/'steamapps/common').glob('SteamLinuxRuntime*/_v2-entry-point'))
    return {'GAME_NOT_FOUND': any(p.is_file() for p in games),
            'PROTON_NOT_FOUND': any(p.is_file() for p in protons),
            'RUNTIME_NOT_FOUND': any(p.is_file() for p in runtimes) or bool(shutil.which('steam-run'))}

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
    if not status_only:
        from ruamel.yaml import YAMLError
        try:
            available = runtime_availability()
            add('CONFIG_INVALID', True, 'FIX_APPLICATION_CONFIG')
            for code, action in [('GAME_NOT_FOUND', 'SELECT_GAME_PATH'),
                                 ('PROTON_NOT_FOUND', 'INSTALL_OR_SELECT_PROTON'),
                                 ('RUNTIME_NOT_FOUND', 'INSTALL_STEAM_RUNTIME')]:
                add(code, available[code], action, 'runtime')
        except (OSError, ValueError, YAMLError):
            add('CONFIG_INVALID', False, 'FIX_APPLICATION_CONFIG')
    units = []
    try:
        result = subprocess.run(['systemctl', '--user', 'list-units', 'aalc-game-*.service', '--no-legend', '--plain'], capture_output=True, text=True, timeout=3)
        add('SESSION_QUERY_FAILED', result.returncode == 0, 'CHECK_USER_MANAGER', 'session')
        units = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        add('SESSION_QUERY_FAILED', False, 'CHECK_USER_MANAGER', 'session')
    return {'schema_version': 1, 'ok': all(c['ok'] for c in checks),
            'state': ('unknown' if any(c['code'] == 'SESSION_QUERY_FAILED' and not c['ok'] for c in checks) else ('running' if units else 'idle')), 'checks': checks,
            'sessions': units, 'log': 'logs/gamescope.log'}
