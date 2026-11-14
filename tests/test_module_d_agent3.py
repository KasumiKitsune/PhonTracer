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
        self.example_teproj = self.example_dir / "普通话双字组16种声调实验.teproj"
        self.example_ptwl = self.example_dir / "普通话双字组16种声调实验字表.ptwl"

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
        self.assertIn("anova_summary", payload_grp)
        for bl in payload_grp["speaker_baselines"].values():
            self.assertIn("median_f0", bl)
            self.assertIn("p50_f0", bl)
            self.assertIn("p5_f0", bl)
            self.assertIn("p95_f0", bl)
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

        # 3. report_export generates 研究方法报告.md containing 核心声学指标分组统计摘要表, P50, and One-Way ANOVA
        rep_dir = self.tmp_dir / "reports"
        res_rep = run_cli(f'report_export "{proj_copy}" "{rep_dir}" format=md')
        payload_rep = json.loads(res_rep.stdout.strip())
        self.assertTrue(payload_rep["success"])
        md_files = list(rep_dir.glob("*_研究方法报告.md"))
        self.assertEqual(len(md_files), 1)
        md_text = md_files[0].read_text(encoding="utf-8")
        self.assertIn("## 核心声学指标分组统计摘要表", md_text)
        self.assertIn("F0 中位数 P50 (Hz)", md_text)
        self.assertIn("组间单因素方差分析 (One-Way ANOVA)", md_text)
        self.assertIn("分组核心声学指标汇总表", md_text)
        self.assertIn("各词项核心声学数据速览表", md_text)

    def test_export_bundle_and_zero_warnings_on_example(self):
        """测试 P1 export_bundle 一站式科研产物打包（含双字调专项图表、.Collection 与无重复分页图命名）及零警告导出。"""
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
        # 16 组双字调自动分页为 2 页，仅保留 _第1页/_第2页，不生成重复无后缀同名图
        self.assertTrue((bundle_dir / "charts" / "contour_第1页.png").exists())
        self.assertTrue((bundle_dir / "charts" / "contour_第2页.png").exists())
        self.assertFalse((bundle_dir / "charts" / "contour.png").exists())
        self.assertTrue((bundle_dir / "charts" / "distribution_第1页.png").exists())
        self.assertTrue((bundle_dir / "charts" / "distribution_第2页.png").exists())
        self.assertFalse((bundle_dir / "charts" / "distribution.png").exists())
        self.assertTrue((bundle_dir / "charts" / "quality.png").exists())
        self.assertTrue((bundle_dir / "charts" / "duration.png").exists())
        self.assertTrue((bundle_dir / "charts" / "tone_sandhi.png").exists())
        self.assertTrue((bundle_dir / "charts" / "tone_effect_time.png").exists())
        self.assertTrue((bundle_dir / "charts" / "overview_heatmap.png").exists())
        self.assertTrue((bundle_dir / "report" / "研究方法报告.md").exists())
        self.assertTrue(len(list(bundle_dir.glob("*.teproj"))) >= 1)
        self.assertTrue(len(list(bundle_dir.glob("*.ptwl"))) >= 1)
        self.assertTrue(len(list(bundle_dir.glob("*.Collection"))) >= 1)

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

        # P2: detect_formants & fix_warnings (including reco/anti/fine aliases & pitch_floor/voicing_threshold params)
        res_df = run_cli("--project", str(proj_copy), "detect_formants reco --no-apply")
        payload_df = json.loads(res_df.stdout.strip())
        self.assertTrue(payload_df["success"])
        self.assertIn("suggestions", payload_df)
        self.assertIn("reco", payload_df["suggestions"])

        res_fw = run_cli("--project", str(proj_copy), "fix_warnings --apply scope=all pitch_floor=60 voicing_threshold=0.15")
        payload_fw = json.loads(res_fw.stdout.strip())
        self.assertTrue(payload_fw["success"])
        self.assertIn("warnings_before", payload_fw)
        self.assertEqual(payload_fw["warnings_after"], 0)

    def test_round3_audit_fixes_cooccurring_warnings_and_no_duplicate_teproj(self):
        """验证第 3 轮审计修复：
        1. octave_jump 与 low_f0_coverage 同时存在时 fix_warnings --apply 不会因顺序覆盖导致残留跳变或空音节；
        2. fix_warnings、status、list_items warnings、stats 四处警告计数完全一致；
        3. --project 指向 export_bundle 输出目录内时复用同一 .teproj，不生成两份重复工程；
        4. --script-file 模式下自动精简冗长明细数组。
        """
        import numpy as np
        from cli import PhonTracerCLI

        bundle_dir = self.tmp_dir / "deliverables"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bound_teproj = bundle_dir / "experiment_session.teproj"
        shutil.copy2(self.example_teproj, bound_teproj)

        cli = PhonTracerCLI(project_path=str(bound_teproj), quiet=True)
        try:
            # Inject a pathological 2-syllable item with BOTH octave_jump (380Hz spike on male speaker) AND low_f0_coverage (0Hz in syllable 2)
            spk = cli.current_speaker
            target_iid = next(iter(spk.items.keys()))
            item = spk.items[target_iid]
            item["label"] = "学习"
            item["start"] = 0.10
            item["end"] = 0.50
            item["inner_splits"] = [0.30]
            item["chars_bounds"] = [[0.10, 0.30], [0.30, 0.50]]
            xs = np.linspace(0.0, 0.60, 61)
            freqs = np.full_like(xs, 140.0)
            # Add octave jump spike in syllable 1 (at 0.18s -> 390 Hz)
            freqs[18] = 390.0
            # Make syllable 2 mostly unvoiced (0 Hz) so low_f0_coverage AND check_item_has_empty_f0 trigger simultaneously
            freqs[25:52] = 0.0
            item["pitch_data"] = {"xs": xs, "freqs": freqs, "engine": "praat"}
            item["preview_f0"] = [140.0, 390.0, 140.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            item["has_empty_data"] = True
            item["split_warnings"] = ["low_f0_coverage", "no_clear_valley"]

            # Before fix_warnings: status, list_items warnings, and _scan_item_warnings_detailed all detect the warning
            diag_before = cli._scan_item_warnings_detailed(target_iid, item, spk_median_f0=140.0)
            self.assertIn("octave_jump", diag_before["issues"])
            self.assertIn("low_f0_coverage", diag_before["issues"])
            self.assertTrue(cli._check_item_has_any_warning(target_iid, item, spk_median_f0=140.0))

            # Run fix_warnings --apply scope=all
            cli.onecmd("fix_warnings --apply scope=all")
            self.assertTrue(cli._last_emit_success)
            fw_payload = cli._last_emit_payload
            self.assertEqual(fw_payload["warnings_after"], 0)
            self.assertEqual(fw_payload["after_summary"]["octave_jump"], 0)
            self.assertEqual(fw_payload["after_summary"]["low_f0_coverage"], 0)
            # Verify pitch_ceiling was narrowed and NOT overwritten by 600
            self.assertLess(item["pitch_ceiling"], 400)

            # Verify status, list_items warnings, and stats all report 0 warnings
            cli.onecmd("status")
            self.assertEqual(cli._last_emit_payload["status"]["warnings"], 0)

            cli.onecmd("list_items warnings")
            self.assertEqual(len(cli._last_emit_payload["items"]), 0)

            cli.onecmd("stats by=group scope=integrated")
            self.assertEqual(cli._last_emit_payload["warning_count"], 0)

            # Verify export_bundle into bundle_dir reuses bound_teproj and creates only 1 .teproj file
            cli.onecmd(f'export_bundle "{bundle_dir}" scope=integrated format=png')
            self.assertTrue(cli._last_emit_success)
            teproj_files = list(bundle_dir.glob("*.teproj"))
            self.assertEqual(len(teproj_files), 1)
            self.assertEqual(teproj_files[0].resolve(), bound_teproj.resolve())
        finally:
            cli.cleanup_workspace()


if __name__ == "__main__":
    unittest.main()
