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

    def test_live_refresh_and_debounce(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        project_tree._get_items_by_group_for_dict.return_value = [("Group1", ["item1"])]

        speaker = MagicMock()
        speaker.name = "Speaker 1"
        speaker.items = {}

        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker

        dummy_data = []

        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=dummy_data), \
             patch.object(AcousticChartExportDialog, 'update_preview') as mock_update:

            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )

            # Live refresh is True by default
            self.assertTrue(dlg.var_live_refresh.get())

            # Mock after method
            dlg.after = MagicMock(return_value="timer_123")
            dlg.after_cancel = MagicMock()

            # Call trigger_preview_update
            dlg.trigger_preview_update()
            dlg.after.assert_called_once_with(300, dlg._debounced_update_preview)
            self.assertEqual(dlg._debounce_timer_id, "timer_123")

            # Call trigger_preview_update again: should cancel previous timer and schedule new one
            dlg.trigger_preview_update()
            dlg.after_cancel.assert_called_once_with("timer_123")

            # Turn off live refresh
            dlg.var_live_refresh.set(False)
            dlg.after.reset_mock()
            dlg.trigger_preview_update()
            dlg.after.assert_not_called()

            # Test live refresh toggle commands
            dlg.var_live_refresh.set(True)
            mock_update.reset_mock()
            dlg._on_live_refresh_toggle()
            mock_update.assert_called_once()

            dlg.destroy()

    def test_legend_position_settings_and_auto_refresh_fix(self):
        project_tree = MagicMock()
        exporter = AcousticChartExporter(project_tree=project_tree)
        
        # Test default legend kwargs
        kwargs = exporter._get_legend_kwargs()
        self.assertEqual(kwargs["loc"], "upper right")
        self.assertNotIn("bbox_to_anchor", kwargs)
        
        # Test various positions and outside values
        exporter.params = {"legend_loc": "右上", "legend_outside": True}
        kwargs = exporter._get_legend_kwargs()
        self.assertEqual(kwargs["loc"], "upper left")
        self.assertEqual(kwargs["bbox_to_anchor"], (1.02, 1))

        exporter.params = {"legend_loc": "左下", "legend_outside": True}
        kwargs = exporter._get_legend_kwargs()
        self.assertEqual(kwargs["loc"], "lower right")
        self.assertEqual(kwargs["bbox_to_anchor"], (-0.02, 0))

        # Test group filter auto-refresh uses trigger_preview_update
        speaker = MagicMock()
        speaker.name = "Speaker 1"
        speaker.items = {}
        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker

        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=[]), \
             patch.object(AcousticChartExportDialog, 'update_preview'):
            
            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )
            
            with patch.object(dlg, 'trigger_preview_update') as mock_trigger:
                dlg._on_group_filter_changed()
                mock_trigger.assert_called_once()
                
            dlg.destroy()

    def test_overview_heatmap_sorting_gaps_and_pagination(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.available_groups = ['Group1', 'Group2']
        exporter.colors = ['#2563EB', '#DC2626']

        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group2',
                'label': 'ba',
                'syl_data': [(0.8, [100.0, 110.0, 120.0, 130.0, 140.0])],
                'normalized_syl_data': [(0.8, [1.0, 2.0, 3.0, 4.0, 5.0])],
            },
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_data': [(0.8, [110.0, 120.0, 130.0, 140.0, 150.0])],
                'normalized_syl_data': [(0.8, [1.5, 2.5, 3.5, 4.5, 5.0])],
            }
        ]

        fig = exporter._plot_tone_overview_heatmap(dummy_data, "label", "T 值")
        self.assertIsNotNone(fig)
        
        ax = fig.axes[0]
        yticks = ax.get_yticks()
        yticklabels = [t.get_text() for t in ax.get_yticklabels()]
        self.assertEqual(list(yticks), [0, 2])
        self.assertIn("ma", yticklabels[0])
        self.assertIn("ba", yticklabels[1])

        large_data = []
        for i in range(25):
            large_data.append({
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': f'word_{i:02d}',
                'syl_data': [(0.8, [100.0] * 5)],
                'normalized_syl_data': [(0.8, [1.0] * 5)],
            })

        exporter.params = {
            'chart_type': 'overview_heatmap',
            'groupby': '按词语',
            'intention': '附录图册 (完整数据)',
            'scale': 't_value',
        }

        exporter._export_overview_heatmap_paginated_pdf = MagicMock()
        exporter._export_overview_heatmap_paginated_images = MagicMock()

        exporter._export_dataset(large_data, 'dummy_out.pdf', '.pdf')

        exporter._export_overview_heatmap_paginated_pdf.assert_called_once()
        args = exporter._export_overview_heatmap_paginated_pdf.call_args[0]
        self.assertEqual(args[0], 'dummy_out.pdf')
        pages = args[3]
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[0]), 20)
        self.assertEqual(len(pages[1]), 5)

    def test_overview_heatmap_keeps_duplicate_labels_separate_across_groups(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'overview_heatmap',
            'groupby': '按词语',
            'intention': '附录图册 (完整数据)',
            'scale': 't_value',
        }

        duplicate_label_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_data': [(0.8, [100.0] * 5)],
                'normalized_syl_data': [(0.8, [1.0] * 5)],
            },
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group2',
                'label': 'ma',
                'syl_data': [(0.8, [120.0] * 5)],
                'normalized_syl_data': [(0.8, [2.0] * 5)],
            },
        ]

        fig = exporter._plot_tone_overview_heatmap(duplicate_label_data, "label", "T 值")
        ax = fig.axes[0]
        yticklabels = [tick.get_text() for tick in ax.get_yticklabels()]
        self.assertEqual(yticklabels, ["ma (N=1)", "ma (N=1)"])

        exporter._export_overview_heatmap_paginated_pdf = MagicMock()
        exporter._export_dataset(duplicate_label_data, 'dup.pdf', '.pdf')
        pages = exporter._export_overview_heatmap_paginated_pdf.call_args[0][3]
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0][0]['row_id'], ('Group1', 'ma'))
        self.assertEqual(pages[1][0]['row_id'], ('Group2', 'ma'))

    def test_aspect_ratio_and_dpi_settings(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}
        exporter = AcousticChartExporter(project_tree=project_tree)

        # 1. Test DPI calculation
        # Default or vector formats should return 300
        exporter.params = {'format': 'pdf', 'image_pixel_mode': '1080 px'}
        self.assertEqual(exporter._get_save_dpi(), 300)

        exporter.params = {'format': 'svg', 'image_pixel_mode': '1080 px'}
        self.assertEqual(exporter._get_save_dpi(), 300)

        # PNG with defaults should return 300
        exporter.params = {'format': 'png', 'image_pixel_mode': '默认'}
        self.assertEqual(exporter._get_save_dpi(), 300)

        dummy_fig = MagicMock()
        dummy_fig.get_size_inches.return_value = (12.0, 6.0)

        # Custom presets should use the figure's true minimum edge
        exporter.params = {'format': 'png', 'image_pixel_mode': '720 px'}
        self.assertEqual(exporter._resolve_save_dpi(dummy_fig), 720 / 6.0) # 120

        # Custom pixels
        exporter.params = {'format': 'png', 'image_pixel_mode': '自定义', 'image_pixel_custom': 600}
        self.assertEqual(exporter._resolve_save_dpi(dummy_fig), 600 / 6.0) # 100

        # Different figure size should scale from the actual minimum edge, not a fixed 6-inch assumption
        dummy_fig_wide = MagicMock()
        dummy_fig_wide.get_size_inches.return_value = (8.0, 4.0)
        exporter.params = {'format': 'png', 'image_pixel_mode': '720 px'}
        self.assertEqual(exporter._resolve_save_dpi(dummy_fig_wide), 720 / 4.0) # 180

        # 2. Test aspect ratio resize in generate_plot
        dummy_data = [{
            'speaker_name': 'S1',
            'group': 'T1',
            'label': 'a',
            'syl_data': [(1.0, [100.0] * 5)],
            'raw_xs': np.linspace(0.0, 1.0, 5),
            'raw_freqs': np.linspace(100.0, 140.0, 5),
            'normalized_raw_freqs': np.linspace(1.0, 4.0, 5),
            'normalized_syl_data': [(1.0, [1.0] * 5)],
            'raw_item': {},
        }]

        # Standard ratio (16:9)
        exporter.params = {
            'chart_type': 'contour',
            'groupby': 'group',
            'scale': 't_value',
            'image_ratio_mode': '16:9',
            'image_ratio_custom': 1.5,
        }
        fig = exporter.generate_plot(dummy_data, is_preview=True)
        w, h = fig.get_size_inches()
        self.assertAlmostEqual(w / h, 16/9, places=4)
        self.assertAlmostEqual(h, 6.0)

        # Custom ratio (2.5)
        exporter.params = {
            'chart_type': 'contour',
            'groupby': 'group',
            'scale': 't_value',
            'image_ratio_mode': '自定义',
            'image_ratio_custom': 2.5,
        }
        fig2 = exporter.generate_plot(dummy_data, is_preview=True)
        w2, h2 = fig2.get_size_inches()
        self.assertAlmostEqual(w2 / h2, 2.5, places=4)

    def test_preview_wrapper_default_ratio_resets_manual_place_geometry(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        project_tree._get_items_by_group_for_dict.return_value = [("Group1", ["item1"])]

        speaker = MagicMock()
        speaker.name = "Speaker 1"
        speaker.items = {}

        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker

        with patch.object(AcousticChartExportDialog, '_extract_active_data', return_value=[]), \
             patch.object(AcousticChartExportDialog, 'update_preview'):
            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='single', all_speakers=[speaker]
            )

            dlg.preview_wrapper = MagicMock()
            dlg.preview_wrapper.winfo_width.return_value = 800
            dlg.preview_wrapper.winfo_height.return_value = 600
            dlg.preview_container = MagicMock()
            dlg.combo_ratio_mode = MagicMock()
            dlg.combo_ratio_mode.get.return_value = "默认"

            dlg.on_preview_wrapper_configure()

            dlg.preview_container.place_forget.assert_called_once()
            dlg.preview_container.configure.assert_called_once_with(width=0, height=0)
            dlg.preview_container.place.assert_called_once_with(
                relx=0.0,
                rely=0.0,
                x=0,
                y=0,
                relwidth=1.0,
                relheight=1.0,
                anchor="nw",
            )

            dlg.destroy()

    def test_formant_density_band_plot(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)
        
        # Mock some dummy formant data entries
        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_formants': [
                    {
                        'char': 'ma',
                        'bounds': (0.1, 0.9),
                        # 11 points for F1 and F2
                        'f1': [500.0, 510.0, 520.0, 530.0, 540.0, 550.0, 560.0, 570.0, 580.0, 590.0, 600.0],
                        'f2': [1500.0, 1490.0, 1480.0, 1470.0, 1460.0, 1450.0, 1440.0, 1430.0, 1420.0, 1410.0, 1400.0]
                    }
                ],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_f1': np.linspace(500.0, 600.0, 20),
                'raw_f2': np.linspace(1500.0, 1400.0, 20)
            }
        ]

        # 1. Test with density_band=True, but only 1 sample (triggers fallback circle logic)
        exporter.params = {
            'groupby': 'group',
            'formant_label_mode': '显示分组标签',
            'formant_ellipse': '1-sigma 置信椭圆',
            'formant_show_raw': True,
            'formant_time_gradient': False,
            'formant_density_band': True,
            'legend_loc': '右上',
            'legend_outside': False,
        }
        fig = exporter._plot_formant_vowel_space(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig)

        # 2. Test with density_band=True and at least 3 samples (triggers covariance ellipse logic)
        dummy_data_3 = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_formants': [
                    {
                        'char': 'ma',
                        'bounds': (0.1, 0.9),
                        'f1': [500.0 + k]*11,
                        'f2': [1500.0 - k]*11
                    }
                ],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_f1': np.linspace(500.0, 600.0, 20),
                'raw_f2': np.linspace(1500.0, 1400.0, 20)
            }
            for k in [-10, 0, 10]
        ]
        fig3 = exporter._plot_formant_vowel_space(dummy_data_3, 'group', 'Hz')
        self.assertIsNotNone(fig3)

if __name__ == '__main__':
    unittest.main()
