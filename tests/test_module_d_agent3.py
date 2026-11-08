# -*- coding: utf-8 -*-
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, input_text=None):
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=ROOT,
        input=input_text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class TestModuleDAgent3(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_te_agent3_"))
        self.example_dir = ROOT / "Example" / "wordlist_tone16"
        self.example_teproj = self.example_dir / "普通话双字组声调基频格局与连读变调实验.teproj"
        self.example_ptwl = self.example_dir / "普通话双字组声调基频格局与连读变调实验字表.ptwl"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_stats_and_report_export_with_example_teproj(self):
        """测试 P0 stats 命令 (by=group|item|speaker, scope=integrated) 与 report_export 核心声学指标分组统计摘要表。"""
        proj_copy = self.tmp_dir / "work.teproj"
        shutil.copy2(self.example_teproj, proj_copy)

        # 1. stats by=group scope=integrated
        stats_json = self.tmp_dir / "stats_group.json"
        res_grp = run_cli("--project", str(proj_copy), f"stats by=group scope=integrated out={stats_json}")
        self.assertEqual(res_grp.returncode, 0, msg=res_grp.stderr)
        payload_grp = json.loads(res_grp.stdout.strip())
        self.assertTrue(payload_grp["success"])
        self.assertEqual(payload_grp["by"], "group")
        self.assertEqual(payload_grp["scope"], "integrated")
        self.assertIn("speaker_baselines", payload_grp)
        self.assertTrue(len(payload_grp["summary"]) >= 16)
        first_row = payload_grp["summary"][0]
        for key in (
            "count",
            "valid_f0_count",
            "duration_ms_mean",
            "f0_start_mean",
            "f0_mid_mean",
            "f0_end_mean",
            "f0_range_mean",
            "f0_mean",
            "f0_sd",
            "t_start",
            "t_mid",
            "t_end",
            "t_contour",
            "t_code",
        ):
            self.assertIn(key, first_row)
        self.assertTrue(stats_json.exists())

        # 2. stats by=speaker scope=integrated
        res_spk = run_cli("--project", str(proj_copy), "stats by=speaker scope=integrated")
        payload_spk = json.loads(res_spk.stdout.strip())
        self.assertTrue(payload_spk["success"])
        self.assertEqual(len(payload_spk["summary"]), 3)

        # 3. report_export generates 研究方法报告.md containing 核心声学指标分组统计摘要表
        rep_dir = self.tmp_dir / "reports"
        res_rep = run_cli(f'report_export "{proj_copy}" "{rep_dir}" format=md')
        payload_rep = json.loads(res_rep.stdout.strip())
        self.assertTrue(payload_rep["success"])
        md_files = list(rep_dir.glob("*_研究方法报告.md"))
        self.assertEqual(len(md_files), 1)
        md_text = md_files[0].read_text(encoding="utf-8")
        self.assertIn("## 核心声学指标分组统计摘要表", md_text)
        self.assertIn("分组核心声学指标汇总表", md_text)
        self.assertIn("各词项核心声学数据速览表", md_text)

    def test_export_bundle_and_zero_warnings_on_example(self):
        """测试 P1 export_bundle 一站式科研产物打包及 overview_heatmap / formant_space 零警告导出。"""
        proj_copy = self.tmp_dir / "bundle_src.teproj"
        shutil.copy2(self.example_teproj, proj_copy)
        bundle_dir = self.tmp_dir / "bundle_out"

        res_bundle = run_cli("--project", str(proj_copy), f'export_bundle "{bundle_dir}" scope=integrated format=png')
        self.assertEqual(res_bundle.returncode, 0, msg=res_bundle.stderr)
        self.assertNotIn("RuntimeWarning", res_bundle.stdout)
        self.assertNotIn("Mean of empty slice", res_bundle.stdout)
        payload = json.loads(res_bundle.stdout.strip())
        self.assertTrue(payload["success"])
        self.assertTrue((bundle_dir / "acoustic_data.xlsx").exists())
        self.assertTrue((bundle_dir / "acoustic_points.txt").exists())
        self.assertTrue((bundle_dir / "acoustic_stats.json").exists())
        self.assertTrue((bundle_dir / "charts" / "contour.png").exists())
        self.assertTrue((bundle_dir / "charts" / "distribution.png").exists())
        self.assertTrue((bundle_dir / "charts" / "quality.png").exists())
        self.assertTrue((bundle_dir / "charts" / "overview_heatmap.png").exists())
        self.assertTrue((bundle_dir / "report" / "研究方法报告.md").exists())
        self.assertTrue(len(list(bundle_dir.glob("*.teproj"))) >= 1)
        self.assertTrue(len(list(bundle_dir.glob("*.ptwl"))) >= 1)

    def test_script_prompt_c02_and_c03_and_p2_commands(self):
        """测试 P1 C-02/C-03 (script_prompt v2 字表字段与参数白名单) 及 P2 detect_formants / fix_warnings。"""
        proj_copy = self.tmp_dir / "p2_work.teproj"
        shutil.copy2(self.example_teproj, proj_copy)

        # C-02: v2 wordlist fields in script_prompt
        res_sp = run_cli(
            "--project",
            str(proj_copy),
            'script_prompt template=custom output_type=script scope=integrated user_request="按首字声调分组统计"',
        )
        payload_sp = json.loads(res_sp.stdout.strip())
        self.assertTrue(payload_sp["success"])
        self.assertIn("高级字表 v2 元数据", payload_sp["prompt_text"])
        self.assertIn("custom_fields", payload_sp["prompt_text"])

        # C-03: unknown key rejected with valid_keys
        res_bad = run_cli("--project", str(proj_copy), "script_prompt bad_unknown_key=123")
        payload_bad = json.loads(res_bad.stdout.strip())
        self.assertFalse(payload_bad["success"])
        self.assertIn("valid_keys", payload_bad)

        # P2: detect_formants & fix_warnings
        res_df = run_cli("--project", str(proj_copy), "detect_formants --no-apply")
        payload_df = json.loads(res_df.stdout.strip())
        self.assertTrue(payload_df["success"])
        self.assertIn("suggestions", payload_df)

        res_fw = run_cli("--project", str(proj_copy), "fix_warnings --apply")
        payload_fw = json.loads(res_fw.stdout.strip())
        self.assertTrue(payload_fw["success"])
        self.assertIn("warnings_before", payload_fw)
        self.assertIn("warnings_after", payload_fw)


if __name__ == "__main__":
    unittest.main()
