import io
import struct
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image


def test_capture_helper_reuses_connection_for_requested_frames(monkeypatch):
    from scripts.linux import preview_frame

    connection = Mock()
    window = Mock()
    window.get_geometry.return_value = SimpleNamespace(width=200, height=120, depth=24)
    window.get_image.return_value = SimpleNamespace(data=b'\x03\x02\x01\x00' * (200 * 120))
    connection.create_resource_object.return_value = window
    connect = Mock(return_value=connection)
    lookup = Mock(return_value=window)
    monkeypatch.setattr(preview_frame.display, 'Display', connect)
    monkeypatch.setattr(preview_frame, 'game_window', lookup)
    monkeypatch.setattr(preview_frame.sys, 'argv', ['preview_frame', '--display', ':91', '--stream'])
    monkeypatch.setattr(preview_frame.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'ff')))
    output = io.BytesIO()
    monkeypatch.setattr(preview_frame.sys, 'stdout', SimpleNamespace(buffer=output))

    assert preview_frame.main() == 0

    payload = output.getvalue()
    for _ in range(2):
        width, height = struct.unpack('!II', payload[:8])
        size = width * height * 3
        frame = Image.frombytes('RGB', (width, height), payload[8:8 + size])
        assert frame.size == (200, 120)
        assert frame.getpixel((0, 0)) == (1, 2, 3)
        payload = payload[8 + size:]
    assert payload == b''
    connect.assert_called_once_with(':91')
    lookup.assert_called_once_with(connection)
    connection.close.assert_called_once()


def test_one_shot_capture_still_emits_png(monkeypatch):
    from scripts.linux import preview_frame

    connection = Mock()
    window = Mock()
    window.get_geometry.return_value = SimpleNamespace(width=200, height=120, depth=24)
    window.get_image.return_value = SimpleNamespace(data=b'\x03\x02\x01\x00' * (200 * 120))
    monkeypatch.setattr(preview_frame.display, 'Display', Mock(return_value=connection))
    monkeypatch.setattr(preview_frame, 'game_window', Mock(return_value=window))
    monkeypatch.setattr(preview_frame.sys, 'argv', ['preview_frame', '--display', ':91'])
    output = io.BytesIO()
    monkeypatch.setattr(preview_frame.sys, 'stdout', SimpleNamespace(buffer=output))

    assert preview_frame.main() == 0

    output.seek(0)
    assert Image.open(output).getpixel((0, 0)) == (1, 2, 3)


def helper_stream(monkeypatch, code):
    from module import preview_stream

    popen = subprocess.Popen
    monkeypatch.setattr(preview_stream.subprocess, 'Popen',
                        lambda command, **kwargs: popen([sys.executable, '-c', code], **kwargs))
    return preview_stream.PreviewStream(':91')


def test_stream_reads_split_frames_and_reaps_helper(monkeypatch):
    stream = helper_stream(monkeypatch, '''
import os, struct, sys
while sys.stdin.buffer.read(1):
    packet = struct.pack('!II', 2, 1) + b'abcdef'
    for byte in packet:
        os.write(1, bytes([byte]))
''')
    try:
        for _ in range(3):
            assert stream.read_frame() == (2, 1, b'abcdef')
    finally:
        stream.close()
    assert stream.process.poll() is not None


@pytest.mark.parametrize('output, error', [
    (b'', EOFError),
    (struct.pack('!II', 2, 1) + b'abc', EOFError),
    (struct.pack('!II', 99999, 1), ValueError),
])
def test_stream_rejects_incomplete_or_invalid_frames(monkeypatch, output, error):
    stream = helper_stream(monkeypatch,
                           f'import os, sys; sys.stdin.buffer.read(1); os.write(1, {output!r})')
    try:
        with pytest.raises(error):
            stream.read_frame()
    finally:
        stream.close()


def test_stream_timeout_and_close_reap_stuck_capture(monkeypatch):
    stream = helper_stream(monkeypatch, 'import time; time.sleep(60)')
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            stream.read_frame(timeout=.1)
    finally:
        stream.close()
    assert time.monotonic() - started < 2
    assert stream.process.poll() is not None


def test_stream_close_cancels_a_pending_read_promptly(monkeypatch):
    stream = helper_stream(monkeypatch, 'import time; time.sleep(60)')
    interrupted = threading.Event()
    timer = threading.Timer(.1, interrupted.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(InterruptedError):
            stream.read_frame(interrupted.is_set)
    finally:
        stream.close()
        timer.join()
    assert time.monotonic() - started < 2
    assert stream.process.poll() is not None


def test_reader_keeps_latest_frame_without_a_gui_event_backlog():
    from PySide6.QtGui import QColor, QImage

    from app.linux_setup import FrameReader

    reader = FrameReader()
    for value in range(30):
        frame = QImage(1, 1, QImage.Format.Format_RGB888)
        frame.fill(QColor(value, 0, 0))
        reader.publish(frame)
    pending, latest = reader.take_latest()
    assert pending
    assert latest.pixelColor(0, 0).red() == 29
    assert reader.take_latest() == (False, None)
    reader.publish(None)
    assert reader.take_latest() == (True, None)


def test_reader_reconnects_after_session_restart_without_showing_old_frame(monkeypatch):
    from app import linux_setup
    from module.game_and_screen import gamescope_launcher

    launcher = SimpleNamespace(process=object(), is_running=lambda: True, display_name=lambda: ':91')
    monkeypatch.setattr(gamescope_launcher, 'GamescopeLauncher', lambda: launcher)
    reader = linux_setup.FrameReader()
    seen = []
    monkeypatch.setattr(reader, 'publish', seen.append)
    streams = []

    class Stream:
        def __init__(self, display):
            assert display == ':91'
            self.close = Mock()
            streams.append(self)

        def read_frame(self, interrupted):
            if len(streams) == 1:
                launcher.process = object()
                return 1, 1, b'\xff\x00\x00'
            reader.requestInterruption()
            return 1, 1, b'\x00\xff\x00'

    monkeypatch.setattr(linux_setup, 'PreviewStream', Stream)
    reader.start()
    assert reader.wait(3000)
    assert len(streams) == 2
    assert seen[0] is None
    assert len(seen) == 2
    assert seen[1].pixelColor(0, 0).green() == 255
    for stream in streams:
        stream.close.assert_called_once()


def test_reader_does_not_capture_without_owned_session(monkeypatch):
    from app import linux_setup
    from module.game_and_screen import gamescope_launcher

    launcher = SimpleNamespace(process=None, is_running=lambda: False, display_name=Mock())
    monkeypatch.setattr(gamescope_launcher, 'GamescopeLauncher', lambda: launcher)
    stream = Mock(side_effect=AssertionError('No capture without an owned display'))
    monkeypatch.setattr(linux_setup, 'PreviewStream', stream)
    reader = linux_setup.FrameReader()
    waiting = threading.Event()
    monkeypatch.setattr(reader, 'publish', lambda image: waiting.set())
    reader.start()
    try:
        assert waiting.wait(3)
    finally:
        reader.requestInterruption()
        assert reader.wait(3000)
    stream.assert_not_called()
    launcher.display_name.assert_not_called()
