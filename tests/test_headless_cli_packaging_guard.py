# -*- coding: utf-8 -*-
"""
无头 CLI 打包纯净度门禁测试：
确保在禁用 tkinter 和 customtkinter 的环境下（模拟 PhonTracerCLI.exe 打包排除 GUI 库），
CLI 与无头核心服务层均可正常导入与初始化，绝不触发 GUI 依赖。
"""
import subprocess
import sys


def test_cli_and_headless_layers_never_import_gui():
    code = """
import sys
sys.modules['tkinter'] = None
sys.modules['tkinter.ttk'] = None
sys.modules['tkinter.messagebox'] = None
sys.modules['tkinter.filedialog'] = None
sys.modules['customtkinter'] = None

import cli
from modules.acoustic_exporter import AcousticChartExporter, PUBLICATION_PRESETS
from modules.report_generator import export_reports_from_teproj
from modules.data_utils import ExcelStyleManager
from modules.script_api import build_dataset_snapshot, ScriptContext
from modules.project_manager import ProjectManager
from modules.project_adaptor import adapt_project_state
from modules.wordlist_v2 import validate_wordlist_document
from modules.audio_core import extract_f0, extract_formants
import modules.core
import modules._fallbacks
import modules.scripting
import modules.dsp
import modules.storage
import modules.wordlist

c = cli.PhonTracerCLI()
c.cleanup_workspace()
print("HEADLESS_OK")
"""
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res.returncode == 0, f"Headless import failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "HEADLESS_OK" in res.stdout
