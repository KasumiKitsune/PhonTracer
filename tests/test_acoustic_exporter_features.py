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

    def _tone_effect_entry(self, group, front_level, back_level, label=None, speaker="Speaker 1"):
        front_curve = [front_level, front_level + back_level * 0.15, back_level]
        back_curve = [front_level * 0.35 + back_level * 0.65, back_level, back_level]
        return {
            'speaker_name': speaker,
            'group': group,
            'label': label or group,
            'syl_data': [(0.5, front_curve), (0.5, back_curve)],
            'normalized_syl_data': [(0.5, front_curve), (0.5, back_curve)],
        }

    def _tone_effect_dataset(self):
        return [
            self._tone_effect_entry("阴平+阴平", 4.7, 4.6),
            self._tone_effect_entry("阴平+阳平", 4.7, 2.4),
            self._tone_effect_entry("阳平+阴平", 2.3, 4.6),
            self._tone_effect_entry("阳平+阳平", 2.3, 2.4),
        ]

    def _grouping_exporter(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        return AcousticChartExporter(project_tree=project_tree)

    def test_chart_group_rule_defaults_to_original_group(self):
        exporter = self._grouping_exporter()
        entries = [
            {"group": "阴平", "label": "妈", "item_tags": ["目标词"], "item_meta": {"结构": "单字"}},
            {"group": "阳平", "label": "麻", "item_tags": ["填充词"], "item_meta": {"结构": "单字"}},
        ]

        grouped = exporter._apply_chart_grouping(entries)

        self.assertEqual([entry["group"] for entry in grouped], ["阴平", "阳平"])
        self.assertEqual(grouped[0]["wordlist_group"], "阴平")
        self.assertEqual(grouped[0]["chart_group"], "阴平")

    def test_chart_group_rule_can_group_by_item_tags_and_duplicate_multitag_items(self):
        exporter = self._grouping_exporter()
        entries = [
            {"group": "阴平", "label": "妈", "item_tags": ["目标词", "单字"]},
            {"group": "阳平", "label": "麻", "item_tags": ["填充词"]},
        ]
        rule = {"source": "item_tags", "tag_mode": "each", "selected_values": ["目标词", "单字"]}

        grouped = exporter._apply_chart_grouping(entries, rule=rule)

        self.assertEqual([entry["group"] for entry in grouped], ["目标词", "单字"])
        self.assertEqual([entry["label"] for entry in grouped], ["妈", "妈"])
        self.assertEqual({entry["wordlist_group"] for entry in grouped}, {"阴平"})

    def test_chart_group_rule_uses_current_dialog_rule_when_rule_is_not_passed(self):
        exporter = self._grouping_exporter()
        exporter.chart_group_rule = {"source": "item_tags", "tag_mode": "each", "selected_values": ["目标词"]}
        entries = [
            {"group": "阴平", "label": "妈", "item_tags": ["目标词"]},
            {"group": "阳平", "label": "麻", "item_tags": ["填充词"]},
        ]

        grouped = exporter._apply_chart_grouping(entries)

        self.assertEqual([entry["group"] for entry in grouped], ["目标词"])

    def test_chart_group_rule_can_filter_by_tag_but_keep_default_group(self):
        exporter = self._grouping_exporter()
        entries = [
            {"group": "阴平", "label": "妈", "item_tags": ["目标词"]},
            {"group": "阳平", "label": "麻", "item_tags": ["填充词"]},
        ]
        rule = {"source": "item_tags", "tag_mode": "filter_default", "selected_values": ["目标词"]}

        grouped = exporter._apply_chart_grouping(entries, rule=rule)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["group"], "阴平")
        self.assertEqual(grouped[0]["label"], "妈")

    def test_chart_group_rule_can_group_by_custom_item_meta_field(self):
        exporter = self._grouping_exporter()
        entries = [
            {"group": "阴平", "label": "妈", "item_meta": {"结构": "单字"}},
            {"group": "阳平", "label": "妈妈", "item_meta": {"结构": "双字"}},
            {"group": "上声", "label": "马", "item_meta": {}},
        ]
        rule = {"source": "item_meta", "field_name": "结构"}

        grouped = exporter._apply_chart_grouping(entries, rule=rule)

        self.assertEqual([entry["group"] for entry in grouped], ["单字", "双字", "未标注"])

    def test_tone_effect_time_preserves_original_group_when_custom_rule_is_active(self):
        exporter = self._grouping_exporter()
        exporter.params = {
            "chart_type": "tone_effect_time",
            "chart_group_rule": {"source": "item_tags", "tag_mode": "each", "selected_values": ["目标词"]},
        }
        entries = [
            {"group": "阴平+阳平", "label": "妈妈", "item_tags": ["目标词"]},
        ]

        grouped = exporter._apply_chart_grouping(entries)

        self.assertEqual(grouped[0]["group"], "阴平+阳平")
        self.assertEqual(grouped[0]["wordlist_group"], "阴平+阳平")
        self.assertEqual(grouped[0]["chart_group"], "阴平+阳平")

    def test_group_filter_bulk_actions_only_touch_visible_groups(self):
        class FakeTextVar:
            def __init__(self, value=""):
                self.value = value

            def get(self):
                return self.value

        class FakeBoolVar:
            def __init__(self, value=False):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = bool(value)

        dlg = AcousticChartExportDialog.__new__(AcousticChartExportDialog)
        dlg.search_group_var = FakeTextVar("阳")
        dlg.entry_min_count = FakeTextVar("0")
        dlg.available_groups = ["阴平", "阳平", "阳去", "上声"]
        dlg.group_counts = {"阴平": 10, "阳平": 12, "阳去": 8, "上声": 5}
        dlg.group_checkbox_vars = {name: FakeBoolVar(False) for name in dlg.available_groups}
        dlg._on_group_filter_changed = MagicMock()

        self.assertEqual(dlg._visible_group_names(), ["阳平", "阳去"])

        dlg._select_all_groups()
        self.assertFalse(dlg.group_checkbox_vars["阴平"].get())
        self.assertTrue(dlg.group_checkbox_vars["阳平"].get())
        self.assertTrue(dlg.group_checkbox_vars["阳去"].get())
        self.assertFalse(dlg.group_checkbox_vars["上声"].get())

        dlg._reverse_groups()
        self.assertFalse(dlg.group_checkbox_vars["阳平"].get())
        self.assertFalse(dlg.group_checkbox_vars["阳去"].get())
        self.assertFalse(dlg.group_checkbox_vars["阴平"].get())
        self.assertFalse(dlg.group_checkbox_vars["上声"].get())

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

    def test_tone_effect_time_type_only_available_for_two_syllable_tone_pairs(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)

        single_syllable_data = [{
            'speaker_name': 'Speaker 1',
            'group': '阴平',
            'label': '妈',
            'syl_data': [(0.5, [4.0, 4.2, 4.1])],
            'normalized_syl_data': [(0.5, [4.0, 4.2, 4.1])],
        }]
        self.assertNotIn("二字组调类效应时间进程", exporter._get_tone_chart_type_values(single_syllable_data))

        two_syllable_data = self._tone_effect_dataset()
        self.assertIn("二字组调类效应时间进程", exporter._get_tone_chart_type_values(two_syllable_data))

    def test_f0_normalization_bounds_ignore_octave_tail(self):
        project_tree = MagicMock()
        exporter = AcousticChartExporter(project_tree=project_tree)
        normal_band = np.linspace(95.0, 150.0, 120)
        octave_tail = np.linspace(430.0, 580.0, 18)
        raw_values = np.concatenate([normal_band, octave_tail])

        _s_min, s_max = exporter._get_f0_normalization_bounds(raw_values)

        self.assertLess(s_max, 160.0)

    def test_tone_effect_time_plot_draws_three_effect_curves(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {'chart_type': 'tone_effect_time', 'legend_loc': '右上'}

        fig = exporter.generate_plot(self._tone_effect_dataset(), is_preview=False)
        labels = [line.get_label() for line in fig.axes[0].get_lines()]

        self.assertIn("前字调类", labels)
        self.assertIn("后字调类", labels)
        self.assertIn("交互项/残差", labels)

    def test_tone_effect_time_respects_selected_group_subset_data(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)

        full_data = self._tone_effect_dataset()
        subset_data = [entry for entry in full_data if entry['group'] in {"阴平+阴平", "阴平+阳平"}]

        self.assertTrue(exporter._has_tone_effect_time_data(full_data))
        self.assertFalse(exporter._has_tone_effect_time_data(subset_data))

    def test_tone_effect_time_export_does_not_paginate_many_groups(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {'chart_type': 'tone_effect_time', 'groupby': 'group', 'scale': 't_value'}
        large_data = []
        for idx in range(12):
            front = "阴平" if idx % 2 == 0 else "阳平"
            back = "阴平" if idx % 3 == 0 else "阳平"
            large_data.append(self._tone_effect_entry(f"{front}+{back}-{idx}", 4.5 if front == "阴平" else 2.2, 4.4 if back == "阴平" else 2.1))

        exporter._export_paginated_images = MagicMock()
        exporter._export_paginated_pdf = MagicMock()
        exporter.generate_plot = MagicMock(return_value=MagicMock())
        exporter._save_figure = MagicMock()

        exporter._export_dataset(large_data, 'tone_effect.png', '.png')

        exporter._export_paginated_images.assert_not_called()
        exporter._export_paginated_pdf.assert_not_called()
        exporter.generate_plot.assert_called_once()
        exporter._save_figure.assert_called_once()

    def test_tone_overview_metric_values_include_combination_heatmap_for_two_syllables(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)

        single_syllable_data = [{
            'speaker_name': 'Speaker 1',
            'group': '阴平',
            'label': '妈',
            'syl_data': [(0.5, [4.0, 4.2, 4.1])],
            'normalized_syl_data': [(0.5, [4.0, 4.2, 4.1])],
        }]
        self.assertNotIn("调类组合前后字均值热图", exporter._get_tone_overview_metric_values(data_entries=single_syllable_data))
        self.assertIn("调类组合前后字均值热图", exporter._get_tone_overview_metric_values(data_entries=self._tone_effect_dataset()))

    def test_tone_overview_combination_heatmap_draws_front_and_back_matrices(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {'overview_metric': '调类组合前后字均值热图'}

        fig = exporter._plot_tone_overview_heatmap(self._tone_effect_dataset(), "group", "T 值")

        self.assertEqual(len(fig.axes[0].images), 1)
        self.assertEqual(len(fig.axes[1].images), 1)
        self.assertIn("前字平均五度值", fig.axes[0].get_title())
        self.assertIn("后字平均五度值", fig.axes[1].get_title())

    def test_tone_overview_combination_heatmap_disables_appendix_pagination(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 3}
        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'overview_heatmap',
            'groupby': '按词语',
            'intention': '附录图册 (完整数据)',
            'overview_metric': '调类组合前后字均值热图',
        }

        large_data = []
        combos = self._tone_effect_dataset()
        for idx in range(12):
            entry = dict(combos[idx % len(combos)])
            entry['label'] = f"词{idx}"
            large_data.append(entry)

        state = exporter._get_group_pagination_state(large_data, 'overview_heatmap', '按词语')

        self.assertFalse(state['is_paginated_heatmap'])
        self.assertEqual(state['total_pages'], 1)

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

    def test_overview_heatmap_plot_with_deviation(self):
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

            # Enable deviation mode
            dlg.var_overview_show_deviation.set(True)

            dlg.combo_overview_metric = MagicMock()
            dlg.combo_overview_metric.get.return_value = "均值热图 (Mean Map)"

            fig = dlg._plot_tone_overview_heatmap(dummy_data, "group", "T 值")
            self.assertIsNotNone(fig)

            # Verify that RdBu_r colormap is used and color limits are symmetric
            ax = fig.axes[0]
            self.assertTrue(len(ax.images) > 0)
            im = ax.images[0]
            self.assertEqual(im.get_cmap().name, 'RdBu_r')
            clim = im.get_clim()
            self.assertIsNotNone(clim)
            self.assertAlmostEqual(clim[0], -clim[1])

            dlg.destroy()

    def test_overview_heatmap_metric_change_callback(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}
        speaker = MagicMock()
        speaker.name = "Speaker 1"
        app = MagicMock()
        app.speaker_manager.get_active_speaker.return_value = speaker

        with patch.object(AcousticChartExportDialog, 'update_preview'):
            dlg = AcousticChartExportDialog(
                self.root, app=app, project_tree=project_tree,
                mode='integrated', all_speakers=[speaker]
            )

            # Switch chart type to overview heatmap to build settings widgets
            dlg.combo_type.set("声调组别概览图")
            dlg._on_type_changed("声调组别概览图")

            # Initially, metric is Mean Map, deviation checkbox should be normal
            self.assertEqual(dlg.cb_show_deviation.cget("state"), "normal")

            # Switch to Standard Deviation Map
            dlg.combo_overview_metric.set("标准差热图 (SD Map)")
            dlg._on_overview_metric_changed("标准差热图 (SD Map)")

            # Deviation checkbox should now be disabled and its variable set to False
            self.assertEqual(dlg.cb_show_deviation.cget("state"), "disabled")
            self.assertFalse(dlg.var_overview_show_deviation.get())

            # Switch back to Mean Map
            dlg.combo_overview_metric.set("均值热图 (Mean Map)")
            dlg._on_overview_metric_changed("均值热图 (Mean Map)")

            # Deviation checkbox should be re-enabled
            self.assertEqual(dlg.cb_show_deviation.cget("state"), "normal")

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

    def test_density_hz_scale_uses_absolute_contours(self):
        project_tree = MagicMock()
        project_tree._get_syllables_and_bounds.return_value = ([], [(0.0, 1.0)])
        project_tree._extract_kde_contour.return_value = np.linspace(100.0, 140.0, 100)

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'density',
            'export_scope': 'integrated',
            'density_facet': 'none',
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
            fig = exporter._plot_temporal_density(data_entries, "group", scale="Hz")

        self.assertIsNotNone(fig)
        project_tree._extract_kde_contour.assert_called()

    def test_density_groupby_alignment(self):
        project_tree = MagicMock()
        project_tree._get_syllables_and_bounds.return_value = ([], [(0.0, 1.0)])
        project_tree._extract_kde_contour.return_value = np.linspace(100.0, 140.0, 100)

        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.params = {
            'chart_type': 'density',
            'export_scope': 'integrated',
            'density_facet': '声调类型分面 (默认)',
        }

        data_entries = [
            {
                'speaker_name': 'SpeakerA',
                'group': 'Tone1',
                'label': 'Word1',
                'syl_data': [(1.0, [100.0, 110.0, 120.0])],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_freqs': np.linspace(100.0, 140.0, 20),
                'normalized_raw_freqs': np.linspace(1.0, 4.0, 20),
                'raw_item': {},
            },
            {
                'speaker_name': 'SpeakerB',
                'group': 'Tone2',
                'label': 'Word2',
                'syl_data': [(1.0, [100.0, 110.0, 120.0])],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_freqs': np.linspace(100.0, 140.0, 20),
                'normalized_raw_freqs': np.linspace(1.0, 4.0, 20),
                'raw_item': {},
            }
        ]

        class FakeKDE:
            def __init__(self, positions, bw_method=None):
                self.positions = positions

            def __call__(self, values):
                return np.ones(values.shape[1])

        with patch('modules.acoustic_exporter.gaussian_kde', FakeKDE):
            # Test By Word
            fig_word = exporter._plot_temporal_density(data_entries, "label", scale="T 值")
            titles_word = [ax.get_title() for ax in fig_word.axes if ax.get_title()]
            self.assertIn("Word1", titles_word)
            self.assertIn("Word2", titles_word)

            # Test By Speaker
            fig_spk = exporter._plot_temporal_density(data_entries, "speaker_name", scale="T 值")
            titles_spk = [ax.get_title() for ax in fig_spk.axes if ax.get_title()]
            self.assertIn("SpeakerA", titles_spk)
            self.assertIn("SpeakerB", titles_spk)

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
            dlg.after_cancel.assert_any_call("timer_123")

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

    def test_formant_overview_heatmap_uses_same_paginated_album_flow(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 5}
        exporter = AcousticChartExporter(project_tree=project_tree)

        large_data = []
        for i in range(25):
            large_data.append({
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': f'word_{i:02d}',
                'syl_formants': [{
                    'char': 'a',
                    'bounds': (0.0, 1.0),
                    'f1': [500.0, 510.0, 520.0, 530.0, 540.0],
                    'f2': [1500.0, 1490.0, 1480.0, 1470.0, 1460.0],
                }],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_f1': np.linspace(500.0, 540.0, 20),
                'raw_f2': np.linspace(1500.0, 1460.0, 20),
            })

        exporter.params = {
            'chart_type': 'formant_overview_heatmap',
            'groupby': '按词语',
            'intention': '附录图册 (完整数据)',
            'scale': 'hz',
            'overview_metric': 'mean',
            'formant_overview_mode': 'F1 & F2 双轨',
            'formant_normalization': '原始频率 (Hz)',
        }

        exporter._export_overview_heatmap_paginated_pdf = MagicMock()
        exporter._export_overview_heatmap_paginated_images = MagicMock()

        exporter._export_dataset(large_data, 'formant_dummy_out.pdf', '.pdf')

        exporter._export_overview_heatmap_paginated_pdf.assert_called_once()
        args = exporter._export_overview_heatmap_paginated_pdf.call_args[0]
        self.assertEqual(args[0], 'formant_dummy_out.pdf')
        pages = args[3]
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[0]), 20)
        self.assertEqual(len(pages[1]), 5)

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

    def test_formant_density_heatmap_plot(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)
        
        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_formants': [
                    {
                        'char': 'ma',
                        'bounds': (0.1, 0.9),
                        'f1': [500.0, 510.0, 520.0, 530.0, 540.0, 550.0, 560.0, 570.0, 580.0, 590.0, 600.0],
                        'f2': [1500.0, 1490.0, 1480.0, 1470.0, 1460.0, 1450.0, 1440.0, 1430.0, 1420.0, 1410.0, 1400.0]
                    }
                ],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_f1': np.linspace(500.0, 600.0, 20),
                'raw_f2': np.linspace(1500.0, 1400.0, 20)
            }
        ]

        exporter.params = {
            'groupby': 'group',
            'formant_density_show_raw': True,
            'formant_density_show_contours': True,
        }
        fig = exporter._plot_formant_density_heatmap(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig)

    def test_formant_density_colorbar_stays_outside_axes_with_external_legend(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)

        dummy_data = []
        for idx, group in enumerate(["Group1", "Group2"]):
            dummy_data.append({
                'speaker_name': 'Speaker 1',
                'group': group,
                'label': f'ma{idx}',
                'syl_formants': [{
                    'char': f'ma{idx}',
                    'bounds': (0.1, 0.9),
                    'f1': [500.0 + idx * 80 + k * 4 for k in range(11)],
                    'f2': [1600.0 - idx * 120 - k * 8 for k in range(11)]
                }],
                'raw_xs': np.linspace(0.0, 1.0, 24),
                'raw_f1': np.linspace(500.0 + idx * 80, 620.0 + idx * 80, 24),
                'raw_f2': np.linspace(1600.0 - idx * 120, 1380.0 - idx * 120, 24)
            })

        exporter.params = {
            'groupby': 'group',
            'formant_label_mode': '显示分组标签',
            'formant_ellipse': '1-sigma 置信椭圆',
            'formant_show_raw': True,
            'formant_time_gradient': False,
            'formant_density_overlay': True,
            'formant_density_bw': 0.14,
            'formant_density_show_raw': False,
            'formant_density_show_contours': True,
            'formant_density_facet': '单图展示 (不分面)',
            'legend_loc': '右上',
            'legend_outside': True,
        }

        fig = exporter._plot_formant_vowel_space(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig)
        self.assertTrue(getattr(fig, "_phontracer_skip_tight_layout", False))

        plot_ax = fig.axes[0]
        colorbar_ax = fig.axes[-1]
        self.assertGreater(colorbar_ax.get_position().x0, plot_ax.get_position().x1)

    def test_formant_space_group_pagination_uses_multiple_pages(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)

        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': f'G{i}',
                'label': f'L{i}',
                'syl_formants': [],
                'raw_xs': [],
                'raw_f1': [],
                'raw_f2': [],
            }
            for i in range(10)
        ]

        state = exporter._get_group_pagination_state(dummy_data, 'formant_space', 'group')
        self.assertEqual(state['total_groups'], 10)
        self.assertEqual(state['total_pages'], 2)

    def test_formant_space_axis_lock_shares_limits_across_facets(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)

        def make_entry(group_name, base_f1, base_f2):
            xs = np.linspace(0.0, 1.0, 30)
            raw_f1 = np.linspace(base_f1, base_f1 + 120.0, 30)
            raw_f2 = np.linspace(base_f2, base_f2 - 200.0, 30)
            return {
                'speaker_name': 'Speaker 1',
                'group': group_name,
                'label': f'{group_name}_label',
                'syl_formants': [{
                    'char': group_name,
                    'bounds': (0.1, 0.9),
                    'f1': np.linspace(base_f1 + 5.0, base_f1 + 95.0, 11).tolist(),
                    'f2': np.linspace(base_f2 - 5.0, base_f2 - 155.0, 11).tolist(),
                }],
                'raw_xs': xs.tolist(),
                'raw_f1': raw_f1.tolist(),
                'raw_f2': raw_f2.tolist(),
            }

        dummy_data = [
            make_entry('A', 400.0, 2000.0),
            make_entry('B', 900.0, 1400.0),
        ]

        exporter.params = {
            'groupby': 'group',
            'formant_label_mode': '显示分组标签',
            'formant_ellipse': '1-sigma 置信椭圆',
            'formant_show_raw': True,
            'formant_time_gradient': False,
            'formant_density_overlay': False,
            'formant_density_facet': '按字表组分面',
            'formant_normalization': '原始频率 (Hz)',
            'formant_axis_lock': True,
            'legend_loc': '右上',
            'legend_outside': False,
            'formant_axis_ref_entries': dummy_data,
        }

        fig = exporter._plot_formant_vowel_space(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig)
        self.assertGreaterEqual(len(fig.axes), 2)

        ax1, ax2 = fig.axes[0], fig.axes[1]
        self.assertEqual(ax1.get_xlim(), ax2.get_xlim())
        self.assertEqual(ax1.get_ylim(), ax2.get_ylim())

    def test_formant_overview_heatmap_plot(self):
        project_tree = MagicMock()
        project_tree.app_state_params = {'pts': 11}
        exporter = AcousticChartExporter(project_tree=project_tree)
        exporter.available_groups = ['Group1']
        exporter.colors = ['#2563EB']
        
        dummy_data = [
            {
                'speaker_name': 'Speaker 1',
                'group': 'Group1',
                'label': 'ma',
                'syl_formants': [
                    {
                        'char': 'ma',
                        'bounds': (0.1, 0.9),
                        'f1': [500.0, 510.0, 520.0, 530.0, 540.0, 550.0, 560.0, 570.0, 580.0, 590.0, 600.0],
                        'f2': [1500.0, 1490.0, 1480.0, 1470.0, 1460.0, 1450.0, 1440.0, 1430.0, 1420.0, 1410.0, 1400.0]
                    }
                ],
                'raw_xs': np.linspace(0.0, 1.0, 20),
                'raw_f1': np.linspace(500.0, 600.0, 20),
                'raw_f2': np.linspace(1500.0, 1400.0, 20)
            }
        ]

        # 1. Test F1 & F2 dual-track
        exporter.params = {
            'groupby': 'group',
            'overview_metric': 'mean',
            'formant_overview_mode': 'F1 & F2 双轨',
            'formant_normalization': '原始频率 (Hz)',
        }
        fig = exporter._plot_formant_overview_heatmap(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig)
        self.assertEqual(len(fig.axes), 4) # 2 subplots + 2 colorbars = 4 axes

        # 2. Test Ratio track
        exporter.params = {
            'groupby': 'group',
            'overview_metric': 'sd',
            'formant_overview_mode': 'F2 / F1 比值',
            'formant_normalization': '原始频率 (Hz)',
        }
        fig2 = exporter._plot_formant_overview_heatmap(dummy_data, 'group', 'Hz')
        self.assertIsNotNone(fig2)
        self.assertEqual(len(fig2.axes), 2) # 1 subplot + 1 colorbar = 2 axes

if __name__ == '__main__':
    unittest.main()
