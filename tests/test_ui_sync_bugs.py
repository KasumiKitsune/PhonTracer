import sys
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.modules['flet'] = MagicMock()
sys.modules['sounddevice'] = MagicMock()
sys.modules['parselmouth'] = MagicMock()
sys.modules['customtkinter'] = MagicMock()

import parselmouth

sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()

mock_img = MagicMock()
type(mock_img).size = PropertyMock(return_value=(100, 100))

mock_pil_image = MagicMock()
mock_pil_image.open.return_value = mock_img
sys.modules['PIL'] = MagicMock()
sys.modules['PIL.Image'] = mock_pil_image
sys.modules['PIL.ImageTk'] = MagicMock()

from modules.app import PhoneticsApp
from modules.project_tree import ProjectTreePanel
from modules.spectrogram_panel import SpectrogramPanel
from modules.data_utils import get_export_text_for_item
import numpy as np

class TestUISyncBugs(unittest.TestCase):
    def setUp(self):
        self.root_mock = MagicMock()

        with patch('PIL.Image.open', return_value=mock_img):
            with patch.object(PhoneticsApp, 'setup_icons'), patch.object(PhoneticsApp, 'setup_ui'): # completely bypass UI setup
                self.app = PhoneticsApp(self.root_mock)
                self.app.icons = {}
                self.app.tk_icons = {}
                self.app.tree_panel = MagicMock()
                self.app.spectrogram_panel = MagicMock()

        self.item_id = "test_item_1"
        self.mock_snd = MagicMock()
        self.mock_snd.get_total_duration.return_value = 10.0

        self.mock_pitch = MagicMock()
        self.mock_pitch.xs.return_value = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        self.mock_pitch.selected_array = {'frequency': np.array([0.0, 120.0, 130.0, 0.0, 0.0])}

        self.item = {
            'label': 'A',
            'group': 'Group1',
            'start': 0.1,
            'end': 0.9,
            'macro_start': 0.0,
            'macro_end': 1.0,
            'snd': self.mock_snd,
            'pitch': self.mock_pitch,
            'chars_bounds': [[0.1, 0.9]],
            'inner_splits': [],
            'pitch_floor': 75,
            'pitch_ceiling': 600,
            'voicing_threshold': 0.25,
            'preview_f0': [100.0] * 11,
            'has_empty_data': False
        }

        self.app.items[self.item_id] = self.item
        self.app.spectrogram_panel.current_item = self.item

    def test_on_spectrogram_time_changed_clears_cache(self):
        """Test that manually changing time clears the preview cache"""
        self.assertTrue('preview_f0' in self.item)
        self.assertTrue('has_empty_data' in self.item)

        self.app.on_spectrogram_time_changed(self.item)

        self.assertFalse('preview_f0' in self.item)
        self.assertFalse('has_empty_data' in self.item)

    def test_apply_manual_time_sets_flag(self):
        """Test that applying manual time bounds sets is_manual_edited flag"""
        # Since spectrogram_panel was mocked out, we instantiate one without actually drawing it
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root_mock, {}, None, None, None)
            panel.current_item = self.item
            panel.var_t_start = MagicMock()
            panel.var_t_end = MagicMock()
            panel.var_t_start.get.return_value = "0.2"
            panel.var_t_end.get.return_value = "0.8"
            panel.plot_item_spectrogram = MagicMock()
            panel.update_ui_times = MagicMock()

            panel.apply_manual_time()

        self.assertEqual(self.item['start'], 0.2)
        self.assertEqual(self.item['end'], 0.8)
        self.assertTrue(self.item.get('is_manual_edited'))

    def test_export_updates_pitch_if_params_changed(self):
        """Test that get_export_text_for_item forces a recalculation if pitch bounds changed"""
        # Change the app state params
        self.item['path'] = 'dummy.wav'

        # Original pitch info
        self.assertEqual(self.item['pitch_floor'], 75)

        # Mock the return values for Pitch recalculation logic
        mock_snd_inst = MagicMock()
        mock_snd_inst.extract_part.return_value = mock_snd_inst
        mock_snd_inst.to_pitch_ac.return_value = self.mock_pitch
        self.item['snd'] = mock_snd_inst

        # get_export_text_for_item uses the passed in pitch_floor and pitch_ceiling (which mimic UI)
        res = get_export_text_for_item(self.item, 1, 11, pitch_floor=100.0, pitch_ceiling=500.0, voicing_threshold=0.25)

        # The lazy load should regenerate pitch
        self.assertEqual(self.item['pitch_floor'], 100.0)
        self.assertEqual(self.item['pitch_ceiling'], 500.0)

if __name__ == '__main__':
    unittest.main()
