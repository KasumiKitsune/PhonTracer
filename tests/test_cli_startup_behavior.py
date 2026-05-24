import subprocess
import sys

from cli import PhonTracerCLI


def test_cli_help_argument_prints_manual_and_exits():
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True,
        timeout=20,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")

    assert result.returncode == 0
    assert "PhonTracer CLI MANUAL" in stdout
    assert "(phontracer)" not in stdout


def test_cli_eof_exits_cleanly(capsys):
    cli = PhonTracerCLI()

    should_stop = cli.do_EOF("")

    captured = capsys.readouterr()
    assert should_stop is True
    assert "Exiting..." in captured.out
