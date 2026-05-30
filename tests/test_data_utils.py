import pytest
import sys
import numpy as np
import parselmouth

from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, has_cjk, split_into_syllables

def test_parse_wordlist_basic():
    raw_text = """
【第一组】
字1 字2
[第二组]
字3,字4
# 第三组
字5、字6
"""
    groups, flat_words = parse_wordlist(raw_text)

    assert len(groups) == 3
    assert groups[0]["group"] == "第一组"
    assert groups[0]["items"] == ["字1", "字2"]

    assert groups[1]["group"] == "第二组"
    assert groups[1]["items"] == ["字3", "字4"]

    assert groups[2]["group"] == "第三组"
    assert groups[2]["items"] == ["字5", "字6"]

    assert flat_words == ["字1", "字2", "字3", "字4", "字5", "字6"]

def test_parse_wordlist_no_group():
    raw_text = "字1 字2"
    groups, flat_words = parse_wordlist(raw_text)

    assert len(groups) == 1
    assert groups[0]["group"] == "未分组"
    assert groups[0]["items"] == ["字1", "字2"]
    assert flat_words == ["字1", "字2"]

def test_parse_wordlist_empty():
    groups, flat_words = parse_wordlist("")
    assert groups == []
    assert flat_words == []

    groups, flat_words = parse_wordlist("\n  \n")
    assert groups == []
    assert flat_words == []

def test_parse_wordlist_separators():
    # Testing space, tab, comma, chinese comma, ideographic comma
    raw_text = "word1 word2\tword3,word4，word5、word6"
    groups, flat_words = parse_wordlist(raw_text)

    expected = ["word1", "word2", "word3", "word4", "word5", "word6"]
    assert flat_words == expected
    assert groups[0]["items"] == expected

def test_parse_wordlist_mixed_headers():
    raw_text = """
【Header1】
Item1
# Header2
Item2
[Header3]
Item3
"""
    groups, flat_words = parse_wordlist(raw_text)
    assert [g["group"] for g in groups] == ["Header1", "Header2", "Header3"]
    assert flat_words == ["Item1", "Item2", "Item3"]

def test_parse_wordlist_trailing_items():
    raw_text = """
【Group1】
Item1
Item2
"""
    groups, _ = parse_wordlist(raw_text)
    assert len(groups) == 1
    assert groups[0]["items"] == ["Item1", "Item2"]

def test_parse_wordlist_multiple_per_line():
    raw_text = "item1 item2\nitem3   item4\titem5"
    groups, flat_words = parse_wordlist(raw_text)
    assert flat_words == ["item1", "item2", "item3", "item4", "item5"]

def test_parse_wordlist_with_empty_lines_and_whitespace():
    raw_text = """

【Header】
   item1

   item2, item3

"""
    groups, flat_words = parse_wordlist(raw_text)
    assert len(groups) == 1
    assert groups[0]["group"] == "Header"
    assert groups[0]["items"] == ["item1", "item2", "item3"]
    assert flat_words == ["item1", "item2", "item3"]

def test_parse_wordlist_header_cleaning():
    raw_text = """
【  Header 1  】
item1
[  Header 2  ]
item2
#  Header 3
item3
"""
    groups, _ = parse_wordlist(raw_text)
    assert groups[0]["group"] == "Header 1"
    assert groups[1]["group"] == "Header 2"
    assert groups[2]["group"] == "Header 3"

def test_parse_wordlist_only_headers():
    raw_text = """
【Header1】
【Header2】
"""
    groups, flat_words = parse_wordlist(raw_text)
    assert len(groups) == 0
    assert flat_words == []

def test_fuzzy_match_basic():
    paths = ["path/to/apple.wav", "path/to/banana.wav"]
    assert fuzzy_match_word_to_path("apple", paths) == 0
    assert fuzzy_match_word_to_path("BANANA", paths) == 1
    assert fuzzy_match_word_to_path("cherry", paths) is None

def test_fuzzy_match_cleaning():
    paths = ["path/to/apple_test.wav"]
    # BOM and case
    assert fuzzy_match_word_to_path("\ufeffAPPLE", paths) == 0

    # NFC normalization
    accented = "e\u0301" # é (NFD)
    normalized = "\u00e9" # é (NFC)
    paths_accented = [f"path/to/{normalized}.wav"]
    assert fuzzy_match_word_to_path(accented, paths_accented) == 0

    # Regex cleaning: [^\w\u4e00-\u9fa5]|_
    paths_regex = ["path/to/word_with_extra!@#.wav"]
    assert fuzzy_match_word_to_path("wordwithextra", paths_regex) == 0

def test_fuzzy_match_substring():
    paths = ["apple_pie.wav", "banana.wav"]
    # word_clean in fname_clean
    assert fuzzy_match_word_to_path("apple", paths) == 0
    # fname_clean in word_clean
    assert fuzzy_match_word_to_path("banana_extra", paths) == 1

def test_fuzzy_match_ranking():
    # Exact match vs Substring
    paths = ["apple_pie.wav", "apple.wav"]
    assert fuzzy_match_word_to_path("apple", paths) == 1 # 1 is exact, 0 is substring

    # Used indices
    paths = ["apple.wav", "apple.wav"]
    assert fuzzy_match_word_to_path("apple", paths, used_indices=[0]) == 1

    # Length difference
    paths = ["apple_1.wav", "apple_123.wav"]
    assert fuzzy_match_word_to_path("apple", paths) == 0 # apple_1 is closer in length

def test_fuzzy_match_edge_cases():
    assert fuzzy_match_word_to_path("", ["a.wav"]) is None
    assert fuzzy_match_word_to_path("a", []) is None
    assert fuzzy_match_word_to_path("   ", ["a.wav"]) is None

def test_has_cjk():
    assert has_cjk("北京") is True
    assert has_cjk("1. 北京_test") is True
    assert has_cjk("brother") is False
    assert has_cjk("bro/ther") is False
    assert has_cjk("") is False

def test_split_into_syllables():
    assert split_into_syllables("北/京") == ["北", "京"]
    assert split_into_syllables("bro/ther") == ["bro", "ther"]
    assert split_into_syllables("1. bro/ther") == ["1. bro", "ther"]
    assert split_into_syllables("北京") == ["北", "京"]
    assert split_into_syllables("1. 北京_test") == ["北", "京"]
    assert split_into_syllables("brother") == ["brother"]
    assert split_into_syllables("") == []

def test_fuzzy_match_smart_cjk_filtering():
    paths = ["01_北京.wav", "02_上海.wav"]
    assert fuzzy_match_word_to_path("1. 北京_spec", paths) == 0
    assert fuzzy_match_word_to_path("北京", paths) == 0

def test_fuzzy_match_smart_latin_filtering():
    paths = ["01_brother.wav", "02_sister.wav"]
    assert fuzzy_match_word_to_path("bro/ther", paths) == 0
    assert fuzzy_match_word_to_path("brother", paths) == 0


def test_get_item_syllable_bounds():
    from modules.data_utils import get_item_syllable_bounds
    
    # 1. Normal chars_bounds matched with label syllables
    item = {
        'start': 0.0,
        'end': 1.0,
        'label': '北京',
        'chars_bounds': [[0.0, 0.4], [0.4, 1.0]]
    }
    assert get_item_syllable_bounds(item) == [[0.0, 0.4], [0.4, 1.0]]

    # 2. Falling back to inner_splits
    item = {
        'start': 0.0,
        'end': 1.0,
        'label': '北京',
        'inner_splits': [0.35]
    }
    assert get_item_syllable_bounds(item) == [[0.0, 0.35], [0.35, 1.0]]

    # 3. Falling back to linear splits
    item = {
        'start': 0.0,
        'end': 1.0,
        'label': '北京',
    }
    assert get_item_syllable_bounds(item) == [[0.0, 0.5], [0.5, 1.0]]


def test_sample_formant_points_by_bounds():
    from modules.data_utils import sample_formant_points_by_bounds
    
    # 1. Empty data
    item = {
        'start': 0.0,
        'end': 1.0,
        'label': '北京'
    }
    bounds = [[0.0, 0.5], [0.5, 1.0]]
    times, f1, f2 = sample_formant_points_by_bounds(item, bounds, pts=3)
    assert len(times) == 6
    assert np.isnan(f1).all()
    assert np.isnan(f2).all()

    # 2. Valid data
    item = {
        'start': 0.0,
        'end': 1.0,
        'label': 'A',
        'formant_data': {
            'xs': np.array([0.0, 0.25, 0.5, 0.75, 1.0]),
            'f1': np.array([500.0, 520.0, 540.0, 560.0, 580.0]),
            'f2': np.array([1500.0, 1520.0, 1540.0, 1560.0, 1580.0])
        }
    }
    bounds = [[0.0, 1.0]]
    times, f1, f2 = sample_formant_points_by_bounds(item, bounds, pts=5, strategy='整段11点')
    assert len(times) == 5
    assert not np.isnan(f1).any()
    assert (np.array(f2) > np.array(f1)).all()


def test_extract_wordlist_from_textgrid(tmp_path):
    import textgrid
    import os
    from modules.data_utils import extract_wordlist_from_textgrid

    # Create a dummy textgrid
    tg = textgrid.TextGrid(maxTime=2.0)
    groups_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=2.0)
    groups_tier.add(0.0, 1.0, "组别A")
    groups_tier.add(1.0, 2.0, "组别B")
    
    words_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=2.0)
    words_tier.add(0.0, 0.5, "词1")
    words_tier.add(0.5, 1.0, "词2")
    words_tier.add(1.0, 1.5, "词3")
    words_tier.add(1.5, 2.0, "")

    tg.append(groups_tier)
    tg.append(words_tier)

    tg_file = os.path.join(tmp_path, "test.TextGrid")
    tg.write(tg_file)

    res = extract_wordlist_from_textgrid(tg_file)
    assert "【组别A】" in res
    assert "词1 词2" in res
    assert "【组别B】" in res
    assert "词3" in res


