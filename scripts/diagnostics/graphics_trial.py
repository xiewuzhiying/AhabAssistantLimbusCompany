"""Capture one bounded graphics startup trial without sending game input."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--api', choices=['d3d11', 'd3d12'], required=True)
    parser.add_argument('--post-processing', choices=['on', 'off'], required=True)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 600:
        parser.error('seconds must be between 10 and 600')
    args.output.mkdir(parents=True, exist_ok=False)
    from module.game_and_screen.automation_profile import set_post_processing
    previous = set_post_processing(args.profile, args.post_processing == 'on')
    os.environ['AALC_COMPAT_DATA_PATH'] = str(args.profile.resolve())
    os.environ['AALC_GRAPHICS_API'] = args.api
    from module.automation.screenshot import ScreenShot
    from module.game_and_screen.gamescope_launcher import GamescopeLauncher
    from module.game_and_screen.x11_handle import X11Handle
    launcher = GamescopeLauncher()
    report: dict[str, Any] = {'api': args.api, 'post_processing': args.post_processing,
              'previous_post_processing': previous, 'frames': [],
              'scope': 'startup only; no login input or loading-stall verdict'}
    start = time.monotonic()
    try:
        report['launched'] = launcher.launch()
        if not report['launched']:
            return 1
        handle = X11Handle()
        while time.monotonic() - start < args.seconds and launcher.is_running():
            handle.init_handle('LimbusCompany')
            if handle.hwnd and handle.is_gamescope:
                try:
                    frame = ScreenShot.take_screenshot_x11(gray=False, handle=handle)
                    index = len(report['frames'])
                    frame.save(args.output / f'frame-{index:03d}.png')
                    report['frames'].append({'seconds': round(time.monotonic()-start, 2),
                                              'sha256': hashlib.sha256(frame.tobytes()).hexdigest()})
                except Exception as exc:
                    report['capture_error'] = str(exc)
            time.sleep(3)
        report['alive_at_end'] = launcher.is_running()
        return 0 if report['frames'] and report['alive_at_end'] else 1
    finally:
        launcher.terminate()
        player_log = args.profile/'pfx/drive_c/users/steamuser/AppData/LocalLow/ProjectMoon/LimbusCompany/Player.log'
        if player_log.is_file():
            shutil.copyfile(player_log, args.output/'Player.log')
        report['elapsed'] = round(time.monotonic()-start, 2)
        (args.output/'report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
