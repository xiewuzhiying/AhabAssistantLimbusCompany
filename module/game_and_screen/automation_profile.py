"""Offline settings for a private AALC Proton prefix; never edit Steam's prefix."""
import json
import os
import re
import subprocess
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


def clone_profile(source: Path, destination: Path) -> None:
    source, destination = source.expanduser().resolve(), destination.expanduser().resolve()
    if destination.exists() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('Choose a new profile directory outside the Steam prefix')
    if not (source / 'pfx/user.reg').is_file():
        raise ValueError('Source must be a Proton compatdata directory')
    if (source / "pfx").is_symlink() or (source / "pfx/user.reg").is_symlink():
        raise ValueError("Source prefix and registry must be real files/directories")
    require_stopped(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    # Preserve Wine symlinks; reflinks avoid duplicating cached assets where supported.
    subprocess.run(['cp', '-a', '--reflink=auto', str(source) + '/.', str(destination)], check=True)
    destination.chmod(0o700)
    (destination / MARKER).write_text(json.dumps({'source': str(source)}, indent=2))


def validate_profile(prefix: Path) -> Path:
    prefix = prefix.expanduser().resolve()
    marker = prefix / MARKER
    if not marker.is_file():
        raise ValueError('Create a private AALC profile first')
    source = Path(json.loads(marker.read_text())['source']).resolve()
    if prefix == source or (prefix / 'pfx').is_symlink() or (prefix / 'pfx/user.reg').is_symlink():
        raise ValueError('Private profile must not link to Steam registry or prefix')
    if not (prefix / 'pfx/user.reg').is_file():
        raise ValueError('Private profile registry is missing')
    return prefix


def set_post_processing(prefix: Path, enabled: bool) -> dict[str, bool]:
    prefix = validate_profile(prefix)
    marker = prefix / MARKER
    source = Path(json.loads(marker.read_text())['source']).resolve()
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
