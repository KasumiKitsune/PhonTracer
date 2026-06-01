import unittest
from unittest.mock import MagicMock, patch
import os
import json
import zipfile
import tempfile
import shutil
from toolkit import ToolkitApp

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

if __name__ == '__main__':
    unittest.main()
