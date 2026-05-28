import pytest
from unittest.mock import MagicMock, patch
import logging
import sys
import numpy as np

from tests.shared_root import get_shared_root
from modules.project_tree import ProjectTreePanel

def test_logging_on_exception(caplog):
    # Setup mocks
    parent = get_shared_root()
    icons = {}
    items_dict = {
        'item1': {
            'path': 'invalid_path.wav',
            'label': 'Test Item',
            'group': 'Group 1'
        }
    }
    app_state_params = {'pts': 10}
    on_item_selected = MagicMock()
    on_clear_canvas = MagicMock()

    # We patch widget classes locally to run headlessly/mocked
    with patch('modules.project_tree.ttk.Style'), \
         patch('modules.project_tree.ctk.CTkFrame'), \
         patch('modules.project_tree.ctk.CTkLabel'), \
         patch('modules.project_tree.ttk.Treeview'), \
         patch('modules.project_tree.ctk.CTkScrollbar'), \
         patch('modules.project_tree.tk.Frame'), \
         patch('modules.project_tree.CTkReleaseButton'), \
         patch('modules.project_tree.ctk.StringVar'), \
         patch('modules.project_tree.ctk.CTkRadioButton'), \
         patch('modules.project_tree.ctk.CTkTextbox'), \
         patch('modules.project_tree.ctk.CTkSegmentedButton'), \
         patch('modules.project_tree.ctk.CTkButton'), \
         patch('modules.project_tree.ctk.CTkEntry'), \
         patch('modules.project_tree.AutoScrollbar'):

        panel = ProjectTreePanel(parent, icons, items_dict, app_state_params, on_item_selected, on_clear_canvas)

        # Mock tree structure for _export_xlsx
        panel.project_groups = ['Group 1']
        panel.group_nodes = {'Group 1': 'group1_node'}
        panel.tree.get_children.return_value = ['item1']
        panel.num_rule_var.get.return_value = 'continuous'

        try:
            # Mock parselmouth.Sound to raise an exception
            with patch('modules.project_tree.parselmouth.Sound', side_effect=Exception("Mocked Error")):
                with caplog.at_level(logging.ERROR):
                    # Trigger _export_xlsx (one of the methods with the fix)
                    panel._export_xlsx('test.xlsx')

                    # Verify that the error was logged
                    assert "Error lazy loading sound/pitch for invalid_path.wav: Mocked Error" in caplog.text
                    assert any(record.levelname == 'ERROR' for record in caplog.records)
        finally:
            import os
            if os.path.exists('test.xlsx'):
                try:
                    os.remove('test.xlsx')
                except Exception:
                    pass

