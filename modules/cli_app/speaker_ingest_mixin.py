# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Speaker Management and Audio/TextGrid Ingest Mixin.
Handles speaker CRUD, audio ingestion (long/batch), wordlist mounting, and TextGrid conversion.
"""
from __future__ import annotations

import concurrent.futures
import copy
import glob
import json
import os
import re
import shlex
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import textgrid

from modules.audio_core import (
    auto_split_inner_word,
    auto_split_to_chars_bounds,
    batch_process_worker,
    core_microscopic_vowel_nucleus,
    extract_f0,
    macroscopic_vad,
    process_single_long_word,
    recalculate_bounds_fast,
    trim_bounds_by_amplitude,
)
from modules.cli_app.io_utils import (
    COMMON_AUDIO_EXTENSIONS,
    _calc_preview_f0,
    _natural_sort_key,
    dumps_utf8,
)
from modules.data_utils import (
    fuzzy_match_word_to_path,
    parse_wordlist,
    split_into_syllables,
)
from modules.textgrid_converter import (
    DEFAULT_GROUP_NAME,
    NONE_TIER_LABEL,
    PAIR_MODE_ADJACENT,
    PAIR_MODE_NONE,
    TextGridMapping,
    convert_textgrid,
    inspect_textgrid,
    load_auxiliary_wordlist_records,
    preview_textgrid_conversion,
    recommend_tier_names,
)
from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    apply_record_metadata,
    build_document_from_csv_text,
    build_document_from_messy_text,
    build_document_from_structured_text,
    flatten_wordlist_document,
    load_wordlist_file,
    load_wordlist_from_csv_file,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
    normalize_wordlist_document,
    summarize_wordlist_document,
    validate_wordlist_document,
)


class CLISpeakerIngestMixin:
    """
    Speaker ingest mixin providing speaker lifecycle, audio loading, wordlist matching,
    and TextGrid converter commands.
    """
    def do_speakers(self, arg):
        """
        List all speakers and show the active one.
        Usage: speakers
        """
        speakers = self.speaker_manager.get_all_speakers()
        active_id = self.speaker_manager.active_speaker_id
        res = []
        for s in speakers:
            res.append({
                "id": s.id,
                "name": s.name,
                "active": s.id == active_id,
                "items_count": len(s.items)
            })
        self._emit(True, speakers=res)


    def do_add_speaker(self, arg):
        """
        Add a new speaker.
        Usage: add_speaker <name> [--switch|--no-switch] [gender=female|male|child] [formant_max_hz=...]
        By default, automatically switches to the newly added speaker unless --no-switch is passed.
        If the initial default shell speaker ('发音人 1' or 'Speaker 1') has no data, it is automatically renamed/reused.
        """
        try:
            tokens = shlex.split(arg)
        except ValueError:
            tokens = arg.strip().split()

        do_switch = True
        name_parts = []
        kv_opts = {}
        for tok in tokens:
            low = tok.lower().strip()
            if low in ("--switch", "-s", "switch=true", "switch=1", "active=true"):
                do_switch = True
            elif low in ("--no-switch", "switch=false", "switch=0", "active=false"):
                do_switch = False
            elif "=" in tok and tok.split("=", 1)[0].strip().lower() in (
                "gender", "sex", "formant_max_hz", "pitch_floor", "pitch_ceiling", "pts", "db", "analysis_mode"
            ):
                k, v = tok.split("=", 1)
                kv_opts[k.strip().lower()] = v.strip()
            else:
                name_parts.append(tok)

        name = " ".join(name_parts).strip()
        if not name:
            self._emit(False, error="Speaker name required", hint="用法: add_speaker <发音人名称> [--switch]")
            return

        all_speakers = self.speaker_manager.get_all_speakers()
        replaced_default = False
        target_speaker = None

        empty_default = None
        for s in all_speakers:
            if self._is_empty_default_speaker(s):
                empty_default = s
                break

        if empty_default is not None and len(all_speakers) == 1:
            self.speaker_manager.rename_speaker(empty_default.id, name)
            target_speaker = empty_default
            replaced_default = True
        else:
            target_speaker = self.speaker_manager.add_speaker(name)
            if empty_default is not None and empty_default.id != target_speaker.id:
                self.speaker_manager.remove_speaker(empty_default.id)
                replaced_default = True

        gender_val = kv_opts.get("gender") or kv_opts.get("sex")
        if gender_val:
            g_low = gender_val.lower()
            if g_low in ("female", "f", "woman", "女", "女性"):
                target_speaker.last_params["formant_max_hz"] = 5500.0
            elif g_low in ("male", "m", "man", "男", "男性"):
                target_speaker.last_params["formant_max_hz"] = 5000.0
            elif g_low in ("child", "c", "kid", "儿童", "小孩"):
                target_speaker.last_params["formant_max_hz"] = 8000.0
        for p_key in ("formant_max_hz", "pitch_floor", "pitch_ceiling", "db"):
            if p_key in kv_opts:
                try:
                    target_speaker.last_params[p_key] = float(kv_opts[p_key])
                except ValueError:
                    pass

        if do_switch:
            self.speaker_manager.set_active_speaker(target_speaker.id)

        self._after_state_change("add_speaker")
        self._emit(
            True,
            f"Speaker '{name}' added",
            speaker_id=target_speaker.id,
            name=target_speaker.name,
            active=(self.speaker_manager.active_speaker_id == target_speaker.id),
            replaced_default=replaced_default,
        )


    def do_switch_speaker(self, arg):
        """
        Switch the active speaker.
        Usage: switch_speaker <name_or_id>
        """
        target = arg.strip().strip('"').strip("'")
        if not target:
            self._emit(False, error="Speaker name or id required", hint="用法: switch_speaker <发音人名称或ID>")
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if found_id:
            self.speaker_manager.set_active_speaker(found_id)
            self._after_state_change("switch_speaker")
            self._emit(
                True,
                f"Switched to speaker '{target}'",
                speaker_id=found_id,
                name=self.current_speaker.name,
                active_speaker_id=found_id,
                active_speaker=self.current_speaker.name,
            )
        else:
            available = [s.name for s in self.speaker_manager.get_all_speakers()]
            self._emit(
                False,
                error=f"Speaker '{target}' not found",
                available_speakers=available,
                hint="请使用 speakers 查看当前可用发音人列表。",
            )


    def do_rename_speaker(self, arg):
        """
        Rename a speaker or the currently active speaker.
        Usage: rename_speaker <old_name_or_id> <new_name>
               rename_speaker <new_name>  (renames the active speaker)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(False, error="Speaker name required. Usage: rename_speaker [<old_name>] <new_name>")
            return

        if len(args) == 1:
            target_spk = self.current_speaker
            new_name = args[0].strip()
        else:
            old_target = args[0].strip()
            new_name = " ".join(args[1:]).strip()
            target_spk = None
            for s in self.speaker_manager.get_all_speakers():
                if s.id == old_target or s.name == old_target:
                    target_spk = s
                    break
            if target_spk is None:
                self._emit(False, error=f"Speaker '{old_target}' not found")
                return

        if not new_name:
            self._emit(False, error="New speaker name required")
            return

        old_name = target_spk.name
        self.speaker_manager.rename_speaker(target_spk.id, new_name)
        for item in target_spk.items.values():
            if isinstance(item, dict):
                item["speaker"] = new_name
                item["speaker_id"] = target_spk.id

        self._after_state_change("rename_speaker")
        self._emit(
            True,
            f"Speaker '{old_name}' renamed to '{new_name}'",
            speaker_id=target_spk.id,
            old_name=old_name,
            new_name=new_name,
            name=new_name,
            active=(self.speaker_manager.active_speaker_id == target_spk.id),
        )


    def do_remove_speaker(self, arg):
        """
        Remove a speaker. Cannot remove the last speaker.
        Usage: remove_speaker <name_or_id>
        """
        target = arg.strip().strip('"').strip("'")
        if not target:
            self._emit(False, error="Speaker name or id required")
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if not found_id:
            self._emit(False, error=f"Speaker '{target}' not found")
            return

        success = self.speaker_manager.remove_speaker(found_id)
        if success:
            self._after_state_change("remove_speaker")
            self._emit(True, f"Speaker '{target}' removed")
        else:
            self._emit(False, error="Cannot remove the last speaker")


    def do_load_long(self, arg):
        """
        Load a single long audio file.
        Usage: load_long <filepath>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Filepath required")
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
            return

        try:
            self.long_snd = parselmouth.Sound(filepath)
            self.long_snd_path = filepath
            self.mode = 'long'
            self.batch_paths = []
            self.items.clear()
            self.groups.clear()
            self._after_state_change("load_long")
            self._emit(True, "长音频已加载。下一步通常是 apply_wordlist 或 apply_textgrid。", mode=self.mode, path=filepath)
        except Exception as e:
            self._emit(False, error=str(e))


    def do_load_batch(self, arg):
        """
        Load multiple independent audio files from file paths, directory paths, or wildcard patterns.
        Usage: load_batch <file_or_dir_or_glob1> [file_or_dir_or_glob2 ...]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(
                False,
                error="Filepaths required",
                hint="请传入音频文件路径、目录路径（如 load_batch ./female_isolated）或 *.wav 通配符（如 load_batch ./male_isolated/*.wav）。",
            )
            return

        valid_paths, err_msg, hint_msg = self._expand_batch_audio_inputs(args)
        if err_msg:
            self._emit(False, error=err_msg, hint=hint_msg)
            return

        self.batch_paths = valid_paths
        self.mode = 'batch'
        self.long_snd = None
        self.long_snd_path = None
        self.items.clear()
        self.groups.clear()
        self._after_state_change("load_batch")
        self._emit(
            True,
            f"已加载 {len(self.batch_paths)} 个独立音频。下一步通常是 apply_wordlist。",
            mode=self.mode,
            count=len(self.batch_paths),
            paths=self.batch_paths,
        )


    def do_apply_wordlist(self, arg):
        """
        Apply a wordlist (.ptwl, .csv, structured .txt, or messy .txt) to split/match the audio.
        Usage: apply_wordlist <wordlist_filepath> [match_mode]
        match_mode for batch: 'fuzzy' or 'order' (default 'fuzzy')
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(False, error="Wordlist filepath required")
            return

        filepath = None
        match_mode = "fuzzy"
        custom_title = None
        for tok in args:
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lower()
                if k_low in ("match_mode", "mode", "match"):
                    match_mode = v.strip().lower()
                elif k_low == "title":
                    custom_title = v.strip()
                elif filepath is None:
                    filepath = tok
            else:
                if filepath is None:
                    filepath = tok
                elif tok.lower() in ("fuzzy", "order"):
                    match_mode = tok.lower()

        if not filepath or not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
            return

        try:
            doc, groups, flat_words, records = self._load_wordlist_any(filepath, title=custom_title)
        except Exception as e:
            self._emit(False, error=f"Failed to load wordlist: {e}")
            return

        if not flat_words:
            self._emit(False, error="No words found in wordlist")
            return

        self.wordlist_doc = doc
        self.groups = [g["group"] for g in groups]
        self.current_speaker.project_groups = list(self.groups)
        self.items.clear()

        if self.mode in ("long", "single"):
            self._process_long_wordlist(groups, flat_words, metadata_records=records, wordlist_doc=doc)
        elif self.mode == "batch":
            self._process_batch_wordlist(groups, flat_words, match_mode, metadata_records=records, wordlist_doc=doc)
        else:
            self._emit(False, error="No audio loaded. Use load_long or load_batch first.")


    def do_apply_textgrid(self, arg):
        """
        Apply a TextGrid file to segment and match the active long audio.
        Usage: apply_textgrid <textgrid_filepath>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="TextGrid filepath required")
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
            return

        if self.mode not in ('long', 'single') or not self.long_snd:
            self._emit(False, error="This command requires active long audio mode. Use load_long first.")
            return

        try:
            import textgrid
            tg = textgrid.TextGrid.fromFile(filepath)

            words_tier = None
            chars_tier = None
            groups_tier = None
            for t in tg.tiers:
                if t.name == "words" and words_tier is None:
                    words_tier = t
                elif t.name == "chars" and chars_tier is None:
                    chars_tier = t
                elif t.name in ["groups", "group"] and groups_tier is None:
                    groups_tier = t

            if not words_tier:
                for t in tg.tiers:
                    if isinstance(t, textgrid.IntervalTier):
                        words_tier = t
                        break

            if not words_tier:
                self._emit(False, error="No IntervalTier found in TextGrid")
                return

            tg_intervals = []
            for interval in words_tier:
                lbl = interval.mark.strip()
                if lbl:
                    grp_name = "导入内容"
                    if groups_tier:
                        center = (interval.minTime + interval.maxTime) / 2.0
                        for g_interval in groups_tier:
                            if g_interval.minTime <= center <= g_interval.maxTime:
                                g_lbl = g_interval.mark.strip()
                                if g_lbl:
                                    grp_name = g_lbl
                                    break

                    chars_bounds = []
                    inner_splits = []
                    if chars_tier:
                        overlapping_chars = []
                        for c_interval in chars_tier:
                            c_lbl = c_interval.mark.strip()
                            if c_lbl:
                                center = (c_interval.minTime + c_interval.maxTime) / 2.0
                                if interval.minTime <= center <= interval.maxTime:
                                    overlapping_chars.append(c_interval)

                        overlapping_chars.sort(key=lambda c: c.minTime)
                        if overlapping_chars:
                            for c in overlapping_chars:
                                chars_bounds.append([c.minTime, c.maxTime])
                            for j in range(len(overlapping_chars) - 1):
                                inner_splits.append(overlapping_chars[j].maxTime)

                    if not chars_bounds:
                        syls = split_into_syllables(lbl)
                        w_len = len(syls)
                        if w_len > 1:
                            splits = np.linspace(interval.minTime, interval.maxTime, w_len + 1).tolist()
                            chars_bounds = [[splits[j], splits[j+1]] for j in range(w_len)]
                            inner_splits = splits[1:-1]
                        else:
                            chars_bounds = [[interval.minTime, interval.maxTime]]
                            inner_splits = []

                    tg_intervals.append({
                        'start': interval.minTime,
                        'end': interval.maxTime,
                        'label': lbl,
                        'group': grp_name,
                        'inner_splits': inner_splits,
                        'chars_bounds': chars_bounds
                    })

            if not tg_intervals:
                self._emit(False, error="No non-empty labeled intervals found in TextGrid")
                return

            self.items.clear()

            unique_groups = []
            for item in tg_intervals:
                g = item.get('group', '导入内容')
                if g not in unique_groups:
                    unique_groups.append(g)
            self.groups = unique_groups

            snd = self.long_snd
            pitch_data = extract_f0(snd, self.params)

            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            tasks = []
            for item in tg_intervals:
                ms = item['start']
                me = item['end']
                word = item['label']
                grp_name = item.get('group', '导入内容')
                ref_splits = item.get('inner_splits', [])
                ref_chars_bounds = item.get('chars_bounds', [])

                valid_ms = max(0, ms)
                valid_me = min(snd.get_total_duration(), me)

                if valid_me > valid_ms:
                    part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                    snd_values = part.values
                    snd_sf = part.sampling_frequency

                    idx_start = np.searchsorted(pitch_xs, valid_ms)
                    idx_end = np.searchsorted(pitch_xs, valid_me)
                    sliced_xs = pitch_xs[idx_start:idx_end]
                    sliced_freqs = pitch_freqs[idx_start:idx_end]

                    tasks.append({
                        'word': word, 'group': grp_name, 'ms': ms, 'me': me,
                        'snd_values': snd_values, 'snd_sf': snd_sf,
                        'sliced_xs': sliced_xs, 'sliced_freqs': sliced_freqs,
                        'ref_splits': ref_splits,
                        'ref_chars_bounds': ref_chars_bounds,
                        'missing': False
                    })
                else:
                    tasks.append({'word': word, 'group': grp_name, 'missing': True})

            from modules.audio_core import process_single_long_word

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        continue

                    future = executor.submit(
                        process_single_long_word,
                        t['snd_values'], t['snd_sf'], t['word'], t['ms'], t['me'],
                        self.params, self.params['trim_silence'], t['sliced_xs'], t['sliced_freqs'], t['ref_splits'], t['ref_chars_bounds']
                    )
                    futures[future] = i

                results = [None] * len(tasks)
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        results[i] = {'label': t['word'], 'group': t['group'], 'missing': True}

                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        res['group'] = tasks[orig_idx]['group']
                        res['missing'] = False
                        results[orig_idx] = res
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'error': str(e)}

            matched_count = 0
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])
            for idx, res in enumerate(results):
                iid = f"item_{idx}"
                if res and res.get('success'):
                    res['snd'] = self.long_snd
                    res['pitch_data'] = pitch_data
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                    preview_times = np.linspace(res['start'], res['end'], 11)
                    res['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                    res['has_empty_data'] = any(f == 0.0 for f in res['preview_f0'])

                    self.items[iid] = res
                    matched_count += 1
                else:
                    self.items[iid] = {
                        'id': iid,
                        'label': res['label'],
                        'group': res['group'],
                        'missing': True
                    }

            self._after_state_change("apply_textgrid")
            self._emit(True, f"TextGrid 已应用：完成 {matched_count}/{len(results)} 项。", processed=matched_count, total=len(results))
        except Exception as e:
            self._emit(False, error=str(e))


    def do_textgrid_inspect(self, arg):
        """
        查看 TextGrid 层摘要并给出转换器推荐层。
        用法：textgrid_inspect <TextGrid路径>
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"参数解析失败：{e}")
            return
        if len(args) != 1:
            self._emit(False, error="用法：textgrid_inspect <TextGrid路径>")
            return
        path = args[0]
        if not os.path.isfile(path) or not path.lower().endswith(".textgrid"):
            self._emit(False, error=f"不是有效的 TextGrid 文件：{path}")
            return
        try:
            summary = inspect_textgrid(path)
            self._emit(
                True,
                "TextGrid 层摘要读取完成。",
                path=os.path.abspath(path),
                summary=summary,
                recommendations=recommend_tier_names(summary),
            )
        except Exception as e:
            self._emit(False, error=f"读取 TextGrid 失败：{e}")


    def do_textgrid_preview(self, arg):
        """
        预览任意 TextGrid 转换为标准 groups / words / chars 的结果。
        用法：textgrid_preview <TextGrid路径> [item=<条目层>] [core=<核心层>] [group=<组层|无>] [pair=none|adjacent] [wordlist=<字表路径>] [override=条目ID或标签:组名] [limit=50|all]
        """
        try:
            options = self._parse_textgrid_converter_args(arg)
            if len(options["sources"]) != 1:
                self._emit(False, error="用法：textgrid_preview <TextGrid路径> [item=<条目层>] [core=<核心层>] ...")
                return
            sources = self._collect_textgrid_sources(options["sources"], recursive=False, allow_directories=False)
            source = sources[0]
            records, wordlist_groups = self._load_textgrid_converter_wordlist(options)
            summary, mapping, recommendations = self._build_textgrid_converter_mapping(source, options)
            preview = preview_textgrid_conversion(
                source,
                mapping,
                group_overrides=options["group_overrides"],
                wordlist_records=records,
            )
            rows, truncated = self._limited_textgrid_preview_rows(preview, options["limit"])
            self._emit(
                True,
                "TextGrid 转换预览完成。",
                source=os.path.abspath(source),
                mapping=self._textgrid_mapping_payload(mapping),
                recommendations=recommendations,
                total_items=len(preview.items),
                shown_items=len(rows),
                truncated=truncated,
                items=rows,
                warnings=preview.warnings,
                tone_pair_report=preview.tone_pair_report,
                wordlist={"path": options["wordlist"], "groups": wordlist_groups, "records": len(records)} if options["wordlist"] else None,
            )
        except Exception as e:
            self._emit(False, error=str(e))


    def do_textgrid_convert(self, arg):
        """
        将一个或多个任意 TextGrid 转换为 PhonTracer 标准 groups / words / chars TextGrid。
        用法：textgrid_convert <TextGrid文件或目录...> out=<输出文件或目录> [item=<条目层>] [core=<核心层>] [group=<组层|无>] [pair=none|adjacent] [wordlist=<字表路径>] [override=条目ID或标签:组名] [suffix=_converted] [overwrite=true] [recursive=true]
        """
        try:
            options = self._parse_textgrid_converter_args(arg, require_output=True)
            sources = self._collect_textgrid_sources(options["sources"], recursive=options["recursive"], allow_directories=True)
            records, wordlist_groups = self._load_textgrid_converter_wordlist(options)
            outputs = []
            failures = []
            for source in sources:
                try:
                    summary, mapping, recommendations = self._build_textgrid_converter_mapping(source, options)
                    out_path = self._resolve_textgrid_converter_output(
                        source=source,
                        output=options["output"],
                        source_count=len(sources),
                        suffix=options["suffix"],
                        overwrite=options["overwrite"],
                    )
                    preview = convert_textgrid(
                        source,
                        out_path,
                        mapping,
                        group_overrides=options["group_overrides"],
                        wordlist_records=records,
                    )
                    outputs.append({
                        "source": os.path.abspath(source),
                        "output": os.path.abspath(out_path),
                        "mapping": self._textgrid_mapping_payload(mapping),
                        "recommendations": recommendations,
                        "items": len(preview.items),
                        "warnings": preview.warnings,
                        "tone_pair_report": preview.tone_pair_report,
                    })
                except Exception as e:
                    failures.append({"source": os.path.abspath(source), "error": str(e)})

            success = len(failures) == 0
            self._emit(
                success,
                "TextGrid 批量转换完成。" if len(sources) > 1 else ("TextGrid 转换完成。" if success else "TextGrid 转换失败。"),
                converted_count=len(outputs),
                failed_count=len(failures),
                total=len(sources),
                outputs=outputs,
                failures=failures,
                wordlist={"path": options["wordlist"], "groups": wordlist_groups, "records": len(records)} if options["wordlist"] else None,
            )
        except Exception as e:
            self._emit(False, error=str(e))


    def do_textgrid_converter(self, arg):
        """
        textgrid_convert 的别名。
        """
        self.do_textgrid_convert(arg)


    def _parse_textgrid_converter_args(self, arg, require_output=False):
        try:
            tokens = shlex.split(arg)
        except ValueError as e:
            raise ValueError(f"参数解析失败：{e}") from e

        aliases = {
            "out": "output",
            "output": "output",
            "输出": "output",
            "item": "item_tier",
            "item_tier": "item_tier",
            "item-tier": "item_tier",
            "word": "item_tier",
            "words": "item_tier",
            "条目": "item_tier",
            "条目层": "item_tier",
            "core": "core_tier",
            "core_tier": "core_tier",
            "core-tier": "core_tier",
            "核心": "core_tier",
            "核心层": "core_tier",
            "group": "group_tier",
            "group_tier": "group_tier",
            "group-tier": "group_tier",
            "组": "group_tier",
            "组层": "group_tier",
            "pair": "pair_mode",
            "pair_mode": "pair_mode",
            "pair-mode": "pair_mode",
            "二字组": "pair_mode",
            "wordlist": "wordlist",
            "字表": "wordlist",
            "aux": "wordlist",
            "override": "override",
            "group_override": "override",
            "group-override": "override",
            "临时分组": "override",
            "suffix": "suffix",
            "后缀": "suffix",
            "overwrite": "overwrite",
            "覆盖": "overwrite",
            "recursive": "recursive",
            "递归": "recursive",
            "limit": "limit",
            "数量": "limit",
        }
        bool_keys = {"overwrite", "recursive"}
        options = {
            "sources": [],
            "output": None,
            "item_tier": None,
            "core_tier": None,
            "group_tier": None,
            "group_tier_provided": False,
            "pair_mode": PAIR_MODE_NONE,
            "wordlist": None,
            "group_overrides": {},
            "suffix": "_converted",
            "overwrite": False,
            "recursive": False,
            "limit": 50,
        }

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("--pair-adjacent", "--adjacent-pair", "--二字组合并"):
                options["pair_mode"] = PAIR_MODE_ADJACENT
                index += 1
                continue
            if token.startswith("--"):
                raw = token[2:]
                if "=" in raw:
                    key, value = raw.split("=", 1)
                else:
                    key = raw
                    normalized = aliases.get(key.strip().lower())
                    if not normalized:
                        raise ValueError(f"未知参数：--{key}")
                    if normalized in bool_keys:
                        value = "true"
                    else:
                        if index + 1 >= len(tokens):
                            raise ValueError(f"参数 --{key} 缺少取值。")
                        index += 1
                        value = tokens[index]
                self._set_textgrid_converter_option(options, aliases, key, value)
            elif "=" in token:
                key, value = token.split("=", 1)
                if aliases.get(key.strip().lower()):
                    self._set_textgrid_converter_option(options, aliases, key, value)
                else:
                    options["sources"].append(token)
            else:
                options["sources"].append(token)
            index += 1

        if require_output and not options["output"]:
            raise ValueError("缺少输出路径，请使用 out=<输出文件或目录>。")
        if not options["sources"]:
            raise ValueError("缺少 TextGrid 输入路径。")
        return options


    def _set_textgrid_converter_option(self, options, aliases, key, value):
        normalized = aliases.get(str(key).strip().lower())
        if not normalized:
            raise ValueError(f"未知参数：{key}")
        text = str(value or "").strip()
        if normalized == "output":
            options["output"] = text
        elif normalized == "item_tier":
            options["item_tier"] = text
        elif normalized == "core_tier":
            options["core_tier"] = text
        elif normalized == "group_tier":
            options["group_tier_provided"] = True
            options["group_tier"] = None if self._is_none_tier_value(text) else text
        elif normalized == "pair_mode":
            lowered = text.lower()
            if lowered in ("adjacent", "pair", "paired", "true", "1", "yes", "on", "二字", "二字组", "相邻", "两两合并"):
                options["pair_mode"] = PAIR_MODE_ADJACENT
            elif lowered in ("none", "false", "0", "no", "off", "无", "关闭"):
                options["pair_mode"] = PAIR_MODE_NONE
            else:
                raise ValueError("pair 只能是 none 或 adjacent。")
        elif normalized == "wordlist":
            options["wordlist"] = text
        elif normalized == "override":
            item_key, group_name = self._split_textgrid_group_override(text)
            options["group_overrides"][item_key] = group_name
        elif normalized == "suffix":
            options["suffix"] = text or "_converted"
        elif normalized == "overwrite":
            options["overwrite"] = self._parse_textgrid_bool(text)
        elif normalized == "recursive":
            options["recursive"] = self._parse_textgrid_bool(text)
        elif normalized == "limit":
            lowered = text.lower()
            if lowered in ("all", "全部", "none", "无"):
                options["limit"] = None
            else:
                options["limit"] = max(0, int(text))


    def _is_none_tier_value(self, value):
        return str(value or "").strip().lower() in ("", "none", "null", "no", "false", "0", NONE_TIER_LABEL.lower(), "无", "不使用", "跳过")


    def _parse_textgrid_bool(self, value):
        return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on", "是", "真", "开启", "覆盖", "递归")


    def _split_textgrid_group_override(self, value):
        text = str(value or "").strip()
        for sep in ("=>", "->", "=", ":", "："):
            if sep in text:
                key, group = text.split(sep, 1)
                key = key.strip()
                group = group.strip()
                if key and group:
                    return key, group
        raise ValueError("临时分组格式应为 override=条目ID或标签:组名，例如 override=0:实验组。")


    def _collect_textgrid_sources(self, source_args, recursive=False, allow_directories=True):
        paths = []
        seen = set()
        for source in source_args:
            path = os.path.normpath(source)
            if os.path.isdir(path):
                if not allow_directories:
                    raise ValueError(f"此命令只接受单个 TextGrid 文件：{source}")
                iterator = os.walk(path) if recursive else [(path, [], os.listdir(path))]
                for root, _dirs, files in iterator:
                    for filename in files:
                        if filename.lower().endswith(".textgrid"):
                            full_path = os.path.join(root, filename)
                            key = os.path.normcase(os.path.abspath(full_path))
                            if key not in seen:
                                paths.append(full_path)
                                seen.add(key)
            elif os.path.isfile(path) and path.lower().endswith(".textgrid"):
                key = os.path.normcase(os.path.abspath(path))
                if key not in seen:
                    paths.append(path)
                    seen.add(key)
            else:
                raise ValueError(f"不是有效的 TextGrid 文件或目录：{source}")

        paths.sort(key=self._natural_textgrid_path_key)
        if not paths:
            raise ValueError("没有找到可转换的 TextGrid 文件。")
        return paths


    def _natural_textgrid_path_key(self, path):
        import re
        text = os.path.basename(str(path))
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"([0-9]+)", text)]


    def _load_textgrid_converter_wordlist(self, options):
        path = options.get("wordlist")
        if not path:
            return [], 0
        if not os.path.isfile(path):
            raise ValueError(f"辅助字表不存在：{path}")
        records, groups_count = load_auxiliary_wordlist_records(path)
        if not records:
            raise ValueError("辅助字表中没有可用词项。")
        return records, groups_count


    def _build_textgrid_converter_mapping(self, source, options):
        summary = inspect_textgrid(source)
        recommendations = recommend_tier_names(summary)
        item_tier = options.get("item_tier") or recommendations.get("item_tier")
        core_tier = options.get("core_tier") or recommendations.get("core_tier")
        if options.get("group_tier_provided"):
            group_tier = options.get("group_tier")
        else:
            group_tier = recommendations.get("group_tier")
        if self._is_none_tier_value(item_tier):
            raise ValueError("无法确定条目层，请使用 item=<层名> 指定。")
        if self._is_none_tier_value(core_tier):
            raise ValueError("无法确定核心层，请使用 core=<层名> 指定。")
        mapping = TextGridMapping(
            item_tier=item_tier,
            core_tier=core_tier,
            group_tier=None if self._is_none_tier_value(group_tier) else group_tier,
            pair_mode=options.get("pair_mode") or PAIR_MODE_NONE,
        )
        return summary, mapping, recommendations


    def _resolve_textgrid_converter_output(self, source, output, source_count, suffix, overwrite=False):
        output = os.path.normpath(output)
        explicit_file = source_count == 1 and output.lower().endswith(".textgrid")
        if explicit_file:
            out_path = output
            parent = os.path.dirname(os.path.abspath(out_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            if os.path.exists(out_path) and not overwrite:
                raise ValueError(f"输出文件已存在，若要覆盖请加 overwrite=true：{out_path}")
            return out_path

        if os.path.exists(output) and not os.path.isdir(output):
            raise ValueError(f"批量输出路径必须是目录：{output}")
        os.makedirs(output, exist_ok=True)
        stem = os.path.splitext(os.path.basename(source))[0]
        suffix = suffix or "_converted"
        filename = f"{stem}{suffix}.TextGrid"
        candidate = os.path.join(output, filename)
        if overwrite:
            return candidate
        base, ext = os.path.splitext(filename)
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(output, f"{base}_{counter}{ext or '.TextGrid'}")
            counter += 1
        return candidate


    def _textgrid_mapping_payload(self, mapping):
        return {
            "item_tier": mapping.item_tier,
            "core_tier": mapping.core_tier,
            "group_tier": mapping.group_tier,
            "pair_mode": mapping.pair_mode,
        }


    def _limited_textgrid_preview_rows(self, preview, limit):
        rows = preview.rows()
        if limit is None:
            return rows, False
        return rows[:limit], len(rows) > limit


    def _process_long_wordlist(self, groups, flat_words, metadata_records=None, wordlist_doc=None, emit_result=True):
        metadata_records = metadata_records or []
        try:
            snd = self.long_snd
            if snd is None and self.long_snd_path and os.path.exists(self.long_snd_path):
                try:
                    snd = parselmouth.Sound(self.long_snd_path)
                    self.long_snd = snd
                except Exception:
                    snd = None

            if snd is not None:
                pitch_data = extract_f0(snd, self.params)
                macro_segments = macroscopic_vad(snd, expected_count=len(flat_words))
                self.current_speaker.current_macro_segments = list(macro_segments)
                pitch_xs = pitch_data['xs']
                pitch_freqs = pitch_data['freqs']
                long_valid_indices = np.where(pitch_freqs > 0)[0]
                long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])
            else:
                pitch_data = None
                macro_segments = []
                pitch_xs = np.array([])
                pitch_freqs = np.array([])
                long_valid_xs = np.array([])

            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    iid = f"item_{word_idx}"
                    record = metadata_records[word_idx] if word_idx < len(metadata_records) else None
                    if snd is not None and word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]

                        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                            snd, pitch_data, ms, me,
                            self.params['db'], self.params['skip_front'], self.params['trim_silence']
                        )

                        inner_splits = []
                        chars_bounds = []
                        split_warnings = []
                        split_confidence = 1.0
                        syls = split_into_syllables(word)
                        if len(syls) > 1:
                            meta = {}
                            inner_splits = auto_split_inner_word(snd, mic_s, mic_e, len(syls), pitch_data=pitch_data, output_meta=meta)
                            split_warnings = meta.get('split_warnings', [])
                            split_confidence = meta.get('split_confidence', 1.0)
                            chars_bounds = auto_split_to_chars_bounds(snd, mic_s, mic_e, inner_splits, len(syls), self.params)
                        else:
                            chars_bounds = [[mic_s, mic_e]]

                        preview_times = np.linspace(mic_s, mic_e, 11)
                        preview_f0 = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                        has_empty = any(f == 0.0 for f in preview_f0)

                        item_dict = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'snd': snd, 'pitch_data': pitch_data,
                            'macro_start': ms, 'macro_end': me,
                            'start': mic_s, 'end': mic_e,
                            'raw_start': raw_s, 'raw_end': raw_e,
                            'inner_splits': inner_splits, 'chars_bounds': chars_bounds,
                            'split_warnings': split_warnings, 'split_confidence': split_confidence,
                            'preview_f0': preview_f0, 'has_empty_data': has_empty, 'missing': False,
                            'pitch_floor': self.params['pitch_floor'],
                            'pitch_ceiling': self.params['pitch_ceiling'],
                            'voicing_threshold': self.params.get('voicing_threshold', 0.25),
                        }
                    else:
                        item_dict = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'missing': True, 'start': None, 'end': None
                        }
                    self._attach_wordlist_record_to_item(item_dict, record, speaker=self.current_speaker)
                    self.items[iid] = item_dict
                    word_idx += 1

            self._after_state_change("apply_wordlist")
            if emit_result:
                doc_info = wordlist_doc or self.wordlist_doc or {}
                self._emit(
                    True,
                    f"长音频已按字表处理，共 {len(flat_words)} 个词/字。",
                    total=len(flat_words),
                    group_count=len(groups),
                    wordlist_title=doc_info.get("title", "") if isinstance(doc_info, dict) else "",
                    schema=doc_info.get("schema", WORDLIST_SCHEMA) if isinstance(doc_info, dict) else WORDLIST_SCHEMA,
                )
        except Exception as e:
            if emit_result:
                self._emit(False, error=str(e))
            else:
                raise


    def _process_batch_wordlist(self, groups, flat_words, match_mode, metadata_records=None, wordlist_doc=None, emit_result=True):
        metadata_records = metadata_records or []
        tasks = []
        record_idx = 0
        if match_mode == 'fuzzy':
            sorted_paths = sorted(self.batch_paths, key=self._natural_sort_key)
            used_indices = []
            for grp in groups:
                for word in grp['items']:
                    record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    idx = fuzzy_match_word_to_path(word, sorted_paths, used_indices)
                    if idx is None and record:
                        for alias in (record.get("item_aliases") or record.get("aliases") or []):
                            idx = fuzzy_match_word_to_path(alias, sorted_paths, used_indices)
                            if idx is not None:
                                break
                    if idx is not None:
                        path = sorted_paths[idx]
                        used_indices.append(idx)
                        tasks.append({'word': word, 'group': grp['group'], 'path': path, 'missing': False, 'record': record})
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'record': record})
                    record_idx += 1
        else:
            path_idx = 0
            for grp in groups:
                for word in grp['items']:
                    record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    if path_idx < len(self.batch_paths):
                        tasks.append({'word': word, 'group': grp['group'], 'path': self.batch_paths[path_idx], 'missing': False, 'record': record})
                        path_idx += 1
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'record': record})
                    record_idx += 1

        futures = {}
        for i, t in enumerate(tasks):
            if not t['missing']:
                path = t['path']
                f = self.executor.submit(batch_process_worker, path, self.params, self.params['trim_silence'], t['word'])
                futures[f] = i

        results = [None] * len(tasks)
        for i, t in enumerate(tasks):
            if t['missing']:
                results[i] = {'label': t['word'], 'group': t['group'], 'missing': True, 'start': None, 'end': None}

        for future in concurrent.futures.as_completed(futures):
            orig_idx = futures[future]
            try:
                res = future.result()
                res['missing'] = False
                res['group'] = tasks[orig_idx]['group']
                res['label'] = tasks[orig_idx]['word']

                word = res['label']
                if res.get('success'):
                    syls = split_into_syllables(word)
                    if len(syls) > 1:
                        snd = parselmouth.Sound(res['path'])
                        meta = {}
                        p_data = res.get('pitch_data')
                        res['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(syls), pitch_data=p_data, output_meta=meta)
                        res['split_warnings'] = meta.get('split_warnings', [])
                        res['split_confidence'] = meta.get('split_confidence', 1.0)
                        res['chars_bounds'] = auto_split_to_chars_bounds(snd, res['start'], res['end'], res['inner_splits'], len(syls), self.params)
                    else:
                        res['split_warnings'] = []
                        res['split_confidence'] = 1.0
                results[orig_idx] = res
            except Exception as e:
                results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'start': None, 'end': None, 'error': str(e)}

        for i, res in enumerate(results):
            iid = f"item_{i}"
            record = tasks[i].get('record')
            if not res.get('missing') and res.get('success'):
                try:
                    snd = parselmouth.Sound(res['path'])
                    pitch_data = extract_f0(snd, self.params)
                    res['snd'] = snd
                    res['pitch_data'] = pitch_data
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                except Exception:
                    pass
            res['id'] = iid
            self._attach_wordlist_record_to_item(res, record, speaker=self.current_speaker)
            self.items[iid] = res

        self._after_state_change("apply_wordlist")
        if emit_result:
            doc_info = wordlist_doc or self.wordlist_doc or {}
            matched_cnt = sum(1 for r in results if r and not r.get('missing') and r.get('success'))
            self._emit(
                True,
                f"独立音频已按字表匹配处理，共 {len(tasks)} 个词/字（已匹配音频 {matched_cnt} 项）。",
                total=len(tasks),
                matched=matched_cnt,
                group_count=len(groups),
                wordlist_title=doc_info.get("title", "") if isinstance(doc_info, dict) else "",
                schema=doc_info.get("schema", WORDLIST_SCHEMA) if isinstance(doc_info, dict) else WORDLIST_SCHEMA,
            )


__all__ = ["CLISpeakerIngestMixin"]
