# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Toolkit Tools and Custom Scripting Mixin.
Provides standalone audio tools (merge, sort, split), wordlist conversion,
custom Python script execution in sandbox, and AI prompt generation.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from modules.audio_core import load_compatible_sound, macroscopic_vad, trim_bounds_by_amplitude
from modules.cli_app.io_utils import _natural_sort_key, dumps_utf8
from modules.data_utils import fuzzy_match_word_to_path, parse_wordlist
from modules.project_patch import _append_or_update_script_run, apply_project_patch_to_teproj
from modules.script_api import (
    FigureResult,
    ProjectPatchResult,
    TableResult,
    build_dataset_snapshot,
)
from modules.script_manager import (
    delete_script,
    export_script,
    import_script,
    load_all_scripts,
    save_script,
)
from modules.script_prompt import (
    SCRIPT_PROMPT_KEY_ALIASES,
    VALID_SCRIPT_PROMPT_KEYS,
    generate_ai_prompt,
    validate_and_normalize_script_prompt_params,
)
from modules.script_runner import run_custom_script
from modules.version import APP_NAME, __version__
from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    save_wordlist_file,
    summarize_wordlist_document,
)


def _is_float_token(val: str) -> bool:
    try:
        float(str(val).strip())
        return True
    except (TypeError, ValueError):
        return False


class CLIToolkitScriptMixin:
    """
    Toolkit and scripting mixin providing tool commands, user script execution,
    and prompt engineering utilities.
    """
    def do_wordlist_convert(self, arg):
        """Alias for tool_wordlist_convert."""
        self.do_tool_wordlist_convert(arg)


    def do_tool_wordlist_convert(self, arg):
        """
        Convert any messy .txt, structured .txt, .csv, or legacy wordlist into a standard v2 .ptwl document.
        Usage: tool_wordlist_convert <in_path> <out_path.ptwl> [title=...]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("tool_wordlist_convert")
            return

        pos_args = []
        custom_title = None
        in_path = None
        out_path = None
        idx = 0
        while idx < len(args):
            tok = args[idx]
            if tok in ("--title", "-t") and idx + 1 < len(args):
                custom_title = args[idx + 1].strip()
                idx += 2
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lstrip("-").lower()
                v_clean = v.strip().strip('"').strip("'")
                if k_low in ("title", "标题"):
                    custom_title = v_clean
                elif k_low in ("in", "input", "src"):
                    in_path = v_clean
                elif k_low in ("out", "output", "dst"):
                    out_path = v_clean
                else:
                    pos_args.append(tok)
            else:
                pos_args.append(tok)
            idx += 1

        if in_path is None and len(pos_args) >= 1:
            in_path = pos_args[0]
        if out_path is None and len(pos_args) >= 2:
            out_path = pos_args[1]
        if custom_title is None and len(pos_args) >= 3:
            custom_title = pos_args[2]

        if not in_path or not out_path:
            self._emit(
                False,
                error="Usage: tool_wordlist_convert <in_path> <out_path.ptwl> [title=...]",
                hint="示例: tool_wordlist_convert raw_words.txt standard.ptwl",
            )
            return

        if not os.path.exists(in_path):
            self._emit(False, error=f"File not found: {in_path}")
            return

        try:
            doc, groups, flat_words, _records = self._load_wordlist_any(in_path, title=custom_title)
            if custom_title:
                doc["title"] = custom_title
            normalized = save_wordlist_file(doc, out_path)
            summary = summarize_wordlist_document(normalized)
            self._emit(
                True,
                f"已将字表转换并保存为标准 v2 高级字表：{out_path}",
                input_file=in_path,
                output_file=out_path,
                input=in_path,
                output=out_path,
                path=out_path,
                schema=normalized.get("schema", WORDLIST_SCHEMA),
                title=normalized.get("title", ""),
                group_count=summary["groups"],
                item_count=summary["items"],
                groups=summary["groups"],
                total_words=summary["items"],
                summary=summary,
            )
        except Exception as e:
            self._emit(False, error=str(e))


    def _safe_audio_label(self, label):
        safe = re.sub(r'[\\/*?:"<>|]', "", str(label)).strip()
        return safe or "segment"


    def do_tool_merge(self, arg):
        """
        Merge short audio files into a single WAV while preserving native sampling rate.
        Usage: tool_merge <output.wav> <gap_sec> <audio1_or_dir_or_glob> [audio2 ...] [sr=<Hz>]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("tool_merge")
            return

        out_path = None
        gap_sec = None
        explicit_sr = None
        raw_inputs = []

        for tok in args:
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lstrip("-").lower()
                v_clean = v.strip().strip('"').strip("'")
                if k_low in ("out", "output", "dst"):
                    out_path = v_clean
                    continue
                elif k_low in ("gap", "gap_sec"):
                    try:
                        gap_sec = float(v_clean)
                    except ValueError:
                        self._emit(False, error="gap_sec must be a number")
                        return
                    continue
                elif k_low in ("sr", "sample_rate", "sampling_frequency"):
                    try:
                        explicit_sr = float(v_clean)
                    except ValueError:
                        self._emit(False, error="sr must be a positive number")
                        return
                    continue
            raw_inputs.append(tok)

        if out_path is None and len(raw_inputs) >= 1:
            out_path = raw_inputs.pop(0)
        if gap_sec is None and len(raw_inputs) >= 1:
            try:
                gap_sec = float(raw_inputs.pop(0))
            except ValueError:
                self._emit(False, error="gap_sec must be a number")
                return
        if gap_sec is None:
            gap_sec = 0.5

        if not out_path or not raw_inputs:
            self._emit(
                False,
                error="Requires output.wav, gap_sec, and audio files (or directory/glob containing at least two audio files)",
                hint="用法: tool_merge <output.wav> <gap_sec> <audio1> <audio2> ... [sr=<Hz>]",
            )
            return

        has_dir_or_glob = any(os.path.isdir(p) or any(ch in p for ch in ("*", "?", "[")) for p in raw_inputs)
        if has_dir_or_glob:
            paths, err_msg, hint_msg = self._expand_batch_audio_inputs(raw_inputs)
            if err_msg:
                self._emit(False, error=err_msg, hint=hint_msg)
                return
        else:
            paths = list(raw_inputs)
            missing = [p for p in paths if not os.path.exists(p)]
            if missing:
                self._emit(False, error=f"File not found: {missing[0]}", missing=missing)
                return

        if len(paths) < 2:
            self._emit(False, error="Requires output.wav, gap_sec, and at least two audio files")
            return

        try:
            loaded_sounds = [load_compatible_sound(path) for path in paths]
            source_srs = [float(snd.sampling_frequency) for snd in loaded_sounds]

            if explicit_sr is not None and explicit_sr > 0:
                target_sr = float(explicit_sr)
            else:
                first_sr = source_srs[0]
                if all(abs(sr - first_sr) < 1e-3 for sr in source_srs):
                    target_sr = first_sr
                else:
                    # 输入采样率不一致时采用最高采样率保真合并，避免损失高频声学分量
                    target_sr = max(source_srs)

            resampled_any = False
            all_vals = []
            gap_samples = max(0, int(round(target_sr * gap_sec)))
            gap_array = np.zeros(gap_samples, dtype=np.float64)

            for snd in loaded_sounds:
                if abs(float(snd.sampling_frequency) - target_sr) >= 1e-3:
                    snd = snd.resample(target_sr)
                    resampled_any = True
                mono = np.mean(snd.values, axis=0) if snd.values.shape[0] > 1 else snd.values[0]
                all_vals.append(np.asarray(mono, dtype=np.float64))
                all_vals.append(gap_array)

            merged_vals = np.concatenate(all_vals[:-1])
            merged_snd = parselmouth.Sound(np.array([merged_vals], dtype=np.float64), sampling_frequency=target_sr)
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            merged_snd.save(out_path, "WAV")

            warnings_list = []
            if resampled_any and explicit_sr is None:
                warnings_list.append(
                    f"输入音频存在不同采样率 ({sorted({int(round(s)) for s in source_srs})} Hz)，已统一对齐至 {int(round(target_sr))} Hz。"
                )

            sr_out = int(round(target_sr)) if abs(target_sr - round(target_sr)) < 1e-3 else target_sr
            self._emit(
                True,
                f"已合并 {len(paths)} 个音频（采样率 {sr_out} Hz）。",
                path=out_path,
                count=len(paths),
                gap_sec=gap_sec,
                sampling_frequency=sr_out,
                resampled=resampled_any,
                warnings=warnings_list,
            )
        except Exception as e:
            self._emit(False, error=str(e))


    def do_tool_sort_batch(self, arg):
        """
        Fuzzy-sort batch audio paths according to a wordlist, like toolkit's auto-sort.
        Usage: tool_sort_batch <wordlist> [audio_or_dir_or_glob ...] [--apply]
        Supports .ptwl, .csv, structured .txt, and messy .txt wordlists.
        If --apply (or apply=true) is specified, directly updates the session's batch_paths.
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("tool_sort_batch")
            return

        if not args:
            self._emit(False, error="Requires wordlist path")
            return

        apply_to_session = False
        pos_args = []
        for tok in args:
            low = tok.lower().strip()
            if low in ("--apply", "-a"):
                apply_to_session = True
            elif low == "--no-apply":
                apply_to_session = False
            elif low.startswith("apply="):
                val = low.split("=", 1)[1].strip()
                apply_to_session = val in ("true", "1", "yes", "on")
            else:
                pos_args.append(tok)

        if not pos_args:
            self._emit(False, error="Requires wordlist path")
            return

        wordlist_path = pos_args[0]
        raw_audio_args = pos_args[1:]
        if not os.path.exists(wordlist_path):
            self._emit(False, error=f"File not found: {wordlist_path}")
            return

        if raw_audio_args:
            paths, err_msg, hint_msg = self._expand_batch_audio_inputs(raw_audio_args)
            if err_msg:
                self._emit(False, error=err_msg, hint=hint_msg)
                return
        else:
            paths = list(self.batch_paths)

        if not paths:
            self._emit(
                False,
                error="No audio paths provided or loaded",
                hint="请传入音频文件路径、目录路径或 *.wav 通配符，或先运行 load_batch。",
            )
            return

        try:
            _doc, _groups, flat_words, records = self._load_wordlist_any(wordlist_path)
        except Exception as e:
            self._emit(False, error=f"Failed to load wordlist: {e}")
            return

        sorted_paths = []
        used_indices = []
        for idx_w, word in enumerate(flat_words):
            idx = fuzzy_match_word_to_path(word, paths, used_indices=list(used_indices))
            if idx is None and idx_w < len(records) and records[idx_w]:
                for alias in (records[idx_w].get("item_aliases") or records[idx_w].get("aliases") or []):
                    idx = fuzzy_match_word_to_path(alias, paths, used_indices=list(used_indices))
                    if idx is not None:
                        break
            if idx is not None:
                sorted_paths.append(paths[idx])
                used_indices.append(idx)

        leftovers = [p for i, p in enumerate(paths) if i not in used_indices]
        result = sorted_paths + leftovers

        if apply_to_session:
            self.batch_paths = list(result)
            self.mode = 'batch'
            self.long_snd = None
            self.long_snd_path = None
            self.items.clear()
            self.groups.clear()
            self._after_state_change("load_batch")
            self._emit(
                True,
                f"已按字表给音频排序并直接载入当前会话（共 {len(result)} 个音频，匹配 {len(sorted_paths)}/{len(flat_words)} 词）。",
                applied=True,
                mode=self.mode,
                count=len(result),
                matched=len(sorted_paths),
                matched_count=len(sorted_paths),
                total_words=len(flat_words),
                sorted_paths=result,
                batch_paths=result,
            )
        else:
            self._emit(
                True,
                "已按字表给音频排序。需要正式载入时可把 sorted_paths 交给 load_batch，或直接加 --apply 参数。",
                applied=False,
                matched=len(sorted_paths),
                matched_count=len(sorted_paths),
                total_words=len(flat_words),
                sorted_paths=result,
            )


    def do_tool_split(self, arg):
        """
        Split a long audio into short WAV files (with .ptwl/.csv/.txt wordlist or pure unlabeled VAD split).
        Usage:
          tool_split <long_audio> <wordlist.ptwl|.txt|.csv> <output_dir> [buffer_sec] [trim]
          tool_split <long_audio> <output_dir> [buffer_sec] [trim]              # 无字表纯拆音
          tool_split <long_audio> none <output_dir> [buffer_sec] [trim]         # 无字表纯拆音
        Options: wordlist=<path|none>, out=<dir>, buffer=<sec>, trim=true|false, merge_thresh=<sec>
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("tool_split")
            return

        audio_path = None
        wordlist_path = None
        no_wordlist_explicit = False
        out_dir = None
        buffer_sec = None
        do_trim = None
        user_merge_thresh = None
        user_min_dur = None
        user_drop_db = None
        pos_args = []

        for tok in args:
            low = tok.strip().lower()
            if low in ("--no-wordlist", "--pure", "--unlabeled"):
                no_wordlist_explicit = True
                wordlist_path = None
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lstrip("-").lower()
                v_clean = v.strip().strip('"').strip("'")
                if k_low in ("audio", "in", "input", "src", "long_audio"):
                    audio_path = v_clean
                    continue
                elif k_low in ("wordlist", "wl", "字表"):
                    if v_clean.lower() in ("", "none", "null", "-", "false", "0", "无", "不带字表"):
                        no_wordlist_explicit = True
                        wordlist_path = None
                    else:
                        wordlist_path = v_clean
                    continue
                elif k_low in ("out", "out_dir", "output", "output_dir", "dst"):
                    out_dir = v_clean
                    continue
                elif k_low in ("buffer", "buffer_sec", "pad"):
                    try:
                        buffer_sec = float(v_clean)
                    except ValueError:
                        self._emit(False, error="buffer_sec must be a number")
                        return
                    continue
                elif k_low in ("trim", "trim_silence"):
                    do_trim = v_clean.lower() in ("1", "true", "yes", "y", "on", "trim")
                    continue
                elif k_low in ("merge_thresh", "gap_thresh", "min_gap"):
                    try:
                        user_merge_thresh = float(v_clean)
                    except ValueError:
                        self._emit(False, error="merge_thresh must be a number")
                        return
                    continue
                elif k_low in ("min_dur", "min_duration"):
                    try:
                        user_min_dur = float(v_clean)
                    except ValueError:
                        self._emit(False, error="min_dur must be a number")
                        return
                    if user_min_dur <= 0:
                        self._emit(False, error="min_dur must be positive")
                        return
                    continue
                elif k_low in ("db", "drop_db", "energy_drop_db"):
                    try:
                        user_drop_db = float(v_clean)
                    except ValueError:
                        self._emit(False, error="db must be a number")
                        return
                    if user_drop_db <= 0:
                        self._emit(False, error="db must be positive")
                        return
                    continue
                elif not os.path.exists(tok) and re.match(r"^[A-Za-z_\-][A-Za-z0-9_\-]*$", k.strip()):
                    self._emit(
                        False,
                        error=f"Unknown option for tool_split: {k.strip()}",
                        hint="支持参数: wordlist=, out=, buffer=, trim=, merge_thresh=, min_dur=, db=",
                    )
                    return
            pos_args.append(tok)

        rest_pos = list(pos_args)
        if audio_path is None and rest_pos:
            audio_path = rest_pos.pop(0)

        none_tokens = {"", "none", "null", "-", "无", "不带字表"}
        wl_exts = (".txt", ".ptwl", ".csv", ".json")

        if no_wordlist_explicit or wordlist_path is not None:
            if out_dir is None and rest_pos:
                out_dir = rest_pos.pop(0)
        else:
            if len(rest_pos) == 1:
                cand = rest_pos[0]
                if cand.lower().endswith(wl_exts):
                    self._emit(
                        False,
                        error="Requires output_dir when wordlist is provided",
                        hint="带字表拆音: tool_split <long_audio> <wordlist> <output_dir>；无字表纯拆音: tool_split <long_audio> <output_dir>",
                    )
                    return
                out_dir = rest_pos.pop(0)
                no_wordlist_explicit = True
            elif len(rest_pos) >= 2:
                cand1, cand2 = rest_pos[0], rest_pos[1]
                if cand1.strip().lower() in none_tokens:
                    no_wordlist_explicit = True
                    wordlist_path = None
                    out_dir = cand2
                    rest_pos = rest_pos[2:]
                elif _is_float_token(cand2) and not cand1.lower().endswith(wl_exts) and not os.path.isfile(cand1):
                    # 形如: tool_split male.wav ./out_clips 0.12 true
                    no_wordlist_explicit = True
                    wordlist_path = None
                    out_dir = cand1
                    rest_pos = rest_pos[1:]
                elif (
                    cand2.lower().endswith(wl_exts)
                    and not cand1.lower().endswith(wl_exts)
                    and (os.path.isfile(cand2) or os.path.isdir(cand1))
                ):
                    # 容错：用户把 <output_dir> 与 <wordlist> 顺序写反时自动识别
                    wordlist_path = cand2
                    out_dir = cand1
                    rest_pos = rest_pos[2:]
                else:
                    wordlist_path = cand1
                    out_dir = cand2
                    rest_pos = rest_pos[2:]

        if buffer_sec is None:
            if rest_pos:
                try:
                    buffer_sec = float(rest_pos.pop(0))
                except ValueError:
                    self._emit(False, error="buffer_sec must be a number")
                    return
            else:
                buffer_sec = 0.1

        if do_trim is None:
            if rest_pos:
                do_trim = rest_pos.pop(0).lower() in ("1", "true", "yes", "y", "on", "trim")
            else:
                do_trim = True

        if not audio_path or not out_dir:
            self._emit(
                False,
                error="Requires long_audio and output_dir (with optional .ptwl/.txt/.csv wordlist)",
                hint="用法: tool_split <long_audio> [wordlist.ptwl|.txt|.csv|none] <output_dir> [buffer_sec] [trim]",
            )
            return

        if not os.path.exists(audio_path):
            self._emit(False, error=f"File not found: {audio_path}")
            return

        flat_words: List[str] = []
        wordlist_groups = []
        wordlist_schema = None
        if wordlist_path is not None and not no_wordlist_explicit:
            if not os.path.exists(wordlist_path):
                self._emit(False, error=f"File not found: {wordlist_path}")
                return
            try:
                wl_doc, wordlist_groups, flat_words, _records = self._load_wordlist_any(wordlist_path)
                wordlist_schema = wl_doc.get("schema", WORDLIST_SCHEMA) if isinstance(wl_doc, dict) else WORDLIST_SCHEMA
            except Exception as e:
                self._emit(False, error=f"Failed to parse wordlist: {e}")
                return
            if not flat_words:
                self._emit(False, error="No words found in wordlist")
                return

        try:
            snd = load_compatible_sound(audio_path)
            base_vad_kwargs: Dict[str, Any] = {}
            if user_min_dur is not None:
                base_vad_kwargs["min_dur"] = float(user_min_dur)
            if user_drop_db is not None:
                base_vad_kwargs["drop_db"] = float(user_drop_db)
            vad_kwargs: Dict[str, Any] = dict(base_vad_kwargs)
            if user_merge_thresh is not None:
                vad_kwargs["merge_thresh"] = float(user_merge_thresh)

            natural_segs = macroscopic_vad(snd, **vad_kwargs)
            if not natural_segs and flat_words:
                natural_segs = macroscopic_vad(snd, expected_count=len(flat_words), **vad_kwargs)
            if not natural_segs:
                self._emit(False, error="No speech segments detected")
                return

            if not flat_words:
                segs = natural_segs
            elif len(natural_segs) == len(flat_words):
                segs = natural_segs
            elif len(natural_segs) > len(flat_words):
                # 仅允许合并短于词间停顿尺度的词内闭塞间隙，严禁把真实独立的词条强行粘连成一段
                gaps = [float(natural_segs[k + 1][0] - natural_segs[k][1]) for k in range(len(natural_segs) - 1)]
                inter_gaps = [g for g in gaps if g >= 0.30]
                med_inter = float(np.median(inter_gaps)) if len(inter_gaps) >= 3 else 0.50
                max_intra_gap = (
                    float(user_merge_thresh)
                    if user_merge_thresh is not None
                    else min(0.28, max(0.20, 0.48 * med_inter))
                )
                segs = macroscopic_vad(
                    snd,
                    expected_count=len(flat_words),
                    max_merge_gap=max_intra_gap,
                    **vad_kwargs,
                )
            else:
                # 检出段数少于词表词数：仅当整体语速极快（词间停顿中位数 < 0.32s）时尝试细粒度重切
                gaps = [float(natural_segs[k + 1][0] - natural_segs[k][1]) for k in range(len(natural_segs) - 1)]
                med_gap = float(np.median(gaps)) if gaps else 0.50
                if user_merge_thresh is None and med_gap < 0.32:
                    segs = macroscopic_vad(
                        snd,
                        expected_count=len(flat_words),
                        merge_thresh=0.08,
                        max_merge_gap=0.18,
                        **base_vad_kwargs,
                    )
                else:
                    segs = natural_segs

            os.makedirs(out_dir, exist_ok=True)
            pad_width = max(3, len(str(max(len(segs), len(flat_words), 1))))
            saved_files: List[str] = []
            matched_files: List[str] = []
            extra_files: List[str] = []
            total_dur = snd.get_total_duration()

            for i, (raw_start, raw_end) in enumerate(segs):
                start, end = float(raw_start), float(raw_end)
                if do_trim:
                    part = snd.extract_part(from_time=start, to_time=end)
                    vals = part.values[0]
                    xs = part.xs()
                    start, end = trim_bounds_by_amplitude(start, end, xs, vals)

                start = max(0.0, start - buffer_sec)
                end = min(total_dur, end + buffer_sec)
                if end <= start:
                    continue

                extract = snd.extract_part(from_time=start, to_time=end)
                idx_prefix = str(i + 1).zfill(pad_width)
                if not flat_words:
                    out_file = os.path.join(out_dir, f"{idx_prefix}_segment.wav")
                    extract.save(out_file, "WAV")
                    saved_files.append(out_file)
                elif i < len(flat_words):
                    word = flat_words[i]
                    out_file = os.path.join(out_dir, f"{idx_prefix}_{self._safe_audio_label(word)}.wav")
                    extract.save(out_file, "WAV")
                    saved_files.append(out_file)
                    matched_files.append(out_file)
                else:
                    extra_idx = i - len(flat_words) + 1
                    out_file = os.path.join(out_dir, f"{idx_prefix}_extra_{str(extra_idx).zfill(2)}.wav")
                    extract.save(out_file, "WAV")
                    saved_files.append(out_file)
                    extra_files.append(out_file)

            warnings_list: List[str] = []
            unmatched_words: List[str] = []
            mismatch = False

            if flat_words and len(segs) != len(flat_words):
                mismatch = True
                if len(segs) > len(flat_words):
                    extra_names = ", ".join(os.path.basename(p) for p in extra_files[:5])
                    if len(extra_files) > 5:
                        extra_names += f" 等共 {len(extra_files)} 个文件"
                    warn_msg = (
                        f"检出音频段数 ({len(segs)}) 多于字表词数 ({len(flat_words)})："
                        f"前 {len(flat_words)} 段已按字表命名，其余 {len(extra_files)} 段多余音频片段未丢弃，"
                        f"已完整保留为 {extra_names}；请人工核对是否存在词内长停顿切裂或重读多录（中间若有错位会影响后续词条对应）。"
                    )
                    warnings_list.append(warn_msg)
                else:
                    unmatched_words = list(flat_words[len(segs):])
                    preview_missing = "、".join(unmatched_words[:6])
                    if len(unmatched_words) > 6:
                        preview_missing += f" 等 {len(unmatched_words)} 词"
                    warn_msg = (
                        f"检出音频段数 ({len(segs)}) 少于字表词数 ({len(flat_words)})："
                        f"末尾 {len(unmatched_words)} 个词条（{preview_missing}）未分配到音频段，请核对是否存在漏读或词间连读粘连。"
                    )
                    warnings_list.append(warn_msg)

            if not flat_words:
                msg = f"无字表纯拆音完成：共检出并保存 {len(saved_files)} 段音频。"
            elif mismatch:
                msg = f"[警告] {warnings_list[0]}"
            else:
                msg = f"已按字表准确对齐并拆分保存 {len(saved_files)} 段音频。"

            sr_val = float(snd.sampling_frequency)
            sr_out = int(round(sr_val)) if abs(sr_val - round(sr_val)) < 1e-3 else sr_val
            payload: Dict[str, Any] = {
                "status": "warning" if mismatch else "ok",
                "mode": "wordlist" if flat_words else "unlabeled",
                "wordlist_mode": "wordlist" if flat_words else "none",
                "output_dir": out_dir,
                "saved_files": saved_files,
                "saved_count": len(saved_files),
                "count": len(saved_files),
                "detected_segments": len(segs),
                "word_count": len(flat_words),
                "expected_words": len(flat_words),
                "matched_count": len(matched_files) if flat_words else 0,
                "mismatch": mismatch,
                "extra_segments": len(extra_files),
                "extra_files": extra_files,
                "missing_words_count": len(unmatched_words),
                "unmatched_words": unmatched_words,
                "sampling_frequency": sr_out,
                "warnings": warnings_list,
            }
            if warnings_list:
                payload["warning"] = warnings_list[0]
            if wordlist_schema:
                payload["wordlist_schema"] = wordlist_schema
                payload["group_count"] = len(wordlist_groups)

            self._emit(True, msg, **payload)
        except Exception as e:
            self._emit(False, error=str(e))


    def do_list_scripts(self, arg):
        """
        List all available custom and builtin scripts.
        Usage: list_scripts
        """
        try:
            from modules.script_manager import load_all_scripts
            scripts = load_all_scripts()
            res_list = []
            for s in scripts:
                res_list.append({
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "type": s.get("type", "chart")
                })
            self._emit(True, "Scripts loaded successfully.", scripts=res_list)
        except Exception as e:
            self._emit(False, error=str(e))


    def do_script_info(self, arg):
        """
        Get details and code of a specific script by ID or Name.
        Usage: script_info <script_id_or_name>
        """
        arg_norm = arg.replace('\\', '/')
        args = shlex.split(arg_norm)
        if not args:
            self._emit(False, error="Requires script ID or name")
            return
            
        target = args[0].strip()
        try:
            from modules.script_manager import load_all_scripts
            scripts = load_all_scripts()
            
            # Find by ID first, then by Name (case-insensitive)
            found = None
            for s in scripts:
                if s.get("id") == target:
                    found = s
                    break
            if not found:
                for s in scripts:
                    if s.get("name", "").lower() == target.lower():
                        found = s
                        break
            
            if found:
                self._emit(True, "Script details retrieved.", script={
                    "id": found.get("id"),
                    "name": found.get("name"),
                    "description": found.get("description"),
                    "type": found.get("type", "chart"),
                    "code": found.get("code")
                })
            else:
                self._emit(False, error=f"Script not found: {target}")
        except Exception as e:
            self._emit(False, error=str(e))


    def do_run_script(self, arg):
        """
        Run a script by ID, Name, or local python file path.
        Usage: run_script <script_id_or_name_or_file_path> [key=value ...]
        Parameters:
          timeout=30       Execution timeout in seconds (default 30)
          archive=true     Archive run history in active project (default true)
          desc="Goal"      User goal description for archiving (default "CLI script execution")
          name="Name"      Custom script name for archiving (default "CLI script")
        """
        arg_norm = arg.replace('\\', '/')
        args = shlex.split(arg_norm)
        if not args:
            self._emit(False, error="Requires script ID, name, or file path")
            return
            
        target = args[0].strip()
        
        # Parse extra key-value parameters
        kwargs = {}
        for item in args[1:]:
            if '=' in item:
                k, v = item.split('=', 1)
                kwargs[k.strip().lower()] = v.strip()
                
        # Parse standard parameter defaults
        try:
            timeout = float(kwargs.get("timeout", 30))
        except ValueError:
            self._emit(False, error="timeout must be a number")
            return
            
        archive = kwargs.get("archive", "true").lower() in ("true", "1", "yes", "y")
        script_desc = kwargs.get("desc", kwargs.get("user_goal", "CLI script execution"))
        script_name = kwargs.get("name", "CLI script")
        script_type = "chart"
        script_id = None
        
        code = None
        # Determine code source: check if target is file
        if target.lower().endswith(".py") or os.path.exists(target):
            if not os.path.exists(target):
                self._emit(False, error=f"File not found: {target}")
                return
            try:
                with open(target, "r", encoding="utf-8") as f:
                    code = f.read()
                script_name = os.path.basename(target)
                script_id = str(hashlib.sha256(code.encode('utf-8')).hexdigest()[:16])
            except Exception as e:
                self._emit(False, error=f"Failed to read script file: {e}")
                return
        else:
            try:
                from modules.script_manager import load_all_scripts
                scripts = load_all_scripts()
                found = None
                for s in scripts:
                    if s.get("id") == target:
                        found = s
                        break
                if not found:
                    for s in scripts:
                        if s.get("name", "").lower() == target.lower():
                            found = s
                            break
                if found:
                    code = found.get("code")
                    script_name = found.get("name")
                    script_id = found.get("id")
                    script_type = found.get("type", "chart")
                    if not script_desc or script_desc == "CLI script execution":
                        script_desc = found.get("description", "CLI script execution")
                else:
                    self._emit(False, error=f"Script target '{target}' not found as a file or registered script.")
                    return
            except Exception as e:
                self._emit(False, error=str(e))
                return
                
        # Build dataset snapshot
        dataset_items = []
        has_active_project = False
        temp_path = None
        try:
            # We can check if any speakers exist to know if there's project data to snapshot
            if self.speaker_manager.get_all_speakers():
                has_active_project = True
                # Export to a temp path
                fd, temp_path = tempfile.mkstemp(suffix=".teproj")
                os.close(fd)
                
                # Make sure all items are loaded in memory so they get exported correctly
                orig_active_id = self.speaker_manager.active_speaker_id
                try:
                    for spk in self.speaker_manager.get_all_speakers():
                        self.speaker_manager.set_active_speaker(spk.id)
                        for item in spk.items.values():
                            self._ensure_item_loaded(item)
                finally:
                    if orig_active_id:
                        self.speaker_manager.set_active_speaker(orig_active_id)
                        
                ok = self.project_manager.export_project(temp_path)
                if ok:
                    from modules.script_api import build_dataset_snapshot
                    dataset_items = build_dataset_snapshot(temp_path)
                else:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    self._emit(False, error="Failed to export project state to temporary file for snapshotting.")
                    return
        except Exception as e:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            self._emit(False, error=f"Failed to prepare project snapshot: {e}")
            return
                    
        # Run custom script
        try:
            from modules.script_runner import run_custom_script
            import matplotlib.pyplot as plt
            plt.close('all')
            
            res, logs, err = run_custom_script(code, dataset_items, timeout=timeout, teproj_path=temp_path)
            
            if err:
                self._emit(False, error=err, logs=logs)
                return
                
            # Process success results
            from modules.script_api import FigureResult, TableResult, ProjectPatchResult
            from modules.project_patch import apply_project_patch_to_teproj
            
            results = res if isinstance(res, list) else [res]
            project_patch_results = [r for r in results if isinstance(r, ProjectPatchResult)]
            if project_patch_results:
                if len(project_patch_results) != 1 or len(results) != 1:
                    self._emit(False, error="数据处理脚本只能返回一个 ctx.project_patch(...)，不能同时返回图表、表格或多个工程补丁。", logs=logs)
                    return
                if not has_active_project or not temp_path:
                    self._emit(False, error="当前未加载任何有效工程，无法应用数据处理工程补丁。", logs=logs)
                    return

                patch_res = project_patch_results[0]
                if not patch_res.operations:
                    logs.append("脚本没有返回任何工程修改操作。")
                    self._emit(True, "脚本没有返回任何工程修改操作", logs=logs, outputs=[])
                    return

                out_project = kwargs.get("out_project") or kwargs.get("output_project") or kwargs.get("out_teproj")
                if not out_project:
                    self._emit(False, error="数据处理脚本返回了工程修改，但未指定输出工程文件。请在参数中指定 out_project=<path.teproj>", logs=logs)
                    return

                out_project = os.path.abspath(out_project)
                if not out_project.lower().endswith(".teproj"):
                    out_project += ".teproj"

                out_dir = os.path.dirname(out_project)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)

                code_sha256 = hashlib.sha256(code.encode('utf-8')).hexdigest()
                run_record = {
                    "script_id": script_id or str(uuid.uuid4()),
                    "script_name": script_name,
                    "script_type": "data_process",
                    "api_version": "1",
                    "software_version": __version__,
                    "code_sha256": code_sha256,
                    "code": code,
                    "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user_goal": script_desc,
                    "status": "成功",
                    "outputs": [{
                        "type": "project",
                        "title": patch_res.title or "数据处理后的工程",
                        "filename": os.path.basename(out_project),
                        "saved_path": out_project
                    }]
                }

                try:
                    summary = apply_project_patch_to_teproj(
                        temp_path,
                        patch_res,
                        out_project,
                        run_record=run_record
                    )
                    output_records = [{
                        "type": "project",
                        "title": patch_res.title or "数据处理后的工程",
                        "filename": os.path.basename(out_project),
                        "saved_path": out_project,
                        "operation_count": summary.get("operation_count", 0),
                        "applied_operations": summary.get("applied_operations", [])
                    }]
                    logs.append(f"数据处理工程补丁已成功应用并保存至：{out_project}")
                    logs.append(f"包含操作总数: {summary.get('operation_count', 0)}，实际执行: {len(summary.get('applied_operations', []))} 项")

                    should_reload = (
                        kwargs.get("reload", "false").lower() in ("true", "1", "yes")
                        or kwargs.get("apply_to_current", "false").lower() in ("true", "1", "yes")
                    )
                    if should_reload:
                        if self.project_manager.load_project(out_project):
                            self._sync_cli_state_after_project_load()
                            logs.append("已将修改后的工程重新载入当前 CLI 会话。")
                        else:
                            logs.append("警告: 重新载入修改后的工程失败。")

                    self._emit(
                        True,
                        "脚本执行完成",
                        logs=logs,
                        output_dir=out_dir,
                        outputs=output_records,
                        patch_summary=summary
                    )
                    return
                except Exception as e:
                    logs.append(f"应用工程补丁失败: {e}")
                    self._emit(False, error=f"应用工程补丁失败: {e}", logs=logs)
                    return

            figure_results = [r for r in results if isinstance(r, FigureResult)]
            table_results = [r for r in results if isinstance(r, TableResult)]
            
            # Inline _safe_script_output_name
            def safe_name(name, fallback="output"):
                raw = str(name or fallback).strip()
                raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
                raw = re.sub(r"\s+", "_", raw)
                raw = raw.strip("._ ")
                return raw[:80] or fallback
                
            # Setup output folder
            custom_out_dir = kwargs.get("output_dir", kwargs.get("out_dir"))
            if custom_out_dir:
                unique_folder = os.path.abspath(custom_out_dir)
                os.makedirs(unique_folder, exist_ok=True)
            else:
                base_out_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "script_outputs")
                safe_script_name = safe_name(script_name, "script")
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                folder = os.path.join(base_out_dir, f"{stamp}_{safe_script_name}")
                suffix = 2
                unique_folder = folder
                while os.path.exists(unique_folder):
                    unique_folder = f"{folder}_{suffix}"
                    suffix += 1
                os.makedirs(unique_folder, exist_ok=True)
            
            output_records = []
            
            # Helper to get unique filename in folder
            def unique_path(folder, filename):
                base, ext_ = os.path.splitext(filename)
                p = os.path.join(folder, filename)
                idx = 2
                while os.path.exists(p):
                    p = os.path.join(folder, f"{base}_{idx}{ext_}")
                    idx += 1
                return p
                
            # Save figures
            for idx, fig_res in enumerate(figure_results, start=1):
                fn = safe_name(fig_res.filename or f"custom_chart_{idx}.png", f"custom_chart_{idx}.png")
                r, ext = os.path.splitext(fn)
                if not ext:
                    fn = f"{fn}.png"
                elif ext.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                    fn = f"{r}.png"
                out_path = unique_path(unique_folder, fn)
                try:
                    fig_res.fig.savefig(out_path, dpi=300, bbox_inches="tight")
                    output_records.append({
                        "type": "figure",
                        "title": fig_res.title,
                        "filename": os.path.basename(out_path),
                        "saved_path": out_path,
                    })
                    logs.append(f"图表 {idx} 已保存：{out_path}")
                except Exception as e:
                    logs.append(f"图表 {idx} 保存失败：{e}")

            try:
                import matplotlib.pyplot as plt
                for fig_res in figure_results:
                    try:
                        plt.close(fig_res.fig)
                    except Exception:
                        pass
            except Exception:
                pass
                    
            # Save tables
            for idx, tbl_res in enumerate(table_results, start=1):
                tbl_name = safe_name(tbl_res.title or f"custom_table_{idx}", f"custom_table_{idx}")
                out_path = unique_path(unique_folder, f"{tbl_name}.csv")
                try:
                    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(list(tbl_res.columns or []))
                        writer.writerows(tbl_res.rows or [])
                    output_records.append({
                        "type": "table",
                        "title": tbl_res.title,
                        "filename": os.path.basename(out_path),
                        "saved_path": out_path,
                    })
                    logs.append(f"表格 {idx} 已保存：{out_path}")
                except Exception as e:
                    logs.append(f"表格 {idx} 保存失败：{e}")
                    
            # If archiving is requested and project is active
            if archive and has_active_project:
                code_sha256 = hashlib.sha256(code.encode('utf-8')).hexdigest()
                run_record = {
                    "script_id": script_id or str(uuid.uuid4()),
                    "script_name": script_name,
                    "script_type": script_type,
                    "api_version": "1",
                    "software_version": __version__,
                    "code_sha256": code_sha256,
                    "code": code,
                    "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user_goal": script_desc,
                    "status": "成功",
                    "outputs": output_records
                }
                
                # Add to app's custom_script_runs (or initialize if needed)
                if not hasattr(self, "custom_script_runs") or self.custom_script_runs is None:
                    self.custom_script_runs = []
                from modules.project_patch import _append_or_update_script_run
                _append_or_update_script_run(self.custom_script_runs, run_record)
                
                # Save project workspace
                try:
                    self.project_manager.save_to_workspace()
                    logs.append("本次脚本运行记录已存入当前工程。")
                except Exception as e:
                    logs.append(f"警告: 脚本记录存入当前工程失败: {e}")
                    
            self._emit(True, "脚本执行完成", logs=logs, output_dir=unique_folder, outputs=output_records)
        except Exception as e:
            tb = traceback.format_exc()
            self._emit(False, error=f"执行发生意外错误: {e}", traceback=tb)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


    def do_script_save(self, arg):
        """
        Save or create a custom script.
        Usage: script_save <id_or_new> <name> <description> <type> <code_file_path>
        Use 'new' for creating a new script.
        """
        args = shlex.split(arg)
        if len(args) != 5:
            self._emit(False, error="Usage: script_save <id_or_new> <name> <description> <type> <code_file_path>")
            return

        script_id = None if args[0].lower() == 'new' else args[0]
        name, description, script_type, code_file_path = args[1], args[2], args[3], args[4]

        if not os.path.exists(code_file_path):
            self._emit(False, error=f"Code file not found: {code_file_path}")
            return

        try:
            with open(code_file_path, 'r', encoding='utf-8') as f:
                code_content = f.read()

            from modules.script_manager import save_script
            saved_id = save_script(script_id, name, description, script_type, code_content)
            self._emit(True, "Script saved successfully.", id=saved_id)
        except Exception as e:
            self._emit(False, error=str(e))


    def do_script_delete(self, arg):
        """
        Delete a custom script.
        Usage: script_delete <script_id>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: script_delete <script_id>")
            return

        script_id = args[0]
        try:
            from modules.script_manager import delete_script
            success = delete_script(script_id)
            if success:
                self._emit(True, f"Script {script_id} deleted successfully.", id=script_id)
            else:
                self._emit(False, error=f"Script ID {script_id} not found in user library.")
        except Exception as e:
            self._emit(False, error=str(e))


    def do_script_import(self, arg):
        """
        Import a custom script from a JSON file.
        Usage: script_import <json_path>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: script_import <json_path>")
            return

        json_path = args[0]
        if not os.path.exists(json_path):
            self._emit(False, error=f"JSON file not found: {json_path}")
            return

        try:
            from modules.script_manager import import_script
            imported_script = import_script(json_path)
            self._emit(True, "Script imported successfully.", script={
                "id": imported_script.get("id"),
                "name": imported_script.get("name")
            })
        except Exception as e:
            self._emit(False, error=str(e))


    def do_script_export(self, arg):
        """
        Export a custom script to a JSON file.
        Usage: script_export <script_id> <dest_json_path>
        """
        args = shlex.split(arg)
        if len(args) != 2:
            self._emit(False, error="Usage: script_export <script_id> <dest_json_path>")
            return

        script_id, dest_json_path = args[0], args[1]
        try:
            from modules.script_manager import export_script
            export_script(script_id, dest_json_path)
            self._emit(True, f"Script exported successfully to {dest_json_path}.", id=script_id, path=dest_json_path)
        except Exception as e:
            self._emit(False, error=str(e))


    def _build_project_data_dict(self):
        wordlist_doc = self._build_wordlist_v2_doc_from_project()
        project_data = {
            "active_speaker_id": self.speaker_manager.active_speaker_id,
            "wordlist_doc": wordlist_doc,
            "wordlist_meta": {"title": wordlist_doc.get("title", "")},
            "groups": wordlist_doc.get("groups", []),
            "speakers": {}
        }
        for spk_id, spk in self.speaker_manager.speakers.items():
            spk_data = {
                "name": spk.name,
                "tab_mode": getattr(spk, 'tab_mode', "多条独立音频"),
                "long_audio_path": getattr(spk, 'long_audio_path', None),
                "pending_batch_paths": spk.pending_batch_paths,
                "last_params": spk.last_params,
                "items": {}
            }
            for item_id, item in spk.items.items():
                item_dict = {k: v for k, v in item.items() if k != "snd"} if isinstance(item, dict) else {}
                tags = list(item.get("item_tags") or item.get("tags") or [])
                note = item.get("item_note") or item.get("note") or ""
                custom_fields = {}
                for fkey in ("meta", "item_meta", "fields", "custom_fields"):
                    fval = item.get(fkey)
                    if isinstance(fval, dict):
                        custom_fields.update(fval)
                item_dict.update({
                    "item_id": item.get("item_id", item_id),
                    "label": item.get("label"),
                    "group": item.get("group"),
                    "is_excluded": item.get("is_excluded", False),
                    "analysis_mode": item.get("analysis_mode", spk.last_params.get("analysis_mode", "f0")),
                    "pitch_data": item.get("pitch_data"),
                    "formant_data": item.get("formant_data"),
                    "pitch_data_file": item.get("pitch_data_file"),
                    "formant_data_file": item.get("formant_data_file"),
                    "wordlist_version": item.get("wordlist_version") or ("v2" if (tags or note or custom_fields) else "v1"),
                    "wordlist_title": item.get("wordlist_title") or wordlist_doc.get("title", ""),
                    "item_note": note,
                    "note": note,
                    "item_tags": tags,
                    "tags": tags,
                    "item_aliases": item.get("item_aliases") or item.get("aliases") or [],
                    "item_meta": dict(custom_fields),
                    "fields": dict(custom_fields),
                    "custom_fields": dict(custom_fields),
                    "group_note": item.get("group_note"),
                    "group_tags": item.get("group_tags", []) or [],
                    "metadata_source": item.get("metadata_source"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "inner_splits": item.get("inner_splits", []) or [],
                    "chars_bounds": item.get("chars_bounds", []) or [],
                    "syllables": item.get("syllables", []) or [],
                })
                spk_data["items"][item_id] = item_dict
            project_data["speakers"][spk_id] = spk_data
        return project_data


    def do_script_prompt(self, arg):
        """
        Generate AI prompt for writing a custom script.
        Usage: script_prompt [key=value ...]
        Valid keys: template, output_type, scope, analysis_mode, user_request, out, format,
                    purpose, goal, script_type, prompt_mode, data_range, group_by, chart_style,
                    x_axis, y_axis, stats, title, filename, img_format, output_table,
                    show_legend, use_chinese, custom_desc, agent_detail_level,
                    agent_chart_count, agent_plan_count, agent_project_summary_mode
        Example: script_prompt template=formant_space scope=integrated user_request="绘制元音空间图" out=prompt.md
        """
        from modules.script_prompt import (
            generate_ai_prompt,
            validate_and_normalize_script_prompt_params,
            VALID_SCRIPT_PROMPT_KEYS,
            SCRIPT_PROMPT_KEY_ALIASES,
        )

        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid argument syntax: {e}", valid_keys=list(VALID_SCRIPT_PROMPT_KEYS))
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit(
                True,
                "Supported parameters for script_prompt",
                usage="script_prompt [key=value ...]",
                valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
            )
            return

        fmt = 'json'
        raw_params = {}
        for kv in args:
            if '=' in kv:
                k, v = kv.split('=', 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean.lower() == 'format':
                    fmt = v_clean.lower()
                raw_params[k_clean] = v_clean
            else:
                val = kv.lower().strip()
                if val in ('text', 'json'):
                    fmt = val
                else:
                    self._emit(
                        False,
                        error=f"Invalid parameter format: '{kv}'. Expected key=value. Valid parameters are: {', '.join(VALID_SCRIPT_PROMPT_KEYS)}",
                        valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
                    )
                    return

        try:
            selections = validate_and_normalize_script_prompt_params(raw_params)
        except ValueError as e:
            self._emit(
                False,
                error=str(e),
                valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
            )
            return

        project_data = self._build_project_data_dict()
        try:
            prompt_text = generate_ai_prompt(project_data, selections)
            out_path = selections.get("out", "").strip()
            saved_out = None
            if out_path:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(prompt_text)
                saved_out = os.path.abspath(out_path)

            if fmt == 'text':
                print(prompt_text)
            else:
                payload = {"prompt_text": prompt_text}
                if saved_out:
                    payload["output_file"] = saved_out
                self._emit(True, "AI prompt generated successfully.", **payload)
        except Exception as e:
            self._emit(False, error=str(e), valid_keys=list(VALID_SCRIPT_PROMPT_KEYS))


    def do_generate_prompt(self, arg):
        """Alias for script_prompt."""
        self.do_script_prompt(arg)


__all__ = ["CLIToolkitScriptMixin"]
