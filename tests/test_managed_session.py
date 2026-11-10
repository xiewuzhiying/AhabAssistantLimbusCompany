"""Real user-manager tests; never launch or signal a game."""
import os
import subprocess
import sys
import time
from pathlib import Path


def wait_for(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('session transition timed out')


def test_stop_reaps_descendant_in_new_session(tmp_path):
    from module.game_and_screen.managed_session import ManagedSession
    pidfile = tmp_path / 'child'
    code = "import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pidfile)
    command = [sys.executable, '-c',
               'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1]],start_new_session=True); time.sleep(60)', code]
    session = ManagedSession()
    with (tmp_path / 'log').open('w') as log:
        try:
            session.start(command, os.environ.copy(), tmp_path, log)
            wait_for(pidfile.exists)
            child = int(pidfile.read_text())
            session.stop()
            wait_for(lambda: not Path(f'/proc/{child}').exists())
        finally:
            session.stop()


def test_owner_sigkill_reaps_service(tmp_path):
    pidfile = tmp_path / 'child'
    owner_code = '''
import os,sys,time
from pathlib import Path
from module.game_and_screen.managed_session import ManagedSession
s=ManagedSession()
with open(sys.argv[2], 'w') as log:
    s.start([sys.executable, '-c', 'import os,time,pathlib; pathlib.Path('+repr(sys.argv[1])+').write_text(str(os.getpid())); time.sleep(60)'], os.environ.copy(), Path.cwd(), log)
    time.sleep(60)
'''
    owner = subprocess.Popen([sys.executable, '-c', owner_code, str(pidfile), str(tmp_path/'log')])
    try:
        wait_for(pidfile.exists)
        child = int(pidfile.read_text())
        owner.kill()
        owner.wait(timeout=3)
        wait_for(lambda: not Path(f'/proc/{child}').exists())
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
