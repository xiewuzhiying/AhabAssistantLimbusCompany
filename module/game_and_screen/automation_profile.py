"""Offline settings for a private AALC Proton prefix; never edit Steam's prefix."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import psutil

MARKER = '.aalc-profile.json'
OPTION = re.compile(r'("LocalSave\.LocalGameOptionData[^"\n]*"=hex:)([^\n]+)')
POST_PROCESSING = ('_ppOnBattle', '_ppBloom', '_ppBlur')


def require_stopped(prefix: Path) -> None:
    for proc in psutil.process_iter(['name']):
        try:
            env = proc.environ()
            for key in ('STEAM_COMPAT_DATA_PATH', 'WINEPREFIX'):
                value = env.get(key)
                if value and Path(value).resolve() in (prefix.resolve(), (prefix / 'pfx').resolve()):
                    raise RuntimeError(f'Prefix is in use by process {proc.pid}; close the game first')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def clone_profile(source: Path, destination: Path, cancelled: Callable[[], bool] = lambda: False) -> None:
    def checkpoint():
        if cancelled():
            raise RuntimeError("Profile copy cancelled; retry when ready")

    checkpoint()
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Choose a new profile directory outside the Steam prefix')
    if not (source / 'pfx/user.reg').is_file():
        raise ValueError('Source must be a Proton compatdata directory')
    if (source / "pfx").is_symlink() or (source / "pfx/user.reg").is_symlink():
        raise ValueError("Source prefix and registry must be real files/directories")
    require_stopped(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    for item in source.rglob('*'):
        checkpoint()
        if item.is_file() and not item.is_symlink():
            size += item.stat().st_size
    if shutil.disk_usage(destination.parent).free < size:
        raise OSError('Not enough free space for a private prefix copy')
    staging = Path(tempfile.mkdtemp(prefix='.aalc-copy-', dir=destination.parent))
    try:
        with subprocess.Popen(['cp', '-a', '--reflink=auto', str(source) + '/.', str(staging)]) as process:
            try:
                while True:
                    checkpoint()
                    try:
                        result = process.wait(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if result:
                    raise subprocess.CalledProcessError(result, process.args)
                checkpoint()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        staging.chmod(0o700)
        (staging / MARKER).write_text(json.dumps({'source': str(source)}, indent=2))
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate_profile(prefix: Path) -> Path:
    from module.native_linux import check_profile
    return check_profile(prefix)


def set_post_processing(prefix: Path, enabled: bool) -> dict[str, bool]:
    prefix = validate_profile(prefix)
    marker = prefix / MARKER
    source = Path(json.loads(marker.read_text())['source']).resolve() if marker.is_file() else prefix.parent / '__no_source__'
    if prefix == source or (prefix / 'pfx').resolve() == (source / 'pfx').resolve():
        raise ValueError('AALC profile must not point to Steam prefix')
    require_stopped(prefix)
    registry = prefix / 'pfx/user.reg'
    if registry.is_symlink():
        raise ValueError('Refusing a linked registry')
    text = registry.read_text().replace('\\\n', '')
    matches = list(OPTION.finditer(text))
    if len(matches) != 1:
        raise ValueError('Expected exactly one known Limbus graphics settings record')
    match = matches[0]
    settings = json.loads(bytes.fromhex(match[2].replace(',', ' ')).rstrip(b'\0'))
    if not all(isinstance(settings.get(key), bool) for key in POST_PROCESSING):
        raise ValueError('Unknown graphics settings schema; use the game settings UI')
    previous = {key: settings[key] for key in POST_PROCESSING}
    settings.update(dict.fromkeys(POST_PROCESSING, enabled))
    encoded = (json.dumps(settings, separators=(',', ':'), ensure_ascii=False) + '\0').encode()
    replacement = match[1] + ','.join(f'{byte:02x}' for byte in encoded)
    backup = registry.with_suffix('.reg.aalc-backup')
    if not backup.exists():
        backup.write_bytes(registry.read_bytes())
        backup.chmod(0o600)
    temp = registry.with_suffix('.reg.aalc-tmp')
    temp.write_text(text[:match.start()] + replacement + text[match.end():])
    temp.chmod(0o600)
    os.replace(temp, registry)
    return previous
