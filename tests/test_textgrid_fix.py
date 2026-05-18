import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.modules['flet'] = MagicMock()
sys.modules['sounddevice'] = MagicMock()
sys.modules['parselmouth'] = MagicMock()
sys.modules['customtkinter'] = MagicMock()
sys.modules['windnd'] = MagicMock()

import numpy as np

# Mock UI components
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.pyplot'] = MagicMock()

from modules.audio_core import process_single_long_word
from modules.project_tree import ProjectTreePanel

class TestTextGridFix(unittest.TestCase):
    @patch('modules.audio_core.long_process_worker')
    def test_process_single_long_word_mapping(self, mock_worker):
        # Mock long_process_worker output
        mock_worker.return_value = {
            'ms': 1.0,
            'me': 2.0,
            'mis': 1.2,
            'mie': 1.8,
            'raw_s': 1.1,
            'raw_e': 1.9,
            'inner_splits': [1.5],
            'chars_bounds': [[1.2, 1.5], [1.5, 1.8]],
            'has_empty_data': False,
            'success': True
        }

        # Run process_single_long_word
        snd_vals = np.array([0.0])
        res = process_single_long_word(
            snd_vals, 16000, "test_word", 1.0, 2.0,
            {'db': 60.0, 'skip_front': 0.0}, False,
            np.array([1.0, 2.0]), np.array([100.0, 100.0])
        )

        # Check that it translated keys correctly
        self.assertTrue(res['success'])
        self.assertEqual(res['label'], "test_word")
        self.assertEqual(res['start'], 1.2)
        self.assertEqual(res['end'], 1.8)
        self.assertEqual(res['raw_start'], 1.1)
        self.assertEqual(res['raw_end'], 1.9)
        self.assertEqual(res['inner_splits'], [1.5])
        self.assertEqual(res['chars_bounds'], [[1.2, 1.5], [1.5, 1.8]])

    @patch('textgrid.TextGrid')
    @patch('textgrid.IntervalTier')
    @patch('os.makedirs')
    def test_export_textgrid_batch_naming(self, mock_makedirs, mock_tier, mock_tg):
        # Instantiate a mock panel
        parent = MagicMock()
        icons = {}
        mock_snd1 = MagicMock()
        mock_snd1.get_total_duration.return_value = 1.0
        mock_snd2 = MagicMock()
        mock_snd2.get_total_duration.return_value = 1.0
        items_dict = {
            'item1': {
                'path': 'C:/audios/ma.wav',
                'label': 'ma',
                'group': '阴平',
                'start': 0.1,
                'end': 0.9,
                'inner_splits': [],
                'chars_bounds': [[0.1, 0.9]],
                'snd': mock_snd1
            },
            'item2': {
                'path': 'C:/audios/ba.wav',
                'label': 'ba',
                'group': '阴平',
                'start': 0.2,
                'end': 0.8,
                'inner_splits': [],
                'chars_bounds': [[0.2, 0.8]],
                'snd': mock_snd2
            }
        }
        app_params = {}
        
        # We Mock setup_ui to avoid customtkinter issues
        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(parent, icons, items_dict, app_params, MagicMock(), MagicMock())
            panel.items = items_dict
            
            # Setup a mock tree structure: [ (group_name, [child_iids]) ]
            tree_structure = [('阴平', ['item1', 'item2'])]
            
            # Mock write method on TextGrid instance
            mock_tg_instance = MagicMock()
            mock_tg.return_value = mock_tg_instance
            
            # Call _export_textgrid_batch
            panel._export_textgrid_batch('C:/output_dir', tree_structure=tree_structure)
            
            # Verify that two separate TextGrids were created using filenames and NOT the group name
            calls = mock_tg_instance.write.call_args_list
            written_paths = [call[0][0].replace('\\', '/') for call in calls]
            
            # They should be saved as ma.TextGrid and ba.TextGrid
            self.assertTrue(any('ma.TextGrid' in path for path in written_paths))
            self.assertTrue(any('ba.TextGrid' in path for path in written_paths))
            # And they should NOT be saved under the group name (e.g. 阴平.TextGrid)
            self.assertFalse(any('阴平.TextGrid' in path for path in written_paths))

    def test_find_minimum_intensity_valley_fallback(self):
        from modules.audio_core import find_minimum_intensity_valley
        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 1.0
        
        # When an exception happens, it should gracefully fall back to the reference time
        mock_snd.extract_part.side_effect = Exception("Mocked error")
        res = find_minimum_intensity_valley(mock_snd, 0.5)
        self.assertEqual(res, 0.5)

    @patch('textgrid.TextGrid.fromFile')
    def test_cli_apply_textgrid_command(self, mock_from_file):
        from cli import PhonTracerCLI
        import numpy as np
        
        # Mock TextGrid structure
        mock_word_interval = MagicMock()
        mock_word_interval.minTime = 0.1
        mock_word_interval.maxTime = 0.9
        mock_word_interval.mark = "test_word"
        
        mock_words_tier = MagicMock()
        mock_words_tier.name = "words"
        mock_words_tier.__iter__.return_value = [mock_word_interval]
        
        mock_tg_instance = MagicMock()
        mock_tg_instance.tiers = [mock_words_tier]
        mock_from_file.return_value = mock_tg_instance
        
        cli = PhonTracerCLI()
        cli.mode = 'long'
        
        mock_pitch = MagicMock()
        mock_pitch.xs.return_value = np.array([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
        mock_pitch.selected_array = {'frequency': np.array([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 190.0])}
        mock_pitch.get_value_at_time.return_value = 150.0
        
        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 2.0
        mock_snd.to_pitch_ac.return_value = mock_pitch
        
        mock_part = MagicMock()
        mock_part.values = np.zeros((1, 1000))
        mock_part.sampling_frequency = 16000
        mock_snd.extract_part.return_value = mock_part
        
        cli.long_snd = mock_snd
        
        # Test applying textgrid command
        with patch('os.path.exists', return_value=True), \
             patch('modules.audio_core.process_single_long_word') as mock_process:
            mock_process.return_value = {
                'success': True,
                'label': 'test_word',
                'group': '导入内容',
                'start': 0.15,
                'end': 0.85,
                'inner_splits': [],
                'chars_bounds': [[0.15, 0.85]]
            }
            
            cli.do_apply_textgrid("fake_path.TextGrid")
            self.assertEqual(len(cli.items), 1)
            self.assertEqual(cli.items['item_0']['label'], 'test_word')
            self.assertEqual(cli.items['item_0']['start'], 0.15)

    @patch('textgrid.TextGrid.fromFile')
    def test_cli_apply_textgrid_grouping(self, mock_from_file):
        from cli import PhonTracerCLI
        import numpy as np
        
        # Mock TextGrid structure with groups tier
        mock_word_interval = MagicMock()
        mock_word_interval.minTime = 0.1
        mock_word_interval.maxTime = 0.9
        mock_word_interval.mark = "test_word"
        
        mock_group_interval = MagicMock()
        mock_group_interval.minTime = 0.0
        mock_group_interval.maxTime = 1.0
        mock_group_interval.mark = "MyCustomGroup"
        
        mock_words_tier = MagicMock()
        mock_words_tier.name = "words"
        mock_words_tier.__iter__.return_value = [mock_word_interval]
        
        mock_groups_tier = MagicMock()
        mock_groups_tier.name = "groups"
        mock_groups_tier.__iter__.return_value = [mock_group_interval]
        
        mock_tg_instance = MagicMock()
        mock_tg_instance.tiers = [mock_words_tier, mock_groups_tier]
        mock_from_file.return_value = mock_tg_instance
        
        cli = PhonTracerCLI()
        cli.mode = 'long'
        
        mock_pitch = MagicMock()
        mock_pitch.xs.return_value = np.array([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
        mock_pitch.selected_array = {'frequency': np.array([100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 190.0])}
        mock_pitch.get_value_at_time.return_value = 150.0
        
        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 2.0
        mock_snd.to_pitch_ac.return_value = mock_pitch
        
        mock_part = MagicMock()
        mock_part.values = np.zeros((1, 1000))
        mock_part.sampling_frequency = 16000
        mock_snd.extract_part.return_value = mock_part
        
        cli.long_snd = mock_snd
        
        # Test applying textgrid command with grouping
        with patch('os.path.exists', return_value=True), \
             patch('modules.audio_core.process_single_long_word') as mock_process:
            mock_process.return_value = {
                'success': True,
                'label': 'test_word',
                'group': 'MyCustomGroup',
                'start': 0.15,
                'end': 0.85,
                'inner_splits': [],
                'chars_bounds': [[0.15, 0.85]]
            }
            
            cli.do_apply_textgrid("fake_path.TextGrid")
            self.assertEqual(len(cli.items), 1)
            self.assertEqual(cli.items['item_0']['label'], 'test_word')
            self.assertEqual(cli.items['item_0']['group'], 'MyCustomGroup')
            self.assertIn('MyCustomGroup', cli.groups)

    @patch('textgrid.TextGrid')
    @patch('textgrid.IntervalTier')
    def test_export_textgrid_long_grouping(self, mock_tier, mock_tg):
        # Instantiate a mock panel
        parent = MagicMock()
        icons = {}
        items_dict = {
            'item1': {
                'label': 'ma',
                'group': 'MyCustomGroup',
                'start': 0.1,
                'end': 0.9,
                'inner_splits': [],
                'chars_bounds': [[0.1, 0.9]],
            }
        }
        app_params = {}
        
        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(parent, icons, items_dict, app_params, MagicMock(), MagicMock())
            panel.items = items_dict
            
            tree_structure = [('MyCustomGroup', ['item1'])]
            
            mock_tg_instance = MagicMock()
            mock_tg.return_value = mock_tg_instance
            
            # Run _export_textgrid_long
            panel._export_textgrid_long('C:/output.TextGrid', tree_structure=tree_structure)
            
            # Verify that group tier is added
            self.assertEqual(mock_tg_instance.append.call_count, 3) # words, groups, chars (because length of 'ma' > 1)

if __name__ == '__main__':
    unittest.main()
