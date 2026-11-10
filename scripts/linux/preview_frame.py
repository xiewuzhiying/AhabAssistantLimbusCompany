"""Capture frames without importing or modifying application configuration."""
import argparse
import struct
import sys

from PIL import Image
from Xlib import X, display
from Xlib.error import XError

from module.x11_connection import enable_abstract_socket_fallback


def game_window(connection):
    pending = [(connection.screen().root, 0)]
    matches = []
    while pending:
        window, depth = pending.pop()
        try:
            title = window.get_wm_name() or ''
            normalized = ''.join(c for c in str(title).casefold() if c.isalnum())
            if normalized == 'limbuscompany' and window.get_attributes().map_state == X.IsViewable:
                geometry = window.get_geometry()
                if geometry.width > 100 and geometry.height > 100:
                    matches.append((depth, geometry.width * geometry.height, window.id))
            if depth < 6:
                pending.extend((child, depth+1) for child in window.query_tree().children)
        except XError:
            continue
    if not matches:
        raise RuntimeError('No game window in the owned display')
    return connection.create_resource_object('window', max(matches)[2])


def capture(window):
    geometry = window.get_geometry()
    if geometry.depth not in (24, 32):
        raise ValueError('Unsupported preview pixel depth')
    pixels = window.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xFFFFFFFF)
    frame = Image.frombuffer('RGB', (geometry.width, geometry.height), pixels.data, 'raw', 'BGRX', 0, 1)
    frame.thumbnail((960, 540), Image.Resampling.BILINEAR)
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', required=True)
    parser.add_argument('--stream', action='store_true', help='Reply to each stdin byte with a sized RGB frame')
    args = parser.parse_args()
    enable_abstract_socket_fallback()
    connection = display.Display(args.display)
    try:
        window = game_window(connection)
        if args.stream:
            # Request/reply keeps at most one frame in flight, even if the GUI is busy.
            while sys.stdin.buffer.read(1):
                frame = capture(window)
                sys.stdout.buffer.write(struct.pack('!II', *frame.size))
                sys.stdout.buffer.write(frame.tobytes())
                sys.stdout.buffer.flush()
        else:
            capture(window).save(sys.stdout.buffer, format='PNG')
        return 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
