"""Recalculation Mixin: 全量与单条音频重算、微观元音核检测调度."""
import os
import threading
import concurrent.futures
import numpy as np

from modules.audio_core import (
    core_microscopic_vowel_nucleus,
    batch_process_worker,
    long_process_worker,
    recalculate_bounds_fast,
    auto_split_inner_word,
    extract_f0,
    load_compatible_sound,
)
from modules.data_utils import split_into_syllables


class AppRecalcMixin:
    """提供当前条目与全量条目的声学参数重算及微观元音核精细化定位."""

    def recalculate_all_audio(self, only_trim_silence=False, recompute_pitch=True, only_pitch_changed=False):
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            if only_trim_silence or not recompute_pitch:
                self.flush_eraser_changes()
            else:
                self.spectrogram_panel.discard_eraser_changes()
        if not self.items: return

        # Capture parameter values before sync
        old_params = dict(self.last_params) if hasattr(self, 'last_params') and self.last_params else {}

        # 1. 确保所有 UI 输入框的最新的参数值都已经同步到了 self.last_params 中（在主线程中执行）
        try:
            self.on_param_change(recalculate_current=False)
        except Exception:
            pass

        # Check if ONLY acoustic parameters (pitch or formant) changed
        if not only_pitch_changed and old_params and hasattr(self, 'last_params') and self.last_params:
            new_params = self.last_params
            pitch_changed = (
                old_params.get('pitch_floor') != new_params.get('pitch_floor') or
                old_params.get('pitch_ceiling') != new_params.get('pitch_ceiling') or
                old_params.get('voicing_threshold') != new_params.get('voicing_threshold')
            )
            formant_changed = (
                old_params.get('formant_max_hz') != new_params.get('formant_max_hz') or
                old_params.get('formant_count') != new_params.get('formant_count') or
                old_params.get('formant_window_length') != new_params.get('formant_window_length') or
                old_params.get('formant_pre_emphasis') != new_params.get('formant_pre_emphasis') or
                old_params.get('formant_sample_strategy') != new_params.get('formant_sample_strategy') or
                old_params.get('analysis_mode') != new_params.get('analysis_mode')
            )
            boundary_params_changed = (
                old_params.get('db') != new_params.get('db') or
                old_params.get('skip_front') != new_params.get('skip_front')
            )
            if (pitch_changed or formant_changed) and not boundary_params_changed:
                only_pitch_changed = True

        items_snapshot = [(iid, item) for iid, item in self.items.items() if not item.get('is_excluded', False)]
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算..."))
            trim_silence = self.switch_trim_silence.get()

            if only_trim_silence:
                for i, (iid, item) in enumerate(items_snapshot):
                    if item.get('snd') and 'raw_start' in item and 'raw_end' in item:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], item.get('pitch_data', item.get('pitch')), item['raw_start'], item['raw_end'], trim_silence
                        )
                        # 等比例缩放内部蓝线和 chars_bounds (如果只因裁切而变化)
                        old_s, old_e = item.get('start', mic_s), item.get('end', mic_e)

                        # 如果用户手动修改了 start/end，我们可以检查 item.get('is_manual_edited', False)
                        # 但由于没有这个 flag，我们依然等比例缩放它们。
                        if 'inner_splits' in item and item['inner_splits'] and old_e > old_s:
                            ratio = (mic_e - mic_s) / (old_e - old_s)
                            item['inner_splits'] = [mic_s + (s - old_s) * ratio for s in item['inner_splits']]
                            if 'chars_bounds' in item and item['chars_bounds']:
                                item['chars_bounds'] = [[mic_s + (c[0] - old_s) * ratio, mic_s + (c[1] - old_s) * ratio] for c in item['chars_bounds']]

                        # 如果有 chars_bounds，则基于 chars_bounds 更新 start/end 以防错位
                        if 'chars_bounds' in item and item['chars_bounds']:
                            item['start'] = item['chars_bounds'][0][0]
                            item['end'] = item['chars_bounds'][-1][1]
                        else:
                            item['start'], item['end'] = mic_s, mic_e

                    if i % 5 == 0 or i == total - 1:
                        self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))
            else:
                tasks = []
                params = {
                    'db': self.last_params['db'],
                    'skip_front': self.last_params['skip_front'],
                    'pitch_floor': self.last_params['pitch_floor'],
                    'pitch_ceiling': self.last_params['pitch_ceiling'],
                    'voicing_threshold': self.last_params.get('voicing_threshold', 0.25),
                    'analysis_mode': self.last_params.get('analysis_mode', 'f0'),
                    'formant_max_hz': self.last_params.get('formant_max_hz', 5500.0),
                    'formant_count': self.last_params.get('formant_count', 5),
                    'formant_window_length': self.last_params.get('formant_window_length', 0.025),
                    'formant_pre_emphasis': self.last_params.get('formant_pre_emphasis', 50.0),
                    'formant_sample_strategy': self.last_params.get('formant_sample_strategy', '整段11点'),
                    'pts': self.last_params.get('pts', 11),
                }

                valid_items = []
                recomputed_pitches = {}
                for iid, item in items_snapshot:
                    macro_bounds = self._get_valid_macro_bounds(item)
                    if item.get('snd') and macro_bounds:
                        snd = item['snd']
                        snd_id = id(snd)

                        mac_s, mac_e = macro_bounds
                        valid_ms = max(0, mac_s)
                        valid_me = min(snd.get_total_duration(), mac_e)

                        # 性能优化：按 snd 实例缓存 Pitch，避免长音频模式下被重复计算上千次
                        if recompute_pitch:
                            try:
                                if snd_id not in recomputed_pitches:
                                    recomputed_pitches[snd_id] = extract_f0(snd, self.last_params)
                                full_pitch = recomputed_pitches[snd_id]
                                p_xs_all = full_pitch['xs']
                                p_freqs_all = full_pitch['freqs']
                                win_s = max(0.0, valid_ms - 0.05)
                                win_e = valid_me + 0.05
                                idx_s = np.searchsorted(p_xs_all, win_s)
                                idx_e = np.searchsorted(p_xs_all, win_e)
                                item['pitch_data'] = {
                                    'xs': p_xs_all[idx_s:idx_e].copy(),
                                    'freqs': p_freqs_all[idx_s:idx_e].copy()
                                }
                                item['_dirty_pitch'] = True
                                if 'pitch' in item:
                                    del item['pitch']
                            except Exception: pass

                        # 保存计算该项时所用的参数实现所见即所得导出
                        item['pitch_floor'] = params['pitch_floor']
                        item['pitch_ceiling'] = params['pitch_ceiling']
                        item['voicing_threshold'] = params['voicing_threshold']

                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)

                            # 性能优化：切片 Pitch 数组，大幅减少 IPC 数据量
                            p_xs = item['pitch_data']['xs']
                            p_freqs = item['pitch_data']['freqs']
                            idx_start = np.searchsorted(p_xs, valid_ms)
                            idx_end = np.searchsorted(p_xs, valid_me)

                            tasks.append({
                                'ms': mac_s, 'me': mac_e,
                                'snd_values': part.values, 'snd_sf': part.sampling_frequency,
                                'pitch_xs': p_xs[idx_start:idx_end], 'pitch_freqs': p_freqs[idx_start:idx_end],
                                'word_label': item['label'].replace(" (缺失)", "")
                            })
                            valid_items.append(item)
                    elif item.get('path'):
                        # 独立音频模式：直接记录路径，后续使用多进程批处理
                        tasks.append({
                            'path': item['path'],
                            'word_label': item['label'].replace(" (缺失)", ""),
                            'type': 'batch'
                        })
                        valid_items.append(item)

                if tasks:
                    # 复用常驻 ProcessPoolExecutor 进行 CPU 密集型任务
                    executor = getattr(self, 'executor', None)
                    own_executor = False
                    if executor is None or getattr(executor, '_broken', False) or getattr(executor, '_shutdown_thread', False):
                        max_workers = min(os.cpu_count() or 4, 8)
                        executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
                        own_executor = True
                    try:
                        futures = {}
                        for idx, task in enumerate(tasks):
                            if task.get('type') == 'batch':
                                # 独立音频：重新运行 batch_process_worker
                                f = executor.submit(batch_process_worker, task['path'], params, trim_silence, task.get('word_label', ""))
                            else:
                                # 长音频：重新运行 long_process_worker
                                f = executor.submit(
                                    long_process_worker,
                                    task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                                    task['ms'], task['me'], params, trim_silence, task['word_label']
                                )
                            futures[f] = idx

                        completed = 0
                        for future in concurrent.futures.as_completed(futures):
                            idx = futures[future]
                            try:
                                res = future.result()
                                if res.get('success'):
                                    target_item = valid_items[idx]
                                    if tasks[idx].get('type') == 'batch':
                                        # 合并独立音频处理结果
                                        if not target_item.get('is_manual_edited') and not only_pitch_changed:
                                            target_item['start'] = res['start']
                                            target_item['end'] = res['end']
                                            target_item['raw_start'] = res['raw_start']
                                            target_item['raw_end'] = res['raw_end']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                            target_item['split_warnings'] = res.get('split_warnings', [])
                                            target_item['split_confidence'] = res.get('split_confidence', 1.0)
                                        target_item['pitch_data'] = res['pitch_data']
                                        target_item['_dirty_pitch'] = True
                                        if 'pitch' in target_item:
                                            del target_item['pitch']
                                        target_item.pop('has_empty_data', None)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                                        if 'formant_data' in res:
                                            target_item['formant_data'] = res['formant_data']
                                            target_item['_dirty_formant'] = True
                                            self._stamp_formant_params_on_item(target_item)
                                        if 'preview_formants' in res:
                                            target_item['preview_formants'] = res['preview_formants']
                                        # 如果是独立音频，还需要把 Cache 也更新了，防止下次加载又是旧的
                                        if target_item.get('path'):
                                            self._set_audio_cache(target_item['path'], res, params)
                                    else:
                                        # 合并长音频处理结果
                                        if not target_item.get('is_manual_edited') and not only_pitch_changed:
                                            target_item['start'] = res['mis']
                                            target_item['end'] = res['mie']
                                            target_item['raw_start'] = res['raw_s']
                                            target_item['raw_end'] = res['raw_e']
                                            target_item['inner_splits'] = res.get('inner_splits', [])
                                            target_item['chars_bounds'] = res.get('chars_bounds', [])
                                            target_item['split_warnings'] = res.get('split_warnings', [])
                                            target_item['split_confidence'] = res.get('split_confidence', 1.0)
                                        target_item.pop('has_empty_data', None)
                                        target_item['preview_f0'] = res.get('preview_f0', [])
                                        if 'formant_data' in res:
                                            target_item['formant_data'] = res['formant_data']
                                            target_item['_dirty_formant'] = True
                                            self._stamp_formant_params_on_item(target_item)
                                        if 'preview_formants' in res:
                                            target_item['preview_formants'] = res['preview_formants']
                            except Exception: pass

                            completed += 1
                            if completed % max(1, len(futures)//10) == 0 or completed == len(futures):
                                self.root.after(0, lambda v=completed/len(futures): self.set_progress(v))
                    finally:
                        if own_executor:
                            executor.shutdown(wait=False)

            def finalize():
                if self.spectrogram_panel.current_item:
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()

                # 更新所有列表项的图标（刷新警告标志）
                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)

                self.tree_panel.update_preview()
                self.mark_modified()
                self.stop_loading("全局参数已应用")

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()


    def recalculate_current_item(self, only_trim_silence=False, recompute_pitch=False, recompute_formant_only=False, preserve_bounds=False, completion_text=None, param_overrides=None):
        """仅针对当前正在编辑的项重新计算参数（暂不影响全局）"""
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            if not recompute_pitch and not recompute_formant_only:
                self.flush_eraser_changes()
            else:
                self.spectrogram_panel.discard_eraser_changes()
        item = self.spectrogram_panel.current_item
        if not item: return

        def run():
            try:
                self.root.after(0, lambda: self.set_status("正在更新当前项...", "#2563EB", "status_loading"))
                worker_params = dict(self.last_params)
                if param_overrides:
                    worker_params.update(param_overrides)

                def get_segmented_f0(snd, params):
                    total_dur = snd.get_total_duration()
                    macro_bounds = self._get_valid_macro_bounds(item)
                    if macro_bounds and total_dur > 15.0:
                        macro_start, macro_end = macro_bounds
                        padding = 1.0
                        seg_start = max(0.0, macro_start - padding)
                        seg_end = min(total_dur, macro_end + padding)
                        part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                        part_pitch_data = extract_f0(part_snd, params)
                        part_pitch_data['xs'] = part_pitch_data['xs'] + seg_start
                        return part_pitch_data
                    else:
                        return extract_f0(snd, params)

                def get_segmented_formant(snd, params):
                    from modules.audio_core import extract_formants
                    total_dur = snd.get_total_duration()
                    macro_bounds = self._get_valid_macro_bounds(item)
                    if macro_bounds and total_dur > 15.0:
                        macro_start, macro_end = macro_bounds
                        padding = 1.0
                        seg_start = max(0.0, macro_start - padding)
                        seg_end = min(total_dur, macro_end + padding)
                        part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                        part_formant_data = extract_formants(part_snd, params)
                        part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                        return part_formant_data
                    else:
                        return extract_formants(snd, params)

                # 如果是独立音频模式且没有加载 Sound 对象
                if not item.get('snd') and item.get('path'):
                    item['snd'] = load_compatible_sound(item['path'])
                    # 总是为单项重新生成 pitch 确保准确性
                    if not recompute_formant_only:
                        item['pitch_data'] = get_segmented_f0(item['snd'], worker_params)
                        item['_dirty_pitch'] = True
                        if 'pitch' in item:
                            del item['pitch']
                        item['pitch_floor'] = worker_params['pitch_floor']
                        item['pitch_ceiling'] = worker_params['pitch_ceiling']
                        item['voicing_threshold'] = worker_params.get('voicing_threshold', 0.25)

                    # 总是为单项重新生成 formant
                    item['formant_data'] = get_segmented_formant(item['snd'], worker_params)
                    item['_dirty_formant'] = True

                    # 独立音频的宏观边界就是全文
                    item['macro_start'] = 0.0
                    item['macro_end'] = item['snd'].get_total_duration()

                # 如果是仅重新计算共振峰且 Sound 对象已存在
                if recompute_formant_only and item.get('snd'):
                    item['formant_data'] = get_segmented_formant(item['snd'], worker_params)
                    item['_dirty_formant'] = True
                # 如果修改了 Pitch Floor/Ceiling 或 Formant 参数，且非仅重算共振峰，重新计算该项
                elif recompute_pitch and item.get('snd'):
                    item['pitch_data'] = get_segmented_f0(item['snd'], worker_params)
                    item['_dirty_pitch'] = True
                    if 'pitch' in item:
                        del item['pitch']
                    item['pitch_floor'] = worker_params['pitch_floor']
                    item['pitch_ceiling'] = worker_params['pitch_ceiling']
                    item['voicing_threshold'] = worker_params.get('voicing_threshold', 0.25)

                    # 重新生成 formant
                    item['formant_data'] = get_segmented_formant(item['snd'], worker_params)
                    item['_dirty_formant'] = True

                if item.get('formant_data'):
                    self._stamp_formant_params_on_item(item, worker_params)

                # Track single item parameter overrides deviating from speaker default
                overrides = dict(item.get('param_overrides') or {})
                for pk in ('pitch_floor', 'pitch_ceiling', 'voicing_threshold',
                           'formant_max_hz', 'formant_count', 'formant_window_length',
                           'formant_pre_emphasis', 'formant_sample_strategy', 'show_f3', 'analysis_mode'):
                    if pk in worker_params:
                        spk_val = self.last_params.get(pk)
                        if worker_params[pk] != spk_val:
                            overrides[pk] = worker_params[pk]
                        elif pk in overrides:
                            del overrides[pk]
                        item[pk] = worker_params[pk]
                if overrides:
                    item['param_overrides'] = overrides
                elif 'param_overrides' in item:
                    del item['param_overrides']

                # 单条参数应用可以只刷新声学数据，保留已经识别或手动调整过的边界。
                macro_bounds = self._get_valid_macro_bounds(item)
                if not preserve_bounds and not recompute_formant_only and item.get('snd') and macro_bounds:
                    current_pitch = item.get('pitch_data', item.get('pitch'))
                    if only_trim_silence:
                        mic_s, mic_e = recalculate_bounds_fast(
                            item['snd'], current_pitch, item['raw_start'], item['raw_end'], self.switch_trim_silence.get()
                        )
                        # 等比例缩放内部蓝线
                        old_s, old_e = item.get('start', mic_s), item.get('end', mic_e)
                        if 'inner_splits' in item and item['inner_splits'] and old_e > old_s:
                            ratio = (mic_e - mic_s) / (old_e - old_s)
                            item['inner_splits'] = [mic_s + (s - old_s) * ratio for s in item['inner_splits']]
                            if 'chars_bounds' in item and item['chars_bounds']:
                                item['chars_bounds'] = [[mic_s + (c[0] - old_s) * ratio, mic_s + (c[1] - old_s) * ratio] for c in item['chars_bounds']]
                        item['start'], item['end'] = mic_s, mic_e
                    else:
                        macro_start, macro_end = macro_bounds
                        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
                            item['snd'], current_pitch, macro_start, macro_end
                        )
                        item['start'], item['end'] = mic_s, mic_e
                        item['raw_start'], item['raw_end'] = raw_s, raw_e

                        label = item['label'].replace(" (缺失)", "")
                        syls = split_into_syllables(label)
                        split_warnings = []
                        split_confidence = 1.0
                        if len(syls) > 1:
                            meta = {}
                            item['inner_splits'] = auto_split_inner_word(item['snd'], raw_s, raw_e, len(syls), pitch_data=current_pitch, output_meta=meta)
                            split_warnings = meta.get('split_warnings', [])
                            split_confidence = meta.get('split_confidence', 1.0)
                            from modules.audio_core import auto_split_to_chars_bounds
                            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), worker_params, pitch_data=current_pitch)
                            if item['chars_bounds']:
                                item['start'] = item['chars_bounds'][0][0]
                                item['end'] = item['chars_bounds'][-1][1]
                        else:
                            item['inner_splits'] = []
                            item['chars_bounds'] = [[item['start'], item['end']]]
                        item['split_warnings'] = split_warnings
                        item['split_confidence'] = split_confidence
                        item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0

                # 重新生成 11 点预览数据用于警告图标状态更新
                if item.get('snd') and (item.get('pitch_data') or item.get('pitch')):
                    preview_times = np.linspace(item['start'], item['end'], 11)
                    if item.get('pitch_data'):
                        p_xs = item['pitch_data']['xs']
                        p_freqs = item['pitch_data']['freqs']
                        preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()
                        valid_xs = p_xs[p_freqs > 0]
                        if len(valid_xs) == 0:
                            preview_f0 = [0.0] * len(preview_times)
                        else:
                            for j, t in enumerate(preview_times):
                                if np.min(np.abs(valid_xs - t)) > 0.025:
                                    preview_f0[j] = 0.0
                    else:
                        preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                        preview_f0 = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                    item['preview_f0'] = preview_f0
                    # 清除缓存标记，让后续 update_item_icon 通过 _check_item_has_empty_data 精准重算
                    item.pop('has_empty_data', None)

                # 同时重新生成 preview_formants
                if item.get('snd') and item.get('formant_data'):
                    pts = int(worker_params.get('pts', 11))
                    strategy = worker_params.get('formant_sample_strategy', '整段11点')
                    show_f3 = bool(item.get("show_f3", worker_params.get("show_f3", self.last_params.get("show_f3", False))))
                    if show_f3:
                        _, preview_f1, preview_f2, preview_f3 = self.sample_formant_points_with_f3(item, pts, strategy)
                        item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                    else:
                        _, preview_f1, preview_f2 = self.sample_formant_points(item, pts, strategy)
                        item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}

                def finalize():
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()
                    # 更新树图标（警告标志）
                    for iid, it in list(self.items.items()):
                        if it is item:
                            self.tree_panel.update_item_icon(iid)
                            break
                    self.tree_panel.update_preview()
                    self.mark_modified()
                    self.set_status(completion_text or "当前项已更新", "#10B981", "status_success")

                self.root.after(0, finalize)
            except Exception as e:
                error_message = f"更新失败: {e}"
                self.root.after(0, lambda msg=error_message: self.set_status(msg, "#EF4444", "status_error"))

        threading.Thread(target=run, daemon=True).start()


    def _microscopic_vowel_nucleus(self, snd, global_pitch, t_min, t_max):
        return core_microscopic_vowel_nucleus(
            snd, global_pitch, t_min, t_max,
            self.last_params['db'], self.last_params['skip_front'],
            self.switch_trim_silence.get()
        )


