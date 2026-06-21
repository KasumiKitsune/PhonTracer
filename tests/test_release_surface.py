import subprocess
import sys
from pathlib import Path

from modules.version import APP_NAME, __version__


ROOT = Path(__file__).resolve().parents[1]


def _run(*args):
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_version_option():
    result = _run("cli.py", "--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"{APP_NAME} CLI {__version__}"


def test_cli_unknown_option_keeps_exit_code_two():
    result = _run("cli.py", "--definitely-unknown")
    assert result.returncode == 2
    assert "Unknown startup option" in result.stdout


def test_release_versions_are_consistent():
    result = _run("scripts/check_release_version.py", "--expected", f"v{__version__}")
    assert result.returncode == 0, result.stderr
    assert f"版本一致：{__version__}" in result.stdout
