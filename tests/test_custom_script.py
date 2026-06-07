# -*- coding: utf-8 -*-
import os
import json
import tempfile
import zipfile
import shutil
import unittest
import warnings
import pytest

from modules.script_api import build_dataset_snapshot, ScriptContext, FigureResult, TableResult
from modules.script_runner import check_script_safety, run_custom_script
from modules.script_prompt import generate_ai_prompt
from modules.project_manager import ProjectManager
from modules.report_generator import write_excel_archive

class DummyApp:
    def __init__(self):
        self.custom_script_runs = []
        self.export_numbering_rule_value = "continuous"

        class MockSpeakerManager:
            def __init__(self):
                self.speakers = {}
                self.active_speaker_id = None
        self.speaker_manager = MockSpeakerManager()

class TestCustomScript(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ast_safety_checks(self):
        # Forbidden imports
        with self.assertRaises(ValueError) as ctx:
            check_script_safety("import os")
        self.assertIn("安全检查拦截", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            check_script_safety("from sys import exit")
        self.assertIn("安全检查拦截", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            check_script_safety("import subprocess")
        self.assertIn("安全检查拦截", str(ctx.exception))

        # Forbidden builtins
        with self.assertRaises(ValueError) as ctx:
            check_script_safety("open('test.txt')")
        self.assertIn("安全检查拦截", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            check_script_safety("eval('1 + 1')")
        self.assertIn("安全检查拦截", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            check_script_safety("exec('print(1)')")
        self.assertIn("安全检查拦截", str(ctx.exception))

        # Syntax errors
        with self.assertRaises(ValueError) as ctx:
            check_script_safety("def run(ctx):\n    print('unclosed quote")
        self.assertIn("语法错误", str(ctx.exception))

        # Allowed libraries
        # Math, statistics, collections, itertools should be allowed, along with numpy, matplotlib, scipy.
        allowed_code = """
import numpy as np
import scipy
import math
import statistics
import collections
import itertools

def run(ctx):
    x = math.sin(0.5)
    return x
"""
        # Should not raise exception
        check_script_safety(allowed_code)

    def test_run_custom_script_success(self):
        code = """
def run(ctx):
    ctx.log("日志记录")
    print("控制台输出")
    return "result_value"
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNone(err)
        self.assertEqual(res, "result_value")
        self.assertIn("日志记录", logs)
        self.assertIn("控制台输出", logs)

    def test_run_custom_script_multiple_chart_results(self):
        code = """
def run(ctx):
    fig1, ax1 = ctx.plt.subplots(figsize=(3, 2))
    ax1.set_title("第一张：基频均值")
    ax1.plot([0, 1], [100, 180])

    fig2, ax2 = ctx.plt.subplots(figsize=(3, 2))
    ax2.set_title("第二张：元音空间")
    ax2.scatter([500, 600], [1500, 1200])

    return [
        ctx.figure(fig1, filename="图表一.png", title="中文图表一"),
        ctx.figure(fig2, filename="图表二.png", title="中文图表二"),
        ctx.table([["a", 1], ["b", 2]], ["组别", "数值"], title="统计表")
    ]
"""
        res, logs, err = run_custom_script(code, [], timeout=5)
        self.assertIsNone(err)
        self.assertEqual(len(res), 3)
        self.assertIsInstance(res[0], FigureResult)
        self.assertIsInstance(res[1], FigureResult)
        self.assertIsInstance(res[2], TableResult)

    def test_script_context_matplotlib_cjk_font(self):
        ctx = ScriptContext([])
        fig, ax = ctx.plt.subplots(figsize=(3, 2))
        ax.set_title("基频 各声调组 均值曲线图")
        ax.set_xlabel("归一化时间")
        ax.set_ylabel("基频 F0 (Hz)")
        ax.plot([0, 1], [100, 180])

        out_path = os.path.join(self.temp_dir, "cjk_font_chart.png")
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            fig.savefig(out_path, dpi=80, bbox_inches="tight")

        missing_glyph_warnings = [w for w in captured if "Glyph" in str(w.message)]
        self.assertEqual(missing_glyph_warnings, [])

    def test_run_custom_script_syntax_error(self):
        code = """
def run(ctx):
    invalid syntax code here
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNotNone(err)
        self.assertIn("语法错误", err)

    def test_run_custom_script_missing_run(self):
        code = """
def other_function(ctx):
    pass
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNotNone(err)
        self.assertIn("未定义 `def run(ctx):` 函数入口", err)

    def test_run_custom_script_forbidden_lib(self):
        code = """
import os
def run(ctx):
    return os.getcwd()
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNotNone(err)
        self.assertIn("禁止在第一版脚本中导入库 'os'", err)

    def test_run_custom_script_blocks_builtins_import_bypass(self):
        code = """
def run(ctx):
    os_mod = __builtins__["__import__"]("os")
    return os_mod.getcwd()
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNone(res)
        self.assertIsNotNone(err)
        self.assertIn("禁止在第一版脚本中导入库 'os'", err)

    def test_run_custom_script_timeout(self):
        import time
        code = """
import time
def run(ctx):
    # Wait for 5 seconds, but our timeout is 0.5s
    for i in range(50):
        time.sleep(0.1)
    return "done"
"""
        res, logs, err = run_custom_script(code, [], timeout=0.5)
        self.assertIsNotNone(err)
        self.assertIn("运行超时", err)

    def test_generate_ai_prompt(self):
        project_data = {
            "speakers": {
                "spk_1": {
                    "name": "张三",
                    "items": {
                        "item_1": {
                            "label": "ma1",
                            "group": "阴平",
                            "analysis_mode": "f0"
                        }
                    }
                }
            }
        }
        selections = {
            "goal": "绘制 F0 曲线图",
            "data_range": "只使用纳入分析的条目",
            "group_by": "按声调/分组",
            "chart_style": "折线图",
            "x_axis": "归一化时间 0-1",
            "y_axis": "F0 Hz",
            "stats": ["绘制均值", "忽略 NaN"],
            "title": "测试图表",
            "filename": "test_chart.png",
            "output_table": True,
            "show_legend": True,
            "use_chinese": True,
            "custom_desc": "希望用粗线绘制"
        }
        prompt = generate_ai_prompt(project_data, selections)
        self.assertIn("绘制 F0 曲线图", prompt)
        self.assertIn("张三", prompt)
        self.assertIn("希望用粗线绘制", prompt)
        self.assertIn("numpy", prompt)
        self.assertIn("matplotlib", prompt)
        self.assertIn("scipy", prompt)
        self.assertIn("pandas", prompt)  # As a forbidden library
        self.assertIn("ctx.is_cancelled()", prompt)

    def test_generate_ai_prompt_goal_oriented(self):
        project_data = {
            "speakers": {
                "spk_1": {
                    "name": "张三",
                    "items": {
                        "item_1": {
                            "label": "ma1",
                            "group": "阴平",
                            "analysis_mode": "f0"
                        }
                    }
                }
            }
        }
        selections = {
            "prompt_mode": "目标导向",
            "goal": "比较不同分组的声学差异",
            "data_range": "使用全部纳入分析条目",
            "group_by": "由 AI 根据目标自动选择",
            "chart_style": "由 AI 根据目标自动选择",
            "x_axis": "由 AI 根据目标自动选择",
            "y_axis": "F0 走势",
            "stats": ["由 AI 根据目标选择合适统计处理"],
            "title": "比较不同分组的声学差异",
            "filename": "goal_oriented_chart.png",
            "custom_desc": "用户具体目标：比较四个声调组的 F0 曲线走势"
        }
        prompt = generate_ai_prompt(project_data, selections)
        self.assertIn("生成模式: 目标导向", prompt)
        self.assertIn("比较四个声调组的 F0 曲线走势", prompt)
        self.assertIn("由 AI 根据目标自动选择", prompt)

    def test_project_manager_load_with_and_without_script_runs(self):
        # 1. Create a project without custom_script_runs
        old_project_data = {
            "version": "1.0",
            "speakers": {
                "sp1": {
                    "name": "测试发音人",
                    "items": {}
                }
            }
        }

        teproj_path_old = os.path.join(self.temp_dir, "old_project.teproj")
        with zipfile.ZipFile(teproj_path_old, 'w') as zf:
            zf.writestr("project.json", json.dumps(old_project_data, ensure_ascii=False))

        app_old = DummyApp()
        pm_old = ProjectManager(app_old)

        # Should open fine
        pm_old.load_project(teproj_path_old)
        self.assertEqual(app_old.custom_script_runs, [])

        # 2. Create a project with custom_script_runs
        new_project_data = {
            "version": "1.0",
            "speakers": {
                "sp1": {
                    "name": "测试发音人",
                    "items": {}
                }
            },
            "custom_script_runs": [
                {
                    "script_id": "test-uuid",
                    "script_name": "F0均值折线图",
                    "script_type": "chart",
                    "code": "def run(ctx): pass",
                    "used_at": "2026-06-07 12:00:00"
                }
            ]
        }
        teproj_path_new = os.path.join(self.temp_dir, "new_project.teproj")
        with zipfile.ZipFile(teproj_path_new, 'w') as zf:
            zf.writestr("project.json", json.dumps(new_project_data, ensure_ascii=False))

        app_new = DummyApp()
        pm_new = ProjectManager(app_new)

        # Should open fine and populate custom_script_runs
        pm_new.load_project(teproj_path_new)
        self.assertEqual(len(app_new.custom_script_runs), 1)
        self.assertEqual(app_new.custom_script_runs[0]["script_id"], "test-uuid")

    def test_excel_report_generation_with_custom_scripts(self):
        state = {
            "version": "1.0",
            "speakers": {
                "sp1": {
                    "name": "测试发音人",
                    "items": {}
                }
            },
            "custom_script_runs": [
                {
                    "script_id": "test-uuid-123",
                    "script_name": "F0均值折线图",
                    "script_type": "chart",
                    "api_version": "1",
                    "software_version": "1.2.0",
                    "code_sha256": "abcdef123456",
                    "code": "def run(ctx):\n    pass",
                    "used_at": "2026-06-07 12:00:00",
                    "user_goal": "按声调分组绘制 F0 均值曲线",
                    "outputs": [
                        {
                            "type": "figure",
                            "title": "F0均值",
                            "filename": "f0_mean.png"
                        }
                    ]
                }
            ]
        }
        teproj_path = os.path.join(self.temp_dir, "project.teproj")
        with zipfile.ZipFile(teproj_path, 'w') as zf:
            zf.writestr("project.json", json.dumps(state, ensure_ascii=False))

        xlsx_path = os.path.join(self.temp_dir, "report.xlsx")
        write_excel_archive(teproj_path, state, xlsx_path)

        # Check that sheet is generated and contains keywords
        with zipfile.ZipFile(xlsx_path, 'r') as z:
            shared_strings = z.read("xl/sharedStrings.xml").decode("utf-8")

        self.assertIn("自定义脚本记录", shared_strings)
        self.assertIn("自定义脚本源码", shared_strings)
        self.assertIn("abcdef123456", shared_strings)
        self.assertIn("def run(ctx):", shared_strings)

if __name__ == '__main__':
    unittest.main()
