# -*- coding: utf-8 -*-
"""
Tests for Praat .Collection workspace export (Sound 2, TextGrid, Pitch 1, Formant 2)
in both ooBinaryFile and ooTextFile formats, including round-trip verification via
Praat's C++ reader (parselmouth) and PhonTracer CLI.
"""

import os
import numpy as np
import pytest
import textgrid

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth

from modules.export_service import (
    clean_praat_object_name,
    write_praat_collection,
    build_combined_textgrid_from_items,
    build_collection_items_for_audio_group,
    build_single_item_collection_objects,
    build_speaker_collection_objects,
    export_speaker_to_praat_collection,
)


def _make_synthetic_item(tmp_path, label="爸爸_阴平", start=0.1, end=0.7):
    sr = 16000.0
    dur = 0.8
    t = np.arange(int(sr * dur)) / sr
    # 180 Hz sine wave
    wave = 0.5 * np.sin(2.0 * np.pi * 180.0 * t)
    snd = parselmouth.Sound(wave, sampling_frequency=sr)
    wav_path = str(tmp_path / "baba.wav")
    snd.save(wav_path, "WAV")

    xs = np.arange(0.01, dur, 0.01)
    freqs = np.full_like(xs, 180.0)
    # Simulate manual Eraser on middle frames (indices 25..30 erased to 0.0)
    freqs[25:30] = 0.0
    # Outside [start, end] unvoiced
    freqs[xs < start] = 0.0
    freqs[xs > end] = 0.0

    fmt_xs = np.arange(0.01, dur, 0.01)
    f1 = np.full_like(fmt_xs, 750.0)
    f2 = np.full_like(fmt_xs, 1250.0)
    f3 = np.full_like(fmt_xs, 2600.0)
    # Simulate manual Formant Eraser on indices 30..35 (both F1 & F2 set to NaN)
    f1[30:35] = np.nan
    f2[30:35] = np.nan
    # Simulate erasing ONLY F1 on indices 45..50 while keeping F2 = 1250.0
    f1[45:50] = np.nan

    return {
        "label": label,
        "group": "阴平组",
        "path": wav_path,
        "snd": snd,
        "start": start,
        "end": end,
        "inner_splits": [0.4],
        "chars_bounds": [[0.1, 0.4], [0.4, 0.7]],
        "pitch_ceiling": 550.0,
        "pitch_data": {
            "xs": xs,
            "freqs": freqs,
        },
        "formant_data": {
            "xs": fmt_xs,
            "f1": f1,
            "f2": f2,
            "f3": f3,
        },
    }


def test_clean_praat_object_name():
    assert clean_praat_object_name("001 爸爸 (阴平).wav") == "001_爸爸_阴平_wav"
    assert clean_praat_object_name("   ", fallback="default_obj") == "default_obj"


@pytest.mark.parametrize("binary", [True, False])
def test_write_and_praat_read_collection_roundtrip(tmp_path, binary):
    item = _make_synthetic_item(tmp_path)
    objs = build_single_item_collection_objects(item, default_ceiling=550.0)
    assert [o["class"] for o in objs] == ["Sound 2", "TextGrid", "Pitch 1", "Formant 2"]

    out_col = str(tmp_path / f"roundtrip_{'bin' if binary else 'txt'}.Collection")
    written = write_praat_collection(out_col, objs, binary=binary)
    assert os.path.exists(written)

    with open(written, "rb") as f:
        head = f.read(32)
    if binary:
        assert head.startswith(b"ooBinaryFile\x0aCollection")
    else:
        assert head.startswith(b'File type = "ooTextFile"')

    # Verify that Praat's native C++ reader (via parselmouth) reads the .Collection
    # and unpacks Sound, TextGrid, Pitch, and Formant without error!
    raw_collection = parselmouth.Data.read(written)
    assert raw_collection.class_name == "Collection"
    loaded = parselmouth.praat.call("Read from file...", written)
    assert isinstance(loaded, list)
    assert len(loaded) == 4

    snd_obj, tg_obj, pitch_obj, formant_obj = loaded
    assert isinstance(snd_obj, parselmouth.Sound)
    assert abs(snd_obj.get_total_duration() - 0.8) < 1e-3

    assert isinstance(tg_obj, parselmouth.TextGrid)
    n_tiers = parselmouth.praat.call(tg_obj, "Get number of tiers")
    assert n_tiers == 3
    tier2_label = parselmouth.praat.call(tg_obj, "Get label of interval", 2, 2)
    assert tier2_label == "爸爸_阴平"

    assert isinstance(pitch_obj, parselmouth.Pitch)
    # Check Voiced frame vs Erased frame in Pitch
    f0_at_020 = parselmouth.praat.call(pitch_obj, "Get value at time", 0.20, "Hertz", "Linear")
    f0_at_028 = parselmouth.praat.call(pitch_obj, "Get value at time", 0.28, "Hertz", "Linear")
    assert abs(f0_at_020 - 180.0) < 1.0
    assert np.isnan(f0_at_028)  # Erased frame in Praat returns undefined (NaN)

    # Check Formant object
    f1_at_020 = parselmouth.praat.call(formant_obj, "Get value at time", 1, 0.20, "hertz", "Linear")
    f1_at_033 = parselmouth.praat.call(formant_obj, "Get value at time", 1, 0.33, "hertz", "Linear")
    f1_at_048 = parselmouth.praat.call(formant_obj, "Get value at time", 1, 0.48, "hertz", "Linear")
    f2_at_048 = parselmouth.praat.call(formant_obj, "Get value at time", 2, 0.48, "hertz", "Linear")
    assert abs(f1_at_020 - 750.0) < 5.0
    assert np.isnan(f1_at_033)  # Erased formant frame returns undefined (NaN)
    assert np.isnan(f1_at_048)  # Single-band erased F1 returns undefined (NaN)
    assert abs(f2_at_048 - 1250.0) < 5.0  # F2 stays in slot 2 without shifting!


def test_long_audio_speaker_export_and_single_item_slice(tmp_path):
    sr = 16000.0
    dur = 2.0
    t = np.arange(int(sr * dur)) / sr
    long_snd = parselmouth.Sound(0.4 * np.sin(2.0 * np.pi * 200.0 * t), sampling_frequency=sr)

    xs1 = np.arange(0.1, 0.8, 0.01)
    freqs1 = np.full_like(xs1, 200.0)
    freqs1[10:15] = 0.0  # Erased segment

    xs2 = np.arange(1.1, 1.8, 0.01)
    freqs2 = np.full_like(xs2, 240.0)

    items_dict = {
        "i1": {
            "label": "第一词",
            "group": "A组",
            "start": 0.2,
            "end": 0.7,
            "macro_start": 0.1,
            "macro_end": 0.8,
            "inner_splits": [0.45],
            "chars_bounds": [[0.2, 0.45], [0.45, 0.7]],
            "pitch_data": {"xs": xs1, "freqs": freqs1},
        },
        "i2": {
            "label": "第二词",
            "group": "B组",
            "start": 1.2,
            "end": 1.7,
            "macro_start": 1.1,
            "macro_end": 1.8,
            "inner_splits": [1.45],
            "chars_bounds": [[1.2, 1.45], [1.45, 1.7]],
            "pitch_data": {"xs": xs2, "freqs": freqs2},
        },
    }
    structure = [("A组", ["i1"]), ("B组", ["i2"])]
    out_col = str(tmp_path / "long_speaker.Collection")
    export_speaker_to_praat_collection(
        out_path=out_col,
        items_dict=items_dict,
        tree_structure=structure,
        is_batch_mode=False,
        long_snd=long_snd,
        speaker_name="发音人甲",
        binary=True,
    )
    loaded = parselmouth.praat.call("Read from file...", out_col)
    assert len(loaded) == 3  # Sound, TextGrid, Pitch
    pitch_obj = loaded[2]
    assert np.isnan(parselmouth.praat.call(pitch_obj, "Get value at time", 0.22, "Hertz", "Linear"))
    assert abs(parselmouth.praat.call(pitch_obj, "Get value at time", 0.50, "Hertz", "Linear") - 200.0) < 1.0
    assert abs(parselmouth.praat.call(pitch_obj, "Get value at time", 1.50, "Hertz", "Linear") - 240.0) < 1.0

    # Also test slicing a single item out of long audio
    single_objs = build_single_item_collection_objects(items_dict["i2"], long_snd=long_snd)
    single_col = str(tmp_path / "single_sliced.Collection")
    write_praat_collection(single_col, single_objs, binary=True)
    loaded_single = parselmouth.praat.call("Read from file...", single_col)
    assert len(loaded_single) == 3
    assert abs(loaded_single[0].get_total_duration() - 0.7) < 0.02


def test_cli_export_collection(tmp_path):
    from cli import PhonTracerCLI

    item = _make_synthetic_item(tmp_path)
    cli_app = PhonTracerCLI(quiet=True, json_mode=True)
    spk = cli_app.current_speaker
    spk.items["item_1"] = item
    spk.cli_groups = ["阴平组"]
    cli_app.groups = ["阴平组"]

    out_bin = str(tmp_path / "cli_out.Collection")
    cli_app.do_export(f'collection "{out_bin}"')
    assert os.path.exists(out_bin)
    loaded_bin = parselmouth.praat.call("Read from file...", out_bin)
    assert len(loaded_bin) == 4

    out_txt = str(tmp_path / "cli_out_txt.Collection")
    cli_app.do_export(f'collection "{out_txt}" binary=false')
    assert os.path.exists(out_txt)
    loaded_txt = parselmouth.praat.call("Read from file...", out_txt)
    assert len(loaded_txt) == 4
