import os
import json
import zipfile
import tempfile
import pytest

def test_anomaly_detection_import_and_downgrade():
    from modules.report_generator import detect_pitch_anomaly_points, analyze_item_anomalies, SPLIT_WARNING_ZH
    assert detect_pitch_anomaly_points.__module__ == 'modules.anomaly_detection'

    assert SPLIT_WARNING_ZH['tiny_segment'] == "子段时长偏短"
    assert SPLIT_WARNING_ZH['imbalanced_duration'] == "子段时长比例不均"
    assert SPLIT_WARNING_ZH['no_clear_valley'] == "能量谷底不明显"
    assert SPLIT_WARNING_ZH['fallback_equal_split'] == "等分兜底切分"
    assert SPLIT_WARNING_ZH['low_f0_coverage'] == "有效基频帧偏少"

    # Test analyze_item_anomalies
    item = {
        'label': '测试',
        'start': 0.1,
        'end': 0.2,
        'split_warnings': ['tiny_segment', 'low_f0_coverage', 'no_clear_valley']
    }
    anomalies = analyze_item_anomalies(item, None, None, {'analysis_mode': 'f0'})
    assert any('[复核] 音段时长较短' in a for a in anomalies)
    assert any('[复核] 有效基频帧比例较低' in a for a in anomalies)
    assert any('[警告] 未能识别到能量谷' in a for a in anomalies)
    assert not any('[致命]' in a for a in anomalies)

    # Test error status
    err_anomalies = analyze_item_anomalies(item, None, None, {'analysis_mode': 'f0'}, pitch_status="损坏")
    assert any('[错误] 缓存文件无法读取' in a for a in err_anomalies)
    assert not any('[致命]' in a for a in err_anomalies)


def test_markdown_report_phrasing_and_numbering():
    from modules.report_generator import generate_markdown_report

    state = {
        'version': '1.0',
        'software_version': '1.2.5',
        'export_numbering_rule': 'per_group',
        'speakers': {
            'spk1': {
                'name': '发音人1',
                'last_params': {'analysis_mode': 'f0', 'pts': 11},
                'items': {
                    'item1': {
                        'label': '汉字',
                        'start': 0.1,
                        'end': 0.3,
                        'split_warnings': ['tiny_segment'],
                        'is_manual_edited': True
                    },
                    'item2': {
                        'label': '方言',
                        'start': 0.4,
                        'end': 0.6,
                        'is_manual_edited': False
                    }
                }
            }
        }
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        teproj_path = os.path.join(tmpdir, 'test.teproj')
        with zipfile.ZipFile(teproj_path, 'w') as z:
            z.writestr('project.json', json.dumps(state))
        with zipfile.ZipFile(teproj_path, 'r') as z:
            md = generate_markdown_report(teproj_path, state, z)

    assert "按分组重新标号" in md
    assert "## 6. 人工复核与质量审计摘要" in md
    assert "- **边界与轨迹修订统计**：纳入分析的条目中，有 **1** 个条目（**50.0%**）" in md
    assert "保持自动检测边界与原始提取轨迹" in md
    assert "## 7. 实验背景与方法学补充清单" in md
    assert "[!WARNING]" not in md
    assert "发音疲兴" not in md
    assert "发音疲劳控制措施" in md
    assert "词项" in md
    assert "条目标签" not in md


def test_excel_archive_structure_and_headers():
    from modules.report_generator import write_excel_archive
    import xml.etree.ElementTree as ET

    state = {
        'version': '1.0',
        'software_version': '1.2.5',
        'export_numbering_rule': 'per_group',
        'speakers': {
            'spk1': {
                'name': '发音人1',
                'last_params': {'analysis_mode': 'f0', 'pts': 11},
                'items': {
                    'item1': {
                        'label': '测试',
                        'start': 0.1,
                        'end': 0.3,
                        'split_warnings': ['tiny_segment'],
                        'is_manual_edited': True
                    }
                }
            }
        }
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        teproj_path = os.path.join(tmpdir, 'test.teproj')
        with zipfile.ZipFile(teproj_path, 'w') as z:
            z.writestr('project.json', json.dumps(state))

        out_xlsx = os.path.join(tmpdir, 'test_archive.xlsx')
        write_excel_archive(teproj_path, state, out_xlsx, include_cache_details=True)
        assert os.path.exists(out_xlsx)

        with zipfile.ZipFile(out_xlsx, 'r') as z:
            wb_xml = z.read('xl/workbook.xml')
            root = ET.fromstring(wb_xml)
            sheets = [node.attrib['name'] for node in root.findall('.//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet')]

            assert '质量控制与复核记录' in sheets
            assert '人工复核与风险' not in sheets

            # Verify styles: styles.xml should contain Microsoft YaHei and borders
            styles_xml = z.read('xl/styles.xml').decode('utf-8')
            assert 'Microsoft YaHei' in styles_xml


def test_paper_usage_guide_defensive_removal():
    guide_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'PAPER_USAGE_GUIDE.md')
    with open(guide_path, 'r', encoding='utf-8') as f:
        content = f.read()

    assert "## 1. 论文方法学报告规范：客观说明软件版本、底层算法与参数配置" in content
    assert "PhonTracer 仅为辅助工具，研究者需对最终数据和结论负责" not in content
    assert "本研究使用语音声学分析软件 PhonTracer (v1.0.0) 进行声学参数提取与标注复核" in content
    assert "本研究使用 PhonTracer (v1.0.0) 完成声学参数提取、轨迹复核与数据导出。" in content


def test_ui_and_chart_wording():
    # app.py
    app_path = os.path.join(os.path.dirname(__file__), '..', 'modules', 'app.py')
    with open(app_path, 'r', encoding='utf-8') as f:
        app_code = f.read()
    assert 'messagebox.showwarning("无法可靠建议"' not in app_code
    assert 'messagebox.showwarning("有效语音帧不足"' in app_code

    # formant_detection_dialog.py
    dlg_path = os.path.join(os.path.dirname(__file__), '..', 'modules', 'formant_detection_dialog.py')
    with open(dlg_path, 'r', encoding='utf-8') as f:
        dlg_code = f.read()
    assert '【警告】有效发音时长不足' not in dlg_code
    assert '提示：当前条目有效时长短于 0.5s，建议结合同发音人更多样本综合确定参数。' in dlg_code

    # acoustic_exporter.py
    exporter_path = os.path.join(os.path.dirname(__file__), '..', 'modules', 'acoustic_exporter.py')
    with open(exporter_path, 'r', encoding='utf-8') as f:
        exp_code = f.read()
    assert 'label="质量良好的常规发音"' not in exp_code
    assert 'label="存在质量异常的发音"' not in exp_code
    assert 'label="未触发复核阈值轨迹"' in exp_code
    assert 'label="触发复核阈值轨迹"' in exp_code


def test_excel_style_manager_and_visual_width():
    import xlsxwriter
    from modules.data_utils import ExcelStyleManager, compute_visual_width

    assert compute_visual_width("abc") == 3.0
    assert compute_visual_width("汉字") == 4.0
    assert compute_visual_width("字1_平均时长(s)") == 15.0

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        temp_path = tf.name
    try:
        wb = xlsxwriter.Workbook(temp_path)
        mgr = ExcelStyleManager(wb)
        assert mgr.header_fmt.font_name == "Microsoft YaHei"
        assert mgr.section_fmt.font_name == "Microsoft YaHei"
        assert mgr.highlight_fmt.font_name == "Microsoft YaHei"
        assert mgr.duration_fmt.font_name == "Microsoft YaHei"
        assert mgr.hz_fmt.font_name == "Microsoft YaHei"
        assert mgr.t_val_fmt.font_name == "Microsoft YaHei"
        assert mgr.int_fmt.font_name == "Microsoft YaHei"
        assert mgr.thousands_fmt.font_name == "Microsoft YaHei"

        assert mgr.duration_fmt.num_format == "0.000"
        assert mgr.hz_fmt.num_format == "0.0"
        assert mgr.t_val_fmt.num_format == "0.00"
        assert mgr.int_fmt.num_format == "0"
        assert mgr.thousands_fmt.num_format == "#,##0"
        assert mgr.header_fmt.bottom == 1
        assert mgr.duration_fmt.bottom == 1
        wb.close()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_gui_export_integrated_logarithmic_t_value_and_empty_padding():
    import numpy as np
    from modules.project_tree import ProjectTreePanel, AnomalyWarningDialog
    import inspect

    sig = inspect.signature(AnomalyWarningDialog.__init__)
    assert sig.parameters['title'].default == "导出前数据复核提示"

    with tempfile.TemporaryDirectory() as tmpdir:
        txt_path = os.path.join(tmpdir, "integrated.txt")
        xlsx_path = os.path.join(tmpdir, "integrated.xlsx")

        tree = ProjectTreePanel.__new__(ProjectTreePanel)
        tree.num_rule_var = type("MockVar", (), {"get": lambda self: "continuous"})()
        tree.app_state_params = {'pts': 3}
        tree.items = {}

        class MockSpk:
            id = "spk1"
            name = "张三"
            items = {
                "item1": {
                    'label': "单",
                    'group': "阴平",
                    'start': 0.1,
                    'end': 0.3,
                    'chars_bounds': [(0.1, 0.3)],
                    'pitch_data': {'xs': np.linspace(0.1, 0.3, 20), 'freqs': np.array([100.0]*5 + [200.0]*10 + [400.0]*5)},
                    'snd': True,
                    'is_excluded': False
                },
                "item2": {
                    'label': "双音",
                    'group': "阴平",
                    'start': 0.1,
                    'end': 0.5,
                    'chars_bounds': [(0.1, 0.3), (0.3, 0.5)],
                    'pitch_data': {'xs': np.linspace(0.1, 0.5, 20), 'freqs': np.full(20, 200.0)},
                    'snd': True,
                    'is_excluded': False
                }
            }

        tree._get_items_by_group_for_dict = lambda items: [("阴平", ["item1", "item2"])]
        tree._ensure_item_loaded = lambda item: None
        def mock_extract(item, pts):
            if item['label'] == "单":
                # 1 syllable: 100 Hz, 200 Hz, 400 Hz -> log10 T-values: 0.00, 2.50, 5.00 (linear would be 1.67 for 200Hz)
                return 0.2, [(0.2, [100.0, 200.0, 400.0])]
            return 0.4, [(0.2, [200.0, 200.0, 200.0]), (0.2, [200.0, 200.0, 200.0])]

        tree._extract_syl_data = mock_extract

        tree._export_integrated(txt_path, format_mode="txt", include_chart=False, all_speakers=[MockSpk()])
        with open(txt_path, 'r', encoding='utf-8-sig') as f:
            lines = [line.rstrip("\n") for line in f]
        assert "组别" in lines[0]
        # First data row ("单"): T2 should be 2.50 (log scale), and 2nd syllable columns should be empty strings, not 0.0000
        parts_row1 = lines[1].split("\t")
        assert "2.50" in parts_row1
        assert "1.67" not in parts_row1
        assert parts_row1[-1] == ""

        tree._export_integrated(xlsx_path, format_mode="xlsx", include_chart=True, all_speakers=[MockSpk()])
        with zipfile.ZipFile(xlsx_path, 'r') as zf:
            wb_xml = zf.read('xl/workbook.xml').decode('utf-8')
            assert 'name="整合数据(T值)"' in wb_xml
            assert 'name="分析结果"' in wb_xml
            assert 'name="原始基频数据"' in wb_xml


def test_cli_export_integrated_and_formant_3_sheets():
    import numpy as np
    from cli import PhonTracerCLI

    cli = PhonTracerCLI()
    try:
        class MockSpeaker:
            name = "TestSpeaker"
            id = "spk1"
            last_params = {'pts': 5, 'show_f3': False, 'formant_sample_strategy': '整段11点', 'num_rule': 'continuous'}
            items = {
                "item1": {
                    'label': "测试",
                    'group': "阴平",
                    'start': 0.1,
                    'end': 0.5,
                    'chars_bounds': [(0.1, 0.3), (0.3, 0.5)],
                    'pitch_data': {
                        'xs': np.linspace(0.1, 0.5, 50),
                        'freqs': np.linspace(150.0, 250.0, 50)
                    },
                    'formant_data': {
                        'xs': np.linspace(0.1, 0.5, 50),
                        'f1': np.full(50, 500.0),
                        'f2': np.full(50, 1500.0)
                    },
                    'snd': True,
                    'is_excluded': False
                }
            }
            cli_groups = ["阴平"]

        class MockSpeakerManager:
            def get_active_speaker(self):
                return MockSpeaker()

        cli.speaker_manager = MockSpeakerManager()
        spk = MockSpeaker()

        with tempfile.TemporaryDirectory() as tmpdir:
            int_xlsx = os.path.join(tmpdir, "cli_int.xlsx")
            cli._export_xlsx_integrated(int_xlsx, [spk], rule="continuous")
            with zipfile.ZipFile(int_xlsx, 'r') as zf:
                wb_xml = zf.read('xl/workbook.xml').decode('utf-8')
                assert 'name="数据"' in wb_xml
                assert 'name="分析结果"' in wb_xml
                assert 'name="原始基频数据"' in wb_xml
                shared_strings = zf.read('xl/sharedStrings.xml').decode('utf-8')
                assert '字1_P1(Hz)' in shared_strings
                assert '字1_P1均值(Hz)' in shared_strings

            fmt_xlsx = os.path.join(tmpdir, "cli_formant.xlsx")
            cli._export_formant_table(fmt_xlsx, [spk])
            with zipfile.ZipFile(fmt_xlsx, 'r') as zf:
                wb_xml = zf.read('xl/workbook.xml').decode('utf-8')
                assert 'name="提取数据"' in wb_xml
                assert 'name="分析图表"' in wb_xml
                assert 'name="原始数据"' in wb_xml
    finally:
        cli.cleanup_workspace()
