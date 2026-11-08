from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from Xlib.error import ConnectionClosedError

from module.game_and_screen.x11_handle import (
    _XLIB_LOCK,
    X11Handle,
    _read_proc_environ,
    detect_gamescope_displays,
)


@pytest.mark.parametrize("cached_window", [0, 99])
def test_same_display_recovers_after_xwayland_restarts(monkeypatch, cached_window):
    handle = object.__new__(X11Handle)
    handle._hwnd = cached_window
    handle._display_name = ":87"
    handle._is_gamescope = True
    handle._enum_windows_list = []
    handle._content_offset = 40
    dead = MagicMock()
    dead.create_resource_object.return_value.get_attributes.side_effect = ConnectionClosedError("old server")
    handle._disp = dead
    replacement = MagicMock()

    def enumerate_windows(dpy=None):
        if (dpy if dpy is not None else handle._disp) is dead:
            raise ConnectionClosedError("old server")
        return [{"wid": 200, "title": "LimbusCompany", "cls": "UnityWndClass", "width": 1920, "height": 1080}]

    monkeypatch.setattr(handle, "_enum_windows", enumerate_windows)
    monkeypatch.setattr("module.game_and_screen.x11_handle.get_configured_gamescope_display", lambda: ":87")
    monkeypatch.setattr("module.game_and_screen.x11_handle.detect_gamescope_displays", lambda: [":87"])
    with patch("Xlib.display.Display", return_value=replacement) as connect:
        assert handle.hwnd == 200
        assert handle.hwnd == 200

    connect.assert_called_once_with(":87")
    dead.close.assert_called_once()
    assert handle.display is replacement
    assert handle.display_name == ":87"
    assert handle.is_gamescope
    assert handle.content_offset == 0


def test_empty_live_display_keeps_connection(monkeypatch):
    handle = object.__new__(X11Handle)
    handle._hwnd = 0
    handle._disp = MagicMock()
    handle._display_name = ":87"
    monkeypatch.setattr(handle, "_enum_windows", lambda: [])
    with patch("Xlib.display.Display") as connect:
        assert handle._try_display(":87") == 0
    connect.assert_not_called()
    handle._disp.close.assert_not_called()


def test_read_proc_environ_parses_null_separated_key_values(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    pid_dir = proc_root / "12345"
    pid_dir.mkdir(parents=True)
    environ_file = pid_dir / "environ"
    environ_file.write_bytes(b"PATH=/bin:/usr/bin\x00DISPLAY=:1\x00USER=tester\x00")

    result = _read_proc_environ(12345, proc_root=proc_root)

    assert result["DISPLAY"] == ":1"
    assert result["PATH"] == "/bin:/usr/bin"
    assert result["USER"] == "tester"


def test_read_proc_environ_handles_nonexistent_pid(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir(parents=True)

    result = _read_proc_environ(99999, proc_root=proc_root)

    assert result == {}


def test_detect_gamescope_displays_from_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("GAMESCOPE_DISPLAY", ":2")
    monkeypatch.delenv("AALC_GAMESCOPE_DISPLAY", raising=False)

    displays = detect_gamescope_displays()

    assert ":2" in displays
    assert displays[0] == ":2"


def test_detect_gamescope_displays_from_game_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GAMESCOPE_DISPLAY", raising=False)
    monkeypatch.delenv("AALC_GAMESCOPE_DISPLAY", raising=False)

    proc_root = tmp_path / "proc"
    pid_dir = proc_root / "54321"
    pid_dir.mkdir(parents=True)
    (pid_dir / "environ").write_bytes(b"DISPLAY=:1\x00FOO=bar\x00")

    fake_proc = SimpleNamespace(info={"pid": 54321, "name": "LimbusCompany.exe", "cmdline": ["Z:\\LimbusCompany.exe"]})

    with patch("psutil.process_iter", return_value=[fake_proc]):
        displays = detect_gamescope_displays(proc_root=proc_root)

    assert ":1" in displays


def test_detect_gamescope_displays_from_gamescope_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GAMESCOPE_DISPLAY", raising=False)
    monkeypatch.delenv("AALC_GAMESCOPE_DISPLAY", raising=False)

    fake_gamescope = MagicMock()
    fake_gamescope.info = {"pid": 2000, "name": "gamescope", "cmdline": ["gamescope", "-W", "1920"]}
    fake_gamescope.children.return_value = []

    with patch("psutil.process_iter", return_value=[fake_gamescope]):
        with patch("psutil.Process", return_value=fake_gamescope):
            displays = detect_gamescope_displays(proc_root=tmp_path / "proc")

    assert ":1" in displays


def test_detect_gamescope_displays_fallback_when_no_processes_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GAMESCOPE_DISPLAY", raising=False)
    monkeypatch.delenv("AALC_GAMESCOPE_DISPLAY", raising=False)

    with patch("psutil.process_iter", return_value=[]):
        displays = detect_gamescope_displays(proc_root=tmp_path / "proc")

    assert ":1" in displays
    assert ":2" in displays


def test_init_handle_auto_detects_gamescope_display(monkeypatch) -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 0
    handle._transparent = False
    handle._display_name = ":0"
    handle._is_gamescope = False
    handle._enum_windows_list = []
    handle._content_offset_cache = {}

    host_disp = MagicMock()
    handle._disp = host_disp

    # Host display (:0) has no game window
    def mock_safe_enum(disp=None):
        if disp is host_disp or disp is None:
            return [
                {
                    "wid": 100,
                    "cls": "gamescope.gamescope",
                    "title": "gamescope",
                    "depth": 1,
                    "width": 1920,
                    "height": 1080,
                }
            ]
        # Gamescope display (:1) has the game window
        return [
            {
                "wid": 200,
                "cls": "UnityWndClass.UnityWndClass",
                "title": "LimbusCompany",
                "depth": 1,
                "width": 1920,
                "height": 1080,
            }
        ]

    handle._safe_enum_windows = mock_safe_enum

    gamescope_disp = MagicMock()
    gamescope_disp.get_display_name.return_value = ":1"

    with patch("module.game_and_screen.x11_handle.detect_gamescope_displays", return_value=[":1"]):
        with patch("Xlib.display.Display", return_value=gamescope_disp):
            wid = handle.init_handle("LimbusCompany")

    assert wid == 200
    assert handle.hwnd == 200
    assert handle.display_name == ":1"
    assert handle.is_gamescope is True
    host_disp.close.assert_called_once()


def test_init_handle_prefers_valid_geometry_over_dummy_window() -> None:
    handle = object.__new__(X11Handle)
    candidates = [
        # 1x1 dummy window created by Wine at depth 2
        {
            "wid": 101,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 2,
            "width": 1,
            "height": 1,
        },
        # 1920x1080 client window at depth 1
        {
            "wid": 102,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 1,
            "width": 1920,
            "height": 1080,
        },
    ]

    matched_wid = handle._match_game_window(candidates, "LimbusCompany")
    assert matched_wid == 102


def test_init_handle_prefers_deeper_window_when_sizes_valid() -> None:
    handle = object.__new__(X11Handle)
    candidates = [
        # Frame window at depth 1
        {
            "wid": 101,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 1,
            "width": 1920,
            "height": 1080,
        },
        # Client window at depth 2
        {
            "wid": 102,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 2,
            "width": 1920,
            "height": 1080,
        },
    ]

    matched_wid = handle._match_game_window(candidates, "LimbusCompany")
    assert matched_wid == 102


def test_init_handle_with_explicit_display_name() -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 0
    handle._transparent = False
    handle._display_name = ":0"
    handle._is_gamescope = False
    handle._enum_windows_list = []
    handle._content_offset_cache = {}
    handle._disp = MagicMock()

    target_disp = MagicMock()
    target_disp.get_display_name.return_value = ":1"

    handle._safe_enum_windows = lambda disp=None: [
        {
            "wid": 300,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 1,
            "width": 1920,
            "height": 1080,
        }
    ]

    with patch("Xlib.display.Display", return_value=target_disp):
        wid = handle.init_handle("LimbusCompany", display_name=":1")

    assert wid == 300
    assert handle.display_name == ":1"
    assert handle.is_gamescope is True


def test_configured_gamescope_display_priority_over_host_display(monkeypatch) -> None:
    monkeypatch.setenv("GAMESCOPE_DISPLAY", ":2")

    handle = object.__new__(X11Handle)
    handle._hwnd = 0
    handle._transparent = False
    handle._display_name = ":0"
    handle._is_gamescope = False
    handle._enum_windows_list = []
    handle._content_offset_cache = {}

    host_disp = MagicMock()
    handle._disp = host_disp

    gs_disp = MagicMock()
    gs_disp.get_display_name.return_value = ":2"

    def mock_safe_enum(disp=None):
        if disp is gs_disp:
            return [
                {
                    "wid": 500,
                    "cls": "UnityWndClass.UnityWndClass",
                    "title": "LimbusCompany",
                    "depth": 1,
                    "width": 1920,
                    "height": 1080,
                }
            ]
        # Host display would also have a window, but configured gamescope display should be probed first
        return [
            {
                "wid": 999,
                "cls": "UnityWndClass.UnityWndClass",
                "title": "LimbusCompany",
                "depth": 1,
                "width": 1920,
                "height": 1080,
            }
        ]

    handle._safe_enum_windows = mock_safe_enum

    with patch("Xlib.display.Display", return_value=gs_disp):
        wid = handle.init_handle("LimbusCompany")

    assert wid == 500
    assert handle.display_name == ":2"
    assert handle.is_gamescope is True


def test_match_game_window_uses_class_name_for_fallback_and_scoring() -> None:
    handle = object.__new__(X11Handle)

    # 1. Fallback when title is empty: matches class_name
    candidates_empty_title = [
        {"wid": 101, "cls": "OtherApp.OtherApp", "title": "", "depth": 1, "width": 1920, "height": 1080},
        {"wid": 102, "cls": "UnityWndClass.UnityWndClass", "title": "", "depth": 1, "width": 1920, "height": 1080},
    ]
    matched = handle._match_game_window(candidates_empty_title, "LimbusCompany", class_name="UnityWndClass")
    assert matched == 102

    # 2. Scoring: among windows with same title, valid size, depth, prefer matching class_name
    candidates_scoring = [
        {"wid": 201, "cls": "SomeGenericWindow", "title": "LimbusCompany", "depth": 1, "width": 1920, "height": 1080},
        {
            "wid": 202,
            "cls": "UnityWndClass.UnityWndClass",
            "title": "LimbusCompany",
            "depth": 1,
            "width": 1920,
            "height": 1080,
        },
    ]
    matched_score = handle._match_game_window(candidates_scoring, "LimbusCompany", class_name="UnityWndClass")
    assert matched_score == 202


def test_gamescope_rect_calculation_without_frame_extents() -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 200
    handle._content_offset = 0

    mock_win = MagicMock()
    mock_win.get_geometry.return_value = SimpleNamespace(x=0, y=0, width=1920, height=1080)
    handle._window_obj = lambda wid=None: mock_win
    handle._root_position = lambda win: (0, 0)
    handle._frame_extents = lambda win: (0, 0, 0, 0)
    handle._known_content_offset = lambda w, h: 0

    rect_client = handle.rect(client=True)
    rect_window = handle.rect(client=False)

    assert rect_client == (0, 0, 1920, 1080)
    assert rect_window == (0, 0, 1920, 1080)


def test_root_position_hierarchical_accumulation() -> None:
    handle = object.__new__(X11Handle)
    root_win = MagicMock()
    root_win.id = 1
    handle._disp = MagicMock()
    handle._disp.screen().root = root_win

    parent_win = MagicMock()
    parent_win.id = 2
    parent_win.get_geometry.return_value = SimpleNamespace(x=10, y=20)
    parent_win.query_tree.return_value = SimpleNamespace(parent=root_win)

    client_win = MagicMock()
    client_win.id = 3
    client_win.get_geometry.return_value = SimpleNamespace(x=5, y=5)
    client_win.query_tree.return_value = SimpleNamespace(parent=parent_win)

    pos_x, pos_y = handle._root_position(client_win)
    assert pos_x == 15
    assert pos_y == 25


def test_xlib_thread_lock_is_maintained() -> None:
    assert _XLIB_LOCK is not None
    acquired = _XLIB_LOCK.acquire(blocking=False)
    assert acquired is True
    _XLIB_LOCK.release()
