"""Keep machine-dependent integration checks explicitly opt-in."""
import os
import sys
from pathlib import Path

REAL = {'test_gamescope_live_integration.py', 'test_gamescope_visual_closed_loop.py',
        'test_gamescope_ei_crash_regression.py', 'test_managed_session.py',
        'test_gamescope_launcher.py'}
LINUX = REAL | {'test_isolation_boundaries.py', 'test_x11_screenshot.py',
                 'test_linux_startup_guard.py', 'test_gamescope_launcher_lifecycle.py',
                 'test_automation_profile.py', 'test_linux_power_leases.py', 'test_linux_preview.py'}


def pytest_ignore_collect(collection_path: Path, config):
    name = collection_path.name
    if not sys.platform.startswith('linux') and (name in LINUX or name.startswith('test_gamescope_')):
        return True
    if name in REAL and os.environ.get('AALC_LIVE_TESTS') != '1':
        return True
    return False
