import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
from tests.shared_root import get_shared_root
from modules.project_tree import ProjectTreePanel

class TestWarningSync(unittest.TestCase):
    def setUp(self):
        # Create a mock parent widget
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
        self.on_item_selected = MagicMock()
        self.on_clear_canvas = MagicMock()
        self.app = MagicMock()
        self.app.active_speaker = MagicMock()

        # Instantiate ProjectTreePanel
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
            # Create a real Treeview inside panel
            self.panel.tree = MagicMock()
            self.panel.tree.exists.side_effect = lambda iid: iid in self.tree_nodes
            self.panel.tree.item.side_effect = lambda iid, option=None, **kwargs: self.tree_item_mock(iid, option, **kwargs)
            self.panel.tree.insert.side_effect = self.tree_insert_mock
            self.panel.tree.get_children.side_effect = self.tree_get_children_mock
            self.panel.tree.parent.side_effect = self.tree_parent_mock
            self.panel.tree.selection.side_effect = self.tree_selection_mock
            self.panel.tree.index.side_effect = self.tree_index_mock
            self.panel.tree.delete.side_effect = self.tree_delete_mock
            self.panel.tree.original_insert = self.panel.tree.insert

            # Provide mocks for setup_ui attributes used in tests
            self.panel.num_rule_var = MagicMock()
            self.panel.num_rule_var.get.return_value = "continuous"
            self.panel.text_preview = MagicMock()

        self.tree_nodes = {}
        self.tree_selection = []

    def tearDown(self):
        pass

    def tree_insert_mock(self, parent, index, iid=None, **kwargs):
        if iid is None:
            import uuid
            iid = str(uuid.uuid4())
        self.tree_nodes[iid] = {
            'parent': parent,
            'text': kwargs.get('text', ''),
            'image': kwargs.get('image', ''),
            'tags': kwargs.get('tags', ()),
            'children': []
        }
        if parent in self.tree_nodes:
            self.tree_nodes[parent]['children'].append(iid)
        return iid

    def tree_item_mock(self, iid, option=None, **kwargs):
        if option == 'tags':
            return self.tree_nodes.get(iid, {}).get('tags', ())
        if option == 'text':
            return self.tree_nodes.get(iid, {}).get('text', '')
        if option == 'image':
            return self.tree_nodes.get(iid, {}).get('image', '')
        if kwargs:
            node = self.tree_nodes.setdefault(iid, {})
            if 'text' in kwargs:
                node['text'] = kwargs['text']
            if 'image' in kwargs:
                node['image'] = kwargs['image']
            if 'tags' in kwargs:
                node['tags'] = kwargs['tags']
        return self.tree_nodes.get(iid, {})

    def tree_get_children_mock(self, node=""):
        if node == "":
            return [k for k, v in self.tree_nodes.items() if v.get('parent') == ""]
        return self.tree_nodes.get(node, {}).get('children', [])

    def tree_parent_mock(self, iid):
        return self.tree_nodes.get(iid, {}).get('parent', "")

    def tree_selection_mock(self):
        return self.tree_selection

    def tree_index_mock(self, iid):
        parent = self.tree_parent_mock(iid)
        siblings = self.tree_get_children_mock(parent)
        try:
            return siblings.index(iid)
        except ValueError:
            return 0

    def tree_delete_mock(self, *iids):
        def recursive_delete(iid):
            if iid in self.tree_nodes:
                children = list(self.tree_nodes[iid].get('children', []))
                for child in children:
                    recursive_delete(child)
                parent = self.tree_nodes[iid]['parent']
                if parent in self.tree_nodes:
                    if iid in self.tree_nodes[parent]['children']:
                        self.tree_nodes[parent]['children'].remove(iid)
                del self.tree_nodes[iid]

        for iid in iids:
            recursive_delete(iid)

    def test_warning_dual_appearance(self):
        """Test that an item with empty data stays in its group and gets a shadow item in the warning group"""
        # 1. Add item to dict and tree
        item_id = "test_item_1"
        self.items_dict[item_id] = {
            'label': 'TestWord',
            'group': 'GroupA',
            'start': 0.1,
            'end': 0.9,
            'has_empty_data': True
        }
        
        # Manually register GroupA
        self.panel.project_groups.append('GroupA')
        self.panel.group_nodes['GroupA'] = 'node_group_a'
        self.tree_nodes['node_group_a'] = {
            'parent': '',
            'text': 'GroupA',
            'tags': ('group',),
            'children': [item_id]
        }
        self.tree_nodes[item_id] = {
            'parent': 'node_group_a',
            'text': 'TestWord',
            'tags': ('item',),
            'children': []
        }

        # Trigger update_item_icon
        self.panel.update_item_icon(item_id)
        self.panel.rebuild_tree()

        # The real item should STILL be inside 'GroupA's group node (not reparented)
        self.assertEqual(self.tree_parent_mock(item_id), self.panel.group_nodes['GroupA'])

        # A shadow item under warning group should exist
        warning_group_id = self.panel.warning_group_id
        self.assertIsNotNone(warning_group_id)
        self.assertTrue(self.tree_nodes[warning_group_id])
        
        # Shadow item should be warning_test_item_1
        shadow_iid = f"warning_{item_id}"
        self.assertTrue(shadow_iid in self.tree_nodes)
        self.assertEqual(self.tree_parent_mock(shadow_iid), warning_group_id)
        self.assertEqual(self.tree_nodes[shadow_iid]['text'], 'TestWord')

        # 2. Trigger selection on the shadow item
        self.tree_selection = [shadow_iid]
        self.panel.on_tree_select(None)

        # verify that the panel mapped selection to real item_id
        self.assertEqual(self.panel.current_iid, item_id)
        self.assertEqual(self.app.active_speaker.last_selected_iid, item_id)
        self.on_item_selected.assert_called_with(item_id)

        # 3. Rename the shadow item
        # We mock bbox so start_inline_edit works
        with patch.object(self.panel.tree, 'bbox', return_value=(0, 0, 100, 20)):
            with patch('tkinter.Entry') as mock_entry_class:
                mock_entry = MagicMock()
                mock_entry.get.return_value = "NewWordName"
                mock_entry.winfo_exists.return_value = True
                mock_entry_class.return_value = mock_entry

                self.panel.start_inline_edit(shadow_iid)
                
                # Retrieve the save_edit inner function registered to Return/FocusOut
                save_edit = mock_entry.bind.call_args_list[0][0][1]
                save_edit(None)

                # The label in self.items must be updated
                self.assertEqual(self.items_dict[item_id]['label'], "NewWordName")
                # Both real tree node and shadow tree node must be updated
                self.assertEqual(self.tree_nodes[item_id]['text'], "NewWordName")
                self.assertEqual(self.tree_nodes[shadow_iid]['text'], "NewWordName")

        # 4. Resolve the warning (has_empty_data = False)
        self.items_dict[item_id]['has_empty_data'] = False
        self.panel.update_item_icon(item_id)
        self.panel.rebuild_tree()

        # Shadow item should be deleted
        self.assertFalse(shadow_iid in self.tree_nodes)
        # Warning group should be deleted because it is empty
        self.assertIsNone(self.panel.warning_group_id)

    def test_warning_deletion(self):
        """Test deleting warning items cleans up shadow nodes and structures properly"""
        item_id = "test_item_2"
        self.items_dict[item_id] = {
            'label': 'DelWord',
            'group': 'GroupB',
            'start': 0.1,
            'end': 0.9,
            'has_empty_data': True
        }
        
        self.panel.project_groups.append('GroupB')
        self.panel.group_nodes['GroupB'] = 'node_group_b'
        self.tree_nodes['node_group_b'] = {
            'parent': '',
            'text': 'GroupB',
            'tags': ('group',),
            'children': [item_id]
        }
        self.tree_nodes[item_id] = {
            'parent': 'node_group_b',
            'text': 'DelWord',
            'tags': ('item',),
            'children': []
        }

        # Create shadow item
        self.panel.update_item_icon(item_id)
        self.panel.rebuild_tree()
        shadow_iid = f"warning_{item_id}"
        self.assertTrue(shadow_iid in self.tree_nodes)

        # Select the shadow item and perform soft exclusion via Backspace
        self.tree_selection = [shadow_iid]
        self.panel.on_tree_backspace(None)

        # Under the new soft-exclusion logic, the item should be excluded, not physically deleted
        self.assertTrue(self.items_dict[item_id].get('is_excluded'))
        self.assertNotIn(shadow_iid, self.tree_nodes)
        self.assertIsNone(self.panel.warning_group_id)

        # Now test physical deletion via permanently_delete_selected_items
        # Restore shadow item and warning group to test physical deletion
        self.items_dict[item_id]['is_excluded'] = False
        self.panel.update_item_icon(item_id)
        self.panel.rebuild_tree()
        self.assertTrue(shadow_iid in self.tree_nodes)

        self.tree_selection = [shadow_iid]
        with patch('tkinter.messagebox.askyesno', return_value=True):
            self.panel.permanently_delete_selected_items()

        # The item should be completely deleted from items_dict, tree, and warning_iids
        self.assertNotIn(item_id, self.items_dict)
        self.assertNotIn(item_id, self.tree_nodes)
        self.assertNotIn(shadow_iid, self.tree_nodes)
        self.assertNotIn(item_id, self.panel.warning_iids)
        self.assertIsNone(self.panel.warning_group_id)

    def test_get_item_index_fallback(self):
        """Test that _get_item_index handles missing tree nodes gracefully without raising TclError"""
        item_id = "test_item_missing"
        self.items_dict[item_id] = {
            'label': 'MissingWord',
            'group': 'GroupC',
            'start': 0.1,
            'end': 0.9,
            'has_empty_data': False
        }
        
        # Verify that calling _get_item_index on a missing item (not in tree_nodes / tree)
        # falls back and returns a valid 1-based index based on items_dict insertion order.
        idx = self.panel._get_item_index(item_id)
        self.assertEqual(idx, 1)

if __name__ == '__main__':
    unittest.main()

