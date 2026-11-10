# -*- coding: utf-8 -*-
"""
PhonTracer CLI - I/O Utilities and Headless Chart Adapters.
Pure headless helper functions, stdout logger, and JSON/UTF-8 utilities.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, List, Optional

import numpy as np

from modules.version import APP_NAME, __version__

COMMON_AUDIO_EXTENSIONS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma')

# Override json.dumps to output raw UTF-8 Chinese characters rather than Unicode escape sequences (\uXXXX)
_orig_dumps = json.dumps
def dumps_utf8(*args, **kwargs):
    if 'ensure_ascii' not in kwargs:
        kwargs['ensure_ascii'] = False
    if 'allow_nan' not in kwargs:
        kwargs['allow_nan'] = False
    return _orig_dumps(*args, **kwargs)

json.dumps = dumps_utf8

def _natural_sort_key(s):
    text = os.path.basename(str(s))
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'([0-9]+)', text)]


class LoggerOut:
    def __init__(self, original_stdout, log_file):
        self.original_stdout = original_stdout
        self.log_file = log_file

    def write(self, message):
        self.original_stdout.write(message)
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self):
        self.original_stdout.flush()
        self.log_file.flush()


class AcousticChartCLIAdapter:
    def __init__(self, cli_instance):
        self.cli = cli_instance
        self.app_state_params = cli_instance.params  # Mapped from self.params in CLI
        self.items = cli_instance.items

    def _get_items_by_group_for_dict(self, items_dict):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_items_by_group_for_dict(items_dict)

    def _ensure_item_loaded(self, item):
        self.cli._ensure_item_loaded(item)

    def _extract_syl_data(self, item, num_points):
        return self.cli._extract_syl_data(item, num_points)

    def _get_pitch_arrays_for_item(self, item):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_pitch_arrays_for_item(item)

    def _get_syllables_and_bounds(self, item):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.get_syllables_and_bounds(item)

    def _extract_kde_contour(self, p_xs, p_freqs, c_s, c_e, n_dense):
        from modules.export_service import UnifiedExportService
        return UnifiedExportService.extract_kde_contour(p_xs, p_freqs, c_s, c_e, n_dense)


def _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, valid_xs=None):
    """
    计算预览 F0 轨迹。使用 np.searchsorted 进行二分查找加速，避免内层循环重复布尔扫描。
    """
    if valid_xs is None:
        valid_indices = np.where(pitch_freqs > 0)[0]
        if len(valid_indices) == 0:
            return [0.0] * len(preview_times)
        valid_xs = pitch_xs[valid_indices]
    elif len(valid_xs) == 0:
        return [0.0] * len(preview_times)

    preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
    idxs = np.searchsorted(valid_xs, preview_times)
    n = len(valid_xs)
    for j, (t, idx) in enumerate(zip(preview_times, idxs)):
        dist = float("inf")
        if idx < n:
            dist = valid_xs[idx] - t
        if idx > 0:
            left_dist = t - valid_xs[idx - 1]
            if left_dist < dist:
                dist = left_dist
        if dist > 0.025:
            preview_f0[j] = 0.0
    return preview_f0


def _has_cjk_in_args(args_list) -> bool:
    for item in (args_list or []):
        if isinstance(item, str) and re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', item):
            return True
    return False


__all__ = [
    "COMMON_AUDIO_EXTENSIONS",
    "dumps_utf8",
    "_natural_sort_key",
    "LoggerOut",
    "AcousticChartCLIAdapter",
    "_calc_preview_f0",
    "_has_cjk_in_args",
]
