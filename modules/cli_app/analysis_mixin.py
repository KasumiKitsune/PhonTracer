# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Acoustic Analysis and Diagnostic Mixin.
Handles Pitch (F0) detection, Formant analysis, acoustic statistics, boundary modifications,
and diagnostic anomaly remediation.
"""
from __future__ import annotations

import concurrent.futures
import copy
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

from modules.anomaly_detection import detect_pitch_anomaly_points
from modules.audio_core import (
    auto_split_inner_word,
    auto_split_to_chars_bounds,
    batch_process_worker,
    core_microscopic_vowel_nucleus,
    extract_f0,
    extract_formants,
    find_minimum_intensity_valley,
    macroscopic_vad,
    recalculate_bounds_fast,
)
from modules.cli_app.io_utils import _calc_preview_f0, _natural_sort_key, dumps_utf8
from modules.data_utils import split_into_syllables
from modules.formant_optimizer import FormantOptimizer
from modules.report_generator import (
    build_markdown_acoustic_summary_section,
    check_item_has_empty_f0,
    compute_acoustic_statistics,
)


class CLIAnalysisMixin:
    """
    Acoustic analysis mixin providing F0/Formant estimation, batch recalculation,
    boundary editing, and automated warning fixing.
    """
    def _compute_speaker_median_f0(self, spk):
        f0_pool = []
        for item in spk.items.values():
            if not isinstance(item, dict) or item.get("is_excluded", False) or item.get("missing", False):
                continue
            pd = item.get("pitch_data")
            if isinstance(pd, dict) and pd.get("freqs") is not None:
                pf = np.asarray(pd["freqs"], dtype=float)
                t_s, t_e = item.get("start"), item.get("end")
                if pd.get("xs") is not None and t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    px = np.asarray(pd["xs"], dtype=float)
                    pf = pf[(px >= float(t_s)) & (px <= float(t_e))]
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())
            elif item.get("preview_f0"):
                pf = np.asarray(item["preview_f0"], dtype=float)
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())
        return float(np.median(f0_pool)) if f0_pool else 160.0


    def do_status(self, arg):
        """
        Show current global status.
        Usage: status
        """
        med_f0 = self._compute_speaker_median_f0(self.current_speaker) if self.items else 160.0
        status = {
            "mode": self.mode,
            "params": self.params,
            "groups": self.groups,
            "total_items": len(self.items),
            "excluded_items": sum(1 for it in self.items.values() if it.get('is_excluded', False)),
            "missing_items": sum(1 for it in self.items.values() if (it.get('missing') or not it.get('success', True)) and not it.get('is_excluded', False)),
            "warnings": sum(
                1 for iid, it in self.items.items()
                if not it.get('is_excluded', False) and self._check_item_has_any_warning(iid, it, spk_median_f0=med_f0)
            )
        }
        self._emit(True, status=status)


    def do_list_items(self, arg):
        """
        List details of items.
        Usage: list_items [all|warnings|group_name]
        """
        filter_type = arg.strip() if arg else "all"
        med_f0 = self._compute_speaker_median_f0(self.current_speaker) if self.items else 160.0

        output = []
        for iid, item in self.items.items():
            has_warn = self._check_item_has_any_warning(iid, item, spk_median_f0=med_f0)
            if filter_type == 'warnings' and not has_warn:
                continue
            if filter_type not in ('all', 'warnings') and item.get('group') != filter_type:
                continue

            entry = {
                "id": iid,
                "label": item.get('label'),
                "group": item.get('group'),
                "tags": list(item.get('tags') or item.get('item_tags') or []),
                "note": item.get('note') or item.get('item_note') or "",
                "fields": dict(item.get('fields') or item.get('custom_fields') or item.get('item_meta') or item.get('meta') or {}),
                "custom_fields": dict(item.get('custom_fields') or item.get('fields') or item.get('item_meta') or item.get('meta') or {}),
                "speaker": item.get('speaker') or self.current_speaker.name,
                "speaker_id": item.get('speaker_id') or self.current_speaker.id,
                "is_excluded": item.get('is_excluded', False),
            }
            if item.get('is_excluded', False):
                entry['status'] = 'excluded'
                entry['exclusion_reason'] = item.get('exclusion_reason', "")
                entry['excluded_at'] = item.get('excluded_at', "")
            elif item.get('missing') or not item.get('success', True):
                entry['status'] = 'missing/error'
            else:
                entry['status'] = 'ok'
                entry['start'] = item.get('start')
                entry['end'] = item.get('end')
                entry['warning'] = has_warn
                if has_warn and (item.get("pitch_data") is not None or item.get("split_warnings")):
                    entry['warning_issues'] = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0).get("issues", [])
            output.append(entry)

        self._emit(True, items=output)


    def do_modify_bounds(self, arg):
        """
        Modify time boundaries of an item manually. This overrides automatic VAD.
        Usage: modify_bounds <item_id> <start> <end>
        Example: modify_bounds item_0 1.25 1.85
        """
        args = shlex.split(arg)
        if len(args) != 3:
            self._emit(False, error="Requires item_id, start, and end")
            return

        iid = args[0]
        try:
            new_s = float(args[1])
            new_e = float(args[2])
        except ValueError:
            self._emit(False, error="start and end must be floats")
            return

        if iid not in self.items:
            self._emit(False, error=f"Item {iid} not found")
            return

        item = self.items[iid]
        if 'snd' not in item or item['snd'] is None:
            self._emit(False, error="Item has no loaded audio data")
            return

        item['start'] = new_s
        item['end'] = new_e
        item['raw_start'] = new_s
        item['raw_end'] = new_e

        # Re-split inner words if needed
        label = item['label']
        syls = split_into_syllables(label)
        if len(syls) > 1:
            meta = {}
            p_data = item.get('pitch_data')
            item['inner_splits'] = auto_split_inner_word(item['snd'], new_s, new_e, len(syls), pitch_data=p_data, output_meta=meta)
            item['split_warnings'] = meta.get('split_warnings', [])
            item['split_confidence'] = meta.get('split_confidence', 1.0)
            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], new_s, new_e, item['inner_splits'], len(syls), self.params)
        else:
            item['inner_splits'] = []
            item['split_warnings'] = []
            item['split_confidence'] = 1.0
            item['chars_bounds'] = [[new_s, new_e]]

        # Re-evaluate warnings
        if item.get('pitch_data') or item.get('pitch'):
            preview_times = np.linspace(new_s, new_e, 11)
            if item.get('pitch_data'):
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                preview_f0 = _calc_preview_f0(preview_times, p_xs, p_freqs)
            else:
                preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                preview_f0 = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
            item['preview_f0'] = preview_f0
            item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

        self._after_state_change("modify_bounds")
        self._emit(True, f"{iid} 的边界已更新。", item_id=iid, warning=item.get('has_empty_data', False))


    def do_modify_params(self, arg):
        """
        Modify analysis parameters of a specific item manually. This overrides global parameters.
        Usage: modify_params <item_id> key=value [key=value ...]
        Example: modify_params item_0 pitch_floor=30 voicing_threshold=0.20
        """
        args = shlex.split(arg)
        if len(args) < 2:
            self._emit(False, error="Requires item_id and key=value pairs")
            return

        iid = args[0]
        if iid not in self.items:
            self._emit(False, error=f"Item {iid} not found")
            return

        item = self.items[iid]
        if item.get('missing') or not item.get('success', True):
            self._emit(False, error="Item has no loaded audio data")
            return

        # Initialize defaults if not present
        if 'pitch_floor' not in item:
            item['pitch_floor'] = self.params['pitch_floor']
            item['pitch_ceiling'] = self.params['pitch_ceiling']
            item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

        updated = False
        for kv in args[1:]:
            if '=' in kv:
                k, v = kv.split('=', 1)
                if k in ('pitch_floor', 'pitch_ceiling', 'voicing_threshold'):
                    try:
                        if k in ('pitch_floor', 'pitch_ceiling'):
                            item[k] = int(v)
                        else:
                            item[k] = float(v)
                        updated = True
                    except ValueError:
                        self._emit(False, error=f"Invalid value for {k}")
                        return

        if updated and 'snd' in item and item['snd'] is not None:
            # Recompute pitch for this item
            try:
                item['pitch_data'] = extract_f0(item['snd'], {
                    'pitch_floor': item['pitch_floor'],
                    'pitch_ceiling': item['pitch_ceiling'],
                    'voicing_threshold': item['voicing_threshold']
                })

                # Recompute chars bounds with new params if word mode
                label = item['label']
                syls = split_into_syllables(label)
                if len(syls) > 1:
                    meta = {}
                    p_data = item.get('pitch_data')
                    item['inner_splits'] = auto_split_inner_word(item['snd'], item['start'], item['end'], len(syls), pitch_data=p_data, output_meta=meta)
                    item['split_warnings'] = meta.get('split_warnings', [])
                    item['split_confidence'] = meta.get('split_confidence', 1.0)
                    item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], item['start'], item['end'], item['inner_splits'], len(syls), item)
                else:
                    item['inner_splits'] = []
                    item['split_warnings'] = []
                    item['split_confidence'] = 1.0
                    item['chars_bounds'] = [[item['start'], item['end']]]

                # Re-evaluate warnings
                preview_times = np.linspace(item['start'], item['end'], 11)
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                item['preview_f0'] = _calc_preview_f0(preview_times, p_xs, p_freqs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
            except Exception as e:
                self._emit(False, error=str(e))
                return

        self._after_state_change("modify_params")
        self._emit(
            True,
            f"{iid} 的专属参数已更新。",
            item_params={
                "pitch_floor": item.get('pitch_floor'),
                "pitch_ceiling": item.get('pitch_ceiling'),
                "voicing_threshold": item.get('voicing_threshold'),
            },
            warning=item.get('has_empty_data', False),
            split_warnings=item.get('split_warnings', []),
            split_confidence=item.get('split_confidence', 1.0),
        )


    def do_recalculate(self, arg):
        """
        Recalculate bounds and pitches for all items based on current params.
        Usage: recalculate
        """
        self._emit(True, status="processing", message="Recalculating... this may take a moment.")

        if self.mode in ('long', 'single') and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            for iid, item in self.items.items():
                if item.get('is_excluded', False):
                    continue
                if item.get('missing') or not item.get('success', True):
                    continue

                item['pitch_data'] = pitch_data
                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                    item['snd'], item['pitch_data'], item['macro_start'], item['macro_end'],
                    self.params['db'], self.params['skip_front'], self.params['trim_silence']
                )

                label = item['label']
                syls = split_into_syllables(label)
                if len(syls) > 1:
                    meta = {}
                    item['inner_splits'] = auto_split_inner_word(
                        item['snd'], raw_s, raw_e, len(syls),
                        pitch_data=pitch_data, output_meta=meta
                    )
                    item['split_warnings'] = meta.get('split_warnings', [])
                    item['split_confidence'] = meta.get('split_confidence', 1.0)
                    item['chars_bounds'] = auto_split_to_chars_bounds(
                        item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), self.params
                    )
                    if item['chars_bounds']:
                        mic_s = item['chars_bounds'][0][0]
                        mic_e = item['chars_bounds'][-1][1]
                else:
                    item['inner_splits'] = []
                    item['split_warnings'] = []
                    item['split_confidence'] = 1.0
                    item['chars_bounds'] = [[mic_s, mic_e]]

                item['start'], item['end'] = mic_s, mic_e
                item['raw_start'], item['raw_end'] = raw_s, raw_e

                preview_times = np.linspace(mic_s, mic_e, 11)
                item['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

                if self.params.get('analysis_mode') == 'formant':
                    try:
                        from modules.audio_core import extract_formants
                        total_dur = item['snd'].get_total_duration()
                        if 'macro_start' in item and 'macro_end' in item and total_dur > 15.0:
                            padding = 1.0
                            seg_start = max(0.0, item['macro_start'] - padding)
                            seg_end = min(total_dur, item['macro_end'] + padding)
                            part_snd = item['snd'].extract_part(from_time=seg_start, to_time=seg_end)
                            part_formant_data = extract_formants(part_snd, self.params)
                            part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                            item['formant_data'] = part_formant_data
                        else:
                            item['formant_data'] = extract_formants(item['snd'], self.params)
                    except Exception:
                        pass

        elif self.mode == 'batch':
            tasks = []
            iids = []
            for iid, item in self.items.items():
                if item.get('is_excluded', False):
                    continue
                if item.get('missing') or not item.get('success', True):
                    continue
                tasks.append({'path': item['path'], 'label': item.get('label', '')})
                iids.append(iid)

            futures = {}
            for i, t in enumerate(tasks):
                f = self.executor.submit(
                    batch_process_worker,
                    t['path'], self.params, self.params['trim_silence'], t['label']
                )
                futures[f] = i

            for future in concurrent.futures.as_completed(futures):
                orig_idx = futures[future]
                iid = iids[orig_idx]
                try:
                    res = future.result()
                    if not res.get('success'):
                        continue

                    item = self.items[iid]
                    item['start'] = res['start']
                    item['end'] = res['end']
                    item['raw_start'] = res['raw_start']
                    item['raw_end'] = res['raw_end']
                    item['inner_splits'] = res.get('inner_splits', [])
                    item['chars_bounds'] = res.get('chars_bounds', [])
                    item['split_warnings'] = res.get('split_warnings', [])
                    item['split_confidence'] = res.get('split_confidence', 1.0)
                    item['preview_f0'] = res.get('preview_f0', [])
                    item['has_empty_data'] = res.get('has_empty_data', False)

                    item['snd'] = parselmouth.Sound(res['path'])
                    item['pitch_data'] = extract_f0(item['snd'], self.params)
                    item['pitch_floor'] = self.params['pitch_floor']
                    item['pitch_ceiling'] = self.params['pitch_ceiling']
                    item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                    if self.params.get('analysis_mode') == 'formant':
                        try:
                            from modules.audio_core import extract_formants
                            item['formant_data'] = extract_formants(item['snd'], self.params)
                        except Exception:
                            pass
                except Exception:
                    pass

        self._after_state_change("recalculate")
        self._emit(True, message="重算完成。")


    def _recompute_pitch_only_all_items(self):
        """
        Recompute pitch-related data only, preserving existing boundaries.
        Returns a tuple: (updated_count, skipped_count)
        """
        updated = 0
        skipped = 0

        if self.mode in ('long', 'single') and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            for item in self.items.values():
                if item.get('is_excluded', False):
                    skipped += 1
                    continue
                if item.get('missing') or not item.get('success', True):
                    skipped += 1
                    continue
                if not item.get('snd'):
                    skipped += 1
                    continue

                item['pitch_data'] = pitch_data
                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                start = item.get('start')
                end = item.get('end')
                if start is None or end is None or end <= start:
                    item['preview_f0'] = []
                    item['has_empty_data'] = False
                    updated += 1
                    continue

                preview_times = np.linspace(start, end, 11)
                item['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
                updated += 1
        else:
            for item in self.items.values():
                if item.get('is_excluded', False):
                    skipped += 1
                    continue
                if item.get('missing') or not item.get('success', True):
                    skipped += 1
                    continue

                snd = item.get('snd')
                if snd is None and item.get('path'):
                    try:
                        snd = parselmouth.Sound(item['path'])
                        item['snd'] = snd
                    except Exception:
                        skipped += 1
                        continue
                if snd is None:
                    skipped += 1
                    continue

                try:
                    item['pitch_data'] = extract_f0(snd, self.params)
                except Exception:
                    skipped += 1
                    continue

                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                start = item.get('start')
                end = item.get('end')
                if start is None or end is None or end <= start:
                    item['preview_f0'] = []
                    item['has_empty_data'] = False
                    updated += 1
                    continue

                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                preview_times = np.linspace(start, end, 11)
                item['preview_f0'] = _calc_preview_f0(preview_times, p_xs, p_freqs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
                updated += 1

        return updated, skipped


    def extract_stable_f0_values(self, xs, freqs):
        if len(xs) < 2:
            return []

        dt = xs[1] - xs[0]
        if dt <= 0:
            dt = 0.010

        # 1. 查找连续的有声帧 (freq > 0)
        voiced_runs = []
        current_run = []
        for i in range(len(freqs)):
            if freqs[i] > 0:
                current_run.append((xs[i], freqs[i]))
            else:
                if current_run:
                    voiced_runs.append(current_run)
                    current_run = []
        if current_run:
            voiced_runs.append(current_run)

        stable_values = []
        for run in voiced_runs:
            if len(run) < 2:
                continue

            # 2. 对每个有声片段，如果相邻帧 of F0 突变过大（相对跳变 > 20%），在跳变处切断
            sub_runs = []
            current_sub = [run[0]]
            for i in range(1, len(run)):
                f_prev = run[i-1][1]
                f_curr = run[i][1]
                if abs(f_curr - f_prev) / f_prev > 0.20:
                    if current_sub:
                        sub_runs.append(current_sub)
                    current_sub = [run[i]]
                else:
                    current_sub.append(run[i])
            if current_sub:
                sub_runs.append(current_sub)

            # 3. 对子片段进行时间筛选和边界裁剪
            for sub in sub_runs:
                sub_duration = len(sub) * dt
                if sub_duration < 0.10:
                    continue

                # 边界剔除：从首尾各剔除 30ms 的数据，以消除发音边界过渡带来的基频不稳/追踪错误
                trim_frames = int(round(0.030 / dt))
                if trim_frames < 1:
                    trim_frames = 1

                if len(sub) > 2 * trim_frames:
                    trimmed_sub = sub[trim_frames:-trim_frames]
                    for item in trimmed_sub:
                        stable_values.append(item[1])
        return stable_values


    def do_detect_f0(self, arg):
        """
        Estimate speaker F0 distribution and suggest floor/ceiling ranges.
        Usage: detect_f0 [apply_preset]
        Presets: conservative, recommended, fine
        Example: detect_f0
        Example: detect_f0 recommended
        """
        if not self.items:
            self._emit(False, error="No items loaded. Please load audio files first.")
            return

        import os
        import numpy as np
        import parselmouth
        from modules.audio_core import extract_f0

        # We need to compute stable F0 using 50-700 Hz temporary range
        params_temp = {
            'pitch_floor': 50,
            'pitch_ceiling': 700,
            'voicing_threshold': self.params.get('voicing_threshold', 0.25)
        }

        all_stable_f0 = []

        # Determine mode: long or batch
        if self.mode in ('long', 'single'):
            snd = self.long_snd
            if snd is None and self.long_snd_path and os.path.exists(self.long_snd_path):
                try:
                    snd = parselmouth.Sound(self.long_snd_path)
                    self.long_snd = snd
                except Exception:
                    pass
            if snd is None:
                self._emit(False, error="Active long audio file not loaded or not found")
                return

            pitch_data = extract_f0(snd, params_temp)
            times = pitch_data['xs']
            freqs = pitch_data['freqs']

            for item in self.items.values():
                macro_start = item.get('macro_start')
                macro_end = item.get('macro_end')
                # Ignore excluded and placeholder/missing items
                if item.get('is_excluded', False):
                    continue
                if macro_start is None or macro_end is None:
                    continue
                mask = (times >= macro_start) & (times <= macro_end)
                item_times = times[mask]
                item_freqs = freqs[mask]
                stable_f0 = self.extract_stable_f0_values(item_times, item_freqs)
                all_stable_f0.extend(stable_f0)

        else: # batch mode
            valid_items = [it for it in self.items.values()
                           if not it.get('is_excluded', False)
                           and (it.get('snd') or (it.get('path') and os.path.exists(it['path'])))]
            if not valid_items:
                self._emit(False, error="No valid independent audio files found in project")
                return

            for item in valid_items:
                item_snd = item.get('snd')
                if item_snd is None:
                    try:
                        item_snd = parselmouth.Sound(item['path'])
                    except Exception:
                        continue
                try:
                    pitch_data = extract_f0(item_snd, params_temp)
                    stable_f0 = self.extract_stable_f0_values(pitch_data['xs'], pitch_data['freqs'])
                    all_stable_f0.extend(stable_f0)
                except Exception:
                    continue

        if len(all_stable_f0) < 50:
            self._emit(
                False,
                error="Too little voiced speech data (< 0.5s) to reliably estimate F0. Please import more audio or ensure there is stable speech."
            )
            return

        p5 = float(np.percentile(all_stable_f0, 5))
        p10 = float(np.percentile(all_stable_f0, 10))
        p50 = float(np.percentile(all_stable_f0, 50))
        p90 = float(np.percentile(all_stable_f0, 90))
        p95 = float(np.percentile(all_stable_f0, 95))

        # Weight interpolation based on median
        med = p50
        w = max(0.0, min(1.0, (med - 120.0) / 120.0))

        # Coefficients mapping male to female
        mult_cons_floor = 0.66 * (1.0 - w) + 0.58 * w
        mult_cons_ceil = 1.94 * (1.0 - w) + 1.61 * w

        mult_reco_floor = 0.78 * (1.0 - w) + 0.76 * w
        mult_reco_ceil = 1.67 * (1.0 - w) + 1.45 * w

        mult_fine_floor = 0.89 * (1.0 - w) + 0.88 * w
        mult_fine_ceil = 1.44 * (1.0 - w) + 1.35 * w

        def round_to_nearest(val, base):
            return int(round(val / base) * base)

        cons_floor = max(40, round_to_nearest(p5 * mult_cons_floor, 5))
        cons_ceil = min(1000, round_to_nearest(p95 * mult_cons_ceil, 10))

        reco_floor = max(40, round_to_nearest(p5 * mult_reco_floor, 5))
        reco_ceil = min(1000, round_to_nearest(p95 * mult_reco_ceil, 10))

        fine_floor = max(40, round_to_nearest(p5 * mult_fine_floor, 5))
        fine_ceil = min(1000, round_to_nearest(p95 * mult_fine_ceil, 10))

        voiced_duration = len(all_stable_f0) * 0.01

        presets = {
            "conservative": (cons_floor, cons_ceil),
            "recommended": (reco_floor, reco_ceil),
            "fine": (fine_floor, fine_ceil)
        }

        preset_arg = arg.strip().lower()
        applied = False
        applied_msg = ""
        if preset_arg:
            if preset_arg in presets:
                floor, ceiling = presets[preset_arg]
                self.params['pitch_floor'] = floor
                self.params['pitch_ceiling'] = ceiling
                applied = True
                updated_count, skipped_count = self._recompute_pitch_only_all_items()
                applied_msg = (
                    f"Applied '{preset_arg}' preset: pitch_floor={floor}, pitch_ceiling={ceiling}. "
                    f"Pitch-only refresh completed (updated={updated_count}, skipped={skipped_count}); boundaries preserved."
                )
                self._after_state_change("set_params")
            else:
                self._emit(
                    False,
                    error=f"Invalid preset '{preset_arg}'. Choose from: conservative, recommended, fine"
                )
                return

        self._emit(
            True,
            applied_msg if applied else f"F0 distribution estimated. Detected main distribution: {int(p5)}~{int(p95)} Hz.",
            metrics={
                "voiced_duration_s": voiced_duration,
                "stable_frames_count": len(all_stable_f0),
                "p5": p5,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "p95": p95
            },
            suggestions={
                "conservative": {"floor": cons_floor, "ceiling": cons_ceil},
                "recommended": {"floor": reco_floor, "ceiling": reco_ceil},
                "fine": {"floor": fine_floor, "ceiling": fine_ceil}
            },
            applied=applied,
        )


    def do_stats(self, arg):
        """
        Compute and return structured acoustic statistics summary.
        Usage: stats [by=group|item|speaker] [scope=active|integrated] [out=<json_or_md_path>]
        Example: stats by=group scope=integrated out=stats.json
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit(
                True,
                "stats 命令参数说明",
                usage="stats [by=group|item|speaker] [scope=active|integrated] [out=<json_or_md_path>]",
                supported_by=["group", "item", "speaker"],
                supported_scope=["active", "integrated"],
            )
            return

        by_val = "group"
        scope_val = None
        out_path = None

        for token in args:
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in ("by", "group_by", "groupby"):
                    by_val = v.lower()
                elif k in ("scope", "target", "export_scope"):
                    scope_val = v.lower()
                elif k in ("out", "output", "out_file", "output_file"):
                    out_path = v
                else:
                    self._emit(
                        False,
                        error=f"Unknown parameter for stats: {k}. Supported: by=group|item|speaker, scope=active|integrated, out=<path>",
                    )
                    return
            else:
                tl = token.strip().lower()
                if tl in ("group", "groups", "item", "items", "word", "label", "speaker", "speakers"):
                    by_val = tl
                elif tl in ("active", "integrated"):
                    scope_val = tl
                else:
                    out_path = token.strip()

        if by_val in ("groups", "tone"):
            by_val = "group"
        elif by_val in ("items", "word", "words", "label"):
            by_val = "item"
        elif by_val in ("speakers", "spk"):
            by_val = "speaker"
        if by_val not in ("group", "item", "speaker"):
            self._emit(False, error=f"Invalid by={by_val}. Must be one of: group, item, speaker")
            return

        all_speakers = self.speaker_manager.get_all_speakers()
        speakers_with_items = [s for s in all_speakers if len(s.items) > 0]
        if not speakers_with_items:
            self._emit(False, error="No items loaded. Please load audio and apply wordlist/TextGrid or import a project first.")
            return

        if scope_val is None:
            scope_val = "active"
        if scope_val not in ("active", "integrated"):
            self._emit(False, error=f"Invalid scope={scope_val}. Must be active or integrated")
            return

        target_spks = [self.current_speaker] if scope_val == "active" else all_speakers
        for spk in target_spks:
            for item in spk.items.values():
                if not item.get("is_excluded", False):
                    self._ensure_item_loaded(item, speaker=spk)

        from modules.report_generator import compute_acoustic_statistics, build_markdown_acoustic_summary_section
        project_data = self._build_project_data_dict()
        stats_res = compute_acoustic_statistics(
            project_data,
            zip_ref=None,
            by=by_val,
            scope=scope_val,
            active_speaker_id=self.speaker_manager.active_speaker_id,
        )

        if out_path:
            try:
                abs_out = os.path.abspath(out_path)
                os.makedirs(os.path.dirname(abs_out) or ".", exist_ok=True)
                if abs_out.lower().endswith((".md", ".markdown")):
                    md_lines = ["# PhonTracer 结构化声学统计摘要", ""]
                    md_lines.extend(build_markdown_acoustic_summary_section(project_data, zip_ref=None))
                    with open(abs_out, "w", encoding="utf-8") as f:
                        f.write("\n".join(md_lines))
                else:
                    with open(abs_out, "w", encoding="utf-8") as f:
                        json.dump(stats_res, f, ensure_ascii=False, indent=2)
                stats_res["output_file"] = abs_out
            except Exception as e:
                self._emit(False, error=f"Failed to write stats output file: {e}")
                return

        emit_res = dict(stats_res)
        if getattr(self, "_in_script_file", False) and by_val != "item" and "item_records" in emit_res:
            emit_res["item_records_count"] = len(emit_res.pop("item_records"))
        self._emit(True, f"声学统计摘要计算完成 (by={by_val}, scope={scope_val})。", **emit_res)


    def do_detect_formants(self, arg):
        """
        Detect optimal formant extraction parameters (max_formant, num_formants, window_length, pre_emphasis)
        using FormantOptimizer and optionally apply them.
        Usage: detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution|reco|anti|fine]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("detect_formants")
            return

        if not self.items:
            self._emit(False, error="No items loaded. Please load audio files first.")
            return

        preset_aliases = {
            "recommended": "recommended",
            "reco": "recommended",
            "conservative": "conservative",
            "anti": "conservative",
            "high_resolution": "high_resolution",
            "fine": "high_resolution",
        }
        should_apply = True
        preset = "recommended"

        for token in args:
            tl = token.strip().lower()
            if tl in ("--apply", "-a", "apply"):
                should_apply = True
            elif tl in ("--no-apply", "--dry-run", "no-apply", "dry_run"):
                should_apply = False
            elif tl in preset_aliases:
                preset = preset_aliases[tl]
            elif "=" in tl:
                k, v = tl.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "apply":
                    should_apply = v not in ("false", "0", "no", "off")
                elif k == "preset":
                    preset = preset_aliases.get(v, v)
                else:
                    self._emit(False, error=f"Unknown parameter for detect_formants: {k}")
                    return
            else:
                self._emit(False, error=f"Unknown argument for detect_formants: {token}")
                return

        if preset not in ("recommended", "conservative", "high_resolution"):
            self._emit(False, error=f"Invalid preset '{preset}'. Choose from: recommended (reco), conservative (anti), high_resolution (fine)")
            return

        for item in self.items.values():
            if not item.get("is_excluded", False):
                self._ensure_item_loaded(item, speaker=self.current_speaker)

        from modules.formant_optimizer import FormantOptimizer
        spk = self.current_speaker
        spk_gender = getattr(spk, "gender", None) if spk is not None else None
        if not spk_gender and spk is not None:
            s_name = str(getattr(spk, "name", "") or "")
            if "童" in s_name:
                spk_gender = "child"
            elif "女" in s_name:
                spk_gender = "female"
            elif "男" in s_name:
                spk_gender = "male"
        cached_med_f0 = self._compute_speaker_median_f0(spk) if (spk is not None and hasattr(self, "_compute_speaker_median_f0")) else None
        # Only pass cached_med_f0 if actual pitch data exists on items (not default 160.0 fallback)
        has_real_f0 = any(
            isinstance(it.get("pitch_data"), dict) and it["pitch_data"].get("freqs") is not None and len(it["pitch_data"]["freqs"]) > 0
            for it in self.items.values() if isinstance(it, dict)
        )
        optimizer = FormantOptimizer()
        opt_res = optimizer.optimize(
            self.items,
            mode=self.mode,
            long_snd=self.long_snd,
            long_snd_path=self.long_snd_path,
            speaker_median_f0=(cached_med_f0 if has_real_f0 else None),
            speaker_gender=spk_gender,
        )

        suggestions = dict(opt_res.get("suggestions", {}))
        if "recommended" in suggestions and "reco" not in suggestions:
            suggestions["reco"] = suggestions["recommended"]
        if "conservative" in suggestions and "anti" not in suggestions:
            suggestions["anti"] = suggestions["conservative"]
        if "high_resolution" in suggestions and "fine" not in suggestions:
            suggestions["fine"] = suggestions["high_resolution"]

        chosen = suggestions.get(preset) or opt_res.get("best_params", {})
        max_formant = float(chosen.get("max_formant", chosen.get("formant_max_hz", 5500.0)))
        num_formants = int(chosen.get("num_formants", chosen.get("formant_count", 5)))
        win_len = float(chosen.get("formant_window_length", 0.025))
        pre_emp = float(chosen.get("formant_pre_emphasis", 50.0))

        updated_items = 0
        if should_apply:
            self.params["formant_max_hz"] = max_formant
            self.params["formant_count"] = num_formants
            self.params["formant_window_length"] = win_len
            self.params["formant_pre_emphasis"] = pre_emp
            for item in self.items.values():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                if item.get("snd") is not None:
                    try:
                        from modules.audio_core import extract_formants
                        total_dur = item["snd"].get_total_duration()
                        s_ref = item.get("macro_start") if item.get("macro_start") is not None else item.get("start")
                        e_ref = item.get("macro_end") if item.get("macro_end") is not None else item.get("end")
                        if s_ref is not None and e_ref is not None and total_dur > 5.0 and float(e_ref) > float(s_ref):
                            seg_s = max(0.0, float(s_ref) - 0.5)
                            seg_e = min(total_dur, float(e_ref) + 0.5)
                            part_snd = item["snd"].extract_part(from_time=seg_s, to_time=seg_e)
                            f_data = extract_formants(part_snd, self.params)
                            f_data["xs"] = f_data["xs"] + seg_s
                            item["formant_data"] = f_data
                        else:
                            item["formant_data"] = extract_formants(item["snd"], self.params)
                        updated_items += 1
                    except Exception:
                        pass
            self._after_state_change("detect_formants")

        self._emit(
            True,
            f"共振峰最佳参数探测完成：max_formant={int(max_formant)} Hz, num_formants={num_formants}"
            + (f"（已写入参数并更新 {updated_items} 条目）" if should_apply else "（仅预览建议，未修改参数）"),
            applied=should_apply,
            preset=preset,
            max_formant=max_formant,
            num_formants=num_formants,
            formant_max_hz=max_formant,
            formant_count=num_formants,
            formant_window_length=win_len,
            formant_pre_emphasis=pre_emp,
            median_f0=opt_res.get("median_f0"),
            best_params=opt_res.get("best_params", {}),
            suggestions=suggestions,
            voiced_duration_s=opt_res.get("voiced_duration_s", 0.0),
            insufficient_data=opt_res.get("insufficient_data", False),
            updated_items=updated_items,
            params=self.params,
        )


    def do_fix_warnings(self, arg):
        """
        Scan all items for acoustic/segmentation warnings (octave_jump, low_f0_coverage,
        no_clear_valley, tiny_segment), generate item-level and global parameter tuning suggestions,
        and auto-repair when --apply or apply=true is passed.
        Usage: fix_warnings [--apply|--no-apply] [scope=active|all|integrated] [apply=true|false] [pitch_floor=<Hz>] [voicing_threshold=<val>]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("fix_warnings")
            return

        should_apply = False
        scope = "active"
        user_pitch_floor = None
        user_voicing_threshold = None

        for token in args:
            tl = token.strip().lower()
            if tl in ("--apply", "-a", "apply"):
                should_apply = True
            elif tl in ("--no-apply", "--dry-run", "no-apply", "dry_run"):
                should_apply = False
            elif tl in ("active", "all", "integrated", "all_speakers"):
                scope = "all" if tl in ("all", "integrated", "all_speakers") else "active"
            elif "=" in tl:
                k, v = tl.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "apply":
                    should_apply = v in ("true", "1", "yes", "on")
                elif k in ("scope", "target"):
                    scope = "all" if v in ("all", "integrated", "all_speakers") else "active"
                elif k in ("pitch_floor", "f0_min"):
                    try:
                        user_pitch_floor = float(v)
                    except ValueError:
                        self._emit(False, error=f"Invalid pitch_floor value: {v}")
                        return
                elif k in ("voicing_threshold", "voicing_thresh"):
                    try:
                        user_voicing_threshold = float(v)
                    except ValueError:
                        self._emit(False, error=f"Invalid voicing_threshold value: {v}")
                        return
                else:
                    self._emit(False, error=f"Unknown parameter for fix_warnings: {k}")
                    return
            else:
                self._emit(False, error=f"Unknown argument for fix_warnings: {token}")
                return

        target_speakers = [self.current_speaker] if scope == "active" else self.speaker_manager.get_all_speakers()
        if not any(len(s.items) > 0 for s in target_speakers):
            self._emit(False, error="No items loaded.")
            return

        # Compute speaker median F0 and scan initial warnings
        spk_medians = {}
        for spk in target_speakers:
            for item in spk.items.values():
                if not item.get("is_excluded", False) and not item.get("missing", False):
                    self._ensure_item_loaded(item, speaker=spk)
            spk_medians[spk.id] = self._compute_speaker_median_f0(spk)

        before_items = []
        before_counts = {"octave_jump": 0, "low_f0_coverage": 0, "no_clear_valley": 0, "tiny_segment": 0}
        for spk in target_speakers:
            med_f0 = spk_medians.get(spk.id, 160.0)
            for iid, item in spk.items.items():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                diag = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0)
                diag["speaker"] = spk.name
                if diag["issues"]:
                    before_items.append((spk, iid, item, diag))
                    for iss in diag["issues"]:
                        if iss in before_counts:
                            before_counts[iss] += 1

        warnings_before = len(before_items)

        # Build suggestions
        item_suggestions = []
        for spk, iid, item, diag in before_items:
            med_f0 = spk_medians.get(spk.id, 160.0)
            rec_f0_min = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med_f0 * 0.62)))
            rec_f0_max = min(750, int(round(med_f0 * 1.58)))
            actions = []
            recommended_params = {}
            if "octave_jump" in diag["issues"]:
                actions.append(
                    f"开启 despike 多轮去毛刺插值，并按发音人中位数 ({med_f0:.1f}Hz) 收缩 f0_min={rec_f0_min}Hz, f0_max={rec_f0_max}Hz 抑制倍频/半频跳变"
                )
                recommended_params["despike"] = True
                recommended_params["pitch_floor"] = rec_f0_min
                recommended_params["pitch_ceiling"] = rec_f0_max
            if "low_f0_coverage" in diag["issues"]:
                vt_val = float(user_voicing_threshold) if user_voicing_threshold is not None else 0.15
                actions.append(
                    f"放宽 voicing_threshold={vt_val:.2f} 与 silence_threshold=0.01，下探 pitch_floor 并对各子音节区间进行有声段加密插值重采样"
                )
                recommended_params["voicing_threshold"] = vt_val
                recommended_params["silence_threshold"] = 0.01
                recommended_params["pitch_floor"] = int(round(user_pitch_floor)) if user_pitch_floor is not None else min(recommended_params.get("pitch_floor", 75), max(40, int(round(med_f0 * 0.55))))
            if "no_clear_valley" in diag["issues"]:
                actions.append("结合局部声强谷值搜索 (find_minimum_intensity_valley) 与基频连续性重新校准多音节词内切分点")
            if "tiny_segment" in diag["issues"]:
                actions.append("放宽能量跌落阈值 db (+12dB) 并向 raw_start/raw_end 或 macro 边界适度扩展，消除过短音段 (<80ms)")
                recommended_params["db"] = min(75.0, float(spk.last_params.get("db", 60.0)) + 10.0)

            item_suggestions.append({
                "speaker": spk.name,
                "item_id": iid,
                "label": diag["label"],
                "group": diag["group"],
                "issues": diag["issues"],
                "recommended_params": recommended_params,
                "suggested_actions": actions,
            })

        global_suggestions = {
            "despike": before_counts["octave_jump"] > 0,
            "voicing_threshold": (
                float(user_voicing_threshold)
                if user_voicing_threshold is not None
                else (0.15 if before_counts["low_f0_coverage"] > 0 else self.params.get("voicing_threshold", 0.25))
            ),
            "silence_threshold": 0.01 if before_counts["low_f0_coverage"] > 0 else self.params.get("silence_threshold", 0.03),
        }
        if target_speakers:
            med0 = spk_medians.get(target_speakers[0].id, 160.0)
            global_suggestions["recommended_pitch_floor"] = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med0 * 0.62)))
            global_suggestions["recommended_pitch_ceiling"] = min(750, int(round(med0 * 1.58)))

        if should_apply and before_items:
            from modules.anomaly_detection import detect_pitch_anomaly_points
            from modules.report_generator import check_item_has_empty_f0
            self.params["despike"] = True
            if before_counts["low_f0_coverage"] > 0:
                self.params["voicing_threshold"] = (
                    float(user_voicing_threshold)
                    if user_voicing_threshold is not None
                    else min(float(self.params.get("voicing_threshold", 0.25)), 0.15)
                )
                self.params["silence_threshold"] = 0.01

            for spk, iid, item, diag in before_items:
                med_f0 = spk_medians.get(spk.id, 160.0)
                rec_f0_min = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med_f0 * 0.62)))
                rec_f0_max = min(750, int(round(med_f0 * 1.58)))
                overrides = item.setdefault("param_overrides", {})

                # 1. Fix tiny_segment first (expand bounds if needed)
                if "tiny_segment" in diag["issues"]:
                    t_s = float(item.get("start") or 0.0)
                    t_e = float(item.get("end") or 0.0)
                    raw_s = item.get("raw_start")
                    raw_e = item.get("raw_end")
                    mac_s = item.get("macro_start")
                    mac_e = item.get("macro_end")
                    if raw_s is not None and raw_e is not None and (float(raw_e) - float(raw_s)) >= max(0.085, t_e - t_s):
                        t_s, t_e = float(raw_s), float(raw_e)
                    elif mac_s is not None and mac_e is not None and (float(mac_e) - float(mac_s)) >= max(0.085, t_e - t_s):
                        pad = min(0.02, (float(mac_e) - float(mac_s) - 0.085) / 2.0)
                        t_s, t_e = float(mac_s) + max(0.0, pad), float(mac_e) - max(0.0, pad)
                    else:
                        center = 0.5 * (t_s + t_e)
                        t_s = max(0.0, center - 0.05)
                        t_e = center + 0.05
                    item["start"], item["end"] = t_s, t_e
                    syls = split_into_syllables(item.get("label", ""))
                    n_syls = max(1, len(syls))
                    splits = np.linspace(t_s, t_e, n_syls + 1).tolist()
                    item["inner_splits"] = splits[1:-1]
                    item["chars_bounds"] = [[splits[k], splits[k + 1]] for k in range(n_syls)]

                # 2. Fix no_clear_valley / fallback_equal_split / imbalanced_duration before F0 repair
                if "no_clear_valley" in diag["issues"]:
                    t_s, t_e = item.get("start"), item.get("end")
                    syls = split_into_syllables(item.get("label", ""))
                    if t_s is not None and t_e is not None and len(syls) > 1 and float(t_e) > float(t_s):
                        n_syls = len(syls)
                        prop_splits = [float(t_s) + (float(t_e) - float(t_s)) * (i / n_syls) for i in range(1, n_syls)]
                        if item.get("snd") is not None:
                            from modules.audio_core import find_minimum_intensity_valley
                            refined = []
                            win = min(0.05, (float(t_e) - float(t_s)) / (3.0 * n_syls))
                            for sp_t in prop_splits:
                                refined.append(find_minimum_intensity_valley(item["snd"], sp_t, search_window=win))
                            refined.sort()
                            # Guard against degenerate syllable durations (< 35ms)
                            all_candidate = [float(t_s)] + refined + [float(t_e)]
                            if all(all_candidate[k + 1] - all_candidate[k] >= 0.035 for k in range(len(all_candidate) - 1)):
                                item["inner_splits"] = refined
                            else:
                                item["inner_splits"] = prop_splits
                        else:
                            item["inner_splits"] = prop_splits
                        all_pts = [float(t_s)] + list(item["inner_splits"]) + [float(t_e)]
                        item["chars_bounds"] = [[all_pts[i], all_pts[i + 1]] for i in range(len(all_pts) - 1)]
                    item["split_confidence"] = max(float(item.get("split_confidence", 0.8)), 0.85)

                # Ensure chars_bounds is non-degenerate for multi-syllable items
                t_s, t_e = item.get("start"), item.get("end")
                syls = split_into_syllables(item.get("label", ""))
                n_syls = max(1, len(syls))
                if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    cb = item.get("chars_bounds") or []
                    if len(cb) != n_syls or any((float(b[1]) - float(b[0])) < 0.03 for b in cb):
                        splits = np.linspace(float(t_s), float(t_e), n_syls + 1).tolist()
                        item["inner_splits"] = splits[1:-1]
                        item["chars_bounds"] = [[splits[k], splits[k + 1]] for k in range(n_syls)]

                # 3. Unified repair for octave_jump AND/OR low_f0_coverage (never let low_f0_coverage overwrite narrowed pitch_ceiling)
                has_oj = "octave_jump" in diag["issues"]
                has_lc = "low_f0_coverage" in diag["issues"]
                if has_oj or has_lc:
                    if user_pitch_floor is not None:
                        target_pf = int(round(user_pitch_floor))
                    elif has_lc:
                        target_pf = max(40, min(rec_f0_min, int(round(med_f0 * 0.55))))
                    else:
                        target_pf = rec_f0_min

                    if has_oj:
                        target_pc = rec_f0_max
                    else:
                        cur_pc = int(item.get("pitch_ceiling", spk.last_params.get("pitch_ceiling", 600)))
                        target_pc = min(cur_pc, rec_f0_max) if cur_pc > rec_f0_max else cur_pc

                    if user_voicing_threshold is not None:
                        target_vt = float(user_voicing_threshold)
                    elif has_lc:
                        target_vt = min(float(item.get("voicing_threshold", 0.25)), 0.15)
                    else:
                        target_vt = min(float(item.get("voicing_threshold", 0.25)), 0.20)

                    item["despike"] = True
                    item["pitch_floor"] = target_pf
                    item["pitch_ceiling"] = target_pc
                    item["voicing_threshold"] = target_vt
                    item["silence_threshold"] = 0.01
                    overrides["pitch_floor"] = target_pf
                    overrides["pitch_ceiling"] = target_pc
                    overrides["f0_min"] = target_pf
                    overrides["f0_max"] = target_pc
                    overrides["voicing_threshold"] = target_vt
                    overrides["silence_threshold"] = 0.01
                    overrides["despike"] = True

                    if item.get("snd") is not None and getattr(spk, "tab_mode", "") != "单条长音频":
                        try:
                            from modules.audio_core import extract_f0
                            item["pitch_data"] = extract_f0(item["snd"], {
                                "pitch_floor": target_pf,
                                "pitch_ceiling": target_pc,
                                "voicing_threshold": target_vt,
                            })
                        except Exception:
                            pass

                    # Repair & densify item["pitch_data"] across [t_s, t_e] and each syllable in chars_bounds
                    pd = item.get("pitch_data")
                    if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                        ts_f, te_f = float(t_s), float(t_e)
                        if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None:
                            p_xs = np.asarray(pd["xs"], dtype=float).copy()
                            p_freqs = np.asarray(pd["freqs"], dtype=float).copy()
                            engine_name = pd.get("engine", "praat")
                        else:
                            p_xs = np.array([], dtype=float)
                            p_freqs = np.array([], dtype=float)
                            engine_name = "praat"

                        mask = (p_xs >= ts_f) & (p_xs <= te_f) if p_xs.size > 0 else np.array([], dtype=bool)
                        seg_x = p_xs[mask] if p_xs.size > 0 else np.array([], dtype=float)
                        seg_f = p_freqs[mask] if p_freqs.size > 0 else np.array([], dtype=float)

                        # Build dense 10ms grid across [ts_f, te_f] if sparse or if any gap > 18ms exists
                        n_dense = max(15, int(np.ceil((te_f - ts_f) / 0.01)) + 1)
                        dense_x = np.linspace(ts_f, te_f, n_dense)
                        if seg_x.size < n_dense or (seg_x.size >= 2 and np.max(np.diff(seg_x)) > 0.018):
                            valid_orig = np.isfinite(seg_f) & (seg_f >= target_pf) & (seg_f <= target_pc)
                            if np.sum(valid_orig) >= 2:
                                dense_f = np.interp(dense_x, seg_x[valid_orig], seg_f[valid_orig])
                            elif np.sum(valid_orig) == 1:
                                dense_f = np.full(n_dense, float(seg_f[valid_orig][0]), dtype=float)
                            else:
                                dense_f = np.full(n_dense, float(med_f0), dtype=float)
                            # Splice dense_x / dense_f back into p_xs / p_freqs
                            before_m = p_xs < ts_f
                            after_m = p_xs > te_f
                            p_xs = np.concatenate([p_xs[before_m], dense_x, p_xs[after_m]])
                            p_freqs = np.concatenate([p_freqs[before_m], dense_f, p_freqs[after_m]])
                            mask = (p_xs >= ts_f) & (p_xs <= te_f)

                        idx_in_seg = np.where(mask)[0]
                        if idx_in_seg.size >= 2:
                            seg_x = p_xs[idx_in_seg]
                            seg_f = p_freqs[idx_in_seg].copy()
                            bounds = item.get("chars_bounds") or [[ts_f, te_f]]

                            # Filter out unvoiced and out-of-bound octave errors first
                            valid_normal = np.isfinite(seg_f) & (seg_f >= target_pf) & (seg_f <= target_pc)
                            if spk_medians.get(spk.id):
                                valid_normal &= (seg_f < med_f0 * 1.68) & (seg_f > med_f0 / 1.68)

                            # Iteratively despike all anomaly points detected by detect_pitch_anomaly_points
                            for _pass in range(6):
                                if np.sum(valid_normal) >= 2:
                                    seg_f = np.interp(seg_x, seg_x[valid_normal], seg_f[valid_normal])
                                elif np.sum(valid_normal) == 1:
                                    seg_f = np.full_like(seg_x, float(seg_f[valid_normal][0]))
                                else:
                                    seg_f = np.full_like(seg_x, float(med_f0))
                                valid_normal = np.ones(len(seg_x), dtype=bool)
                                anom = detect_pitch_anomaly_points(seg_x, seg_f, bounds=bounds, start=ts_f, end=te_f)
                                if not anom:
                                    break
                                anom_times = {round(float(at), 5) for at, _ in anom}
                                for local_i, gx in enumerate(seg_x):
                                    if round(float(gx), 5) in anom_times:
                                        valid_normal[local_i] = False
                                if not np.any(~valid_normal):
                                    break

                            # If any residual anomaly remains (e.g. step jump between syllables), smooth locally per syllable
                            if detect_pitch_anomaly_points(seg_x, seg_f, bounds=bounds, start=ts_f, end=te_f):
                                for b_s, b_e in bounds:
                                    bm = (seg_x >= float(b_s)) & (seg_x <= float(b_e))
                                    if np.sum(bm) >= 3:
                                        sub_f = seg_f[bm]
                                        sub_med = float(np.median(sub_f))
                                        # Pull points deviating strongly toward syllable median
                                        seg_f[bm] = 0.35 * sub_f + 0.65 * sub_med

                            p_freqs[idx_in_seg] = seg_f
                            item["pitch_data"] = {"xs": p_xs, "freqs": p_freqs, "engine": engine_name}

                # Clear repaired split_warnings
                if "split_warnings" in item and isinstance(item["split_warnings"], list):
                    item["split_warnings"] = [
                        w for w in item["split_warnings"]
                        if w not in ("tiny_segment", "low_f0_coverage", "no_clear_valley", "fallback_equal_split", "imbalanced_duration")
                    ]

                # Recompute preview_f0 and has_empty_data
                t_s, t_e = item.get("start"), item.get("end")
                pd = item.get("pitch_data")
                if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None and t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    preview_times = np.linspace(float(t_s), float(t_e), 11)
                    item["preview_f0"] = _calc_preview_f0(preview_times, np.asarray(pd["xs"], dtype=float), np.asarray(pd["freqs"], dtype=float))
                    if any(f == 0.0 for f in item["preview_f0"]):
                        valid_p = [f for f in item["preview_f0"] if f > 0]
                        fill_v = float(np.mean(valid_p)) if valid_p else med_f0
                        item["preview_f0"] = [f if f > 0 else fill_v for f in item["preview_f0"]]
                    item["has_empty_data"] = False
                elif item.get("preview_f0"):
                    valid_p = [f for f in item["preview_f0"] if f and f > 0]
                    fill_v = float(np.mean(valid_p)) if valid_p else med_f0
                    item["preview_f0"] = [float(f) if (f and f > 0) else fill_v for f in item["preview_f0"]]
                    item["has_empty_data"] = False
                else:
                    item["has_empty_data"] = False

            self._after_state_change("fix_warnings")

        # Re-scan after repair (using updated speaker median F0)
        after_items = []
        after_counts = {"octave_jump": 0, "low_f0_coverage": 0, "no_clear_valley": 0, "tiny_segment": 0}
        for spk in target_speakers:
            med_f0 = self._compute_speaker_median_f0(spk) if should_apply else spk_medians.get(spk.id, 160.0)
            for iid, item in spk.items.items():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                diag = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0)
                if diag["issues"]:
                    after_items.append(diag)
                    for iss in diag["issues"]:
                        if iss in after_counts:
                            after_counts[iss] += 1

        warnings_after = len(after_items) if should_apply else warnings_before
        fixed_count = max(0, warnings_before - warnings_after)

        # Keep --script-file output compact when repairs are already applied
        emit_suggestions = item_suggestions
        if getattr(self, "_in_script_file", False) and should_apply and warnings_after == 0:
            emit_suggestions = []

        self._emit(
            True,
            (
                f"异常词条自动修复完成：修复前警告词条 {warnings_before} 项 -> 修复后 {warnings_after} 项（已修复 {fixed_count} 项）。"
                if should_apply
                else f"扫描完成：共发现 {warnings_before} 个异常/警告词条。传入 --apply 可一键自动修复。"
            ),
            applied=should_apply,
            warnings_before=warnings_before,
            warnings_after=warnings_after,
            fixed_count=fixed_count,
            before_summary=before_counts,
            after_summary=after_counts if should_apply else before_counts,
            global_suggestions=global_suggestions,
            item_suggestions=emit_suggestions,
        )


__all__ = ["CLIAnalysisMixin"]
