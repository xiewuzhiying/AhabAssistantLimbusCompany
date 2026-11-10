"""Installed dependencies must not shadow the repository's diagnostic tools."""
import subprocess
import sys


def test_project_scripts_are_not_shadowed_by_dependency_package(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/__init__.py').write_text('')
    code = 'import sys;sys.path.append(sys.argv[1]);from scripts.diagnostics.run_mirror_soak import IncidentLog'
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
