import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np

from tests.shared_root import get_shared_root
from modules.app import PhoneticsApp
from modules.spectrogram_panel import SpectrogramPanel

mock_img = MagicMock()
type(mock_img).size = PropertyMock(return_value=(100, 100))

class TestUISyncBugs(unittest.TestCase):
    def setUp(self):
        self.root = get_shared_root()

        with patch('PIL.Image.open', return_value=mock_img):
            with patch.object(PhoneticsApp, 'setup_icons'), \
                 patch.object(PhoneticsApp, 'setup_ui'), \
                 patch.dict('sys.modules', {'windnd': MagicMock()}):
                self.app = PhoneticsApp(self.root)
                self.app.icons = {}
                self.app.tk_icons = {}
                self.app.tree_panel = MagicMock()
                self.app.spectrogram_panel = MagicMock()

        self.item_id = "test_item_1"
        self.mock_snd = MagicMock()
        self.mock_snd.get_total_duration.return_value = 10.0

        self.mock_pitch = MagicMock()
        self.mock_pitch.xs.return_value = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        self.mock_pitch.selected_array = {'frequency': np.array([0.0, 120.0, 130.0, 0.0, 0.0])}

        self.item = {
            'label': 'A',
            'group': 'Group1',
            'start': 0.1,
            'end': 0.9,
            'macro_start': 0.0,
            'macro_end': 1.0,
            'snd': self.mock_snd,
            'pitch': self.mock_pitch,
            'chars_bounds': [[0.1, 0.9]],
            'inner_splits': [],
            'pitch_floor': 75,
            'pitch_ceiling': 600,
            'voicing_threshold': 0.25,
            'preview_f0': [100.0] * 11,
            'has_empty_data': False
        }

        self.app.items[self.item_id] = self.item
        self.app.spectrogram_panel.current_item = self.item

    def test_on_spectrogram_time_changed_clears_cache(self):
        """Test that manually changing time clears the preview cache"""
        self.assertTrue('preview_f0' in self.item)
        self.assertTrue('has_empty_data' in self.item)

        self.app.on_spectrogram_time_changed(self.item)

        self.assertFalse('preview_f0' in self.item)
        self.assertFalse('has_empty_data' in self.item)

    def test_apply_manual_time_sets_flag(self):
        """Test that applying manual time bounds sets is_manual_edited flag"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.current_item = self.item
            panel.var_t_start = MagicMock()
            panel.var_t_end = MagicMock()
            panel.var_t_start.get.return_value = "0.2"
            panel.var_t_end.get.return_value = "0.8"
            panel.plot_item_spectrogram = MagicMock()
            panel.update_ui_times = MagicMock()

            panel.apply_manual_time()

        self.assertEqual(self.item['start'], 0.2)
        self.assertEqual(self.item['end'], 0.8)
        self.assertTrue(self.item.get('is_manual_edited'))

    def test_wordlist_drag_and_drop_txt(self):
        """Test that dropping a .txt file onto the wordlist dialog imports the file content"""
        # Set up a mock dialog
        mock_dlg = MagicMock()
        mock_textbox = MagicMock()
        mock_update_stats = MagicMock()
        
        self.app.active_import_dlg = mock_dlg
        self.app.active_import_textbox = mock_textbox
        self.app.active_import_update_stats = mock_update_stats
        self.app.active_import_mode = 'long'
        
        # Simulate dropping a .txt file path (in bytes or string)
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"Group1\nword1 word2")
            f_path = f.name
            
        try:
            # Put the drag and drop item in the drop queue
            self.app.drop_queue.put(('dlg', [f_path.encode('utf-8')]))
            
            # Check the drop queue
            self.app._check_drop_queue()
            
            # Assert text_box.delete and text_box.insert were called
            mock_textbox.delete.assert_called_with("1.0", "end")
            mock_textbox.insert.assert_called_with("1.0", "Group1\nword1 word2")
            mock_update_stats.assert_called_once()
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)

    def test_add_segment_by_right_click(self):
        """Test right-clicking on an empty area adds a 0.5s segment and fits it in sequence"""
        from modules.visual_splitter import VisualSplitter
        
        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 10.0
        mock_snd.values = [np.zeros(20000)]
        mock_snd.sampling_frequency = 2000
        
        callback = MagicMock()
        
        existing_items = [
            {'id': 0, 'label': 'A', 'start': 1.0, 'end': 2.0},
            {'id': 1, 'label': 'B', 'start': 4.0, 'end': 5.0}
        ]
        
        with patch.object(VisualSplitter, 'setup_ui'), \
             patch.object(VisualSplitter, 'init_data'), \
             patch.object(VisualSplitter, 'update_dynamic_labels'), \
             patch.object(VisualSplitter, 'render_canvas'), \
             patch.object(VisualSplitter, 'auto_fit_scale'):
             
            splitter = VisualSplitter(
                master=self.root,
                snd=mock_snd,
                icons={},
                callback=callback,
                existing_items=existing_items
            )
            
            splitter.original_words = [
                {'id': 0, 'label': 'A'},
                {'id': 1, 'label': 'B'}
            ]
            splitter.deleted_indices = set()
            
            mock_event = MagicMock()
            splitter.px_per_sec = 100
            mock_event.x = 300
            splitter.canvas = MagicMock()
            splitter.canvas.canvasx.return_value = 300.0
            
            splitter.on_right_click(mock_event)
            
            self.assertEqual(len(splitter.segments), 3)
            self.assertEqual(splitter.segments[0]['start'], 1.0)
            self.assertEqual(splitter.segments[1]['start'], 2.75) # 3.0 - 0.25
            self.assertEqual(splitter.segments[1]['end'], 3.25)   # 3.0 + 0.25
            self.assertEqual(splitter.segments[2]['start'], 4.0)

    def test_spectrogram_panel_eraser_blitting_and_warning_fix(self):
        """Test that the eraser circle is created without warnings and uses blitting for high performance"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.eraser_mode = True
            panel.ax2 = MagicMock()
            panel.fig = MagicMock()
            panel.canvas = MagicMock()
            
            # Mock the background for blitting
            mock_bg = MagicMock()
            panel.background = mock_bg
            
            # Simulate a motion event inside axes
            mock_event = MagicMock()
            mock_event.x = 200
            mock_event.y = 300
            mock_event.inaxes = panel.ax2
            
            # The circle is originally None, it should be created
            self.assertIsNone(panel.eraser_circle)
            panel.update_eraser_circle(mock_event)
            
            # Circle should be created, animated=True, and added to ax2.patches
            self.assertIsNotNone(panel.eraser_circle)
            self.assertTrue(panel.eraser_circle.get_animated())
            panel.ax2.add_patch.assert_called_once_with(panel.eraser_circle)
            
            # Calling restore_region, draw_artist, and blit should be invoked for high-performance blitting
            panel.canvas.restore_region.assert_called_once_with(mock_bg)
            panel.ax2.draw_artist.assert_called_once_with(panel.eraser_circle)
            panel.canvas.blit.assert_called_once_with(panel.fig.bbox)
            
            # Test that on_draw updates background
            draw_event = MagicMock()
            panel.on_draw(draw_event)
            panel.canvas.copy_from_bbox.assert_called_once_with(panel.fig.bbox)

if __name__ == '__main__':
    unittest.main()
