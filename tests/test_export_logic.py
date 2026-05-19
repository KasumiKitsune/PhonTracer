import unittest
import customtkinter as ctk
import sys
sys.path.append('.')
from modules.app import PhoneticsApp
from unittest.mock import patch, MagicMock

class TestExportLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):

        cls.root = ctk.CTk()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()


    @patch('sounddevice.stop')
    def test_multi_speaker_export_prep(self, mock_stop):
        def mock_setup_icons(self):
            self.icons = {}
            self.tk_icons = {}
        with patch.object(PhoneticsApp, 'setup_icons', mock_setup_icons):
            app = PhoneticsApp(self.root)
        s1 = app.speaker_manager.add_speaker('Speaker 1')
        s2 = app.speaker_manager.add_speaker('Speaker 2')
        self.assertTrue(hasattr(app.tree_panel, '_show_multi_speaker_export_dialog'))
        self.assertTrue(hasattr(app.tree_panel, '_do_export_preparation'))
        self.assertTrue(hasattr(app.tree_panel, '_export_integrated'))

if __name__ == '__main__':
    unittest.main()
