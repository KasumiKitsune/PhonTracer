import os
import sys
import numpy as np
import pytest

from modules.script_runner import run_custom_script, check_script_safety
from modules.script_api import TableResult, FigureResult
from cli import PhonTracerCLI


def test_toolkit_deduplication_imports():
    import toolkit
    from modules.ui_widgets import CTkReleaseButton
    from modules.visual_splitter import VisualSplitter
    from modules.audio_core import macroscopic_vad, SILENCE_AMPLITUDE_THRESHOLD

    assert toolkit.CTkReleaseButton is CTkReleaseButton
    assert toolkit.VisualSplitter is VisualSplitter
    assert toolkit.macroscopic_vad is macroscopic_vad
    assert toolkit.SILENCE_AMPLITUDE_THRESHOLD == SILENCE_AMPLITUDE_THRESHOLD

    # Verify dead code _send_files_to_main_app is removed
    assert not hasattr(toolkit.ToolkitApp, "_send_files_to_main_app")


def test_modules_detection_removed():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    detection_dir = os.path.join(base_dir, "modules", "detection")
    assert not os.path.exists(detection_dir)


def test_script_runner_allows_gaussian_kde():
    code = """
import numpy as np
from scipy.stats import gaussian_kde

def run(ctx):
    data = np.array([1.0, 2.0, 2.5, 3.0, 3.5, 4.0])
    kde = gaussian_kde(data)
    density = kde([2.0, 3.0]).tolist()
    return ctx.table([[density[0], density[1]]], ["d1", "d2"], title="KDE密度")
"""
    result, logs, err = run_custom_script(code, [], timeout=5)
    assert err is None, f"Expected no error, got: {err}"
    assert isinstance(result, TableResult)
    assert len(result.rows) == 1
    assert result.rows[0][0] > 0.0


def test_cli_safe_normalize_cli_line():
    norm = PhonTracerCLI._safe_normalize_cli_line

    # Windows path with backslashes is normalized
    assert norm(r'load_project C:\Users\Test\audio.teproj') == 'load_project C:/Users/Test/audio.teproj'

    # UNC path prefix \\ is preserved
    unc = r'import \\server\share\audio.wav'
    res_unc = norm(unc)
    assert res_unc.startswith(r'import \\server/share/')

    # Escaped quotes \" are preserved
    quoted = r'run_script test.py desc="Contains \"escaped\" quotes"'
    res_quoted = norm(quoted)
    assert r'\"escaped\"' in res_quoted

    # Quoted path with backslashes
    quoted_path = r'load_project "C:\My Data\project.teproj"'
    res_qp = norm(quoted_path)
    assert res_qp == 'load_project "C:/My Data/project.teproj"'


def test_cli_external_command_detection():
    cli = object.__new__(PhonTracerCLI)

    # Should detect shell/scripts
    assert cli._is_external_command("python test.py") is True
    assert cli._is_external_command("& python script.py") is True
    assert cli._is_external_command("powershell.exe -Command dir") is True
    assert cli._is_external_command("run.bat") is True
    assert cli._is_external_command(r".\install.ps1") is True

    # Should NOT flag regular PhonTracer commands as external
    assert cli._is_external_command("status") is False
    assert cli._is_external_command("list_items") is False
    assert cli._is_external_command("nonexistent_command") is False


def test_project_adaptor_module_level_helpers():
    from modules.project_adaptor import to_list, to_dict

    assert to_list(["a", "b"]) == ["a", "b"]
    assert to_list("x, y, z") == ["x", "y", "z"]
    assert to_list(None) == []
    assert to_dict({"k": "v"}) == {"k": "v"}
    assert to_dict(None) == {}
