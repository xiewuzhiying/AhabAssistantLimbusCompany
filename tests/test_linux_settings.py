import pytest

from module.native_linux import check_profile, load_settings, save_settings


def test_settings_survive_new_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    save_settings('/private/game', 'd3d12')
    assert load_settings() == {'profile': '/private/game', 'graphics_api': 'd3d12'}


def test_import_rejects_steam_prefix(tmp_path):
    prefix = tmp_path/'steamapps/compatdata/1973530'
    (prefix/'pfx').mkdir(parents=True)
    (prefix/'pfx/user.reg').write_text('registry')
    with pytest.raises(ValueError):
        check_profile(prefix)
