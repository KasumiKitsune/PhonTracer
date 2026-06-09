import unittest
import os
import shutil
import tempfile
import json
from unittest.mock import MagicMock, patch
from cli import PhonTracerCLI
from modules.script_api import FigureResult, TableResult

class TestCliScriptCommands(unittest.TestCase):
    def setUp(self):
        self.cli = PhonTracerCLI()
        
        # Capture _emit outputs
        self.emitted = []
        def spy_emit(success=True, message="", **payload):
            self.emitted.append({
                "success": success,
                "message": message,
                "payload": payload
            })
        self.cli._emit = spy_emit

        # Mock speakers
        self.speaker = MagicMock()
        self.speaker.id = "spk_1"
        self.speaker.name = "TestSpeaker"
        self.speaker.items = {}
        
        self.cli.speaker_manager = MagicMock()
        self.cli.speaker_manager.get_active_speaker.return_value = self.speaker
        self.cli.speaker_manager.get_all_speakers.return_value = [self.speaker]
        self.cli.speaker_manager.speakers = {"spk_1": self.speaker}
        
        # Mock ProjectManager
        self.cli.project_manager = MagicMock()
        self.cli.project_manager.export_project.return_value = True
        self.cli.project_manager.save_to_workspace.return_value = True

    def test_list_scripts(self):
        self.cli.do_list_scripts("")
        self.assertEqual(len(self.emitted), 1)
        res = self.emitted[0]
        self.assertTrue(res["success"])
        self.assertIn("scripts", res["payload"])
        # Should contain default built-in scripts
        scripts = res["payload"]["scripts"]
        self.assertTrue(any(s["id"] == "builtin_f0_group_mean" for s in scripts))

    def test_script_info_success(self):
        self.cli.do_script_info("builtin_f0_group_mean")
        self.assertEqual(len(self.emitted), 1)
        res = self.emitted[0]
        self.assertTrue(res["success"])
        self.assertIn("script", res["payload"])
        script = res["payload"]["script"]
        self.assertEqual(script["id"], "builtin_f0_group_mean")
        self.assertIn("def run(ctx):", script["code"])

    def test_script_info_by_name(self):
        self.cli.do_script_info('"F1/F2 元音空间图 (示例)"')
        self.assertEqual(len(self.emitted), 1)
        res = self.emitted[0]
        self.assertTrue(res["success"])
        script = res["payload"]["script"]
        self.assertEqual(script["id"], "builtin_vowel_space")

    def test_script_info_not_found(self):
        self.cli.do_script_info("non_existent_script_id")
        self.assertEqual(len(self.emitted), 1)
        res = self.emitted[0]
        self.assertFalse(res["success"])
        self.assertIn("Script not found", res["payload"]["error"])

    def test_run_script_registered_success(self):
        # We need to mock build_dataset_snapshot to return some mock items
        mock_items = [{"speaker_name": "TestSpeaker", "group": "T1", "label": "ma"}]
        
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        dummy_res = FigureResult(fig, filename="test_chart.png", title="My Test Chart")
        
        def mock_run(ctx):
            ctx.log("running mock script")
            return dummy_res

        with patch("modules.script_api.build_dataset_snapshot", return_value=mock_items), \
             patch("modules.script_runner.run_custom_script", return_value=(dummy_res, ["running mock script"], None)):
            self.cli.do_run_script("builtin_f0_group_mean archive=false")
            
        self.assertEqual(len(self.emitted), 1)
        res = self.emitted[0]
        self.assertTrue(res["success"])
        self.assertIn("output_dir", res["payload"])
        self.assertIn("outputs", res["payload"])
        outputs = res["payload"]["outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["type"], "figure")
        self.assertEqual(outputs[0]["title"], "My Test Chart")
        
        # Clean up the output dir if it got created
        out_dir = res["payload"]["output_dir"]
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        plt.close(fig)

    def test_run_script_file_success(self):
        code_content = """def run(ctx):
    ctx.log("script file run")
    return ctx.table([[1, 2], [3, 4]], ["col1", "col2"], title="MyTable")
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code_content)
            temp_script_path = f.name

        try:
            with patch("modules.script_api.build_dataset_snapshot", return_value=[]):
                self.cli.do_run_script(f"{temp_script_path} archive=false timeout=10")

            self.assertEqual(len(self.emitted), 1)
            res = self.emitted[0]
            self.assertTrue(res["success"])
            self.assertIn("outputs", res["payload"])
            outputs = res["payload"]["outputs"]
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0]["type"], "table")
            self.assertEqual(outputs[0]["title"], "MyTable")

            # Clean up the output dir
            out_dir = res["payload"]["output_dir"]
            if os.path.exists(out_dir):
                shutil.rmtree(out_dir)
        finally:
            if os.path.exists(temp_script_path):
                os.remove(temp_script_path)

    def test_run_script_safety_violation(self):
        # A script that attempts a forbidden action (e.g. calling compile or eval or importing os)
        code_content = """def run(ctx):
    import os
    ctx.log(os.name)
    return None
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code_content)
            temp_script_path = f.name

        try:
            with patch("modules.script_api.build_dataset_snapshot", return_value=[]):
                self.cli.do_run_script(f"{temp_script_path} archive=false")

            self.assertEqual(len(self.emitted), 1)
            res = self.emitted[0]
            self.assertFalse(res["success"])
            self.assertIn("安全检查拦截", res["payload"]["error"])
        finally:
            if os.path.exists(temp_script_path):
                os.remove(temp_script_path)

    def test_run_script_syntax_error(self):
        # A script with syntax error
        code_content = """def run(ctx)
    invalid syntax here
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code_content)
            temp_script_path = f.name

        try:
            with patch("modules.script_api.build_dataset_snapshot", return_value=[]):
                self.cli.do_run_script(f"{temp_script_path} archive=false")

            self.assertEqual(len(self.emitted), 1)
            res = self.emitted[0]
            self.assertFalse(res["success"])
            self.assertIn("脚本语法错误", res["payload"]["error"])
        finally:
            if os.path.exists(temp_script_path):
                os.remove(temp_script_path)

    def test_run_script_custom_output_dir(self):
        code_content = """def run(ctx):
    return ctx.table([[1]], ["col1"], title="MyCustomDirTable")
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code_content)
            temp_script_path = f.name

        custom_dir = os.path.abspath("temp_custom_script_out")
        try:
            with patch("modules.script_api.build_dataset_snapshot", return_value=[]):
                self.cli.do_run_script(f"{temp_script_path} archive=false output_dir={custom_dir}")

            self.assertEqual(len(self.emitted), 1)
            res = self.emitted[0]
            self.assertTrue(res["success"])
            self.assertEqual(os.path.abspath(res["payload"]["output_dir"]), custom_dir)
            self.assertTrue(os.path.exists(os.path.join(custom_dir, "MyCustomDirTable.csv")))
        finally:
            if os.path.exists(temp_script_path):
                os.remove(temp_script_path)
            if os.path.exists(custom_dir):
                shutil.rmtree(custom_dir)

if __name__ == "__main__":
    unittest.main()
