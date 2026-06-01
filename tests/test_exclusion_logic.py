import os
import json
import shutil
import tempfile
import zipfile
import unittest
import threading
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from modules.project_manager import ProjectManager
from modules.project_tree import ProjectTreePanel
from modules.report_generator import generate_markdown_report, write_excel_archive
from modules.acoustic_exporter import AcousticChartExporter
from cli import PhonTracerCLI


def _make_project_manager(app, workspace_dir):
    manager = ProjectManager.__new__(ProjectManager)
    manager.app = app
    manager.workspace_dir = workspace_dir
    manager.backup_path = os.path.join(os.path.dirname(workspace_dir), "auto_save_backup.teproj")
    manager.auto_save_enabled = False
    manager._auto_save_timer = None
    manager._save_lock = threading.RLock()
    os.makedirs(workspace_dir, exist_ok=True)
    return manager


def _write_test_wav(path, sample_value=100):
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(int(sample_value).to_bytes(2, "little", signed=True) * 800)


class TestExclusionLogic(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.temp_dir, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_serialization_compatibility(self):
        """Test serialization & backward compatibility of exclusion fields"""
        # Create dummy WAV files on disk to prevent FileNotFoundError
        active_wav = os.path.join(self.temp_dir, "active.wav")
        excluded_wav = os.path.join(self.temp_dir, "excluded.wav")
        _write_test_wav(active_wav)
        _write_test_wav(excluded_wav)

        # Create a mock speaker with one excluded item and one active item
        speaker = SimpleNamespace(
            id="sp1",
            name="TestSpeaker",
            last_params={"pts": 10},
            tab_mode="多条独立音频",
            long_audio_path=None,
            pending_batch_paths=[],
            current_macro_segments=[],
            manual_segments=None,
            items={
                "item1": {
                    "label": "ActiveWord",
                    "path": active_wav,
                },
                "item2": {
                    "label": "ExcludedWord",
                    "path": excluded_wav,
                    "is_excluded": True,
                    "exclusion_reason": "录音中断",
                    "excluded_at": "2026-06-01 12:00:00",
                }
            }
        )
        app = SimpleNamespace(
            root=None,
            export_numbering_rule_value="continuous",
            speaker_manager=SimpleNamespace(active_speaker_id="sp1", speakers={"sp1": speaker}),
        )

        manager = _make_project_manager(app, self.workspace_dir)
        
        # Save to workspace
        self.assertTrue(manager.save_to_workspace())

        # Read project.json directly to check serialization
        project_json = os.path.join(self.workspace_dir, "project.json")
        with open(project_json, "r", encoding="utf-8") as f:
            state = json.load(f)

        saved_items = state["speakers"]["sp1"]["items"]
        self.assertFalse(saved_items["item1"].get("is_excluded", False))
        self.assertTrue(saved_items["item2"].get("is_excluded", False))
        self.assertEqual(saved_items["item2"].get("exclusion_reason"), "录音中断")
        self.assertEqual(saved_items["item2"].get("excluded_at"), "2026-06-01 12:00:00")

        # Now test loading / backward compatibility
        # Load back into a fresh app state
        target_app = SimpleNamespace(
            root=None,
            export_numbering_rule_value="continuous",
            speaker_manager=SimpleNamespace(active_speaker_id=None, speakers={}),
        )
        target_manager = _make_project_manager(target_app, os.path.join(self.temp_dir, "target_workspace"))
        
        # Write state to targets workspace to load it
        os.makedirs(target_manager.workspace_dir, exist_ok=True)
        shutil.copytree(
            os.path.join(manager.workspace_dir, "audio"),
            os.path.join(target_manager.workspace_dir, "audio")
        )
        with open(os.path.join(target_manager.workspace_dir, "project.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)

        # Load
        self.assertTrue(target_manager.load_from_workspace())
        
        loaded_sp = target_app.speaker_manager.speakers["sp1"]
        self.assertFalse(loaded_sp.items["item1"].get("is_excluded", False))
        self.assertTrue(loaded_sp.items["item2"].get("is_excluded", False))
        self.assertEqual(loaded_sp.items["item2"].get("exclusion_reason"), "录音中断")

    def test_project_tree_exclusion_filtering(self):
        """Test ProjectTreePanel filtering of excluded items"""
        items_dict = {
            "item1": {"label": "Word1", "group": "GroupA", "is_excluded": False},
            "item2": {"label": "Word2", "group": "GroupA", "is_excluded": True},
        }

        # Instantiate ProjectTreePanel with setup_ui patched
        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(
                parent=MagicMock(),
                icons={},
                items_dict=items_dict,
                app_state_params={},
                on_item_selected_callback=MagicMock(),
                on_clear_canvas_callback=MagicMock(),
                tk_icons={},
                app=MagicMock()
            )
            panel.tree = MagicMock()
            
            # Mock get_children for group node to return tree nodes of both items
            panel.tree.get_children.return_value = ["item1", "item2"]
            panel.group_nodes = {"GroupA": "group_node_GroupA"}
            panel.project_groups = ["GroupA"]

            # Verify _get_all_items_by_group filters out item2
            struct = panel._get_all_items_by_group()
            self.assertEqual(len(struct), 1)
            self.assertEqual(struct[0][0], "GroupA")
            self.assertEqual(struct[0][1], ["item1"]) # Only active items

            # Verify _get_items_by_group_for_dict filters out item2
            group_struct = panel._get_items_by_group_for_dict(items_dict)
            self.assertEqual(group_struct, [("GroupA", ["item1"])])

    def test_report_generation_counts_and_exclusion_details(self):
        """Test reports reflect correct metadata counters and exclusion tables"""
        state = {
            "version": "1.0",
            "active_speaker_id": "sp1",
            "speakers": {
                "sp1": {
                    "id": "sp1",
                    "name": "Spk1",
                    "tab_mode": "多条独立音频",
                    "last_params": {
                        "analysis_mode": "F0",
                        "pitch_floor": 75,
                        "pitch_ceiling": 600,
                        "voicing_threshold": 0.25,
                    },
                    "items": {
                        "item1": {
                            "label": "Word1",
                            "group": "GroupA",
                            "path": "audio/item1.wav",
                            "is_excluded": False,
                            "start": 0.1,
                            "end": 0.5,
                        },
                        "item2": {
                            "label": "Word2",
                            "group": "GroupA",
                            "path": "audio/item2.wav",
                            "is_excluded": True,
                            "exclusion_reason": "发音错误",
                            "excluded_at": "2026-06-01 12:00:00",
                            "start": 0.2,
                            "end": 0.6,
                        }
                    }
                }
            }
        }

        # Create a real temporary .teproj zip archive
        teproj_path = os.path.join(self.temp_dir, "test.teproj")
        with zipfile.ZipFile(teproj_path, "w") as z:
            z.writestr("project.json", json.dumps(state, ensure_ascii=False))

        # Generate markdown report
        with zipfile.ZipFile(teproj_path, "r") as z:
            md = generate_markdown_report(teproj_path, state, z)
        
        # Assert exclusion count shows in元数据表格
        self.assertIn("忽略/排除条目数", md)
        self.assertIn("最终分析条目数", md)
        
        # Assert section 8 contains detail table
        self.assertIn("## 8. 数据清洗与排除条目清单", md)
        self.assertIn("Spk1", md)
        self.assertIn("Word2", md)
        self.assertIn("发音错误", md)
        self.assertIn("2026-06-01 12:00:00", md)

        # Generate Excel archive
        xlsx_path = os.path.join(self.temp_dir, "report.xlsx")
        write_excel_archive(teproj_path, state, xlsx_path, include_cache_details=False)
        self.assertTrue(os.path.exists(xlsx_path))
        self.assertGreater(os.path.getsize(xlsx_path), 0)

    def test_cli_export_excludes_items(self):
        """Test that CLI commands respect item exclusion status"""
        state = {
            "version": "1.0",
            "active_speaker_id": "sp1",
            "speakers": {
                "sp1": {
                    "id": "sp1",
                    "name": "Sp1",
                    "tab_mode": "多条独立音频",
                    "last_params": {
                        "analysis_mode": "F0",
                        "pitch_floor": 75,
                        "pitch_ceiling": 600,
                        "voicing_threshold": 0.25,
                    },
                    "items": {
                        "item1": {
                            "label": "Word1",
                            "group": "GroupA",
                            "path": "audio/item1.wav",
                            "is_excluded": False,
                        },
                        "item2": {
                            "label": "Word2",
                            "group": "GroupA",
                            "path": "audio/item2.wav",
                            "is_excluded": True,
                        }
                    }
                }
            }
        }

        # Mock the project state and project manager
        mock_proj = MagicMock()
        mock_proj.name = "Sp1"
        mock_proj.tab_mode = "多条独立音频"
        mock_proj.items = state["speakers"]["sp1"]["items"]
        mock_proj.last_params = state["speakers"]["sp1"]["last_params"]
        mock_proj.cli_groups = ["GroupA"]

        # Setup CLI instance
        cli = PhonTracerCLI()
        cli.speaker_manager = MagicMock()
        cli.speaker_manager.get_active_speaker.return_value = mock_proj
        cli.speaker_manager.get_all_speakers.return_value = [mock_proj]
        cli.groups = ["GroupA"]

        # Test exporting as txt
        output_txt = os.path.join(self.temp_dir, "out.txt")
        
        with patch.object(cli, '_export_txt') as mock_export_txt:
            # Run CLI do_export
            cli.do_export("txt {} continuous active".format(output_txt.replace('\\', '/')))
            
            # Verify mock_export_txt was called with structure omitting item2
            self.assertTrue(mock_export_txt.called)
            args = mock_export_txt.call_args[0]
            # args[1] is structure. It should be [('GroupA', ['item1'])]
            structure = args[1]
            self.assertEqual(structure, [('GroupA', ['item1'])])

    def test_project_tree_group_name_parsing_parentheses(self):
        """Test that group name parsing with parentheses works correctly without splitting"""
        # Create ProjectTreePanel with mocked setup_ui
        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(
                parent=MagicMock(),
                icons={},
                items_dict={},
                app_state_params={},
                on_item_selected_callback=MagicMock(),
                on_clear_canvas_callback=MagicMock(),
                tk_icons={},
                app=MagicMock()
            )
            panel.tree = MagicMock()
            panel.rebuild_tree = MagicMock()
            panel.update_preview = MagicMock()
            
            # 1. Test permanently_delete_selected_items
            gid = "group_node_ao (3)"
            panel.tree.selection.return_value = [gid]
            panel.tree.item.side_effect = lambda item_id, option: {
                ('group_node_ao (3)', 'tags'): ('group',),
                ('group_node_ao (3)', 'text'): 'ao (3) (1/2, 已忽略 1 项)'
            }.get((item_id, option), None)
            
            panel.tree.get_children.return_value = []
            panel.warning_group_id = "group_node___warning__"
            panel.project_groups = ["ao (3)"]
            panel.group_nodes = {"ao (3)": gid}
            
            with patch('tkinter.messagebox.askyesno', return_value=True):
                panel.permanently_delete_selected_items()
                # Verify that 'ao (3)' was correctly removed from project_groups (which shows it parsed the correct group name, not splitting into 'ao')
                self.assertNotIn("ao (3)", panel.project_groups)
                
            # 2. Test clear_group_items & delete_group_and_items group_name parsing
            panel.project_groups = ["ao (3)"]
            panel.items = {
                "item1": {"label": "Word1", "group": "ao (3)"}
            }
            # Mock tree existence
            panel.tree.exists.return_value = True
            
            with patch('tkinter.messagebox.askyesno', return_value=True), \
                 patch('tkinter.messagebox.showinfo'):
                # Call clear_group_items on "group_node_ao (3)"
                panel.clear_group_items("group_node_ao (3)")
                # If parsed correctly, item1 group "ao (3)" matches and item1 is popped
                self.assertNotIn("item1", panel.items)

    def test_chart_filters_retrieve_all_groups_even_if_excluded(self):
        """Test that AcousticChartExporter extracts all groups when filter_groups=False,
        including a group that is unchecked in the UI (different from is_excluded).
        Also verifies that items with is_excluded=True are stripped before populating
        the cache (i.e., chart data pipeline never sees excluded items)."""
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}

        # Instantiate exporter
        exporter = AcousticChartExporter(project_tree=project_tree)
        # Mock group_checkbox_vars where 'GroupB' is unchecked (UI filter only)
        exporter.group_checkbox_vars = {
            'GroupA': MagicMock(get=lambda: True),
            'GroupB': MagicMock(get=lambda: False),
        }

        exporter.active_speaker = 'sp1'
        exporter.all_speakers = ['sp1']

        # Cache includes GroupA (active) and GroupB (all items excluded).
        # The cache itself should already have excluded items stripped out —
        # cache population is tested via _get_items_by_group_for_dict below.
        # Here we populate the cache manually to simulate both groups existing.
        exporter._speaker_data_cache = {
            'sp1': [
                {'group': 'GroupA', 'label': 'Word1'},
                {'group': 'GroupB', 'label': 'Word2'},
            ]
        }

        # With filter_groups=True, GroupB should be filtered out (checkbox is False)
        entries_filtered = exporter._extract_active_data(['sp1'], filter_groups=True)
        groups_filtered = [e['group'] for e in entries_filtered]
        self.assertIn('GroupA', groups_filtered)
        self.assertNotIn('GroupB', groups_filtered)

        # With filter_groups=False, GroupB must NOT be filtered out
        entries_unfiltered = exporter._extract_active_data(['sp1'], filter_groups=False)
        groups_unfiltered = [e['group'] for e in entries_unfiltered]
        self.assertIn('GroupA', groups_unfiltered)
        self.assertIn('GroupB', groups_unfiltered)

    def test_get_items_by_group_excludes_fully_excluded_groups(self):
        """_get_items_by_group_for_dict must omit a group entirely when all its
        items carry is_excluded=True, and must not omit groups that still have
        at least one active item."""
        # GroupA has one active item; GroupB has ALL items excluded.
        items_dict = {
            "item1": {"label": "Word1", "group": "GroupA", "is_excluded": False},
            "item2": {"label": "Word2", "group": "GroupB", "is_excluded": True},
            "item3": {"label": "Word3", "group": "GroupB", "is_excluded": True},
        }

        with patch.object(ProjectTreePanel, 'setup_ui'):
            panel = ProjectTreePanel(
                parent=MagicMock(),
                icons={},
                items_dict=items_dict,
                app_state_params={},
                on_item_selected_callback=MagicMock(),
                on_clear_canvas_callback=MagicMock(),
                tk_icons={},
                app=MagicMock()
            )
            panel.tree = MagicMock()
            panel.group_nodes = {"GroupA": "gn_GroupA", "GroupB": "gn_GroupB"}
            panel.project_groups = ["GroupA", "GroupB"]

            result = panel._get_items_by_group_for_dict(items_dict)
            result_groups = [g for g, _ in result]

            # GroupA must be present (has active item)
            self.assertIn("GroupA", result_groups)
            # GroupB must NOT appear (all items excluded)
            self.assertNotIn("GroupB", result_groups)

            # Also verify GroupA contains only item1
            group_a_items = next(items for g, items in result if g == "GroupA")
            self.assertEqual(group_a_items, ["item1"])


if __name__ == "__main__":
    unittest.main()
