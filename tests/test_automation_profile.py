import json

import pytest

from module.game_and_screen.automation_profile import clone_profile, set_post_processing


def test_private_profile_changes_only_graphics_and_keeps_source(tmp_path):
    source = tmp_path / 'steamapps/compatdata/1973530'
    (source / 'pfx').mkdir(parents=True)
    options = {'_ppOnBattle': True, '_ppBloom': True, '_ppBlur': True,
               '_ppSettingUsed': True, 'unrelated': 42}
    raw = ','.join(f'{b:02x}' for b in (json.dumps(options)+'\0').encode())
    registry = 'WINE REGISTRY Version 2\n"LocalSave.LocalGameOptionData_h123"=hex:'+raw+'\n'
    (source / 'pfx/user.reg').write_text(registry)
    target = tmp_path / 'automation'
    clone_profile(source, target)
    set_post_processing(target, False)
    assert (source / 'pfx/user.reg').read_text() == registry
    edited = (target / 'pfx/user.reg').read_text().split('=hex:')[1].strip()
    result = json.loads(bytes.fromhex(edited.replace(',', ' ')).rstrip(b'\0'))
    assert result == dict(options, _ppOnBattle=False, _ppBloom=False, _ppBlur=False)
    with pytest.raises(ValueError):
        set_post_processing(source, False)
    with pytest.raises(ValueError):
        clone_profile(source, target)


def test_linked_source_cannot_become_private_profile(tmp_path):
    source = tmp_path / 'source'
    external = tmp_path / 'external'
    source.mkdir()
    external.mkdir()
    (external / 'user.reg').write_text('registry')
    (source / 'pfx').symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError):
        clone_profile(source, tmp_path / 'target')


def test_cancel_copy_reaps_process_and_removes_staging(tmp_path, monkeypatch):
    import subprocess
    from unittest.mock import MagicMock

    source = tmp_path / 'source'
    (source / 'pfx').mkdir(parents=True)
    (source / 'pfx/user.reg').write_text('registry')
    stopped = False
    process = MagicMock()
    process.__enter__.return_value = process
    process.poll.return_value = None

    def wait(timeout):
        nonlocal stopped
        if not stopped:
            stopped = True
            raise subprocess.TimeoutExpired('cp', timeout)
        return -15

    process.wait.side_effect = wait
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: process)
    with pytest.raises(RuntimeError, match='cancelled'):
        clone_profile(source, tmp_path / 'target', lambda: stopped)
    process.terminate.assert_called_once()
    assert not (tmp_path / 'target').exists()
    assert not list(tmp_path.glob('.aalc-copy-*'))
    assert (source / 'pfx/user.reg').read_text() == 'registry'
