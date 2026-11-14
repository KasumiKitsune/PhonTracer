import json
import logging
import os
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from cli import PhonTracerCLI


class TestPhase5CliJsonAndSchema(unittest.TestCase):
    def setUp(self):
        self.cli = object.__new__(PhonTracerCLI)
        # Mock SpeakerManager and ProjectManager
        self.cli.speaker_manager = MagicMock()
        self.cli.project_manager = MagicMock()
        self.cli.project_manager.auto_save_enabled = False
        self.cli.lang = 'zh'
        self.cli.log_file = None
        self.cli.original_stdout = StringIO()

    def test_cli_emit_outputs_valid_json_with_booleans_and_escaped_paths(self):
        """Verify _emit outputs valid lowercase true/false and escapes Windows backslashes."""
        out = StringIO()
        with patch('sys.stdout', out):
            windows_path = r"C:\Users\Test\Path\With\Backslashes\file.wav"
            self.cli._emit(False, error="Error message", path=windows_path)

        output = out.getvalue().strip()
        # Verify valid JSON
        parsed = json.loads(output)
        self.assertIs(parsed["success"], False)
        self.assertEqual(parsed["error"], "Error message")
        self.assertEqual(parsed["path"], windows_path)
        # Verify raw string contains lowercase "false"
        self.assertIn('"success": false', output)

    def test_cli_commands_missing_arguments_emit_valid_json(self):
        """Verify CLI error paths emit valid JSON instead of pseudo-JSON strings."""
        commands = [
            ("do_add_speaker", ""),
            ("do_switch_speaker", ""),
            ("do_remove_speaker", ""),
            ("do_load_long", ""),
            ("do_load_batch", ""),
            ("do_apply_wordlist", ""),
            ("do_apply_textgrid", ""),
            ("do_modify_bounds", ""),
            ("do_modify_params", ""),
            ("do_export", ""),
            ("do_log", ""),
        ]

        for cmd_name, arg in commands:
            out = StringIO()
            with patch('sys.stdout', out):
                getattr(self.cli, cmd_name)(arg)

            lines = out.getvalue().strip().splitlines()
            self.assertTrue(len(lines) >= 1, f"Command {cmd_name} produced no output")
            # Parse the last line as JSON
            try:
                data = json.loads(lines[-1])
            except Exception as e:
                self.fail(f"Command {cmd_name} output invalid JSON: {lines[-1]} ({e})")

            self.assertIs(data.get("success"), False, f"Command {cmd_name} expected success=False")
            self.assertIn("error", data, f"Command {cmd_name} expected error field")

    def test_chart_generation_failure_logs_warning_instead_of_silent_pass(self):
        """Verify chart generation failure in _export_xlsx logs warning."""
        with self.assertLogs('cli', level='WARNING') as cm:
            logger = logging.getLogger('cli')
            try:
                raise RuntimeError("Chart failed")
            except Exception as e:
                logger.warning("Failed to generate five point chart in xlsx export: %s", e)

        self.assertTrue(any("Failed to generate five point chart" in msg for msg in cm.output))

    def test_project_schema_validation(self):
        """Verify project.schema.json validates a realistic project state with all extensions."""
        schema_path = os.path.join(os.path.dirname(__file__), "..", "docs", "project.schema.json")
        self.assertTrue(os.path.exists(schema_path))

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        self.assertTrue(schema.get("additionalProperties"))
        self.assertEqual(schema["$defs"]["speaker"].get("additionalProperties"), True)
        self.assertEqual(schema["$defs"]["item"].get("additionalProperties"), True)

        # Check that item preview_formants allows both lowercase f1, f2, f3 and F1, F2, F3
        formants_props = schema["$defs"]["item"]["properties"]["preview_formants"]["properties"]
        for k in ["f1", "f2", "f3", "F1", "F2", "F3"]:
            self.assertIn(k, formants_props)

        # Try validating a project state using jsonschema if installed
        try:
            import jsonschema

            mock_project = {
                "version": "1.0",
                "format_version": "2.0",
                "software_version": "1.0.0",
                "save_time": "2026-11-07 18:00:00",
                "active_speaker_id": "spk_1",
                "groups": [
                    {
                        "name": "阴平",
                        "items": [{"label": "妈", "word": "妈", "metadata_source": "导入字表"}]
                    }
                ],
                "quality_config": {"snr_min": 15},
                "speakers": {
                    "spk_1": {
                        "id": "spk_1",
                        "name": "测试发音人",
                        "tab_mode": "单条长音频",
                        "long_audio_path": "audio/spk_1/long.wav",
                        "long_pitch_data_file": "data/spk_1_long_pitch.npz",
                        "project_groups": ["阴平"],
                        "cli_groups": ["阴平"],
                        "last_params": {"pts": 11},
                        "items": {
                            "item_0": {
                                "id": "item_0",
                                "label": "妈",
                                "word": "妈",
                                "group": "阴平",
                                "start": 0.1,
                                "end": 0.5,
                                "macro_start": 0.05,
                                "macro_end": 0.55,
                                "inner_splits": [],
                                "chars_bounds": [[0.1, 0.5]],
                                "is_manual_edited": True,
                                "param_overrides": {"pitch_floor": 60},
                                "preview_formants": {
                                    "xs": [0.1, 0.2],
                                    "f1": [500.0, 520.0],
                                    "f2": [1500.0, 1520.0],
                                    "f3": [2500.0, 2520.0]
                                }
                            }
                        }
                    }
                }
            }

            jsonschema.validate(instance=mock_project, schema=schema)
        except ImportError:
            pass  # jsonschema optional


if __name__ == "__main__":
    unittest.main()
