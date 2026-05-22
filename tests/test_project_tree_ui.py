import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
import numpy as np
from tests.shared_root import get_shared_root
from modules.project_tree import ProjectTreePanel

class TestProjectTreeUI(unittest.TestCase):
    def setUp(self):
        self.parent = get_shared_root()
        self.icons = {
            'folder_close': 'folder_close_mock',
            'folder_open': 'folder_open_mock',
            'audio_wave': 'audio_wave_mock',
            'blue_dot': 'blue_dot_mock',
            'warning': 'warning_mock'
        }
        self.tk_icons = {
            'folder_close': 'tk_folder_close_mock',
            'folder_open': 'tk_folder_open_mock',
            'audio_wave': 'tk_audio_wave_mock',
            'blue_dot': 'tk_blue_dot_mock',
            'warning': 'tk_warning_mock'
        }
        self.items_dict = {
            'item_normal': {
                'label': 'Apple',
                'group': 'Group1',
                'start': 0.0,
                'end': 1.0,
                'has_empty_data': False
            },
            'item_warning': {
                'label': 'Banana',
                'group': 'Group1',
                'start': 0.0,
                'end': 1.0,
                'has_empty_data': True
            },
            'item_edited': {
                'label': 'Cherry',
                'group': 'Group2',
                'start': 0.0,
                'end': 1.0,
                'has_empty_data': False,
                'is_manual_edited': True
            }
        }
        self.app_state_params = {'pts': 10}
        self.on_item_selected = MagicMock()
        self.on_clear_canvas = MagicMock()
        self.app = MagicMock()
        self.app.active_speaker = MagicMock()

        with patch.object(ProjectTreePanel, 'setup_ui'):
            self.panel = ProjectTreePanel(
                parent=self.parent,
                icons=self.icons,
                items_dict=self.items_dict,
                app_state_params=self.app_state_params,
                on_item_selected_callback=self.on_item_selected,
                on_clear_canvas_callback=self.on_clear_canvas,
                tk_icons=self.tk_icons,
                app=self.app
            )
            # Create a mocked tree structure
            self.panel.tree = MagicMock()
            self.tree_nodes = {}
            self.panel.tree.exists.side_effect = lambda iid: iid in self.tree_nodes
            self.panel.tree.item.side_effect = self.tree_item_mock
            self.panel.tree.insert.side_effect = self.tree_insert_mock
            self.panel.tree.original_insert = self.tree_insert_mock
            self.panel.tree.get_children.side_effect = self.tree_get_children_mock
            self.panel.tree.parent.side_effect = self.tree_parent_mock
            self.panel.tree.delete.side_effect = self.tree_delete_mock

            # UI properties
            self.panel.search_var = MagicMock()
            self.panel.search_var.get.return_value = ""
            self.panel.filter_var = MagicMock()
            self.panel.filter_var.get.return_value = "全部"
            self.panel.project_groups = ['Group1', 'Group2']

    def tree_insert_mock(self, parent, index, iid=None, **kwargs):
        if iid is None:
            import uuid
            iid = str(uuid.uuid4())
        self.tree_nodes[iid] = {
            'parent': parent,
            'text': kwargs.get('text', ''),
            'image': kwargs.get('image', ''),
            'tags': kwargs.get('tags', ()),
            'children': [],
            'open': kwargs.get('open', True)
        }
        if parent in self.tree_nodes:
            self.tree_nodes[parent]['children'].append(iid)
        return iid

    def tree_item_mock(self, iid, option=None, **kwargs):
        node = self.tree_nodes.setdefault(iid, {'parent': '', 'text': '', 'image': '', 'tags': (), 'children': [], 'open': True})
        if option == 'tags':
            return node.get('tags', ())
        if option == 'text':
            return node.get('text', '')
        if option == 'image':
            return node.get('image', '')
        if option == 'open':
            return node.get('open', True)
        if kwargs:
            if 'text' in kwargs:
                node['text'] = kwargs['text']
            if 'image' in kwargs:
                node['image'] = kwargs['image']
            if 'tags' in kwargs:
                node['tags'] = kwargs['tags']
            if 'open' in kwargs:
                node['open'] = kwargs['open']
        return node

    def tree_get_children_mock(self, node=""):
        if node == "":
            return [k for k, v in self.tree_nodes.items() if v.get('parent') == ""]
        return self.tree_nodes.get(node, {}).get('children', [])

    def tree_parent_mock(self, iid):
        return self.tree_nodes.get(iid, {}).get('parent', "")

    def tree_delete_mock(self, *iids):
        for iid in iids:
            if iid in self.tree_nodes:
                parent = self.tree_nodes[iid]['parent']
                if parent in self.tree_nodes:
                    if iid in self.tree_nodes[parent]['children']:
                        self.tree_nodes[parent]['children'].remove(iid)
                del self.tree_nodes[iid]

    def test_rebuild_tree_counts_and_badges(self):
        """Test that rebuild_tree adds proper badge counts to group headers"""
        self.panel.rebuild_tree()

        # Group1 should have 2 children ('item_normal', 'item_warning')
        group1_id = self.panel.group_nodes['Group1']
        group1_text = self.tree_nodes[group1_id]['text']
        self.assertEqual(group1_text, "Group1 (2)")

        # Group2 should have 1 child ('item_edited')
        group2_id = self.panel.group_nodes['Group2']
        group2_text = self.tree_nodes[group2_id]['text']
        self.assertEqual(group2_text, "Group2 (1)")

        # Warning group should have 1 child ('item_warning')
        self.assertIsNotNone(self.panel.warning_group_id)
        warning_text = self.tree_nodes[self.panel.warning_group_id]['text']
        self.assertEqual(warning_text, "需要检查 (1)")

    def test_rebuild_tree_text_search(self):
        """Test search filter only renders matching items and groups"""
        self.panel.search_var.get.return_value = "apple"
        self.panel.rebuild_tree()

        # Group1 should be rendered since it contains 'Apple'
        self.assertIn('Group1', self.panel.group_nodes)
        group1_id = self.panel.group_nodes['Group1']
        children = self.tree_get_children_mock(group1_id)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0], 'item_normal')

        # Group2 should not be rendered since 'Cherry' does not match 'apple'
        self.assertNotIn('Group2', self.panel.group_nodes)

    def test_rebuild_tree_status_filter_warning(self):
        """Test warning status filter only renders items with empty data"""
        self.panel.filter_var.get.return_value = "需检查"
        self.panel.rebuild_tree()

        # Group1 should be rendered since it contains 'Banana' (warning)
        self.assertIn('Group1', self.panel.group_nodes)
        group1_id = self.panel.group_nodes['Group1']
        children = self.tree_get_children_mock(group1_id)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0], 'item_warning')

        # Group2 should not be rendered since 'Cherry' is not a warning item
        self.assertNotIn('Group2', self.panel.group_nodes)

    def test_rebuild_tree_status_filter_edited(self):
        """Test edited status filter only renders manually edited items"""
        self.panel.filter_var.get.return_value = "已修改"
        self.panel.rebuild_tree()

        # Group1 should not be rendered
        self.assertNotIn('Group1', self.panel.group_nodes)

        # Group2 should be rendered since 'Cherry' is manually edited
        self.assertIn('Group2', self.panel.group_nodes)
        group2_id = self.panel.group_nodes['Group2']
        children = self.tree_get_children_mock(group2_id)
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0], 'item_edited')

    def test_kde_uses_chars_bounds_and_syllable_splitter(self):
        item = {
            'label': '米汤',
            'start': 0.1,
            'end': 0.9,
            'inner_splits': [0.4],
            'chars_bounds': [[0.12, 0.33], [0.55, 0.88]]
        }

        syls, bounds = self.panel._get_syllables_and_bounds(item)

        self.assertEqual(syls, ['米', '汤'])
        self.assertEqual(bounds, [[0.12, 0.33], [0.55, 0.88]])

    def test_kde_contour_preserves_erased_gap(self):
        xs = np.linspace(0.0, 1.0, 101)
        freqs = np.linspace(120.0, 180.0, 101)
        freqs[(xs >= 0.45) & (xs <= 0.55)] = 0.0

        contour = self.panel._extract_kde_contour(xs, freqs, 0.0, 1.0, 101)

        self.assertIsNotNone(contour)
        self.assertTrue(np.isfinite(contour[10]))
        self.assertTrue(np.isnan(contour[50]))
        self.assertTrue(np.isfinite(contour[90]))

if __name__ == '__main__':
    unittest.main()
