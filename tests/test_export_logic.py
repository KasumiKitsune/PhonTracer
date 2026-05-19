import unittest
import customtkinter as ctk
import sys
sys.path.append('.')
from modules.app import PhoneticsApp
from unittest.mock import patch, MagicMock

from tests.shared_root import get_shared_root

class TestExportLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_shared_root()

    @classmethod
    def tearDownClass(cls):
        pass

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

    @patch('xlsxwriter.Workbook')
    def test_export_integrated_xlsx_chart(self, mock_workbook_class):
        parent = get_shared_root()
        
        # Setup mock speakers
        speaker1 = MagicMock()
        speaker1.id = "sp1"
        speaker1.name = "Speaker 1"
        speaker1.items = {
            'item1': {
                'label': 'ma',
                'group': 'Group1',
                'start': 0.1,
                'end': 0.9,
                'inner_splits': [],
                'chars_bounds': [[0.1, 0.9]],
                'snd': MagicMock(),
                'pitch': MagicMock()
            }
        }
        
        # Instantiate ProjectTreePanel
        from modules.project_tree import ProjectTreePanel
        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(parent, {}, {}, {'pts': 2, 'pitch_floor': 75.0, 'pitch_ceiling': 600.0, 'voicing_threshold': 0.25}, MagicMock(), MagicMock())
            panel.num_rule_var = MagicMock()
            panel.num_rule_var.get.return_value = "continuous"
            
        mock_workbook = MagicMock()
        mock_workbook_class.return_value = mock_workbook
        
        # Mock _extract_syl_data
        panel._extract_syl_data = MagicMock(return_value=(0.8, [(0.8, [100.0, 110.0])]))
        
        # Run export
        panel._export_integrated('dummy_path.xlsx', 'xlsx', True, [speaker1])
        
        # Assert workbook was created, sheets added, and closed
        mock_workbook_class.assert_called_once_with('dummy_path.xlsx')
        self.assertTrue(mock_workbook.add_worksheet.called)
        self.assertTrue(mock_workbook.close.called)

if __name__ == '__main__':
    unittest.main()
