import unittest
from unittest.mock import MagicMock, patch
import os
import json
import zipfile
import tempfile
import shutil
from toolkit import ToolkitApp, parse_script_metadata_comments

class TestToolkitTeprojTab(unittest.TestCase):
    def setUp(self):
        # Create a mock temporary project file (.teproj)
        self.temp_dir = tempfile.mkdtemp()
        self.teproj_path = os.path.join(self.temp_dir, "test_project.teproj")
        
        # Create dummy project.json structure
        self.project_data = {
            "version": "1.0",
            "active_speaker_id": "spk_1",
            "speakers": {
                "spk_1": {
                    "id": "spk_1",
                    "name": "测试发音人",
                    "tab_mode": "单条长音频",
                    "last_params": {
                        "f0_min": 80,
                        "f0_max": 500,
                        "pts": 11,
                        "method": "ac"
                    },
                    "long_audio_path": "audio/spk_1_long_audio.wav",
                    "items": {
                        "item_1": {
                            "label": "声调一",
                            "start": 0.1,
                            "end": 0.5,
                            "pitch_data_file": "data/spk_1_item_1.npz"
                        }
                    }
                }
            }
        }
        
        # Package into a zip (teproj)
        with zipfile.ZipFile(self.teproj_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(self.project_data))
            zf.writestr("audio/spk_1_long_audio.wav", b"dummy audio data")
            zf.writestr("data/spk_1_item_1.npz", b"dummy npz data")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch('toolkit.ctk.CTk')
    def test_format_project_preview(self, mock_ctk):
        # Initialize app with mocked Tkinter to avoid starting GUI window
        app = MagicMock(spec=ToolkitApp)
        app.font_title = MagicMock()
        app.font_main = MagicMock()
        
        # Test formatting helper methods
        with zipfile.ZipFile(self.teproj_path, 'r') as zf:
            namelist = zf.namelist()
            
        preview_text = ToolkitApp.format_project_preview(app, self.project_data, namelist)
        
        self.assertIn("PHONTRACER", preview_text)
        self.assertIn(".teproj", preview_text)
        self.assertIn("1.0", preview_text)
        self.assertIn("spk_1", preview_text)
        self.assertIn("audio/spk_1_long_audio.wav", preview_text)
        self.assertIn("0.100s", preview_text)
        self.assertIn("0.500s", preview_text)
        self.assertIn("audio/", preview_text)
        self.assertIn("data/", preview_text)

    @patch('toolkit.ctk.CTk')
    @patch('toolkit.messagebox')
    @patch('toolkit.filedialog')
    def test_convert_project_to_zip(self, mock_dialog, mock_msgbox, mock_ctk):
        # Mock dialogs
        zip_output_path = os.path.join(self.temp_dir, "output.zip")
        mock_dialog.asksaveasfilename.return_value = zip_output_path
        
        app = MagicMock(spec=ToolkitApp)
        app.loaded_teproj_path = self.teproj_path
        
        # Call convert_project_to_zip
        ToolkitApp.convert_project_to_zip(app)
        
        # Verify file copied to zip path
        self.assertTrue(os.path.exists(zip_output_path))
        self.assertTrue(zipfile.is_zipfile(zip_output_path))
        
        with zipfile.ZipFile(zip_output_path, 'r') as zf:
            self.assertIn("project.json", zf.namelist())
            
        mock_msgbox.showinfo.assert_called_once()

    @patch('toolkit.ctk.CTk')
    @patch('toolkit.messagebox')
    def test_display_script_result_deduplication(self, mock_msgbox, mock_ctk):
        # We need a mock of ToolkitApp
        app = MagicMock(spec=ToolkitApp)
        app.selected_script_id = "test_id"
        app.local_scripts = [{"id": "test_id", "name": "Test Script", "description": "Desc", "type": "chart"}]
        app.get_script_output_dir = MagicMock(return_value=self.temp_dir)
        app.append_script_log = MagicMock()
        app._safe_script_output_name = MagicMock(side_effect=lambda name, fallback: name or fallback)
        app._unique_path = MagicMock(side_effect=lambda folder, filename: os.path.join(folder, filename))
        app.show_script_figure_at = MagicMock()
        
        # Mock configure_matplotlib_chinese_font and modules.script_api classes
        with patch('modules.script_api.configure_matplotlib_chinese_font') as mock_font:
            # Create mock FigureResult objects
            fig_png = MagicMock()
            fig_res_png = MagicMock()
            fig_res_png.fig = fig_png
            fig_res_png.filename = "chart.png"
            fig_res_png.title = "Chart Title"
            
            fig_svg = MagicMock()
            fig_res_svg = MagicMock()
            fig_res_svg.fig = fig_svg
            fig_res_svg.filename = "chart.svg"
            fig_res_svg.title = "Chart Title"

            # Execute display_script_result
            from modules.script_api import FigureResult
            with patch('toolkit.isinstance', side_effect=lambda obj, cls: True if (cls == FigureResult) else isinstance(obj, cls)):
                ToolkitApp.display_script_result(app, [fig_res_png, fig_res_svg])
            
            # Verify fig.savefig was called on both
            fig_png.savefig.assert_called_once()
            fig_svg.savefig.assert_called_once()
            
            # Verify that self.script_figure_results only contains one preview item (the PNG one)
            self.assertEqual(len(app.script_figure_results), 1)
            self.assertEqual(app.script_figure_results[0]["filename"], "chart.png")
            self.assertEqual(app.script_figure_results[0]["ext"], ".png")
            
            # Verify the preview_path is the same as output_path (no temp preview files)
            self.assertEqual(app.script_figure_results[0]["preview_path"], app.script_figure_results[0]["output_path"])


class TestScriptEditorMetadata(unittest.TestCase):
    def test_parse_script_metadata_comments(self):
        code = """# 脚本名称：F0 声调均值曲线
# 功能说明：按声调分组绘制 11 点对齐 T 值均值曲线
def run(ctx):
    pass
"""
        metadata = parse_script_metadata_comments(code)

        self.assertEqual(metadata["name"], "F0 声调均值曲线")
        self.assertEqual(metadata["description"], "按声调分组绘制 11 点对齐 T 值均值曲线")

    def test_parse_script_metadata_comments_gracefully_falls_back(self):
        metadata = parse_script_metadata_comments("def run(ctx):\n    pass\n")

        self.assertEqual(metadata["name"], "")
        self.assertEqual(metadata["description"], "")


class TestToolkitStartupWordlist(unittest.TestCase):
    def test_ptwl_startup_path_normalization_and_detection(self):
        files = ToolkitApp._normalize_startup_files(['"file:///C:/Users/Sager/Desktop/wordlist.ptwl"'])
        self.assertEqual(files, [os.path.normpath("C:/Users/Sager/Desktop/wordlist.ptwl")])
        found = ToolkitApp._find_startup_wordlist_file(files)
        self.assertEqual(found, os.path.abspath(os.path.normpath("C:/Users/Sager/Desktop/wordlist.ptwl")))

    def test_installer_registers_ptwl_icon_and_toolkit_open_command(self):
        source = open("installer.iss", "r", encoding="utf-8").read()
        self.assertIn('#define WordlistAssocExt ".ptwl"', source)
        self.assertIn('#define WordlistAssocKey "PhonTracer.Wordlist"', source)
        self.assertIn('application/vnd.phontracer.wordlist', source)
        self.assertIn('_internal\\assets\\ptwl.ico', source)
        self.assertIn('{#WordlistAssocKey}\\shell\\open\\command', source)
        self.assertIn('{#ToolkitExeName}"" ""%1', source)
        self.assertTrue(os.path.exists(os.path.join("assets", "ptwl.ico")))


if __name__ == '__main__':
    unittest.main()
