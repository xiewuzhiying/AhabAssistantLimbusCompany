"""Own a Linux game cgroup and end it when the calling application disappears."""
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import IO


def process_identity(pid: int) -> str:
    # comm may contain spaces and parentheses; starttime is field 22.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


class ManagedSession:
    def __init__(self) -> None:
        self.unit = f'aalc-game-{uuid.uuid4().hex}.service'
        self.process: subprocess.Popen[bytes] | None = None
        self._payload: Path | None = None

    def start(self, command: list[str], env: dict[str, str], cwd: Path, log: IO[str]) -> subprocess.Popen[bytes]:
        if getattr(sys, "frozen", False):
            raise RuntimeError("Managed Linux sessions currently require the source Python entry point")
        if self.process is not None:
            raise RuntimeError('Session already started')
        if not shutil.which('systemd-run') or not shutil.which('systemctl'):
            raise RuntimeError('Native Linux automation requires a systemd user manager')
        fd, name = tempfile.mkstemp(prefix='aalc-session-', suffix='.json')
        self._payload = Path(name)
        with os.fdopen(fd, 'w') as output:
            json.dump({'command': command, 'env': env, 'owner': os.getpid(),
                       'identity': process_identity(os.getpid())}, output)
        try:
            self.process = subprocess.Popen([
                'systemd-run', '--user', '--quiet', '--collect', '--wait', '--pipe',
                f'--unit={self.unit}', '--service-type=exec',
                '--property=KillMode=control-group', '--property=TimeoutStopSec=5s',
                f'--working-directory={cwd}',
                sys.executable, str(Path(__file__).resolve()), str(self._payload),
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            return self.process
        except BaseException:
            self._payload.unlink(missing_ok=True)
            raise

    def member_pids(self) -> list[int]:
        if self.process is None:
            return []
        result = subprocess.run(['systemctl', '--user', 'show', self.unit,
                                 '--property=ControlGroup', '--value'],
                                capture_output=True, text=True, timeout=3, check=True)
        group = result.stdout.strip()
        if not group or not group.startswith('/'):
            return []
        root = Path('/sys/fs/cgroup') / group.lstrip('/')
        pids = set()
        for path in root.rglob('cgroup.procs'):
            try:
                pids.update(int(pid) for pid in path.read_text().split())
            except FileNotFoundError:
                pass
        return sorted(pids)

    def stop(self) -> None:
        if self.process is None:
            return
        # No process-name or PGID fallback: stop only this unique owned unit.
        result = subprocess.run(['systemctl', '--user', 'stop', self.unit],
                                capture_output=True, text=True, timeout=12)
        if result.returncode and self.process.poll() is None:
            raise RuntimeError(f'Cannot stop {self.unit}: {result.stderr.strip()}')
        self.process.wait(timeout=12)
        self.process = None
        if self._payload:
            self._payload.unlink(missing_ok=True)


def supervise(payload: Path) -> int:
    data = json.loads(payload.read_text())
    payload.unlink()
    owner = data['owner']
    # pidfd prevents PID reuse from extending the lifetime of an orphaned session.
    try:
        owner_fd = os.pidfd_open(owner)
    except ProcessLookupError:
        return 0
    try:
        if process_identity(owner) != data['identity']:
            return 0
        child = subprocess.Popen(data['command'], env=data['env'])
        while child.poll() is None:
            if select.select([owner_fd], [], [], .2)[0]:
                return 0  # systemd kills every remaining process in this service.
        return child.returncode
    finally:
        os.close(owner_fd)


if __name__ == '__main__':
    raise SystemExit(supervise(Path(sys.argv[1])))
