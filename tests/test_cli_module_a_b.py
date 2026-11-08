import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from modules.version import APP_NAME, __version__


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


class TestCliModuleAB(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_te_agent1_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_release_surface_version_and_unknown_option(self):
        res_ver = run_cli("--version")
        self.assertEqual(res_ver.returncode, 0)
        self.assertEqual(res_ver.stdout.strip(), f"{APP_NAME} CLI {__version__}")

        res_unk = run_cli("--definitely-unknown")
        self.assertEqual(res_unk.returncode, 2)
        self.assertIn("Unknown startup option", res_unk.stdout)

    def test_help_and_lang_options(self):
        res_en = run_cli("--help")
        self.assertEqual(res_en.returncode, 0)
        for token in (
            "--project",
            "--workspace",
            "--script-file",
            "--batch-cmds",
            "--lang",
            "--quiet",
            "--json",
            "speakers",
            "add_speaker",
            "switch_speaker",
            "remove_speaker",
            "rename_speaker",
            "stats",
            "export_bundle",
            "tool_wordlist_convert",
            "detect_formants",
            "fix_warnings",
        ):
            self.assertIn(token, res_en.stdout)

        res_zh = run_cli("--lang", "zh", "--help")
        self.assertEqual(res_zh.returncode, 0)
        self.assertIn("全局启动选项", res_zh.stdout)
        self.assertIn("发音人管理", res_zh.stdout)

    def test_agent_guide_stateful_best_practices_and_workflows(self):
        res = run_cli("agent_guide")
        self.assertEqual(res.returncode, 0)
        payload = json.loads(res.stdout.strip())
        self.assertTrue(payload["success"])
        self.assertIn("stateful_session_best_practices", payload)
        self.assertIn("multi_speaker_workflow", payload["task_paths"])
        self.assertIn("batch_audio_with_wordlist_workflow", payload["task_paths"])
        for cmd_name in (
            "speakers",
            "add_speaker",
            "switch_speaker",
            "remove_speaker",
            "rename_speaker",
            "stats",
            "export_bundle",
            "tool_wordlist_convert",
            "detect_formants",
            "fix_warnings",
        ):
            self.assertIn(cmd_name, payload["supported_commands"])

    def test_subcommand_help_introspection(self):
        for query in (
            "export --help",
            "export -h",
            "export ?",
            "help export",
            "help speaker",
            "help speakers",
            "apply_wordlist ?",
            "load_batch ?",
            "stats ?",
            "export_bundle ?",
        ):
            res = run_cli(query)
            self.assertEqual(res.returncode, 0, msg=f"Failed on query: {query}")
            payload = json.loads(res.stdout.strip())
            self.assertTrue(payload["success"], msg=f"Failed payload on {query}")
            self.assertIn("command", payload)
            self.assertIn("syntax", payload)

        export_help = json.loads(run_cli("export ?").stdout.strip())
        for fmt in (
            "xlsx",
            "txt",
            "textgrid",
            "ptwl",
            "contour",
            "density",
            "distribution",
            "quality",
            "overview_heatmap",
            "formant_space",
            "f1_f2",
            "duration",
            "trend",
        ):
            self.assertIn(fmt, export_help["supported_formats"])
        for opt_key in (
            "scale",
            "groupby",
            "facet",
            "normalization",
            "chart_group",
            "format",
            "target",
        ):
            self.assertIn(opt_key, export_help["options"])

    def test_export_self_correction_hints(self):
        # 1. Invalid format check precedes empty-items check
        res_bad_fmt = run_cli("export invalid_fmt out.png")
        payload_bad = json.loads(res_bad_fmt.stdout.strip())
        self.assertFalse(payload_bad["success"])
        self.assertIn("supported_formats", payload_bad)
        self.assertIn("hint", payload_bad)

        # 2. Valid format on empty project emits actionable hint
        out_xlsx = self.tmp_dir / "empty.xlsx"
        res_empty = run_cli(f"export xlsx {out_xlsx}")
        payload_empty = json.loads(res_empty.stdout.strip())
        self.assertFalse(payload_empty["success"])
        self.assertIn("load_long / load_batch + apply_wordlist", payload_empty["hint"])

    def test_bom_sanitization_and_pure_stdout(self):
        res_arg = run_cli("\ufeffstatus")
        self.assertEqual(res_arg.returncode, 0)
        payload_arg = json.loads(res_arg.stdout.strip())
        self.assertTrue(payload_arg["success"])
        self.assertIn("status", payload_arg)

        res_pipe = run_cli(input_text="\ufeffstatus\n\ufeffspeakers\n")
        self.assertEqual(res_pipe.returncode, 0)
        lines = [ln for ln in res_pipe.stdout.strip().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertIn("status", json.loads(lines[0]))
        self.assertIn("speakers", json.loads(lines[1]))

    def test_project_and_workspace_persistence(self):
        proj_path = self.tmp_dir / "session.teproj"
        res1 = run_cli("--project", str(proj_path), "add_speaker Alice gender=female")
        self.assertEqual(res1.returncode, 0)
        self.assertTrue(proj_path.exists())

        res2 = run_cli("--project", str(proj_path), "speakers")
        self.assertEqual(res2.returncode, 0)
        speakers_data = json.loads(res2.stdout.strip())["speakers"]
        self.assertTrue(any(s["name"] == "Alice" for s in speakers_data))

        ws_dir = self.tmp_dir / "user_ws"
        res_ws1 = run_cli("--workspace", str(ws_dir), "add_speaker Bob gender=male")
        self.assertEqual(res_ws1.returncode, 0)
        self.assertTrue((ws_dir / "project.json").exists())

        res_ws2 = run_cli("--workspace", str(ws_dir), "speakers")
        self.assertEqual(res_ws2.returncode, 0)
        ws_speakers = json.loads(res_ws2.stdout.strip())["speakers"]
        self.assertTrue(any(s["name"] == "Bob" for s in ws_speakers))
        self.assertTrue(ws_dir.exists())

    def test_script_file_and_fail_fast(self):
        proj_path = self.tmp_dir / "batch.teproj"
        script_ok = self.tmp_dir / "ok.txt"
        script_ok.write_text(
            "\ufeff# Comment line\n\nadd_speaker S1 gender=female\nrename_speaker S1 S1_Renamed\nspeakers\n",
            encoding="utf-8",
        )
        res_ok = run_cli("--project", str(proj_path), "--script-file", str(script_ok))
        self.assertEqual(res_ok.returncode, 0)
        lines = [json.loads(ln) for ln in res_ok.stdout.strip().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 3)
        self.assertTrue(any(s["name"] == "S1_Renamed" for s in lines[-1]["speakers"]))

        script_fail = self.tmp_dir / "fail.txt"
        script_fail.write_text(
            "# Step 1 ok\nstatus\n# Step 2 fails\nexport invalid_format_xyz out.png\n# Step 3 skipped\nstatus\n",
            encoding="utf-8",
        )
        res_fail = run_cli("--batch-cmds", str(script_fail))
        self.assertNotEqual(res_fail.returncode, 0)
        fail_lines = [json.loads(ln) for ln in res_fail.stdout.strip().splitlines() if ln.strip()]
        self.assertFalse(fail_lines[-1]["success"])
        self.assertEqual(fail_lines[-1]["failed_line"], 4)
        self.assertEqual(fail_lines[-1]["failed_command"], "export invalid_format_xyz out.png")


if __name__ == "__main__":
    unittest.main()
