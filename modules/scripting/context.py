# -*- coding: utf-8 -*-
"""
传递给脚本 run(ctx) 函数的上下文对象 ScriptContext。
"""

import json
import os
import tempfile
import zipfile
from collections import OrderedDict

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from .acoustic_operators import (
    _draw_formant_arrows,
    _extract_waveform_for_analysis,
    extract_spectral_moments,
    extract_voice_quality,
    normalize_formants,
    plot_spectrogram_grid,
    spectrogram_data,
)
from .models import (
    FigureResult,
    ProjectPatchResult,
    TableResult,
    _build_zip_member_lookup,
    _coerce_time,
    _normalize_audio_resource_path,
    configure_matplotlib_chinese_font,
    get_f0_normalization_bounds,
)
from .snapshot import (
    DatasetSnapshot,
    _GLOBAL_PROJECT_JSON_CACHE,
    _GLOBAL_SOUND_LRU_CACHE,
    _MAX_GLOBAL_SOUND_CACHE,
    _SOUND_LRU_MAX_ENTRIES,
)


class ScriptContext:
    """
    传递给脚本 run(ctx) 函数的上下文对象。
    """
    def __init__(self, dataset_items, cancel_event=None, teproj_path=None, params=None):
        import numpy as np
        import matplotlib.pyplot as plt
        import scipy
        import parselmouth as _pm
        try:
            import pandas as pd
        except ImportError:
            pd = None

        configure_matplotlib_chinese_font()
        self.dataset = DatasetSnapshot(dataset_items) if not isinstance(dataset_items, DatasetSnapshot) else dataset_items
        self.np = np
        self.pd = pd
        self.plt = plt
        self.scipy = scipy
        self.parselmouth = _pm
        self.params = dict(params or {})
        self._teproj_path = teproj_path
        self._cancel_event = cancel_event
        self._logs = []
        self._project_json_cache = {}
        self._long_sound_cache = OrderedDict()

    def log(self, message):
        """记录一条运行日志"""
        self._logs.append(str(message))

    def figure(self, fig, filename="custom_chart.png", title="自定义图表", formats=None, dpi=None):
        """返回图表结果对象（支持 formats=("png", "pdf", "svg") 多格式同步导出与自定义 dpi）"""
        return FigureResult(fig, filename=filename, title=title, formats=formats, dpi=dpi)

    def table(self, rows, columns=None, title="自定义表格"):
        """返回表格结果对象（支持直接传入 pandas.DataFrame 或 (rows, columns)）"""
        if columns is None and hasattr(rows, "columns") and hasattr(rows, "values"):
            df = rows
            return TableResult(df.values.tolist(), list(df.columns), title)
        return TableResult(rows, columns or [], title)

    def to_point_table(self, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_point_table"""
        return self.dataset.to_point_table(included_only=included_only, as_df=as_df)

    def to_syllable_table(self, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_syllable_table"""
        return self.dataset.to_syllable_table(included_only=included_only, as_df=as_df)

    def to_frame_table(self, item_or_items=None, included_only=True, as_df=True):
        """快捷调用 ctx.dataset.to_frame_table"""
        return self.dataset.to_frame_table(item_or_items=item_or_items, included_only=included_only, as_df=as_df)

    def get_f0_bounds(self, f0_values):
        """计算发音人稳健五度值调域上下限 [s_min, s_max]（1.5×IQR 剔除离群值 + P5/P95 分位数）"""
        s_min, s_max = get_f0_normalization_bounds(f0_values)
        return [s_min, s_max]

    def _get_teproj_cache_token(self):
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            return None
        abs_p = os.path.abspath(self._teproj_path)
        try:
            st = os.stat(abs_p)
            return (abs_p, st.st_mtime_ns, st.st_size)
        except OSError:
            return (abs_p, 0, 0)

    def _read_audio_resource(self, rel_path):
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        norm_path = _normalize_audio_resource_path(rel_path)
        with zipfile.ZipFile(self._teproj_path, "r") as zf:
            member_lookup = _build_zip_member_lookup(zf)
            member = member_lookup.get(norm_path)
            if not member:
                raise FileNotFoundError(f"工程中找不到音频资源：{norm_path}")
            return zf.read(member)

    def _load_sound_from_resource(self, rel_path):
        norm_path = _normalize_audio_resource_path(rel_path)
        tok = self._get_teproj_cache_token()
        cache_key = (tok, norm_path) if tok is not None else (self._teproj_path, norm_path)

        if cache_key in self._long_sound_cache:
            self._long_sound_cache.move_to_end(cache_key)
            return self._long_sound_cache[cache_key]

        if tok is not None and cache_key in _GLOBAL_SOUND_LRU_CACHE:
            _GLOBAL_SOUND_LRU_CACHE.move_to_end(cache_key)
            snd = _GLOBAL_SOUND_LRU_CACHE[cache_key]
            self._long_sound_cache[cache_key] = snd
            return snd

        audio_bytes = self._read_audio_resource(norm_path)
        suffix = os.path.splitext(str(norm_path))[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(prefix="phontracer_script_audio_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            snd = self.parselmouth.Sound(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        self._long_sound_cache[cache_key] = snd
        while len(self._long_sound_cache) > _MAX_GLOBAL_SOUND_CACHE:
            self._long_sound_cache.popitem(last=False)

        if tok is not None:
            _GLOBAL_SOUND_LRU_CACHE[cache_key] = snd
            while len(_GLOBAL_SOUND_LRU_CACHE) > _MAX_GLOBAL_SOUND_CACHE:
                _GLOBAL_SOUND_LRU_CACHE.popitem(last=False)

        return snd

    def _project_item_for_snapshot(self, item):
        if not isinstance(item, dict):
            raise ValueError("load_item_sound 需要传入 ctx.dataset.items 中的条目字典。")
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        tok = self._get_teproj_cache_token()
        project_data = self._project_json_cache.get(tok) if tok is not None else None
        if project_data is None and tok is not None and tok in _GLOBAL_PROJECT_JSON_CACHE:
            project_data = _GLOBAL_PROJECT_JSON_CACHE[tok]
            self._project_json_cache[tok] = project_data

        if project_data is None:
            with zipfile.ZipFile(self._teproj_path, "r") as zf:
                try:
                    raw = zf.read("project.json")
                except KeyError as exc:
                    raise ValueError("工程文件缺少 project.json，无法读取音频。") from exc
            project_data = json.loads(raw.decode("utf-8"))
            if tok is not None:
                self._project_json_cache[tok] = project_data
                _GLOBAL_PROJECT_JSON_CACHE[tok] = project_data
                while len(_GLOBAL_PROJECT_JSON_CACHE) > 16:
                    _GLOBAL_PROJECT_JSON_CACHE.popitem(last=False)

        speakers = project_data.get("speakers", {})
        speaker_id = item.get("speaker_id")
        item_id = item.get("item_id")

        if speaker_id in speakers:
            spk = speakers[speaker_id]
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        for spk in speakers.values():
            if not isinstance(spk, dict):
                continue
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        label = item.get("label", "")
        raise ValueError(f"工程中找不到目标条目：speaker_id={speaker_id}, item_id={item_id}, label={label}")

    def load_item_sound(self, item, padding=0.0):
        """
        从当前 .teproj 中受控读取条目音频，返回 parselmouth.Sound。
        独立音频优先读取条目 path；长音频条目会按 start/end 提取当前片段。
        """
        spk, project_item = self._project_item_for_snapshot(item)
        item_path = project_item.get("path")
        padding = max(0.0, _coerce_time(padding, 0.0))

        if item_path:
            snd = self._load_sound_from_resource(item_path)
            total = float(snd.get_total_duration())
            start = _coerce_time(project_item.get("start"), 0.0)
            end = _coerce_time(project_item.get("end"), total)
            if end > start and (start > 0.0 or end < total):
                from_time = max(0.0, start - padding)
                to_time = min(total, end + padding)
                if to_time > from_time:
                    return snd.extract_part(from_time=from_time, to_time=to_time)
            return snd

        long_audio_path = spk.get("long_audio_path")
        if not long_audio_path:
            raise ValueError(f"条目 {project_item.get('label', '')} 缺少可用音频资源。")

        snd = self._load_sound_from_resource(long_audio_path)
        total = float(snd.get_total_duration())
        start = _coerce_time(project_item.get("start"), 0.0)
        end = _coerce_time(project_item.get("end"), total)
        from_time = max(0.0, start - padding)
        to_time = min(total, end + padding)
        if to_time <= from_time:
            raise ValueError(f"条目 {project_item.get('label', '')} 的音频时间范围无效。")
        return snd.extract_part(from_time=from_time, to_time=to_time)

    # 绑定声学算子方法
    spectrogram_data = spectrogram_data
    plot_spectrogram_grid = plot_spectrogram_grid
    _draw_formant_arrows = _draw_formant_arrows
    _extract_waveform_for_analysis = _extract_waveform_for_analysis
    extract_voice_quality = extract_voice_quality
    extract_spectral_moments = extract_spectral_moments
    normalize_formants = normalize_formants

    def _item_target(self, item):
        if not isinstance(item, dict):
            raise ValueError("数据处理操作需要传入工程条目 item 字典。")
        target = {
            "speaker_id": item.get("speaker_id"),
            "speaker_name": item.get("speaker_name"),
            "item_id": item.get("item_id"),
            "label": item.get("label", ""),
        }
        if not target["speaker_id"] or not target["item_id"]:
            raise ValueError("条目缺少 speaker_id 或 item_id，无法生成可写回操作。")
        return target

    def set_item_fields(self, item, fields, reason=""):
        """生成条目字段修改操作。"""
        if not isinstance(fields, dict):
            raise ValueError("set_item_fields 的 fields 必须是字典。")
        return {
            "op": "set_item_fields",
            "target": self._item_target(item),
            "fields": dict(fields),
            "reason": str(reason or ""),
        }

    def recompute_pitch(self, item, params=None, reason=""):
        """生成重算条目 F0 缓存的操作。"""
        return {
            "op": "recompute_pitch",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def recompute_formant(self, item, params=None, reason=""):
        """生成重算条目共振峰缓存的操作。"""
        return {
            "op": "recompute_formant",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def trim_item_audio(self, item, start=None, end=None, padding=0.0, reason=""):
        """生成裁剪条目音频并替换引用的操作。"""
        return {
            "op": "trim_item_audio",
            "target": self._item_target(item),
            "start": start,
            "end": end,
            "padding": float(padding or 0.0),
            "reason": str(reason or ""),
        }

    def split_project(self, name, item_ids=None, speaker_ids=None, reason=""):
        """生成按发音人或条目拆出新工程的操作。"""
        return {
            "op": "split_project",
            "name": str(name or "拆分工程"),
            "item_ids": list(item_ids or []),
            "speaker_ids": list(speaker_ids or []),
            "reason": str(reason or ""),
        }

    def import_csv_metadata(self, rows, match_on="label", field_map=None, reason=""):
        """
        生成外部表格元数据合并操作。
        第一版不允许脚本读取文件；rows 必须是已经结构化的字典列表。
        """
        if not isinstance(rows, list):
            raise ValueError("import_csv_metadata 的 rows 必须是字典列表。")
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("import_csv_metadata 的每一行必须是字典。")
            clean_rows.append(dict(row))
        return {
            "op": "import_csv_metadata",
            "rows": clean_rows,
            "match_on": str(match_on or "label"),
            "field_map": dict(field_map or {}),
            "reason": str(reason or ""),
        }

    def project_patch(self, operations, title="数据处理脚本结果", description=""):
        """返回工程数据处理结果对象。"""
        return ProjectPatchResult(operations, title=title, description=description)

    def is_cancelled(self):
        """脚本长循环中可调用，用于协作式响应用户取消。"""
        return bool(self._cancel_event and self._cancel_event.is_set())
