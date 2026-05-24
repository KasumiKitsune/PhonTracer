import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import customtkinter as ctk
from modules.acoustic_exporter import AcousticChartExportDialog, AcousticChartExporter
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

    def test_integrated_density_defaults_to_speaker_normalized_contours(self):
        project_tree = MagicMock()
        project_tree._get_syllables_and_bounds.return_value = ([], [(0.0, 1.0)])
        project_tree._extract_kde_contour.side_effect = AssertionError("raw global contour should not be used")

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'density',
            'export_scope': 'integrated',
            'density_facet': 'none',
        }

        data_entries = [
            {
                'speaker_name': 'S1',
                'group': 'T1',
                'label': 'a',
                'syl_data': [(1.0, [100.0, 110.0, 120.0])],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_freqs': np.linspace(100.0, 140.0, 20),
                'normalized_raw_freqs': np.linspace(1.0, 4.0, 20),
                'raw_item': {},
            },
            {
                'speaker_name': 'S2',
                'group': 'T1',
                'label': 'a',
                'syl_data': [(1.0, [210.0, 220.0, 230.0])],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_freqs': np.linspace(210.0, 260.0, 20),
                'normalized_raw_freqs': np.linspace(1.0, 4.0, 20),
                'raw_item': {},
            },
        ]

        class FakeKDE:
            def __init__(self, positions, bw_method=None):
                self.positions = positions

            def __call__(self, values):
                return np.ones(values.shape[1])

        with patch('modules.acoustic_exporter.gaussian_kde', FakeKDE):
            fig = exporter._plot_temporal_density(data_entries, "group")

        self.assertIsNotNone(fig)
        project_tree._extract_kde_contour.assert_not_called()

    def test_density_global_normalization_preserves_legacy_path(self):
        project_tree = MagicMock()
        project_tree._get_syllables_and_bounds.return_value = ([], [(0.0, 1.0)])
        project_tree._extract_kde_contour.return_value = np.linspace(100.0, 140.0, 100)

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'density',
            'export_scope': 'integrated',
            'density_facet': 'none',
            'normalization': 'global',
        }

        data_entries = [{
            'speaker_name': 'S1',
            'group': 'T1',
            'label': 'a',
            'syl_data': [(1.0, [100.0, 110.0, 120.0])],
            'raw_xs': np.linspace(0.0, 1.0, 20),
            'raw_freqs': np.linspace(100.0, 140.0, 20),
            'normalized_raw_freqs': np.linspace(1.0, 4.0, 20),
            'raw_item': {},
        }]

        class FakeKDE:
            def __init__(self, positions, bw_method=None):
                self.positions = positions

            def __call__(self, values):
                return np.ones(values.shape[1])

        with patch('modules.acoustic_exporter.gaussian_kde', FakeKDE):
            fig = exporter._plot_temporal_density(data_entries, "group")

        self.assertIsNotNone(fig)
        project_tree._extract_kde_contour.assert_called_once()

    def test_group_pagination(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        project_tree._get_items_by_group_for_dict.return_value = [("Group1", ["item1"])]

        speaker = MagicMock()
        speaker.name = "Speaker 1"
        speaker.items = {}

        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker

        # 10 groups, which exceeds the page size of 8
        dummy_data = []
        for i in range(10):
            dummy_data.append({
                'speaker_name': 'Speaker 1',
                'group': f'Group_{i}',
                'label': f'word_{i}',
                'total_dur': 0.8,
                'syl_data': [(0.8, [100.0, 110.0])],
                'normalized_syl_data': [(0.8, [2.0, 2.5])],
                'raw_xs': np.array([0, 1]),
                'raw_freqs': np.array([100.0, 110.0]),
                'normalized_raw_freqs': np.array([2.0, 2.5]),
                'active_ratio': 1.0,
                'warnings': [],
                'raw_item': {}
            })

        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=dummy_data), \
             patch.object(AcousticChartExportDialog, 'update_preview'):

            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )

            # Initial group page should be 0
            self.assertEqual(dlg.current_group_page, 0)

            # Check dynamic option changes resets page index
            dlg.current_group_page = 1
            dlg._on_groupby_changed("按词语")
            self.assertEqual(dlg.current_group_page, 0)

            # Test group pagination navigation wrapping
            with patch.object(dlg, '_get_current_data_entries', return_value=dummy_data):
                dlg.current_group_page = 0
                dlg._next_group_page()
                # total 10 groups, page_size 8, so total pages = 2. Page index shifts to 1.
                self.assertEqual(dlg.current_group_page, 1)

                dlg._next_group_page()
                # wraps back to 0
                self.assertEqual(dlg.current_group_page, 0)

                dlg._prev_group_page()
                # wraps to 1
                self.assertEqual(dlg.current_group_page, 1)

                # Preview rendering should pass only the current page of groups to
                # the concrete plotting function.
                fake_fig = MagicMock()
                with patch.object(dlg, '_plot_tone_contour', return_value=fake_fig) as plot_mock:
                    dlg.var_chart_type.set("contour")
                    dlg.current_group_page = 1
                    dlg.generate_plot(dummy_data, is_preview=True)

                    plotted_entries = plot_mock.call_args[0][0]
                    self.assertEqual([e['group'] for e in plotted_entries], ["Group_8", "Group_9"])

                # Overview heatmaps are the compact all-group view, so they should
                # not be paginated in preview.
                with patch.object(dlg, '_plot_tone_overview_heatmap', return_value=MagicMock()) as heatmap_mock:
                    dlg.var_chart_type.set("overview_heatmap")
                    dlg.current_group_page = 1
                    dlg.generate_plot(dummy_data, is_preview=True)

                    plotted_entries = heatmap_mock.call_args[0][0]
                    self.assertEqual(len(plotted_entries), 10)

            dlg.destroy()

if __name__ == '__main__':
    unittest.main()
