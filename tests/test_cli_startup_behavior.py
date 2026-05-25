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


def test_cli_block_python_script(capsys):
    cli = PhonTracerCLI()
    cli.default("python process.py")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_python_exe_script(capsys):
    cli = PhonTracerCLI()
    cli.default("python.exe process.py")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_bat_script(capsys):
    cli = PhonTracerCLI()
    cli.default(".\\process_female_simple.bat")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_powershell_script(capsys):
    cli = PhonTracerCLI()
    cli.default("powershell -File test.ps1")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_powershell_exe_script(capsys):
    cli = PhonTracerCLI()
    cli.default("powershell.exe -File test.ps1")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_cmd_exe_script(capsys):
    cli = PhonTracerCLI()
    cli.default("cmd.exe /c test.bat")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_start_command(capsys):
    cli = PhonTracerCLI()
    cli.default("start process_female_simple.bat")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_call_command(capsys):
    cli = PhonTracerCLI()
    cli.default("call process_female_simple.bat")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_block_nested_token_script(capsys):
    cli = PhonTracerCLI()
    cli.default("do_something script.py")
    captured = capsys.readouterr()
    assert "External automation" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out


def test_cli_unknown_command(capsys):
    cli = PhonTracerCLI()
    cli.default("hello_world")
    captured = capsys.readouterr()
    assert "Unknown CLI command" in captured.out
    assert "success" in captured.out
    assert "false" in captured.out
