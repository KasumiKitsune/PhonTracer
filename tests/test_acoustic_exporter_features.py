import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import customtkinter as ctk
from modules.acoustic_exporter import AcousticChartExportDialog
from tests.shared_root import get_shared_root

class TestAcousticExporterFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_shared_root()

    def test_exporter_dialog_init_and_variables(self):
        # Mock tree panel and active speaker
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        project_tree._get_items_by_group_for_dict.return_value = [("Group1", ["item1"])]
        
        speaker = MagicMock()
        speaker.name = "Speaker 1"
        speaker.items = {
            "item1": {
                'label': 'ma',
                'group': 'Group1',
                'start': 0.1,
                'end': 0.9,
                'snd': MagicMock(),
                'pitch': MagicMock(),
                'warnings': []
            }
        }
        
        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker
        
        dummy_data = [{
            'speaker_name': 'Speaker 1',
            'group': 'Group1',
            'label': 'ma',
            'total_dur': 0.8,
            'syl_data': [(0.8, [100.0, 110.0])],
            'normalized_syl_data': [(0.8, [2.0, 2.5])],
            'raw_xs': np.array([0, 1]),
            'raw_freqs': np.array([100.0, 110.0]),
            'normalized_raw_freqs': np.array([2.0, 2.5]),
            'active_ratio': 1.0,
            'warnings': [],
            'raw_item': {}
        }]
        
        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=dummy_data), \
             patch.object(AcousticChartExportDialog, 'update_preview'):
            
            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )
            
            # Check variables
            self.assertEqual(dlg.var_chart_type.get(), "contour")
            self.assertFalse(dlg.sort_by_count)
            self.assertIn("Group1", dlg.available_groups)
            self.assertTrue(dlg.group_checkbox_vars["Group1"].get())
            
            # Test group sorting toggle
            dlg._toggle_groups_sorting()
            self.assertTrue(dlg.sort_by_count)
            dlg._toggle_groups_sorting()
            self.assertFalse(dlg.sort_by_count)
            
            # Test all/none selection
            dlg._select_all_groups()
            self.assertTrue(dlg.group_checkbox_vars["Group1"].get())
            dlg._reverse_groups()
            self.assertFalse(dlg.group_checkbox_vars["Group1"].get())
            
            # Clean up top level window
            dlg.destroy()

    def test_overview_heatmap_plot(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}
        
        speaker = MagicMock()
        speaker.name = "Speaker 1"
        
        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker
        
        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_data': [(0.8, [100.0, 110.0, 120.0, 130.0, 140.0])],
                'normalized_syl_data': [(0.8, [1.0, 2.0, 3.0, 4.0, 5.0])],
            },
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group2',
                'label': 'ba',
                'syl_data': [(0.8, [110.0, 120.0, 130.0, 140.0, 150.0])],
                'normalized_syl_data': [(0.8, [1.5, 2.5, 3.5, 4.5, 5.0])],
            }
        ]
        
        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=dummy_data), \
             patch.object(AcousticChartExportDialog, 'update_preview'):
            
            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )
            
            # Generate heatmap plot
            dlg.combo_overview_metric = MagicMock()
            dlg.combo_overview_metric.get.return_value = "均值热图 (Mean Map)"
            
            fig = dlg._plot_tone_overview_heatmap(dummy_data, "group", "T 值")
            self.assertIsNotNone(fig)
            
            dlg.destroy()

if __name__ == '__main__':
    unittest.main()
