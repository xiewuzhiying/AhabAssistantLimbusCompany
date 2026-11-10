import pytest

from module.native_linux import check_profile, load_settings, save_settings


def test_settings_survive_new_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    profile = str((tmp_path/'private/game').resolve())
    save_settings(profile, 'd3d12')
    assert load_settings() == {'profile': profile, 'graphics_api': 'd3d12'}


def test_import_rejects_steam_prefix(tmp_path):
    prefix = tmp_path/'steamapps/compatdata/1973530'
    (prefix/'pfx').mkdir(parents=True)
    (prefix/'pfx/user.reg').write_text('registry')
    with pytest.raises(ValueError):
        check_profile(prefix)


def test_diagnostics_read_custom_paths_without_creating_config(tmp_path, monkeypatch):
    from module.native_linux import runtime_availability

    monkeypatch.chdir(tmp_path)
    for name in ('GAME_PATH', 'GAME_DIR', 'STEAM_ROOT', 'PROTON_PATH', 'SLR_ENTRYPOINT'):
        monkeypatch.delenv('AALC_' + name, raising=False)
    game = tmp_path / 'game'
    game.mkdir()
    (game / 'LimbusCompany.exe').touch()
    proton = tmp_path / 'proton'
    proton.touch()
    runtime = tmp_path / 'runtime'
    runtime.touch()
    config = f'game_path: {game.as_posix()}\nproton_path: {proton.as_posix()}\nslr_entrypoint: {runtime.as_posix()}\n'
    (tmp_path / 'config.yaml').write_text(config)
    before = set(tmp_path.rglob('*'))
    assert all(runtime_availability().values())
    assert set(tmp_path.rglob('*')) == before
    assert (tmp_path / 'config.yaml').read_text() == config
