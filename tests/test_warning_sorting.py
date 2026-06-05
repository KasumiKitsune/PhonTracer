import unittest
from unittest.mock import MagicMock, patch
from tests.shared_root import get_shared_root
from modules.project_tree import ProjectTreePanel

class TestWarningSorting(unittest.TestCase):
    def setUp(self):
        self.parent = get_shared_root()
        self.icons = {}
        self.tk_icons = {'warning': 'warning_mock_image'}
        self.items_dict = {}
        self.app_state_params = {
            'pts': 10,
            'pitch_floor': 75.0,
            'pitch_ceiling': 600.0,
            'voicing_threshold': 0.25
        }
        self.app = MagicMock()
        
        with patch.object(ProjectTreePanel, 'setup_ui'):
            self.panel = ProjectTreePanel(
                parent=self.parent,
                icons=self.icons,
                items_dict=self.items_dict,
                app_state_params=self.app_state_params,
                on_item_selected_callback=MagicMock(),
                on_clear_canvas_callback=MagicMock(),
                tk_icons=self.tk_icons,
                app=self.app
            )
            # Create a mock tree
            self.panel.tree = MagicMock()
            self.panel.tree.exists.return_value = False
            self.panel.tree.insert.side_effect = self.tree_insert_mock
            
        self.inserted_warning_items = []

    def tree_insert_mock(self, parent, index, iid=None, **kwargs):
        if parent == "group_node___warning__":
            self.inserted_warning_items.append(iid)
        return iid

    def test_warning_items_sorted_by_import_index(self):
        # We put items out of order in items_dict, but with import_index ordering them
        self.items_dict["item_b"] = {
            'label': 'Item B',
            'group': 'GroupA',
            'has_empty_data': True,
            'import_index': 2
        }
        self.items_dict["item_c"] = {
            'label': 'Item C',
            'group': 'GroupA',
            'has_empty_data': True,
            'import_index': 3
        }
        self.items_dict["item_a"] = {
            'label': 'Item A',
            'group': 'GroupA',
            'has_empty_data': True,
            'import_index': 1
        }
        
        # Mock analyze_item_anomalies to return a checkable warning
        self.panel.analyze_item_anomalies = MagicMock(return_value=["[警告] 空数据"])
        
        self.panel.rebuild_tree()
        
        # Check that warning items were inserted in order: warning_item_a, warning_item_b, warning_item_c
        self.assertEqual(
            self.inserted_warning_items,
            ["warning_item_a", "warning_item_b", "warning_item_c"]
        )

if __name__ == '__main__':
    unittest.main()
