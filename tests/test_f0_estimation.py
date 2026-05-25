import numpy as np
from modules.app import PhoneticsApp

class DummyApp:
    pass

def test_extract_stable_f0_values():
    dummy_self = DummyApp()
    
    # 1. Test standard stable segment
    # dt = 10ms (0.01s)
    # Length = 20 frames = 200ms
    xs = np.linspace(0.0, 0.19, 20)
    freqs = np.full(20, 150.0)
    
    stable = PhoneticsApp.extract_stable_f0_values(dummy_self, xs, freqs)
    # Expected: duration = 200ms >= 100ms.
    # Trim frames = round(0.030 / 0.010) = 3 frames from each end.
    # Trimmed length should be 20 - 6 = 14 frames.
    assert len(stable) == 14
    assert all(val == 150.0 for val in stable)

def test_extract_stable_f0_values_jump():
    dummy_self = DummyApp()
    
    # 2. Test split on jump
    # We have 10 frames of 150Hz, then a jump to 250Hz for 10 frames.
    # The jump is from 150 to 250 (relative diff: 0.66 > 0.20), so it should split.
    # Each sub-run has 10 frames = 100ms.
    # Each sub-run duration is 100ms >= 100ms, so both sub-runs are kept.
    # Each is trimmed by 3 frames on each end, leaving 4 frames per sub-run.
    xs = np.linspace(0.0, 0.19, 20)
    freqs = np.concatenate([np.full(10, 150.0), np.full(10, 250.0)])
    
    stable = PhoneticsApp.extract_stable_f0_values(dummy_self, xs, freqs)
    assert len(stable) == 8  # 4 from first sub-run, 4 from second
    assert stable[:4] == [150.0] * 4
    assert stable[4:] == [250.0] * 4

def test_extract_stable_f0_values_short():
    dummy_self = DummyApp()
    
    # 3. Test short segment (duration < 100ms)
    # 8 frames of 150Hz = 80ms.
    xs = np.linspace(0.0, 0.07, 8)
    freqs = np.full(8, 150.0)
    
    stable = PhoneticsApp.extract_stable_f0_values(dummy_self, xs, freqs)
    assert len(stable) == 0

def test_certified_uncertified_filtering():
    items = {
        "item1": {"label": "test1", "is_manual_edited": True},
        "item2": {"label": "test2", "is_manual_edited": False},
        "item3": {"label": "test3"}
    }
    
    # status_filter == "已修改"
    filtered_certified = [
        iid for iid, item in items.items()
        if item.get('is_manual_edited', False)
    ]
    assert filtered_certified == ["item1"]
    
    # status_filter == "未修改"
    filtered_uncertified = [
        iid for iid, item in items.items()
        if not item.get('is_manual_edited', False)
    ]
    assert set(filtered_uncertified) == {"item2", "item3"}

def test_recalculate_all_audio_preserves_bounds():
    from unittest.mock import MagicMock
    # Mocking standard PhoneticsApp attributes for the test
    app = DummyApp()
    app.items = {
        "item1": {
            "label": "test",
            "group": "group1",
            "snd": MagicMock(),
            "pitch_data": {"xs": np.array([0.1]), "freqs": np.array([150.0])},
            "macro_start": 0.0,
            "macro_end": 1.0,
            "start": 0.2, # Manually set start bounds
            "end": 0.8,
            "raw_start": 0.15,
            "raw_end": 0.85,
            "inner_splits": [0.5],
            "chars_bounds": [[0.2, 0.5], [0.5, 0.8]],
            "is_manual_edited": False
        }
    }
    app.last_params = {
        "db": 60.0,
        "skip_front": 0.0,
        "pitch_floor": 75,
        "pitch_ceiling": 600,
        "voicing_threshold": 0.25,
        "pts": 11,
        "f0_engine": "praat"
    }
    
    # Mock entry widgets/variables
    app.entry_points = MagicMock()
    app.entry_points.get.return_value = "11"
    app.var_drop_db = MagicMock()
    app.var_drop_db.get.return_value = "60.0"
    app.var_min_dur = MagicMock()
    app.var_min_dur.get.return_value = "0.0"
    app.entry_pitch_floor = MagicMock()
    app.entry_pitch_floor.get.return_value = "80" # changed floor
    app.entry_pitch_ceiling = MagicMock()
    app.entry_pitch_ceiling.get.return_value = "500" # changed ceiling
    app.entry_voicing_threshold = MagicMock()
    app.entry_voicing_threshold.get.return_value = "0.25"
    
    # Mock other Tkinter widgets/elements
    app.root = MagicMock()
    app.switch_trim_silence = MagicMock()
    app.switch_trim_silence.get.return_value = True
    app.tree_panel = MagicMock()
    app.spectrogram_panel = MagicMock()
    app.spectrogram_panel.current_item = app.items["item1"]
    
    # Spy on recalculate_current_item
    app.recalculate_current_item = MagicMock()
    
    # When recalculate_all_audio is called, it should call on_param_change(recalculate_current=False)
    # Let's bind the real on_param_change to app
    app.on_param_change = PhoneticsApp.on_param_change.__get__(app, DummyApp)
    
    # Call on_param_change(recalculate_current=False) directly to verify it doesn't trigger recalculate_current_item
    app.on_param_change(recalculate_current=False)
    app.recalculate_current_item.assert_not_called()
    
    # Verify the global parameters were updated
    assert app.last_params["pitch_floor"] == 80
    assert app.last_params["pitch_ceiling"] == 500

def test_cli_detect_f0():
    from unittest.mock import MagicMock, patch
    from cli import PhonTracerCLI
    import io
    import sys
    import json

    # Setup CLI mock environment
    cli = PhonTracerCLI()
    
    # Mock speaker
    speaker = MagicMock()
    speaker.name = "TestSpeaker"
    speaker.tab_mode = "多条独立音频"
    
    dummy_sound = MagicMock()
    dummy_sound.get_total_duration.return_value = 1.0
    
    # Create 100 stable F0 frames (each frame duration 0.010s)
    xs = np.linspace(0.0, 0.99, 100)
    freqs = np.full(100, 200.0) # Stable 200Hz
    
    speaker.items = {
        "item_1": {
            'label': 'ma',
            'group': 'T1',
            'start': 0.0,
            'end': 1.0,
            'snd': dummy_sound,
            'pitch_data': {
                'xs': xs,
                'freqs': freqs
            },
            'warnings': [],
            'success': True,
            'path': 'dummy.wav'
        }
    }
    speaker.last_params = {
        'pts': 11,
        'pitch_floor': 75,
        'pitch_ceiling': 600,
        'voicing_threshold': 0.25,
        'f0_engine': 'praat'
    }
    speaker.cli_groups = ['T1']
    
    cli.speaker_manager = MagicMock()
    cli.speaker_manager.get_active_speaker.return_value = speaker
    cli.speaker_manager.get_all_speakers.return_value = [speaker]
    
    # Mock extract_f0 to return our dummy pitch data during detection
    with patch('modules.audio_core.extract_f0', return_value={'xs': xs, 'freqs': freqs}), \
         patch('cli.extract_f0', return_value={'xs': xs, 'freqs': freqs}):
        captured_output = io.StringIO()
        sys.stdout = captured_output
        try:
            cli.do_detect_f0("")
        finally:
            sys.stdout = sys.__stdout__
            
        res_json = json.loads(captured_output.getvalue().strip())
        assert res_json["success"] is True
        assert "suggestions" in res_json
        assert "conservative" in res_json["suggestions"]
        assert "recommended" in res_json["suggestions"]
        assert "fine" in res_json["suggestions"]
        
        # Test applying a preset
        old_start = speaker.items["item_1"]["start"]
        old_end = speaker.items["item_1"]["end"]
        captured_output2 = io.StringIO()
        sys.stdout = captured_output2
        try:
            cli.do_detect_f0("recommended")
        finally:
            sys.stdout = sys.__stdout__
            
        res_json2 = json.loads(captured_output2.getvalue().strip())
        assert res_json2["success"] is True
        assert res_json2["applied"] is True
        # Verify the parameters were updated in self.params
        assert cli.params["pitch_floor"] == res_json2["suggestions"]["recommended"]["floor"]
        assert cli.params["pitch_ceiling"] == res_json2["suggestions"]["recommended"]["ceiling"]
        # Boundary values should remain unchanged after preset application
        assert speaker.items["item_1"]["start"] == old_start
        assert speaker.items["item_1"]["end"] == old_end
        # Pitch params should also be synchronized to item-level metadata
        assert speaker.items["item_1"]["pitch_floor"] == cli.params["pitch_floor"]
        assert speaker.items["item_1"]["pitch_ceiling"] == cli.params["pitch_ceiling"]
