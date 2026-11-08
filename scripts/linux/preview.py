"""Local development viewer: read only the nested Limbus client window."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from Xlib import X, display
from Xlib.error import XError


def game_window(dpy):
    pending = [(dpy.screen().root, 0)]
    found = []
    while pending:
        window, depth = pending.pop()
        try:
            title = window.get_wm_name() or ''
            classes = window.get_wm_class() or ()
            if 'limbuscompany' in str(title).lower() or any('steam_app_1973530' in str(c) for c in classes):
                if window.get_attributes().map_state == X.IsViewable:
                    geo = window.get_geometry()
                    if geo.width > 100 and geo.height > 100:
                        found.append((geo.width * geo.height, window.id, geo.width, geo.height))
            if depth < 2:
                pending.extend((child, depth+1) for child in window.query_tree().children)
        except XError:
            continue
    if not found:
        raise RuntimeError('嵌套显示器中没有找到 Limbus Company 窗口')
    return max(found)[1:]


def float_on_niri(pid, width, height):
    if not os.environ.get('NIRI_SOCKET') or not shutil.which('niri'):
        return
    for _ in range(30):
        try:
            windows = json.loads(subprocess.check_output(['niri', 'msg', '-j', 'windows'], text=True, timeout=2))
            for window in windows:
                if window.get('pid') == pid:
                    for args in [('move-window-to-floating',), ('set-window-width', str(width)), ('set-window-height', str(height))]:
                        subprocess.run(['niri', 'msg', 'action', *args, '--id', str(window['id'])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                    return
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fps', type=int, default=10)
    parser.add_argument('--width', type=int, default=960)
    args = parser.parse_args()
    if not 1 <= args.fps <= 30 or not 320 <= args.width <= 2560:
        parser.error('fps must be 1–30 and width must be 320–2560')
    nodes = json.loads(subprocess.check_output(['pw-dump'], text=True, timeout=5))
    displays = set()
    for node in nodes:
        props = node.get('info', {}).get('props', {})
        if node.get('type') == 'PipeWire:Interface:Client' and 'gamescope' in str(props.get('application.process.binary', '')).lower():
            nested = props.get('window.x11.display')
            if nested and nested != os.environ.get('DISPLAY'):
                displays.add(nested)
    if len(displays) != 1:
        raise RuntimeError(f'需要且只能存在一个 Gamescope 嵌套会话，当前找到 {sorted(displays)}')
    nested = displays.pop()
    dpy = display.Display(nested)
    try:
        wid, game_width, game_height = game_window(dpy)
    finally:
        dpy.close()
    height = round(args.width * game_height / game_width)
    cmd = ['ffplay', '-hide_banner', '-loglevel', 'warning', '-nostats', '-an', '-sn',
           '-f', 'x11grab', '-window_id', str(wid), '-draw_mouse', '0', '-framerate', str(args.fps),
           '-video_size', f'{game_width}x{game_height}', '-i', nested,
           '-x', str(args.width), '-y', str(height), '-window_title', 'AALC 开发预览 · Q / Esc 关闭',
           '-autoexit', '-framedrop']
    env = os.environ.copy()
    if env.get('WAYLAND_DISPLAY'):
        env['SDL_VIDEODRIVER'] = 'wayland'
    log_dir = Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'aalc'
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir/'preview.log').open('w') as log:
        log.write(f'Nested display {nested}, window {wid}, {game_width}x{game_height}, {args.fps} FPS\n')
        log.flush()
        child = subprocess.Popen(cmd, env=env, stdout=log, stderr=log)
        threading.Thread(target=float_on_niri, args=(child.pid, args.width, height), daemon=True).start()
        try:
            return child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        sys.stderr.write(f'AALC 开发预览: {exc}\n')
        sys.exit(1)
