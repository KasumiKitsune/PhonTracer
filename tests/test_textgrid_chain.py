import numpy as np
import textgrid

from cli import PhonTracerCLI
from modules.audio_core import process_single_long_word
from modules.data_utils import get_export_textgrid_for_item


def _tier_names(tg):
    return [tier.name for tier in tg.tiers]


def _non_empty(tier):
    return [(round(iv.minTime, 4), round(iv.maxTime, 4), iv.mark) for iv in tier if iv.mark.strip()]


def test_textgrid_locked_chars_bounds_drive_analysis_boundaries():
    sample_rate = 16000
    duration = 1.0
    times = np.arange(int(sample_rate * duration)) / sample_rate
    snd_values = np.sin(2 * np.pi * 220 * times).reshape(1, -1)
    pitch_xs = np.linspace(0.0, duration, 101)
    pitch_freqs = np.full_like(pitch_xs, 220.0)
    params = {"db": 25.0, "skip_front": 0.0, "analysis_mode": "pitch"}

    result = process_single_long_word(
        snd_values,
        sample_rate,
        "卡达",
        0.0,
        duration,
        params,
        True,
        pitch_xs,
        pitch_freqs,
        ref_splits=[0.4],
        ref_chars_bounds=[[0.2, 0.4], [0.6, 0.8]],
    )

    assert result["success"] is True
    assert result["start"] == 0.2
    assert result["end"] == 0.8
    assert result["raw_start"] == 0.2
    assert result["raw_end"] == 0.8
    assert result["inner_splits"] == [0.4]
    assert result["chars_bounds"] == [[0.2, 0.4], [0.6, 0.8]]


def test_single_item_export_always_contains_chars_tier():
    tg = get_export_textgrid_for_item({
        "label": "卡",
        "group": "单字组",
        "start": 0.2,
        "end": 0.7,
        "chars_bounds": [[0.2, 0.7]],
    }, max_time=1.0)

    assert _tier_names(tg) == ["groups", "words", "chars"]
    chars = next(tier for tier in tg.tiers if tier.name == "chars")
    assert _non_empty(chars) == [(0.2, 0.7, "卡")]


def test_cli_textgrid_export_always_contains_chars_tier(tmp_path):
    out_path = tmp_path / "single.TextGrid"
    cli = object.__new__(PhonTracerCLI)

    cli._write_textgrid(str(out_path), [{
        "label": "卡",
        "group": "单字组",
        "start": 0.2,
        "end": 0.7,
        "chars_bounds": [[0.2, 0.7]],
    }])

    tg = textgrid.TextGrid.fromFile(str(out_path))
    assert _tier_names(tg) == ["groups", "words", "chars"]
    chars = next(tier for tier in tg.tiers if tier.name == "chars")
    assert _non_empty(chars) == [(0.2, 0.7, "卡")]
