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
            splitter.destroy()

    def test_add_segment_assigns_next_missing_word_in_sequence(self):
        """新增段落后，后续段落应顺延到字表中的缺失词，而不是显示为未分配段"""
        from modules.visual_splitter import VisualSplitter

        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 10.0
        existing_items = [
            {'id': 'a', 'label': '甲', 'start': 1.0, 'end': 2.0},
            {'id': 'b', 'label': '乙', 'start': 4.0, 'end': 5.0}
        ]
        word_items = [
            {'id': 'a', 'label': '甲'},
            {'id': 'b', 'label': '乙'},
            {'id': 'c', 'label': '丙'}
        ]

        with patch.object(VisualSplitter, 'setup_ui'), \
             patch.object(VisualSplitter, 'init_data'), \
             patch.object(VisualSplitter, 'render_canvas'), \
             patch.object(VisualSplitter, 'auto_fit_scale'):
            splitter = VisualSplitter(
                master=self.root,
                snd=mock_snd,
                icons={},
                callback=MagicMock(),
                existing_items=existing_items,
                word_items=word_items
            )
            splitter.add_segment_at(3.0, 3.5)

        self.assertEqual([seg['dyn_label'] for seg in splitter.segments], ['甲', '乙', '丙'])
        self.assertEqual([seg['dyn_id'] for seg in splitter.segments], ['a', 'b', 'c'])
        splitter.destroy()

    def test_visual_splitter_edits_private_inner_split_copy(self):
        """打开编辑器后修改蓝线，不应在确认前直接污染原始段落"""
        from modules.visual_splitter import VisualSplitter

        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 10.0
        existing_items = [
            {'id': 'a', 'label': '甲/乙', 'start': 1.0, 'end': 2.0, 'inner_splits': [1.5]}
        ]

        with patch.object(VisualSplitter, 'setup_ui'), \
             patch.object(VisualSplitter, 'init_data'), \
             patch.object(VisualSplitter, 'render_canvas'), \
             patch.object(VisualSplitter, 'auto_fit_scale'):
            splitter = VisualSplitter(
                master=self.root,
                snd=mock_snd,
                icons={},
                callback=MagicMock(),
                existing_items=existing_items
            )
            splitter.segments[0]['inner_splits'][0] = 1.6

        self.assertEqual(existing_items[0]['inner_splits'], [1.5])
        splitter.destroy()

    def test_visual_splitter_confirm_preserves_unassigned_segments(self):
        """确认编辑时仍需保留超出字表数量的音频段，便于下次继续调整"""
        from modules.visual_splitter import VisualSplitter

        mock_snd = MagicMock()
        mock_snd.get_total_duration.return_value = 10.0
        callback = MagicMock()
        existing_items = [
            {'id': 'a', 'label': '甲', 'start': 1.0, 'end': 2.0},
            {'id': None, 'label': '【未分配段】', 'start': 3.0, 'end': 4.0}
        ]

        with patch.object(VisualSplitter, 'setup_ui'), \
             patch.object(VisualSplitter, 'init_data'), \
             patch.object(VisualSplitter, 'render_canvas'), \
             patch.object(VisualSplitter, 'auto_fit_scale'):
            splitter = VisualSplitter(
                master=self.root,
                snd=mock_snd,
                icons={},
                callback=callback,
                existing_items=existing_items
            )
            splitter.confirm()

        segments = callback.call_args.args[0]
        self.assertEqual(len(segments), 2)
        self.assertIsNone(segments[1]['id'])

    def test_visual_split_confirm_reorders_from_snapshot(self):
        """段落顺延或交换时，应从确认前快照复制边界，避免读到已被覆盖的数据"""
        items = {
            'a': {
                'label': '甲', 'snd': MagicMock(), 'macro_start': 0.0, 'macro_end': 1.0,
                'start': 0.1, 'end': 0.9, 'raw_start': 0.0, 'raw_end': 1.0,
                'inner_splits': [], 'chars_bounds': [[0.1, 0.9]]
            },
            'b': {
                'label': '乙', 'snd': MagicMock(), 'macro_start': 1.0, 'macro_end': 2.0,
                'start': 1.1, 'end': 1.9, 'raw_start': 1.0, 'raw_end': 2.0,
                'inner_splits': [], 'chars_bounds': [[1.1, 1.9]]
            }
        }
        self.app.active_speaker.items = items
        self.app.tree_panel.project_groups = ['组']
        self.app.tree_panel.group_nodes = {'组': 'group'}
        self.app.tree_panel.tree.get_children.return_value = ('a', 'b')
        self.app.spectrogram_panel.current_item = None
        self.app.mark_modified = MagicMock()
        segments = [
            {'id': 'a', 'old_id': 'b', 'start': 1.0, 'end': 2.0, 'inner_splits': [], 'is_modified': False},
            {'id': 'b', 'old_id': 'a', 'start': 0.0, 'end': 1.0, 'inner_splits': [], 'is_modified': False}
        ]

        with patch('tkinter.messagebox.showinfo'):
            self.app.on_visual_split_confirm(segments, is_update=True)

        self.assertEqual((items['a']['raw_start'], items['a']['raw_end']), (1.0, 2.0))
        self.assertEqual((items['b']['raw_start'], items['b']['raw_end']), (0.0, 1.0))
        self.assertEqual(self.app.manual_segments, [(1.0, 2.0), (0.0, 1.0)])
        self.app.mark_modified.assert_called_once()

    def test_visual_split_confirm_rejects_cross_speaker_write(self):
        """编辑器打开后若切换发音人，确认操作不得写入当前发音人的数据"""
        self.app.mark_modified = MagicMock()

        with patch('tkinter.messagebox.showwarning') as warning:
            self.app.on_visual_split_confirm([], is_update=True, speaker_id='另一个发音人')

        warning.assert_called_once()
        self.app.mark_modified.assert_not_called()

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

    def test_spectrogram_panel_plots_anomaly_points(self):
        """Test that spectrogram panel detects and plots anomaly pitch jumps in red on ax2"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.fig = MagicMock()
            panel.canvas = MagicMock()
            panel.switch_trim_silence = MagicMock()
            panel.switch_trim_silence.get.return_value = False
            panel.var_t_start = MagicMock()
            panel.var_t_end = MagicMock()

            # Mock sound device extract_part to return a mock spectrogram
            mock_part = MagicMock()
            mock_sg = MagicMock()
            mock_sg.x_grid.return_value = np.array([0.0, 0.5, 1.0])
            mock_sg.y_grid.return_value = np.array([0.0, 100.0, 200.0])
            mock_sg.values = np.zeros((3, 3))
            mock_part.to_spectrogram.return_value = mock_sg

            mock_snd = MagicMock()
            mock_snd.extract_part.return_value = mock_part
            mock_snd.get_total_duration.return_value = 1.0

            # A short doubled-F0 run should be marked at every bad point.
            xs = np.array([0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10])
            freqs = np.array([150.0, 150.0, 148.0, 151.0, 150.0, 300.0, 305.0, 295.0, 150.0, 149.0, 151.0])

            item_jump = {
                'start': 0.0,
                'end': 1.0,
                'macro_start': 0.0,
                'macro_end': 1.0,
                'label': 'a',
                'snd': mock_snd,
                'pitch_data': {
                    'xs': xs,
                    'freqs': freqs
                },
                'chars_bounds': [[0.0, 1.0]]
            }
            panel.current_item = item_jump

            panel.plot_item_spectrogram()

            # Verify ax2.plot was called for the red anomaly scatter dots
            called_with_red = False
            for call in panel.ax2.plot.call_args_list:
                args, kwargs = call
                if kwargs.get('color') == '#EF4444' or kwargs.get('label') == '异常点':
                    called_with_red = True
                    self.assertEqual([round(x, 2) for x in args[0]], [0.05, 0.06, 0.07])
                    self.assertEqual([round(y) for y in args[1]], [300, 305, 295])
            self.assertTrue(called_with_red)
            ylim = panel.ax2.set_ylim.call_args.args[0]
            self.assertGreaterEqual(ylim[1], 600.0)


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

    def test_spectrogram_panel_eraser_session_caching_and_deferral(self):
        """测试橡皮擦模式使用会话缓存，将实际的物理落盘和界面重绘进行解耦控制"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()

            # 设置 F0 模式模拟条目
            xs = np.array([0.1, 0.2, 0.3])
            freqs = np.array([150.0, 160.0, 170.0])
            item = {
                'start': 0.1,
                'end': 0.9,
                'label': 'test',
                'pitch_data': {
                    'xs': xs,
                    'freqs': freqs.copy()
                }
            }
            panel.current_item = item
            panel.eraser_mode = True

            # 模拟 F0 预览图层
            mock_layer = MagicMock()
            panel.erased_pitch_layer = mock_layer

            # 模拟靠近索引 1 的点击事件 (x=0.2, y=160)
            mock_event = MagicMock()
            mock_event.inaxes = panel.ax2
            mock_event.x = 200
            mock_event.y = 300
            mock_event.xdata = 0.2

            # 模拟 transData.transform 返回像素坐标
            panel.ax2.transData.transform.return_value = np.array([
                [100, 300], # 索引 0: 距离 100 像素
                [200, 300], # 索引 1: 距离 0 像素
                [300, 300]  # 索引 2: 距离 100 像素
            ])

            panel.erase_radius = 15.0

            # 1. 擦除靠近的点：应该仅加入会话缓存，暂不修改底层 freqs 数组
            panel.erase_points_near(mock_event)

            # 会话缓存中应包含索引 1
            self.assertIn(1, panel.session_erased_pitch_indices)
            self.assertNotIn(0, panel.session_erased_pitch_indices)
            self.assertNotIn(2, panel.session_erased_pitch_indices)

            # 底层原始数据依然保持完整（暂缓提交）
            self.assertEqual(item['pitch_data']['freqs'][1], 160.0)

            # 预览图层的 set_data 和 draw_idle 应该已被调用（保证流畅展示）
            mock_layer.set_data.assert_called_once()
            panel.canvas.draw_idle.assert_called_once()

            # 2. 调用 apply_eraser_changes 进行提交：应该将物理改动写入实际数组，并清空临时缓存
            panel.plot_item_spectrogram = MagicMock()
            panel.update_ui_times = MagicMock()

            panel.apply_eraser_changes()

            # 底层数组中的 1 号索引已成功置为 0.0
            self.assertEqual(item['pitch_data']['freqs'][1], 0.0)
            # 临时缓存清空
            self.assertEqual(len(panel.session_erased_pitch_indices), 0)
            # 条目已被标记为手动编辑
            self.assertTrue(item.get('is_manual_edited'))

    def test_spectrogram_panel_eraser_discard_changes(self):
        """测试 discard_eraser_changes 能够安全清空临时擦除会话而不影响底层实际数据"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()

            # 设置共振峰模式的模拟条目
            xs = np.array([0.1, 0.2, 0.3])
            f1 = np.array([500.0, 550.0, 600.0])
            f2 = np.array([1500.0, 1550.0, 1600.0])
            item = {
                'start': 0.1,
                'end': 0.9,
                'label': 'test',
                'formant_data': {
                    'xs': xs,
                    'f1': f1.copy(),
                    'f2': f2.copy()
                }
            }
            panel.current_item = item
            panel.eraser_mode = True

            # 模拟未提交的会话缓存
            panel.session_erased_formant_indices["f1"].add(1)

            # 放弃本次会话的全部更改
            panel.discard_eraser_changes()

            # 会话缓存已清空
            self.assertEqual(len(panel.session_erased_formant_indices["f1"]), 0)
            # 底层的原始物理数据依然保持完好，完全没有被修改
            self.assertEqual(item['formant_data']['f1'][1], 550.0)

    def test_eraser_background_save_thread_safety(self):
        """测试在后台线程保存/导出时，绝对不调用 UI 相关的刷新逻辑，确保多线程安全性"""
        import threading

        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()
            panel.plot_item_spectrogram = MagicMock()

            # 模拟一个有未提交擦除点的项
            xs = np.array([0.1, 0.2, 0.3])
            freqs = np.array([150.0, 160.0, 170.0])
            item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'pitch_data': {'xs': xs, 'freqs': freqs.copy()}
            }
            panel.current_item = item
            panel.eraser_mode = True
            panel.session_erased_pitch_indices.add(1)

            # 创建 ProjectManager，不绑定真实的 Tk UI 重绘
            app_mock = MagicMock()
            app_mock.spectrogram_panel = panel
            app_mock.speaker_manager.active_speaker_id = "spk1"
            app_mock.speaker_manager.speakers = {}
            app_mock.export_numbering_rule_value = "continuous"
            app_mock.flush_eraser_changes = MagicMock()

            from modules.project_manager import ProjectManager
            manager = ProjectManager(app_mock)

            # 后台线程直接调用 save_to_workspace()
            # 应该在我们的修改下决不调用 app_mock.flush_eraser_changes()
            errors = []
            def save_in_background():
                try:
                    manager.save_to_workspace()
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(
                target=save_in_background,
                daemon=True
            )
            worker.start()
            worker.join(timeout=3)

            # 确保 flush_eraser_changes 没有在 save_to_workspace 内被执行
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            app_mock.flush_eraser_changes.assert_not_called()
            panel.plot_item_spectrogram.assert_not_called()

    def test_eraser_clean_project_mark_modified(self):
        """测试干净工程（has_changes=False）在擦除并释放鼠标时，立刻触发 mark_modified，启动自动保存"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()
            panel.erased_pitch_layer = MagicMock()
            panel.update_ui_times = MagicMock()

            xs = np.array([0.1, 0.2, 0.3])
            freqs = np.array([150.0, 160.0, 170.0])
            item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'pitch_data': {'xs': xs, 'freqs': freqs.copy()}
            }
            panel.current_item = item
            panel.eraser_mode = True

            app_mock = MagicMock()
            app_mock.mark_modified = MagicMock()
            panel.app = app_mock

            # 模拟拖拽擦除索引 1
            panel.session_erased_pitch_indices.add(1)

            # 释放鼠标触发轻量落盘
            mock_event = MagicMock()
            mock_event.x = 100
            mock_event.y = 100
            panel.erasing = True
            panel.on_release(mock_event)

            # 应该直接写入底层频率数组，并且只触发一次 app.mark_modified()
            self.assertEqual(item['pitch_data']['freqs'][1], 0.0)
            app_mock.mark_modified.assert_called_once()
            panel.update_ui_times.assert_not_called()

    def test_eraser_highlight_stays_at_original_frequency_after_light_apply(self):
        """测试轻量落盘后继续拖动时，历史红点仍停留在原始频率位置"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()
            panel.erased_pitch_layer = MagicMock()

            xs = np.array([0.1, 0.2, 0.3])
            freqs = np.array([150.0, 160.0, 170.0])
            panel.current_item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'pitch_data': {'xs': xs, 'freqs': freqs.copy()}
            }
            panel.eraser_mode = True
            panel.erase_radius = 15.0
            panel.ax2.transData.transform.return_value = np.array([
                [100, 300],
                [200, 300],
                [300, 300]
            ])

            first_event = MagicMock(inaxes=panel.ax2, x=200, y=300)
            panel.erase_points_near(first_event)
            panel.light_apply_eraser_changes()
            self.assertEqual(panel.current_item['pitch_data']['freqs'][1], 0.0)

            second_event = MagicMock(inaxes=panel.ax2, x=300, y=300)
            panel.erase_points_near(second_event)

            shown_xs, shown_freqs = panel.erased_pitch_layer.set_data.call_args.args
            self.assertEqual(shown_xs, [0.2, 0.3])
            self.assertEqual(shown_freqs, [160.0, 170.0])

    def test_formant_highlight_stays_visible_after_light_apply(self):
        """测试共振峰轻量落盘为 NaN 后，继续拖动时历史红点仍保持可见"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()
            panel.erased_f1_layer = MagicMock()
            panel.erased_f2_layer = MagicMock()

            xs = np.array([0.1, 0.2, 0.3])
            f1 = np.array([500.0, 550.0, 600.0])
            f2 = np.array([1500.0, 1550.0, 1600.0])
            panel.current_item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'analysis_mode': 'formant',
                'formant_data': {'xs': xs, 'f1': f1.copy(), 'f2': f2.copy()}
            }
            panel.eraser_mode = True
            panel.erase_radius = 15.0

            def transform(points):
                if np.max(points[:, 1]) < 1000:
                    return np.array([[100, 100], [200, 100], [300, 100]])
                return np.array([[100, 1000], [200, 1000], [300, 1000]])

            panel.ax.transData.transform.side_effect = transform

            first_event = MagicMock(inaxes=panel.ax, x=200, y=100)
            panel.erase_points_near(first_event)
            panel.light_apply_eraser_changes()
            self.assertTrue(np.isnan(panel.current_item['formant_data']['f1'][1]))

            second_event = MagicMock(inaxes=panel.ax, x=300, y=100)
            panel.erase_points_near(second_event)

            offsets = panel.erased_f1_layer.set_offsets.call_args.args[0]
            self.assertEqual(offsets.tolist(), [[0.2, 550.0], [0.3, 600.0]])

    def test_eraser_mode_switch_flush(self):
        """测试由 F0 模式切换至共振峰模式时，切换前能够自动且安全地落盘已擦除的数据点"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()
            panel.plot_item_spectrogram = MagicMock()

            xs = np.array([0.1, 0.2, 0.3])
            freqs = np.array([150.0, 160.0, 170.0])
            item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'pitch_data': {'xs': xs, 'freqs': freqs.copy()}
            }
            panel.current_item = item
            panel.eraser_mode = True

            app_mock = MagicMock()
            app_mock.spectrogram_panel = panel
            panel.app = app_mock

            # 模拟未提交的 F0 擦除
            panel.session_erased_pitch_indices.add(2)

            # 模拟在 app 中切换分析模式（触发 flush）
            app_mock.flush_eraser_changes.side_effect = lambda: panel.apply_eraser_changes()
            app_mock.flush_eraser_changes()

            # 切换前应该已经将 2 号索引的值写入底层的 freqs 数组
            self.assertEqual(item['pitch_data']['freqs'][2], 0.0)

    def test_eraser_formant_actual_apply(self):
        """测试共振峰模式下的橡皮擦擦除，能够在鼠标释放时将 NaN 写入 f1 和 f2 数组"""
        with patch.object(SpectrogramPanel, 'setup_ui'):
            panel = SpectrogramPanel(self.root, {}, None, None, None)
            panel.ax = MagicMock()
            panel.ax2 = MagicMock()
            panel.canvas = MagicMock()

            xs = np.array([0.1, 0.2, 0.3])
            f1 = np.array([500.0, 550.0, 600.0])
            f2 = np.array([1500.0, 1550.0, 1600.0])
            item = {
                'start': 0.1, 'end': 0.9, 'label': 'test',
                'formant_data': {'xs': xs, 'f1': f1.copy(), 'f2': f2.copy()}
            }
            panel.current_item = item
            panel.eraser_mode = True
            panel.app = MagicMock()

            # 模拟拖拽剔除了 f1 的 1 号点，以及 f2 的 0 号点
            panel.session_erased_formant_indices["f1"].add(1)
            panel.session_erased_formant_indices["f2"].add(0)

            # 调用轻量提交
            panel.light_apply_eraser_changes()

            # 验证底层数组数据已被成功更改为 NaN
            self.assertTrue(np.isnan(item['formant_data']['f1'][1]))
            self.assertTrue(np.isnan(item['formant_data']['f2'][0]))
            self.assertEqual(item['formant_data']['f1'][0], 500.0)  # 未擦除的点保持原样

if __name__ == '__main__':
    unittest.main()
