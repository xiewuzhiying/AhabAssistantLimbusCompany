"""Capture one frame without importing or modifying application configuration."""
import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display', required=True)
    args = parser.parse_args()
    enable_abstract_socket_fallback()
    connection = display.Display(args.display)
    try:
        window = game_window(connection)
        geometry = window.get_geometry()
        if geometry.depth not in (24, 32):
            raise ValueError('Unsupported preview pixel depth')
        pixels = window.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xFFFFFFFF)
        frame = Image.frombuffer('RGB', (geometry.width, geometry.height), pixels.data, 'raw', 'BGRX', 0, 1)
        frame.thumbnail((960, 540))
        frame.save(sys.stdout.buffer, format='PNG')
        return 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
