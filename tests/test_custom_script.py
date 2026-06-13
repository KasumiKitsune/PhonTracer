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
from modules.script_manager import DEFAULT_SCRIPTS
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

        allowed_code = """
import numpy as np
import scipy
import math
import statistics
import collections
import itertools
import warnings

def run(ctx):
    warnings.warn("test")
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

    def test_builtin_example_scripts_do_not_emit_extra_svg_outputs(self):
        for script in DEFAULT_SCRIPTS:
            self.assertNotIn(".svg", script["code"])

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

    def test_run_custom_script_blocks_expensive_kde(self):
        code = """
import scipy.stats
def run(ctx):
    return scipy.stats.gaussian_kde([[0, 1], [0, 1]])
"""
        res, logs, err = run_custom_script(code, [])
        self.assertIsNone(res)
        self.assertIsNotNone(err)
        self.assertIn("gaussian_kde", err)

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
        self.assertIn("不要使用 scipy.stats.gaussian_kde", prompt)
        self.assertIn("项目数据说明与数据来源", prompt)
        self.assertIn("分组及条目数", prompt)
        self.assertIn("张三: 条目 1", prompt)
        self.assertIn("syl_t_values", prompt)
        self.assertIn("严禁为了比较声调/F0走势", prompt)
        self.assertIn("不要把热力图当作主要统计结论", prompt)
        self.assertIn("复刻参考图的图表类型、变量关系、统计口径、布局和视觉表达方式", prompt)
        self.assertIn("严禁照抄参考图里的数值", prompt)
        self.assertIn("# 脚本名称：测试图表", prompt)
        self.assertIn("# 功能说明：绘制 F0 曲线图", prompt)

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
        self.assertIn("推荐代码骨架", prompt)
        self.assertIn("每组样本数", prompt)
        self.assertIn("共享同一套颜色归一化范围", prompt)
        self.assertIn("复刻参考图的图表类型、变量关系、统计口径、布局和视觉表达方式", prompt)
        self.assertIn("# 脚本名称：比较不同分组的声学差异", prompt)
        self.assertIn("# 功能说明：比较不同分组的声学差异", prompt)

    def test_generate_ai_prompt_agent_mode(self):
        items = {}
        for idx in range(25):
            items[f"item_{idx}"] = {
                "label": f"词{idx}",
                "group": f"组{idx:02d}",
                "analysis_mode": "f0",
            }
        project_data = {
            "speakers": {
                "spk_1": {
                    "name": "张三",
                    "items": items,
                }
            }
        }
        selections = {
            "prompt_mode": "Agent协作",
            "agent_detail_level": "详细",
            "agent_chart_count": "5",
            "agent_include_project_summary": True,
            "custom_desc": "优先考虑论文图，不要无端消耗 token。",
        }
        prompt = generate_ai_prompt(project_data, selections)
        self.assertIn("自定义图表脚本 Agent", prompt)
        self.assertIn("第一轮回复不要直接输出代码", prompt)
        self.assertIn("主动猜测用户可能的目的", prompt)
        self.assertIn("推荐 5 种图表候选", prompt)
        self.assertIn("用户选择图表或明确目标之后，再进入代码阶段", prompt)
        self.assertIn("def run(ctx)", prompt)
        self.assertIn("ctx.figure", prompt)
        self.assertIn("pandas", prompt)
        self.assertIn("结构类图表", prompt)
        self.assertIn("其余 5 类已省略", prompt)
        self.assertIn("优先考虑论文图", prompt)
        self.assertIn("文档级脚本说明", prompt)
        self.assertIn("项目数据说明与数据来源", prompt)
        self.assertIn("推荐代码骨架", prompt)
        self.assertIn("复刻参考图的图表类型、变量关系、统计口径、布局和视觉表达方式", prompt)
        self.assertIn("严禁照抄参考图里的数值", prompt)
        self.assertIn("# 脚本名称：", prompt)
        self.assertIn("# 功能说明：", prompt)

    def test_generate_ai_prompt_agent_compact_is_still_specific(self):
        project_data = {
            "speakers": {
                "spk_1": {
                    "name": "张三",
                    "items": {
                        "item_1": {
                            "label": "ma1",
                            "group": "阴平",
                            "analysis_mode": "f0",
                        }
                    },
                }
            }
        }
        selections = {
            "prompt_mode": "Agent协作",
            "agent_detail_level": "精简",
            "agent_chart_count": "3",
            "agent_include_project_summary": True,
        }
        prompt = generate_ai_prompt(project_data, selections)
        self.assertIn("使用精简说明：推荐图表时给出字段、统计口径、风险和适用场景", prompt)
        self.assertIn("推荐 3 种图表候选", prompt)
        self.assertIn("参考图表", prompt)
        self.assertNotIn("文档级脚本说明", prompt)

    def test_generate_ai_prompt_exposes_v2_wordlist_metadata(self):
        project_data = {
            "speakers": {
                "spk_1": {
                    "name": "张三",
                    "items": {
                        "item_1": {
                            "label": "妈",
                            "group": "阴平",
                            "analysis_mode": "f0",
                            "wordlist_version": "v2",
                            "wordlist_title": "声调综合测试高级字表",
                            "item_tags": ["目标词", "单字"],
                            "group_tags": ["主测试"],
                            "item_meta": {"结构": "单字", "实验条件": "声调基线"},
                            "metadata_source": "AI推断，需人工复核",
                        }
                    },
                }
            }
        }
        agent_prompt = generate_ai_prompt(project_data, {
            "prompt_mode": "Agent协作",
            "agent_detail_level": "精简",
            "agent_chart_count": "3",
            "agent_include_project_summary": True,
        })
        self.assertIn("高级字表元数据: v2 条目 1", agent_prompt)
        self.assertIn("声调综合测试高级字表", agent_prompt)
        self.assertIn("目标词 (1条)", agent_prompt)
        self.assertIn("实验条件 (1条)", agent_prompt)
        self.assertIn("高级字表字段", agent_prompt)
        self.assertIn("item_meta", agent_prompt)
        self.assertIn("metadata_source", agent_prompt)

        normal_prompt = generate_ai_prompt(project_data, {
            "prompt_mode": "参数选项",
            "goal": "按高级字表标签统计",
        })
        self.assertIn("高级字表 v2 元数据字段", normal_prompt)
        self.assertIn("group_tags / item_tags", normal_prompt)
        self.assertIn("item_meta", normal_prompt)

    def test_generate_ai_prompt_agent_detailed_project_summary(self):
        item_ma = {
            "label": "马",
            "group": "需要检查",
            "analysis_mode": "f0",
            "wordlist_version": "v2",
            "wordlist_title": "声调综合测试高级字表",
            "group_note": "三声相关条目，优先观察变调和复核状态。",
            "group_tags": ["主测试"],
            "item_note": "三声目标词",
            "item_tags": ["目标词", "单字"],
            "item_aliases": ["ma3"],
            "item_meta": {"结构": "单字", "词频等级": "高"},
            "metadata_source": "AI推断，需人工复核",
        }
        item_li = {
            "label": "梨",
            "group": "需要检查",
            "analysis_mode": "f0",
            "wordlist_version": "v2",
            "wordlist_title": "声调综合测试高级字表",
            "group_tags": ["主测试"],
            "item_tags": ["目标词", "单字"],
            "item_meta": {"结构": "单字", "词频等级": "中"},
            "metadata_source": "已人工复核",
        }
        project_data = {
            "speakers": {
                "spk_1": {"name": "张三", "items": {"item_1": dict(item_ma), "item_2": dict(item_li)}},
                "spk_2": {"name": "李四", "items": {"item_1": dict(item_ma)}},
            }
        }
        prompt = generate_ai_prompt(project_data, {
            "prompt_mode": "Agent协作",
            "agent_detail_level": "精简",
            "agent_chart_count": "3",
            "agent_project_summary_mode": "包含详细工程摘要",
        })

        self.assertIn("当前附带详细工程摘要", prompt)
        self.assertIn("不要要求用户重复粘贴字表", prompt)
        self.assertIn("详细字表信息", prompt)
        self.assertIn("声调综合测试高级字表 / 需要检查: 去重词项 2，工程条目 3", prompt)
        self.assertIn("组备注: 三声相关条目，优先观察变调和复核状态。", prompt)
        self.assertIn("词项标签汇总: 单字 (2条), 目标词 (2条)", prompt)
        self.assertIn("结构=单字 (2条)", prompt)
        self.assertIn("词频等级=高 (1条)", prompt)
        self.assertIn("马[标签:目标词, 单字", prompt)
        self.assertIn("状态:AI推断，需人工复核", prompt)

        toolkit_path = os.path.join(os.path.dirname(__file__), "..", "toolkit.py")
        with open(toolkit_path, "r", encoding="utf-8") as f:
            toolkit_source = f.read()
        self.assertIn("包含详细工程摘要", toolkit_source)
        self.assertIn("agent_project_summary_mode", toolkit_source)

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
