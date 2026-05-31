import unittest
import os
import shutil
import numpy as np
import parselmouth
from unittest.mock import MagicMock, patch
from cli import PhonTracerCLI, AcousticChartCLIAdapter
from modules.acoustic_exporter import AcousticChartExporter

class TestCliAdvancedExport(unittest.TestCase):
    def setUp(self):
        # Create temp directory for testing exports
        self.test_dir = os.path.abspath("temp_test_exports").replace('\\', '/')
        os.makedirs(self.test_dir, exist_ok=True)
        
        # Mock active speaker
        self.speaker = MagicMock()
        self.speaker.name = "TestSpeaker"
        self.speaker.tab_mode = "多条独立音频"
        
        dummy_sound = MagicMock()
        dummy_sound.get_total_duration.return_value = 1.0
        
        self.speaker.items = {
            "item_1": {
                'label': 'ma',
                'group': 'T1',
                'start': 0.0,
                'end': 1.0,
                'snd': dummy_sound,
                'pitch_data': {
                    'xs': np.linspace(0.0, 1.0, 100),
                    'freqs': np.linspace(100.0, 200.0, 100)
                },
                'warnings': [],
                'success': True,
                'path': 'dummy.wav'
            },
            "item_2": {
                'label': 'ba',
                'group': 'T2',
                'start': 0.0,
                'end': 1.0,
                'snd': dummy_sound,
                'pitch_data': {
                    'xs': np.linspace(0.0, 1.0, 100),
                    'freqs': np.linspace(100.0, 200.0, 100)
                },
                'warnings': [],
                'success': True,
                'path': 'dummy.wav'
            }
        }
        self.speaker.last_params = {
            'pts': 11,
            'pitch_floor': 75.0,
            'pitch_ceiling': 600.0,
            'voicing_threshold': 0.25,
        }
        self.speaker.cli_groups = ['T1', 'T2']

        # Setup CLI instance
        self.cli = PhonTracerCLI()
        self.cli.speaker_manager = MagicMock()
        self.cli.speaker_manager.get_active_speaker.return_value = self.speaker
        self.cli.speaker_manager.get_all_speakers.return_value = [self.speaker]

    def tearDown(self):
        # Clean up temp test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_cli_adapter_and_exporter_direct(self):
        adapter = AcousticChartCLIAdapter(self.cli)
        self.assertEqual(adapter.items, self.speaker.items)
        
        # Test item bounds extraction
        syls, bounds = adapter._get_syllables_and_bounds(self.speaker.items["item_1"])
        self.assertEqual(syls, ["ma"])
        self.assertEqual(bounds, [[0.0, 1.0]])

        # Test pitch arrays extraction
        p_xs, p_freqs = adapter._get_pitch_arrays_for_item(self.speaker.items["item_1"])
        self.assertIsNotNone(p_xs)
        self.assertIsNotNone(p_freqs)
        self.assertEqual(len(p_xs), 100)

        # Test exporter compilation and parameters lookup in GUI-less base class
        exporter = AcousticChartExporter(project_tree=adapter, app=self.cli, all_speakers=[self.speaker])
        exporter.params = {'scale': 'hz', 'groupby': 'label'}
        self.assertEqual(exporter.get_param('scale'), 'hz')
        self.assertEqual(exporter.get_param('groupby'), 'label')
        self.assertEqual(exporter.get_param('non_existent', 'default_val'), 'default_val')

    def test_cli_exports_all_scientific_types(self):
        chart_types = ['contour', 'distribution', 'density', 'quality', 'overview_heatmap']
        
        for chart in chart_types:
            out_file = f"{self.test_dir}/test_{chart}.png"
            cmd_arg = f"{chart} {out_file} active scale=hz groupby=label"
            self.cli.do_export(cmd_arg)
            self.assertTrue(os.path.exists(out_file))

    def test_cli_export_advanced_parameter_parsing(self):
        out_file = f"{self.test_dir}/test_params.svg"
        cmd_arg = "contour {} density_bw=0.12 scale=t_value groupby=speaker selected_groups=T1".format(out_file)
        
        real_exporter = None
        orig_exporter = AcousticChartExporter
        
        def spy_create(*args, **kwargs):
            nonlocal real_exporter
            real_exporter = orig_exporter(*args, **kwargs)
            return real_exporter
            
        with patch('cli.AcousticChartExporter', side_effect=spy_create):
            self.cli.do_export(cmd_arg)
            
        self.assertIsNotNone(real_exporter)
        params = real_exporter.params
        self.assertEqual(params.get('density_bw'), 0.12)
        self.assertEqual(params.get('scale'), 't_value')
        self.assertEqual(params.get('groupby'), 'speaker')
        self.assertEqual(params.get('selected_groups'), 'T1')
        self.assertTrue(os.path.exists(out_file))

    def test_cli_export_accepts_generic_rule_target_positions(self):
        out_file = f"{self.test_dir}/test_integrated.png"

        real_exporter = None
        orig_exporter = AcousticChartExporter

        def spy_create(*args, **kwargs):
            nonlocal real_exporter
            real_exporter = orig_exporter(*args, **kwargs)
            return real_exporter

        with patch('cli.AcousticChartExporter', side_effect=spy_create):
            self.cli.do_export(f"contour {out_file} continuous integrated scale=hz")

        self.assertIsNotNone(real_exporter)
        self.assertEqual(real_exporter.params.get('export_scope'), 'integrated')
        self.assertTrue(os.path.exists(out_file))

    def test_cli_export_maps_facet_alias_by_chart_type(self):
        contour_file = f"{self.test_dir}/test_contour_facet.png"
        density_file = f"{self.test_dir}/test_density_facet.png"
        created_exporters = []
        orig_exporter = AcousticChartExporter

        def spy_create(*args, **kwargs):
            exporter = orig_exporter(*args, **kwargs)
            created_exporters.append(exporter)
            return exporter

        with patch('cli.AcousticChartExporter', side_effect=spy_create):
            self.cli.do_export(f"contour {contour_file} facet=group")
            self.cli.do_export(f"density {density_file} facet=label")

        self.assertEqual(created_exporters[0].params.get('contour_facet'), 'group')
        self.assertNotIn('facet', created_exporters[0].params)
        self.assertEqual(created_exporters[1].params.get('density_facet'), 'label')
        self.assertNotIn('facet', created_exporters[1].params)
        self.assertTrue(os.path.exists(contour_file))
        self.assertTrue(os.path.exists(density_file))

    def test_cli_export_separate_directory_creation(self):
        out_dir = f"{self.test_dir}/separate_charts"
        
        # Mock other speaker
        other_speaker = MagicMock()
        other_speaker.name = "OtherSpeaker"
        other_speaker.tab_mode = "多条独立音频"
        other_speaker.items = self.speaker.items
        other_speaker.last_params = self.speaker.last_params
        other_speaker.cli_groups = self.speaker.cli_groups
        
        self.cli.speaker_manager.get_all_speakers.return_value = [self.speaker, other_speaker]
        
        cmd_arg = f"contour {out_dir} separate"
        self.cli.do_export(cmd_arg)
        
        self.assertTrue(os.path.exists(out_dir))
        self.assertTrue(os.path.exists(os.path.join(out_dir, "TestSpeaker_contour.png")))
        self.assertTrue(os.path.exists(os.path.join(out_dir, "OtherSpeaker_contour.png")))

if __name__ == '__main__':
    unittest.main()
