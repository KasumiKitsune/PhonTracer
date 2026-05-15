import pytest
from unittest.mock import MagicMock
import sys

# Mocking modules that might not be present in the environment
sys.modules['numpy'] = MagicMock()
sys.modules['parselmouth'] = MagicMock()

from modules.data_utils import parse_wordlist

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
