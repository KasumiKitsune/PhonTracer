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

    def test_main_window_drag_and_drop_txt_no_audio(self):
        """Test that dropping a .txt file onto the main window without audio shows a warning"""
        self.app.pending_long_snd = None
        self.app.pending_batch_paths = []
        self.app.tabview = MagicMock()
        self.app.tabview.get.return_value = "单条长音频"
        self.app.open_text_dialog = MagicMock()

        with patch('tkinter.messagebox.showwarning') as mock_warning:
            self.app.drop_queue.put([b"dummy.txt"])
            self.app._check_drop_queue()
            mock_warning.assert_called_once()
            self.app.open_text_dialog.assert_not_called()

    def test_main_window_drag_and_drop_txt_with_audio(self):
        """Test that dropping a .txt file onto the main window with audio opens dialog and populates it"""
        self.app.pending_long_snd = MagicMock()
        self.app.tabview = MagicMock()
        self.app.tabview.get.return_value = "单条长音频"

        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            f.write(b"Group1\nword1")
            f_path = f.name

        try:
            self.app.open_text_dialog = MagicMock()
            mock_dlg = MagicMock()
            mock_textbox = MagicMock()
            mock_update_stats = MagicMock()
            
            # Setup active import dlg references that would be set by open_text_dialog
            def mock_open(mode):
                self.app.active_import_dlg = mock_dlg
                self.app.active_import_textbox = mock_textbox
                self.app.active_import_update_stats = mock_update_stats
                self.app.active_import_mode = mode

            self.app.open_text_dialog.side_effect = mock_open

            self.app.drop_queue.put([f_path.encode('utf-8')])
            self.app._check_drop_queue()

            self.app.open_text_dialog.assert_called_once_with('long')
            mock_textbox.delete.assert_called_with("1.0", "end")
            mock_textbox.insert.assert_called_with("1.0", "Group1\nword1")
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

    def test_spectrogram_panel_on_motion_ignores_none_coordinates(self):
        """Test that on_motion ignores events with None xdata during dragging to prevent cursor reset"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.current_item = self.item
            panel.dragging = 'cursor'
            panel.cursor_x = 0.5

            mock_event = MagicMock()
            mock_event.xdata = None
            mock_event.x = 200
            mock_event.y = 300

            panel.on_motion(mock_event)

            self.assertEqual(panel.cursor_x, 0.5)

    def test_cursor_release_does_not_sync_bounds(self):
        """Moving the green playback cursor should not be treated as a boundary edit"""
        on_time_changed = MagicMock()
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, on_time_changed, None, None)
            panel.current_item = self.item
            panel.dragging = 'cursor'
            panel.cursor_x = 0.6
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.bound_lines = []
            panel.plot_item_spectrogram = MagicMock()
            panel.canvas = MagicMock()

            mock_event = MagicMock()
            panel.on_release(mock_event)

            panel.plot_item_spectrogram.assert_not_called()
            on_time_changed.assert_not_called()
            self.assertEqual(panel.cursor_x, 0.6)

    def test_shared_boundary_click_prefers_next_character_start(self):
        """A shared red line should select the next character's start, not the previous end"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.ax.transData.transform.side_effect = lambda xy: (xy[0] * 100, 0)
            panel.current_item = {
                **self.item,
                'label': 'A/B',
                'chars_bounds': [[0.1, 0.5], [0.5, 0.9]]
            }
            panel.bound_lines = [(MagicMock(), MagicMock()), (MagicMock(), MagicMock())]
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()

            mock_event = MagicMock()
            mock_event.inaxes = panel.ax
            mock_event.button = 1
            mock_event.x = 50
            mock_event.xdata = 0.5

            panel.on_press(mock_event)

            self.assertEqual(panel.dragging, ('start', 1))
            self.assertEqual(panel.cursor_x, 0.5)
            self.assertEqual(panel.cursor_char_index, 1)
            panel.bound_lines[1][0].set_color.assert_called_with('#047857')

    def test_playback_at_shared_boundary_uses_next_character(self):
        """Playback from a shared boundary should continue through the following character"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            part = MagicMock()
            part.values = np.zeros((1, 10))
            part.sampling_frequency = 1000
            snd = MagicMock()
            snd.get_total_duration.return_value = 1.0
            snd.extract_part.return_value = part
            panel.current_item = {
                **self.item,
                'snd': snd,
                'start': 0.1,
                'end': 0.9,
                'chars_bounds': [[0.1, 0.5], [0.5, 0.9]]
            }
            panel.cursor_x = 0.5
            panel.canvas = MagicMock()
            panel.update_cursor_graphics = MagicMock()
            panel._update_play_button_state = MagicMock()

            with patch('modules.spectrogram_panel.sd.play'):
                panel.play_selected()

            snd.extract_part.assert_called_with(from_time=0.5, to_time=0.9)
            self.assertEqual(panel.play_selection_start, 0.5)
            self.assertEqual(panel.play_end_audio_time, 0.9)

    def test_playback_at_overlapping_boundary_prefers_stored_character(self):
        """The cursor's remembered character should win when adjacent bounds overlap"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            part = MagicMock()
            part.values = np.zeros((1, 10))
            part.sampling_frequency = 1000
            snd = MagicMock()
            snd.get_total_duration.return_value = 1.0
            snd.extract_part.return_value = part
            panel.current_item = {
                **self.item,
                'snd': snd,
                'start': 0.1,
                'end': 0.9,
                'chars_bounds': [[0.1, 0.55], [0.5, 0.9]]
            }
            panel.cursor_x = 0.5
            panel.cursor_char_index = 1
            panel.canvas = MagicMock()
            panel.update_cursor_graphics = MagicMock()
            panel._update_play_button_state = MagicMock()

            with patch('modules.spectrogram_panel.sd.play'):
                panel.play_selected()

            snd.extract_part.assert_called_with(from_time=0.5, to_time=0.9)
            self.assertEqual(panel.play_selection_start, 0.5)
            self.assertEqual(panel.play_end_audio_time, 0.9)

    def test_dragging_empty_area_creates_playback_selection_without_editing_bounds(self):
        """Dragging away from red lines should create a temporary playback selection only"""
        on_time_changed = MagicMock()
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, on_time_changed, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.ax.transData.transform.side_effect = lambda xy: (xy[0] * 100, 0)
            panel.ax.axvspan.return_value = MagicMock()
            panel.current_item = {
                **self.item,
                'start': 0.1,
                'end': 0.9,
                'chars_bounds': [[0.1, 0.9]]
            }
            panel.bound_lines = [(MagicMock(), MagicMock())]
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()

            press_event = MagicMock()
            press_event.inaxes = panel.ax
            press_event.button = 1
            press_event.x = 35
            press_event.xdata = 0.35
            panel.on_press(press_event)

            motion_event = MagicMock()
            motion_event.x = 65
            motion_event.xdata = 0.65
            panel.on_motion(motion_event)

            release_event = MagicMock()
            panel.on_release(release_event)

            self.assertEqual(panel.dragging, None)
            self.assertEqual(panel.playback_selection, (0.35, 0.65))
            self.assertEqual(panel.current_item['start'], 0.1)
            self.assertEqual(panel.current_item['end'], 0.9)
            self.assertFalse(panel.current_item.get('is_manual_edited', False))
            on_time_changed.assert_not_called()

    def test_playback_selection_can_extend_outside_red_boundaries(self):
        """Temporary playback selection should use the visible audio range, not the red edit bounds"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.ax.transData.transform.side_effect = lambda xy: (xy[0] * 100, 0)
            panel.ax.axvspan.return_value = MagicMock()
            panel.current_item = {
                **self.item,
                'start': 0.3,
                'end': 0.7,
                'chars_bounds': [[0.3, 0.7]]
            }
            panel.playback_domain_start = 0.1
            panel.playback_domain_end = 0.9
            panel.bound_lines = [(MagicMock(), MagicMock())]
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()

            press_event = MagicMock()
            press_event.inaxes = panel.ax
            press_event.button = 1
            press_event.x = 12
            press_event.xdata = 0.2
            panel.on_press(press_event)

            motion_event = MagicMock()
            motion_event.x = 80
            motion_event.xdata = 0.8
            panel.on_motion(motion_event)

            self.assertEqual(panel.playback_selection, (0.2, 0.8))

    def test_play_selected_prefers_temporary_playback_selection(self):
        """The main play button should prioritize a Praat-style temporary selection"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            part = MagicMock()
            part.values = np.zeros((1, 10))
            part.sampling_frequency = 1000
            snd = MagicMock()
            snd.get_total_duration.return_value = 1.0
            snd.extract_part.return_value = part
            panel.current_item = {
                **self.item,
                'snd': snd,
                'start': 0.1,
                'end': 0.9,
                'chars_bounds': [[0.1, 0.9]]
            }
            panel.cursor_x = 0.2
            panel.playback_selection = (0.25, 0.55)
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()
            panel._update_play_button_state = MagicMock()

            with patch('modules.spectrogram_panel.sd.play'):
                panel.play_selected()

            snd.extract_part.assert_called_with(from_time=0.25, to_time=0.55)
            self.assertEqual(panel.play_selection_start, 0.25)
            self.assertEqual(panel.play_end_audio_time, 0.55)

    def test_current_char_mode_plays_whole_character_under_cursor(self):
        """Current-character mode should play the whole character, not only after the cursor"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            part = MagicMock()
            part.values = np.zeros((1, 10))
            part.sampling_frequency = 1000
            snd = MagicMock()
            snd.get_total_duration.return_value = 1.0
            snd.extract_part.return_value = part
            panel.current_item = {
                **self.item,
                'snd': snd,
                'start': 0.1,
                'end': 0.9,
                'chars_bounds': [[0.1, 0.5], [0.5, 0.9]]
            }
            panel.cursor_x = 0.72
            panel.playback_range_mode = "当前字"
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()
            panel._update_play_button_state = MagicMock()

            with patch('modules.spectrogram_panel.sd.play'):
                panel.play_selected()

            snd.extract_part.assert_called_with(from_time=0.5, to_time=0.9)
            self.assertEqual(panel.play_selection_start, 0.5)
            self.assertEqual(panel.play_end_audio_time, 0.9)

    def test_whole_segment_mode_uses_visible_audio_domain_not_red_boundaries(self):
        """Whole-segment playback should include audio outside the red edit boundaries"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            part = MagicMock()
            part.values = np.zeros((1, 10))
            part.sampling_frequency = 1000
            snd = MagicMock()
            snd.get_total_duration.return_value = 1.0
            snd.extract_part.return_value = part
            panel.current_item = {
                **self.item,
                'snd': snd,
                'start': 0.3,
                'end': 0.7,
                'chars_bounds': [[0.3, 0.7]]
            }
            panel.playback_domain_start = 0.1
            panel.playback_domain_end = 0.9
            panel.playback_range_mode = "整段"
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.canvas = MagicMock()
            panel._update_play_button_state = MagicMock()

            with patch('modules.spectrogram_panel.sd.play'):
                panel.play_selected()

            snd.extract_part.assert_called_with(from_time=0.1, to_time=0.9)
            self.assertEqual(panel.play_selection_start, 0.1)
            self.assertEqual(panel.play_end_audio_time, 0.9)

    def test_play_button_uses_same_width_pause_icon_when_playing(self):
        """The combined play/stop button should stay the same width and use the pause icon"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {'play': 'play_icon', 'pause': 'pause_icon'}, None, None, None)
            panel.btn_play = MagicMock()

            panel._update_play_button_state(True)
            panel.btn_play.configure.assert_called_with(text=" 停止", image='pause_icon', width=76)

            panel._update_play_button_state(False)
            panel.btn_play.configure.assert_called_with(text=" 播放", image='play_icon', width=76)

    def test_cursor_update_uses_blit_when_background_is_available(self):
        """Playback cursor updates should not redraw the whole spectrogram when blitting can be used"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.cursor_x = 0.5
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.playback_status_var = None
            panel.background = MagicMock()
            panel.fig = MagicMock()
            panel.fig.bbox = MagicMock()
            panel.ax = MagicMock()
            panel.canvas = MagicMock()

            panel.update_cursor_graphics(prefer_blit=True)

            panel.canvas.restore_region.assert_called_once_with(panel.background)
            panel.ax.draw_artist.assert_any_call(panel.cursor_line)
            panel.ax.draw_artist.assert_any_call(panel.cursor_text)
            panel.canvas.blit.assert_called_once_with(panel.fig.bbox)
            panel.canvas.draw_idle.assert_not_called()

    def test_draw_event_restores_animated_cursor(self):
        """Animated cursor artists should be restored after regular canvas redraws"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.cursor_x = 0.5
            panel.cursor_line = MagicMock()
            panel.cursor_text = MagicMock()
            panel.playback_status_var = None
            panel.fig = MagicMock()
            panel.fig.bbox = MagicMock()
            panel.ax = MagicMock()
            panel.canvas = MagicMock()
            panel.canvas.copy_from_bbox.return_value = "background"

            panel.on_draw(MagicMock())

            panel.canvas.copy_from_bbox.assert_called_once_with(panel.fig.bbox)
            panel.canvas.restore_region.assert_called_once_with("background")
            panel.ax.draw_artist.assert_any_call(panel.cursor_line)
            panel.ax.draw_artist.assert_any_call(panel.cursor_text)

    def test_engine_switch_only_recalculates_current_item(self):
        """Switching to REAPER should not immediately launch full-project recomputation"""
        self.app.engine_button = MagicMock()
        self.app.engine_button.get.return_value = "reaper"
        self.app.engine_button._buttons_dict = {}
        self.app.audio_cache = {}
        self.app.recalculate_current_item = MagicMock()
        self.app.recalculate_all_audio = MagicMock()

        self.app.on_engine_change("reaper")

        self.assertEqual(self.app.last_params['f0_engine'], "reaper")
        self.app.recalculate_current_item.assert_called_once_with(recompute_pitch=True)
        self.app.recalculate_all_audio.assert_not_called()

if __name__ == '__main__':
    unittest.main()
