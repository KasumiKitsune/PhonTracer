import unittest
import customtkinter as ctk
import sys
sys.path.append('.')
from modules.app import PhoneticsApp
from unittest.mock import patch, MagicMock

class TestSpeakerStateSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):

        cls.root = ctk.CTk()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()


    @patch('sounddevice.stop')
    def test_switch_speakers(self, mock_stop):
        def mock_setup_icons(self):
            self.icons = {}
            self.tk_icons = {}
        with patch.object(PhoneticsApp, 'setup_icons', mock_setup_icons):
            app = PhoneticsApp(self.root)
        s1 = app.active_speaker
        app.pending_batch_paths = ["/path/to/a.wav"]
        app.last_params['db'] = 80
        self.assertEqual(app.last_params['db'], 80)

        app.speaker_option_var.set('Test Speaker')
        s2 = app.speaker_manager.add_speaker('Test Speaker')
        app.on_speaker_changed('Test Speaker')

        self.assertEqual(app.active_speaker.id, s2.id)
        self.assertEqual(app.pending_batch_paths, [])
        self.assertEqual(app.last_params['db'], 60.0)

        app.pending_long_snd = MagicMock()
        app.on_speaker_changed('发音人 1')

        self.assertEqual(app.active_speaker.id, s1.id)
        self.assertEqual(app.pending_batch_paths, ["/path/to/a.wav"])
        self.assertEqual(app.last_params['db'], 80)
        self.assertIsNone(app.pending_long_snd)

if __name__ == '__main__':
    unittest.main()
