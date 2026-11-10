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
    run.add_argument('--graphics-api', choices=['auto', 'd3d11', 'd3d12'], default=None)
    run.add_argument('--profile', type=Path)
    run.add_argument('--skip-setup', action='store_true')
    for name in ('doctor', 'status'):
        diagnostic = sub.add_parser(name)
        diagnostic.add_argument('--json', action='store_true')
    sub.add_parser('preview', help='Open a read-only game preview')
    init = sub.add_parser('create-profile', help='Copy an inactive Steam prefix once')
    init.add_argument('--source', type=Path, required=True)
    init.add_argument('--profile', type=Path)
    settings = sub.add_parser('post-processing', help='Change post processing in an inactive private profile')
    settings.add_argument('state', choices=['on', 'off'])
    settings.add_argument('--profile', type=Path)
    args = parser.parse_args()
    os.chdir(ROOT)
    from module.native_linux import load_settings, report
    if args.action in ('doctor', 'status'):
        import json
        result = report(status_only=args.action == 'status')
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + '\n')
        return 0 if result['ok'] else 1
    profile = getattr(args, 'profile', None) or Path(load_settings()['profile'])
    profile = profile.expanduser().resolve()
    if args.action == 'preview':
        # Ask the owning GUI to show its own session, never scan unrelated displays.
        os.execv(sys.executable, [sys.executable, str(ROOT/'main.py'), '--native-preview'])
    from module.game_and_screen.automation_profile import clone_profile, set_post_processing, validate_profile
    if args.action == 'create-profile':
        clone_profile(args.source, profile)
        sys.stdout.write(f'Private profile ready: {profile}\n')
    elif args.action == 'post-processing':
        set_post_processing(profile, args.state == 'on')
        sys.stdout.write(f'Post processing {args.state}: {profile}\n')
    else:
        api = args.graphics_api or load_settings()['graphics_api']
        if args.skip_setup or args.profile:
            validate_profile(profile)
            from module.game_and_screen.automation_profile import require_stopped
            require_stopped(profile)
        command = [sys.executable, str(ROOT/'main.py')]
        if args.profile or args.graphics_api or args.skip_setup:
            command += ['--native-profile', str(profile), '--native-api', api]
            if args.skip_setup:
                command.append('--skip-setup')
        os.execv(sys.executable, command)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f'AALC: {exc}\n')
        raise SystemExit(1) from exc
