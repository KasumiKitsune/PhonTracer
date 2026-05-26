import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
from modules.app import PhoneticsApp
from tests.shared_root import get_shared_root

class SynchronousThread:
    def __init__(self, target, args=(), kwargs=None, daemon=False):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
    def start(self):
        self.target(*self.args, **self.kwargs)

class TestCloseConfirmation(unittest.TestCase):
    def setUp(self):
        self.parent = get_shared_root()

        # Intercept protocol registration to capture the private on_closing closure
        self.on_closing_callback = None
        self.orig_protocol = self.parent.protocol
        def mock_protocol(name, func=None):
            if name == "WM_DELETE_WINDOW" and func is not None:
                self.on_closing_callback = func
            return self.orig_protocol(name, func)
        self.parent.protocol = mock_protocol

        # Save and mock parent.after to run synchronously to prevent race conditions in tests
        self.orig_after = self.parent.after
        self.parent.after = lambda delay, func: func() if func else None

        # Patch the UI setup, drop queue checking, and managers to isolate the tests
        with patch('modules.app.PhoneticsApp.setup_icons'), \
             patch('modules.app.PhoneticsApp.setup_ui'), \
             patch('modules.app.PhoneticsApp._schedule_drop_queue_check'), \
             patch('modules.app.PhoneticsApp._schedule_window_guard'), \
             patch('modules.app.PhoneticsApp.check_update'), \
             patch('windnd.hook_dropfiles'), \
             patch('modules.app.ProjectManager'), \
             patch('modules.app.SpeakerManager') as mock_spk_mgr_cls:

            # Setup active speaker mocks
            self.mock_spk_mgr = MagicMock()
            self.mock_speaker = MagicMock()
            self.mock_speaker.items = {}
            self.mock_speaker.audio_cache = {}
            self.mock_speaker.last_params = {
                'pts': 10,
                'db': -20.0,
                'skip_front': 0.05,
                'pitch_floor': 75,
                'pitch_ceiling': 600,
                'voicing_threshold': 0.25
            }
            self.mock_spk_mgr.get_active_speaker.return_value = self.mock_speaker
            mock_spk_mgr_cls.return_value = self.mock_spk_mgr

            # Instantiate PhoneticsApp
            self.app = PhoneticsApp(self.parent)
            self.app.project_manager = MagicMock()
            self.app.start_loading = MagicMock()
            self.app.stop_loading = MagicMock()
            self.app.set_status = MagicMock()
            self.app._update_speaker_dropdown = MagicMock()
            self.app._refresh_ui_for_speaker = MagicMock()
            self.app.speaker_option_var = MagicMock()
            self.app.tabview = MagicMock()
            self.app.lbl_batch_files = MagicMock()
            self.app.lbl_long_file = MagicMock()
            self.app.spectrogram_panel = MagicMock()

    def tearDown(self):
        # Restore original protocol and after methods on the shared parent singleton
        self.parent.protocol = self.orig_protocol
        self.parent.after = self.orig_after

    def test_initial_state_has_no_changes(self):
        """Verify that a newly initialized app has no changes and path is None"""
        self.assertFalse(self.app.has_changes)
        self.assertIsNone(self.app.current_project_path)

    def test_mark_modified_sets_flag_and_saves(self):
        """Verify that mark_modified sets has_changes and triggers auto-save"""
        self.app.mark_modified()
        self.assertTrue(self.app.has_changes)
        self.app.project_manager.trigger_auto_save.assert_called_once()

    def test_project_import_resets_flag_and_sets_path(self):
        """Verify that importing a project successfully resets dirty flag and stores path"""
        self.app.has_changes = True
        test_path = "C:/projects/test.teproj"

        with patch('tkinter.filedialog.askopenfilename', return_value=test_path), \
             patch('threading.Thread', side_effect=SynchronousThread), \
             patch('tkinter.messagebox.showinfo') as mock_info:

            self.app.project_manager.load_project.return_value = True

            # Trigger import
            self.app.on_import_project()

            # Check state after load and sync UI
            self.assertEqual(self.app._last_imported_path, test_path)
            self.assertFalse(self.app.has_changes)
            self.assertEqual(self.app.current_project_path, test_path)
            mock_info.assert_called_once()

    def test_project_export_resets_flag_and_sets_path(self):
        """Verify that exporting a project successfully resets dirty flag and stores path"""
        self.app.has_changes = True
        test_path = "C:/projects/export.teproj"

        with patch('tkinter.filedialog.asksaveasfilename', return_value=test_path), \
             patch('threading.Thread', side_effect=SynchronousThread), \
             patch('tkinter.messagebox.showinfo') as mock_info:

            self.app.project_manager.export_project.return_value = True

            # Trigger export
            self.app.on_export_project()

            # Since thread is synchronous, it executed run() target
            self.assertEqual(self.app._last_exported_path, test_path)
            self.assertFalse(self.app.has_changes)
            self.assertEqual(self.app.current_project_path, test_path)
            mock_info.assert_called_once()

    def test_import_project_blocked_when_chart_dialog_is_open(self):
        self.app.active_chart_dialog = MagicMock()
        self.app.active_chart_dialog.winfo_exists.return_value = True

        with patch('tkinter.messagebox.showwarning') as mock_warning:
            self.app.on_import_project()

        mock_warning.assert_called_once()
        self.app.project_manager.load_project.assert_not_called()

    def test_stale_chart_dialog_reference_is_cleared(self):
        stale_dialog = MagicMock()
        stale_dialog.winfo_exists.return_value = False
        self.app.active_chart_dialog = stale_dialog

        with patch('tkinter.filedialog.askopenfilename', return_value=""), \
             patch('tkinter.messagebox.showwarning') as mock_warning:
            self.app.on_import_project()

        mock_warning.assert_not_called()
        self.assertIsNone(self.app.active_chart_dialog)

    def test_on_closing_without_changes_destroys_root(self):
        """Verify that on_closing immediately destroys the app if there are no changes"""
        self.assertIsNotNone(self.on_closing_callback)

        with patch.object(self.app.root, 'destroy') as mock_destroy, \
             patch.object(self.app.executor, 'shutdown') as mock_shutdown:

            self.on_closing_callback()
            mock_shutdown.assert_called_once_with(wait=False)
            mock_destroy.assert_called_once()

    def test_on_closing_with_changes_yes_saves_and_destroys(self):
        """Verify that choosing 'Yes' on closing saves current project path and exits"""
        self.app.has_changes = True
        self.app.current_project_path = "C:/projects/auto.teproj"

        self.assertIsNotNone(self.on_closing_callback)

        with patch('tkinter.messagebox.askyesnocancel', return_value=True) as mock_ask, \
             patch.object(self.app.root, 'destroy') as mock_destroy:

            self.app.project_manager.export_project.return_value = True

            self.on_closing_callback()
            mock_ask.assert_called_once()
            self.app.project_manager.export_project.assert_called_with("C:/projects/auto.teproj")
            mock_destroy.assert_called_once()

    def test_on_closing_with_changes_cancel_does_not_destroy(self):
        """Verify that choosing 'Cancel' on closing keeps the app running"""
        self.app.has_changes = True

        self.assertIsNotNone(self.on_closing_callback)

        with patch('tkinter.messagebox.askyesnocancel', return_value=None) as mock_ask, \
             patch.object(self.app.root, 'destroy') as mock_destroy:

            self.on_closing_callback()
            mock_ask.assert_called_once()
            mock_destroy.assert_not_called()

    def test_default_save_filename_suggested_on_export(self):
        """Verify that a default filename in the format SpeakerName_MMDD-HHMM is suggested"""
        self.mock_speaker.name = "发音人1"
        self.app.has_changes = True
        test_path = "C:/projects/export.teproj"

        with patch('tkinter.filedialog.asksaveasfilename', return_value=test_path) as mock_ask, \
             patch('threading.Thread', side_effect=SynchronousThread), \
             patch('tkinter.messagebox.showinfo'):

            self.app.project_manager.export_project.return_value = True

            # Trigger export
            self.app.on_export_project()

            # Check filedialog mock calls
            mock_ask.assert_called_once()
            kwargs = mock_ask.call_args[1]
            self.assertIn('initialfile', kwargs)
            initialfile = kwargs['initialfile']

            # initialfile should start with speaker name
            self.assertTrue(initialfile.startswith("发音人1_"))

            # initialfile should end with 4 digits, hyphen, 4 digits (e.g. 0524-1034)
            import re
            self.assertTrue(re.match(r"^发音人1_\d{4}-\d{4}$", initialfile))

    def test_default_save_filename_suggested_on_closing_new_project(self):
        """Verify that default filename is suggested during on_closing when it's a new project"""
        self.mock_speaker.name = "测试发音人"
        self.app.has_changes = True
        self.app.current_project_path = None
        test_path = "C:/projects/new_save.teproj"

        with patch('tkinter.messagebox.askyesnocancel', return_value=True), \
             patch('tkinter.filedialog.asksaveasfilename', return_value=test_path) as mock_ask, \
             patch.object(self.app.root, 'destroy') as mock_destroy:

            self.app.project_manager.export_project.return_value = True

            self.on_closing_callback()

            mock_ask.assert_called_once()
            kwargs = mock_ask.call_args[1]
            self.assertIn('initialfile', kwargs)
            initialfile = kwargs['initialfile']

            # initialfile should start with speaker name
            self.assertTrue(initialfile.startswith("测试发音人_"))
            import re
            self.assertTrue(re.match(r"^测试发音人_\d{4}-\d{4}$", initialfile))
            mock_destroy.assert_called_once()

if __name__ == '__main__':
    unittest.main()
