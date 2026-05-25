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
