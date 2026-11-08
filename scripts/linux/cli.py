"""Linux entry points for AALC and its private game profile."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='Open AALC using its private game profile')
    run.add_argument('--graphics-api', choices=['auto', 'd3d11', 'd3d12'], default='d3d11')
    run.add_argument('--profile', type=Path)
    preview = sub.add_parser('preview', help='Open a read-only game preview')
    preview.add_argument('--fps', type=int, default=5)
    preview.add_argument('--width', type=int, default=960)
    init = sub.add_parser('create-profile', help='Copy an inactive Steam prefix once')
    init.add_argument('--source', type=Path, required=True)
    init.add_argument('--profile', type=Path)
    settings = sub.add_parser('post-processing', help='Change post processing in an inactive private profile')
    settings.add_argument('state', choices=['on', 'off'])
    settings.add_argument('--profile', type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    profile = getattr(args, 'profile', None) or Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'aalc/compatdata/1973530'
    profile = profile.expanduser().resolve()
    if args.action == 'preview':
        return subprocess.call([sys.executable, str(ROOT/'scripts/linux/preview.py'),
                                '--fps', str(args.fps), '--width', str(args.width)])
    from module.game_and_screen.automation_profile import clone_profile, set_post_processing, validate_profile
    if args.action == 'create-profile':
        clone_profile(args.source, profile)
        sys.stdout.write(f'Private profile ready: {profile}\n')
    elif args.action == 'post-processing':
        set_post_processing(profile, args.state == 'on')
        sys.stdout.write(f'Post processing {args.state}: {profile}\n')
    else:
        validate_profile(profile)
        os.environ['AALC_COMPAT_DATA_PATH'] = str(profile)
        os.environ['AALC_GRAPHICS_API'] = args.graphics_api
        os.execv(sys.executable, [sys.executable, str(ROOT/'main.py')])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f'AALC: {exc}\n')
        raise SystemExit(1) from exc
