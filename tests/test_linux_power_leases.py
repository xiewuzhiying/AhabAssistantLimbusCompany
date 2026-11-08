"""Exercise production inhibitor functions with independent thread identities."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import threading


def test_threads_release_only_their_own_inhibitor():
    source = ast.parse(Path('module/system_actions.py').read_text())
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('_update_keep_awake_depth', 'apply_power_keep_awake')]
    identity = Mock(return_value=1)
    processes = []

    def spawn(*args, **kwargs):
        process = Mock()
        process.poll.return_value = None
        processes.append(process)
        return process

    namespace = dict(IS_WINDOWS=False, IS_LINUX=True,
                     threading=SimpleNamespace(get_ident=identity),
                     _power_keep_awake_lock=threading.Lock(),
                     _power_keep_awake_depths={}, _linux_inhibit_processes={},
                     shutil=SimpleNamespace(which=lambda _: '/bin/systemd-inhibit'),
                     subprocess=SimpleNamespace(Popen=spawn, DEVNULL=-3), log=Mock())
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<power-actions>', 'exec'), namespace)
    apply = namespace['apply_power_keep_awake']
    apply(True)
    apply(True)
    identity.return_value = 2
    apply(True)
    identity.return_value = 1
    apply(False)
    processes[0].terminate.assert_not_called()
    apply(False)
    processes[0].terminate.assert_called_once()
    processes[1].terminate.assert_not_called()
    identity.return_value = 2
    apply(False)
    processes[1].terminate.assert_called_once()
    assert not namespace['_linux_inhibit_processes']
    assert not namespace['_power_keep_awake_depths']
