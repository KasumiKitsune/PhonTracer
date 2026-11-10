"""Batch Workflow Mixin: 长音频切分器联动、字表批量对齐与 TextGrid 批处理."""
import os
import re
import copy
import threading
import concurrent.futures
import numpy as np
import tkinter as tk
from tkinter import messagebox, filedialog

from modules.visual_splitter import VisualSplitter
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, split_into_syllables
from modules.audio_core import (
    batch_process_worker,
    macroscopic_vad,
    long_process_worker,
    auto_split_inner_word,
    extract_f0,
    batch_process_worker_with_textgrid,
    load_compatible_sound,
)
from modules.wordlist_v2 import (
    apply_record_metadata,
    build_document_from_csv_text,
    build_document_from_structured_text,
    flatten_wordlist_document,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
)


class AppBatchWorkflowMixin:
    """提供可视化切分器联动、字表批量处理与 TextGrid 双向同步导入工作流."""

    def load_long_audio(self):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入音频文件。")
            return
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not path: return
        self.lbl_long_file.configure(text=os.path.basename(path), text_color="#9CA3AF")
        def run():
            self.root.after(0, lambda: self.start_loading(f"正在加载: {os.path.basename(path)}"))
            try:
                snd = load_compatible_sound(path)
                def done():
                    self.pending_long_snd = snd
                    self.long_audio_path = path
                    audio_name = os.path.splitext(os.path.basename(path))[0]
                    self.lbl_long_file.configure(text=os.path.basename(path), text_color="#2563EB")
                    self.mark_modified()
                    self.stop_loading("长音频就绪")
                    if self.active_speaker.name.startswith("发音人"):
                        self.speaker_manager.rename_speaker(self.speaker_manager.active_speaker_id, audio_name)
                        self._update_speaker_dropdown()
                        self.speaker_option_var.set(audio_name)
                    if self._has_unified_wordlist():
                        self._process_unified_wordlist('long')
                self.root.after(0, done)
            except Exception as e:
                error_message = f"加载失败: {e}"
                self.root.after(0, lambda msg=error_message: self.stop_loading(msg))
        threading.Thread(target=run, daemon=True).start()


    def open_visual_splitter(self):
        if not self.pending_long_snd:
            return messagebox.showwarning("提示", "请先导入一条长音频。")

        speaker_id = self.speaker_manager.active_speaker_id
        callback = lambda segments, is_update=False, deleted_count=0: self.on_visual_split_confirm(
            segments, is_update, deleted_count, speaker_id=speaker_id
        )
        existing_items = []
        if self.items:
            for iid, item in self.items.items():
                macro_bounds = self._get_valid_macro_bounds(item)
                if item.get('snd') is not None and macro_bounds:
                    macro_start, macro_end = macro_bounds
                    existing_items.append({
                        'id': iid,
                        'label': item['label'],
                        'start': macro_start,
                        'end': macro_end,
                        'inner_splits': item.get('inner_splits', [])
                    })
            existing_items.sort(key=lambda x: x['start'])

            # 追加剩余的未分配音频段
            if hasattr(self, 'current_macro_segments') and self.current_macro_segments:
                assigned_bounds = {(item['start'], item['end']) for item in existing_items}
                for ms, me in self.current_macro_segments:
                    if (ms, me) not in assigned_bounds:
                        existing_items.append({
                            'id': None,
                            'label': f"【未分配段】",
                            'start': ms,
                            'end': me,
                            'inner_splits': []
                        })
                existing_items.sort(key=lambda x: x['start'])

        if existing_items:
            # 已有字表匹配结果 → 直接进入 edit 模式微调
            word_items = [
                {'id': iid, 'label': self.items[iid]['label'].replace(" (缺失)", "")}
                for iid in self._get_all_ordered_iids()
            ]
            VisualSplitter(
                self.root, self.pending_long_snd, self.icons, callback,
                existing_items=existing_items, word_items=word_items
            )
        else:
            # 未导入字表 → 先自动跑 VAD，然后进入 review 模式
            def run_vad():
                self.root.after(0, lambda: self.start_loading("正在自动检测音频区段..."))
                try:
                    vad_segs = macroscopic_vad(self.pending_long_snd)
                    def open_splitter():
                        self.stop_loading(f"检测到 {len(vad_segs)} 个区段")
                        VisualSplitter(self.root, self.pending_long_snd, self.icons,
                                      callback, vad_segments=vad_segs)
                    self.root.after(0, open_splitter)
                except Exception as e:
                    error_message = f"检测失败: {e}"
                    self.root.after(0, lambda msg=error_message: self.stop_loading(msg))
            threading.Thread(target=run_vad, daemon=True).start()


    def _backup_old_micro_bounds(self):
        old_micro_bounds = {}
        for iid, item in self.items.items():
            if item.get('start') is not None and item.get('end') is not None:
                old_micro_bounds[iid] = {
                    'start': item['start'],
                    'end': item['end'],
                    'inner_splits': copy.deepcopy(item.get('inner_splits', [])),
                    'chars_bounds': copy.deepcopy(item.get('chars_bounds', [])),
                    'raw_start': item.get('raw_start'),
                    'raw_end': item.get('raw_end')
                }
        return old_micro_bounds


    def _get_all_ordered_iids(self):
        all_iids = []
        seen = set()
        project_groups = list(getattr(self.tree_panel, 'project_groups', []))
        tree = getattr(self.tree_panel, 'tree', None)
        group_nodes = getattr(self.tree_panel, 'group_nodes', {})

        def append_iid(iid):
            real_iid = str(iid)[8:] if str(iid).startswith('warning_') else iid
            if real_iid in self.items and real_iid not in seen:
                all_iids.append(real_iid)
                seen.add(real_iid)

        for grp_name in project_groups:
            grp_node = group_nodes.get(grp_name)
            if tree is not None and grp_node:
                try:
                    if tree.exists(grp_node):
                        for child in tree.get_children(grp_node):
                            append_iid(child)
                except tk.TclError:
                    pass

            for iid, item in self.items.items():
                if item.get('group', '导入内容') == grp_name:
                    append_iid(iid)

        for iid in self.items.keys():
            append_iid(iid)
        return all_iids


    def _apply_modified_segment(self, item, seg):
        item['start'] = seg['start']
        item['end'] = seg['end']
        item['inner_splits'] = list(seg.get('inner_splits', []))

        label = item['label'].replace(" (缺失)", "")
        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(label)
        if len(syls) > 1:
            from modules.audio_core import auto_split_to_chars_bounds
            item['chars_bounds'] = auto_split_to_chars_bounds(
                item['snd'], item['start'], item['end'],
                item['inner_splits'], len(syls), self.last_params,
                pitch_data=item.get('pitch_data', item.get('pitch'))
            )
        else:
            item['chars_bounds'] = [[item['start'], item['end']]]

        item['raw_start'] = seg['start']
        item['raw_end'] = seg['end']


    def _apply_new_segment(self, item):
        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(
            item['snd'], item.get('pitch_data', item.get('pitch')), item['macro_start'], item['macro_end']
        )
        item['start'], item['end'] = mic_s, mic_e
        item['raw_start'], item['raw_end'] = raw_s, raw_e

        label = item['label'].replace(" (缺失)", "")
        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(label)
        split_warnings = []
        split_confidence = 1.0
        if len(syls) > 1:
            meta = {}
            p_data = item.get('pitch_data', item.get('pitch'))
            from modules.audio_core import auto_split_inner_word
            item['inner_splits'] = auto_split_inner_word(item['snd'], raw_s, raw_e, len(syls), pitch_data=p_data, output_meta=meta)
            split_warnings = meta.get('split_warnings', [])
            split_confidence = meta.get('split_confidence', 1.0)
            from modules.audio_core import auto_split_to_chars_bounds
            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), self.last_params, pitch_data=p_data)
            if item['chars_bounds']:
                item['start'] = item['chars_bounds'][0][0]
                item['end'] = item['chars_bounds'][-1][1]
        else:
            item['inner_splits'] = []
            item['chars_bounds'] = [[item['start'], item['end']]]
        item['split_warnings'] = split_warnings
        item['split_confidence'] = split_confidence
        item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0


    def _mark_item_missing(self, iid, item):
        item['snd'] = None
        item['pitch'] = None
        item['macro_start'] = None
        item['macro_end'] = None
        item['start'] = None
        item['end'] = None
        item['inner_splits'] = []

        if not item['label'].endswith(" (缺失)"):
            self.tree_panel.tree.item(iid, text=item['label'] + " (缺失)")
        self.tree_panel.tree.item(iid, image='')


    def on_visual_split_confirm(self, segments, is_update=False, deleted_count=0, speaker_id=None):
        if speaker_id is not None and speaker_id != self.speaker_manager.active_speaker_id:
            messagebox.showwarning("提示", "段落编辑期间发音人已切换。为避免覆盖其他发音人的数据，请切回原发音人后重新打开段落编辑器。")
            return

        if not is_update:
            self.manual_segments = segments
            self.current_macro_segments = list(segments)
            self.mark_modified()
            messagebox.showinfo("提示", f"全新手动切分完成，共 {len(segments)} 个片段。\n现在请点击“导入字表”来匹配文本。")
            return

        mapped_segs = {seg['id']: seg for seg in segments if seg.get('id') is not None}
        old_micro_bounds = self._backup_old_micro_bounds()
        all_iids = self._get_all_ordered_iids()

        global_pitch_cache = None
        from modules.audio_core import extract_f0

        for iid in all_iids:
            item = self.items[iid]
            if iid not in mapped_segs:
                self._mark_item_missing(iid, item)
                continue

            seg = mapped_segs[iid]
            old_macro_s = item.get('macro_start')
            old_macro_e = item.get('macro_end')
            macro_unchanged = (
                old_macro_s is not None and old_macro_e is not None and
                abs(seg['start'] - old_macro_s) <= 1e-4 and
                abs(seg['end'] - old_macro_e) <= 1e-4
            )
            item['macro_start'] = seg['start']
            item['macro_end'] = seg['end']

            if not item.get('snd'):
                item['snd'] = self.pending_long_snd

            syls = split_into_syllables(item.get('label', ''))
            can_reuse_micro = (
                not seg.get('is_modified') and
                macro_unchanged and
                seg.get('old_id') is not None and
                seg.get('old_id') == item.get('id') and
                seg['old_id'] in old_micro_bounds and
                len(old_micro_bounds[seg['old_id']].get('chars_bounds', [])) == len(syls)
            )

            # VS-NEW-02: 仅当区段发生修改/顺延或缺少基频缓存时，才基于新的 macro 边界重新切片 pitch_data 并清空过期共振峰，保护未修改词条的橡皮擦精修数据
            if not can_reuse_micro or not item.get('pitch_data'):
                if global_pitch_cache is None and self.pending_long_snd is not None:
                    global_pitch_cache = extract_f0(self.pending_long_snd, self.last_params)
                if global_pitch_cache is not None:
                    total_dur = self.pending_long_snd.get_total_duration() if self.pending_long_snd else 999999.0
                    ms = max(0.0, float(item.get('macro_start', 0.0)) - 0.05)
                    me = float(item.get('macro_end', total_dur)) + 0.05
                    pxs = global_pitch_cache['xs']
                    pfreqs = global_pitch_cache['freqs']
                    mask = (pxs >= ms) & (pxs <= me)
                    item['pitch_data'] = {'xs': pxs[mask].copy(), 'freqs': pfreqs[mask].copy()}
                    item['_dirty_pitch'] = True
                    item.pop('pitch', None)
                    item['formant_data'] = None
                    item.pop('preview_f0', None)
                    item.pop('preview_formants', None)

            if can_reuse_micro:
                old_bounds = old_micro_bounds[seg['old_id']]
                item['start'] = old_bounds['start']
                item['end'] = old_bounds['end']
                item['inner_splits'] = copy.deepcopy(old_bounds['inner_splits'])
                item['chars_bounds'] = copy.deepcopy(old_bounds['chars_bounds'])
                if old_bounds.get('raw_start') is not None:
                    item['raw_start'] = old_bounds['raw_start']
                    item['raw_end'] = old_bounds['raw_end']
            elif seg.get('is_modified'):
                self._apply_modified_segment(item, seg)
                item['split_warnings'] = []
                item['split_confidence'] = 1.0
            else:
                self._apply_new_segment(item)

            if item['label'].endswith(" (缺失)"):
                item['label'] = item['label'].replace(" (缺失)", "")
            self.tree_panel.tree.item(iid, text=item['label'])
            self.tree_panel.update_item_icon(iid)

        if self.spectrogram_panel.current_item:
            self.spectrogram_panel.clear_canvas()

        self.current_macro_segments = [(seg['start'], seg['end']) for seg in segments]
        self.manual_segments = list(self.current_macro_segments)
        self.tree_panel.update_preview()
        self.mark_modified()

        deleted_msg = f"\n由于您删除了音频段，后续字表已自动向前顺延对齐。" if deleted_count else ""
        messagebox.showinfo("提示", f"手动微调已应用，时间边界已更新。{deleted_msg}")


    def _prepare_wordlist_payload(self, raw_text="", wordlist_document=None):
        if wordlist_document is None and raw_text:
            if looks_like_structured_wordlist_text(raw_text):
                try:
                    wordlist_document = build_document_from_structured_text(raw_text)
                except Exception:
                    wordlist_document = None
            elif looks_like_csv_wordlist_text(raw_text):
                try:
                    wordlist_document = build_document_from_csv_text(raw_text)
                except Exception:
                    wordlist_document = None
        if wordlist_document is not None:
            return flatten_wordlist_document(wordlist_document)
        groups, flat_words = parse_wordlist(raw_text or "")
        return groups, flat_words, [None] * len(flat_words)


    def _has_unified_wordlist(self):
        if not (getattr(self, 'switch_unified_wordlist', None) and self.switch_unified_wordlist.get()):
            return False
        return bool(getattr(self, 'global_wordlist_document', None) or getattr(self, 'global_wordlist_text', None))


    def _process_unified_wordlist(self, mode, match_mode='fuzzy'):
        doc = getattr(self, 'global_wordlist_document', None)
        if mode == 'batch':
            self.process_batch_with_wordlist(
                getattr(self, 'global_wordlist_text', ''),
                match_mode=getattr(self, 'global_wordlist_match_mode', match_mode),
                wordlist_document=doc,
            )
        else:
            self.process_long_with_wordlist(getattr(self, 'global_wordlist_text', ''), wordlist_document=doc)


    def process_long_with_wordlist(self, raw_text, wordlist_document=None):
        groups, flat_words, metadata_records = self._prepare_wordlist_payload(raw_text, wordlist_document)
        if not flat_words: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在处理长音频..."))
            self.root.after(0, self.tree_panel.clear_all)

            snd = self.pending_long_snd
            global_pitch_data = extract_f0(snd, self.last_params)
            total = len(flat_words)

            if hasattr(self, 'manual_segments') and self.manual_segments:
                macro_segments = self.manual_segments
            else:
                macro_segments = macroscopic_vad(snd, expected_count=total)

            self.current_macro_segments = macro_segments.copy()
            results = []

            # 准备参数
            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()
            pitch_xs = global_pitch_data['xs']
            pitch_freqs = global_pitch_data['freqs']

            # 构建任务数据
            tasks = []
            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    metadata_record = metadata_records[word_idx] if word_idx < len(metadata_records) else None
                    if word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]

                        # 提前提取小段音频的数据和采样率
                        valid_ms = max(0, ms)
                        valid_me = min(snd.get_total_duration(), me)
                        if valid_me > valid_ms:
                            part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                            snd_values = part.values
                            snd_sf = part.sampling_frequency

                            # 性能优化：切片 Pitch 数组
                            idx_start = np.searchsorted(pitch_xs, valid_ms)
                            idx_end = np.searchsorted(pitch_xs, valid_me)
                            sliced_xs = pitch_xs[idx_start:idx_end]
                            sliced_freqs = pitch_freqs[idx_start:idx_end]

                            tasks.append({
                                'word': word, 'group': grp['group'], 'ms': ms, 'me': me,
                                'snd_values': snd_values, 'snd_sf': snd_sf,
                                'pitch_xs': sliced_xs, 'pitch_freqs': sliced_freqs,
                                'missing': False,
                                'metadata_record': metadata_record
                            })
                        else:
                            tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'metadata_record': metadata_record})
                        word_idx += 1
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'metadata_record': metadata_record})

            # 多进程执行（优先复用常驻进程池）
            executor = getattr(self, 'executor', None)
            own_executor = False
            if executor is None or getattr(executor, '_broken', False) or getattr(executor, '_shutdown_thread', False):
                executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
                own_executor = True
            try:
                futures = {}
                for idx, task in enumerate(tasks):
                    if not task.get('missing'):
                        f = executor.submit(
                            long_process_worker,
                            task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                            task['ms'], task['me'], params, trim, task['word']
                        )
                        futures[f] = idx

                # 等待完成
                completed_count = 0
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    res = future.result()
                    if res.get('success'):
                        tasks[idx]['mis'] = res['mis']
                        tasks[idx]['mie'] = res['mie']
                        tasks[idx]['raw_s'] = res['raw_s']
                        tasks[idx]['raw_e'] = res['raw_e']
                        tasks[idx]['inner_splits'] = res.get('inner_splits', [])
                        if 'chars_bounds' in res: tasks[idx]['chars_bounds'] = res['chars_bounds']
                        tasks[idx]['preview_f0'] = res.get('preview_f0', [])
                        tasks[idx]['has_empty_data'] = res.get('has_empty_data', False)
                        tasks[idx]['split_warnings'] = res.get('split_warnings', [])
                        tasks[idx]['split_confidence'] = res.get('split_confidence', 1.0)
                        tasks[idx]['formant_data'] = res.get('formant_data')
                        tasks[idx]['preview_formants'] = res.get('preview_formants')
                    else:
                        tasks[idx]['missing'] = True # fallback

                    completed_count += 1
                    if completed_count % 10 == 0 or completed_count == len(futures):
                        self.root.after(0, lambda v=completed_count/len(futures) if len(futures) else 1: self.set_progress(v))
            finally:
                if own_executor:
                    executor.shutdown(wait=False)

            results = tasks

            def finalize():
                for idx, res in enumerate(results):
                    gid = self.tree_panel.ensure_group(res['group'])
                    if not res.get('missing'):
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'], tags=('item',), image=img)
                        win_s = max(0.0, float(res['ms']) - 0.05)
                        win_e = float(res['me']) + 0.05
                        p_mask = (pitch_xs >= win_s) & (pitch_xs <= win_e)
                        item_pitch_data = {'xs': pitch_xs[p_mask].copy(), 'freqs': pitch_freqs[p_mask].copy()}
                        item_data = {
                            'label': res['word'], 'group': res['group'], 'snd': snd, 'pitch_data': item_pitch_data,
                            '_dirty_pitch': True,
                            '_dirty_formant': True if res.get('formant_data') is not None else False,
                            'macro_start': res['ms'], 'macro_end': res['me'],
                            'start': res['mis'], 'end': res['mie'],
                            'inner_splits': res.get('inner_splits', []),
                            'chars_bounds': res.get('chars_bounds', []),
                            'raw_start': res.get('raw_s', res['mis']), 'raw_end': res.get('raw_e', res['mie']),
                            'pitch_floor': params['pitch_floor'],
                            'pitch_ceiling': params['pitch_ceiling'],
                            'voicing_threshold': params['voicing_threshold'],
                            'analysis_mode': params.get('analysis_mode', 'f0'),
                            'formant_data': res.get('formant_data'),
                            'preview_formants': res.get('preview_formants'),
                            'formant_max_hz': params.get('formant_max_hz'),
                            'formant_count': params.get('formant_count'),
                            'formant_window_length': params.get('formant_window_length'),
                            'formant_pre_emphasis': params.get('formant_pre_emphasis'),
                            'formant_sample_strategy': params.get('formant_sample_strategy'),
                            'is_user_specified_structure': '/' in res['word'],
                            'split_warnings': res.get('split_warnings', []),
                            'split_confidence': res.get('split_confidence', 1.0),
                            'preview_f0': res.get('preview_f0', []),
                            'has_empty_data': res.get('has_empty_data', False),
                            'import_index': idx
                        }
                        self.items[iid] = apply_record_metadata(item_data, res.get('metadata_record'))
                        self.tree_panel.update_item_icon(iid)
                    else:
                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res['word'] + " (缺失)", tags=('item',))
                        item_data = {'label': res['word'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': [], 'import_index': idx}
                        self.items[iid] = apply_record_metadata(item_data, res.get('metadata_record'))

                self.stop_loading("长音频切分完成")
                self.tree_panel.select_first_item()
                if hasattr(self, 'manual_segments'): self.manual_segments = None
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()


    def load_batch_audio(self):
        if self._has_active_chart_dialog():
            messagebox.showwarning("提示", "图表编辑器已打开，修改图表期间禁止导入音频文件。")
            return
        paths = filedialog.askopenfilenames(filetypes=[("Audio Files", "*.wav *.mp3")])
        if not paths: return
        self.pending_batch_paths = list(paths)
        self.lbl_batch_files.configure(text=f"已选 {len(paths)} 个文件", text_color="#2563EB")
        self.lbl_status.configure(text="独立音频就绪，正在后台分析...", text_color="#10B981")
        self.mark_modified()
        self.start_background_batch_processing(paths)
        if self._has_unified_wordlist():
            self.root.after(100, lambda: self._process_unified_wordlist('batch', match_mode='fuzzy'))


    def start_background_batch_processing(self, paths):
        def run():
            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()
            paths_to_process = [p for p in paths if self._get_audio_cache(p, params) is None]
            if not paths_to_process:
                self.root.after(0, lambda: self.lbl_status.configure(text="后台分析完成", text_color="#10B981"))
                return

            total = len(paths_to_process)
            self.root.after(0, lambda: self.start_loading(f"正在后台预分析 {total} 个音频..."))

            futures = {self.executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                p = futures[future]
                try:
                    self._set_audio_cache(p, future.result(), params)
                except Exception as e:
                    self._set_audio_cache(p, {'success': False, 'error': str(e), 'path': p}, params)

                self.root.after(0, lambda v=(i+1)/total: self.set_progress(v))

            self.root.after(0, lambda: self.stop_loading("后台分析完成"))
        threading.Thread(target=run, daemon=True).start()


    def process_batch_direct(self):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件")

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行批量提取..."))
            self.root.after(0, self.tree_panel.clear_all)

            total = len(self.pending_batch_paths)
            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()

            results = []
            futures = {}
            executor = getattr(self, 'executor', None)
            own_executor = False
            if executor is None or getattr(executor, '_broken', False) or getattr(executor, '_shutdown_thread', False):
                executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
                own_executor = True
            for i, p in enumerate(self.pending_batch_paths):
                cached = self._get_audio_cache(p, params)
                if cached is not None:
                    results.append((i, cached))
                else:
                    futures[executor.submit(batch_process_worker, p, params, trim)] = i

            if futures:
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self._set_audio_cache(self.pending_batch_paths[orig_idx], res, params)
                        results.append((orig_idx, copy.deepcopy(res)))
                    except Exception as e: print(f"Error: {e}")

                    if i % 2 == 0 or i == len(futures) - 1:
                        self.root.after(0, lambda v=(len(results))/total: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))

            executor.shutdown(wait=False)

            def finalize():
                results.sort(key=lambda x: x[0])
                gid = self.tree_panel.ensure_group("独立文件")
                for i, (orig_idx, res) in enumerate(results):
                    if res.get('success'):
                        res['group'] = "独立文件"
                        res['pitch_floor'] = params['pitch_floor']
                        res['pitch_ceiling'] = params['pitch_ceiling']
                        res['voicing_threshold'] = params['voicing_threshold']
                        res['import_index'] = i
                        self._stamp_formant_params_on_item(res, params)
                        iid = f"batch_{res['label']}_{id(res)}"
                        self.items[iid] = copy.deepcopy(res)
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',), image=img)
                        self.tree_panel.update_item_icon(iid)
                    else:
                        lbl = res.get('label') or os.path.splitext(os.path.basename(res.get('path', '')))[0] or f"条目_{i+1}"
                        iid = f"missing_{lbl}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=f"{lbl} (失败)", tags=('item',))
                        self.items[iid] = {'label': lbl, 'group': "独立文件", 'snd': None, 'start': None, 'end': None, 'inner_splits': [], 'error': res.get('error', '')}

                self.set_status(f"批量并行提取完成 ({len(results)}/{total})")
                self.stop_loading()
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()


    def _build_batch_tasks(self, groups, match_mode, metadata_records=None):
        tasks = []
        metadata_records = metadata_records or []
        record_idx = 0
        if match_mode == 'fuzzy':
            # 自然排序：确保 1, 2, 10 的顺序
            def natural_sort_key(s):
                return [int(text) if text.isdigit() else text.lower()
                        for text in re.split('([0-9]+)', s)]

            sorted_paths = sorted(self.pending_batch_paths, key=natural_sort_key)
            used_indices = set()

            for grp in groups:
                group_name = grp['group']
                for word in grp['items']:
                    metadata_record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    idx = fuzzy_match_word_to_path(word, sorted_paths, used_indices=list(used_indices))
                    if idx is not None:
                        path = sorted_paths[idx]
                        used_indices.add(idx)
                        tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False, 'metadata_record': metadata_record})
                    else:
                        tasks.append({'word': word, 'group': group_name, 'missing': True, 'metadata_record': metadata_record})
                    record_idx += 1
        else:
            path_idx = 0
            for grp in groups:
                group_name = grp['group']
                for word in grp['items']:
                    metadata_record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    if path_idx < len(self.pending_batch_paths):
                        path = self.pending_batch_paths[path_idx]
                        tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False, 'metadata_record': metadata_record})
                        path_idx += 1
                    else:
                        tasks.append({'word': word, 'group': group_name, 'missing': True, 'metadata_record': metadata_record})
                    record_idx += 1
        return tasks


    def _process_batch_tasks(self, tasks, params, trim):
        results = [None] * len(tasks)
        futures = {}
        executor = getattr(self, 'executor', None)
        own_executor = False
        if executor is None or getattr(executor, '_broken', False) or getattr(executor, '_shutdown_thread', False):
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
            own_executor = True
        for i, t in enumerate(tasks):
            if t['missing']:
                results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
            else:
                path = t['path']
                cached = self._get_audio_cache(path, params)
                if cached is not None:
                    results[i] = {**cached, 'missing': False, 'group': t['group']}
                else:
                    futures[executor.submit(batch_process_worker, path, params, trim, tasks[i]['word'])] = i

        total_futures = len(futures) if futures else 1
        done_count = 0
        if futures:
            for future in concurrent.futures.as_completed(futures):
                orig_idx = futures[future]
                try:
                    res = future.result()
                    self._set_audio_cache(tasks[orig_idx]['path'], res, params)
                    results[orig_idx] = {**copy.deepcopy(res), 'missing': False, 'group': tasks[orig_idx]['group']}
                except Exception as e:
                    results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}

                done_count += 1
                self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
        else:
            self.root.after(0, lambda: self.set_progress(1.0))

        if own_executor:
            executor.shutdown(wait=False)
        return results


    def _fix_batch_mismatches(self, tasks, results):
        # 在后台线程补全字表不匹配时的蓝线修复（因为基于Cache抓取的可能是错误的）
        for i, res in enumerate(results):
            if res and not res.get('missing') and res.get('success'):
                word = tasks[i]['word']
                syls = split_into_syllables(word)
                cached_label = res.get('label', '')
                cached_syls = split_into_syllables(cached_label)
                if len(syls) > 1 and (len(cached_syls) != len(syls) or ('/' in word and cached_label != word)):
                    try:
                        snd = load_compatible_sound(res['path'])
                        meta = {}
                        p_data = res.get('pitch_data')
                        r_s = res.get('raw_start', res['start'])
                        r_e = res.get('raw_end', res['end'])
                        res['inner_splits'] = auto_split_inner_word(snd, r_s, r_e, len(syls), pitch_data=p_data, output_meta=meta)
                        from modules.audio_core import auto_split_to_chars_bounds
                        res['chars_bounds'] = auto_split_to_chars_bounds(snd, r_s, r_e, res['inner_splits'], len(syls), self.last_params, pitch_data=p_data)
                        if res['chars_bounds']:
                            res['start'] = res['chars_bounds'][0][0]
                            res['end'] = res['chars_bounds'][-1][1]
                        res['split_warnings'] = meta.get('split_warnings', [])
                        res['split_confidence'] = meta.get('split_confidence', 1.0)
                        res['has_empty_data'] = res.get('has_empty_data', False)
                    except Exception:
                        res['inner_splits'] = []
                        res['chars_bounds'] = [[res['start'], res['end']]]
                elif len(syls) <= 1:
                    res['inner_splits'] = []
                    res['chars_bounds'] = [[res['start'], res['end']]]
                    res['split_warnings'] = []
                    res['split_confidence'] = 1.0


    def _finalize_batch_processing(self, tasks, results, match_mode, params, total):
        matched_count = 0
        for i, res in enumerate(results):
            gid = self.tree_panel.ensure_group(res['group'])
            if not res.get('missing') and res.get('success'):
                res['group'] = tasks[i]['group']
                res['label'] = tasks[i]['word']
                res['import_index'] = i
                if 'pitch_floor' not in res:
                    res['pitch_floor'] = params['pitch_floor']
                    res['pitch_ceiling'] = params['pitch_ceiling']
                    res['voicing_threshold'] = params['voicing_threshold']
                self._stamp_formant_params_on_item(res, params)
                display = f"{res['label']} ← {os.path.basename(res['path'])}" if match_mode == 'fuzzy' else res['label']
                iid = f"batch_wl_{res['label']}_{id(res)}"

                has_empty = res.get('has_empty_data', False)
                img = self.tk_icons.get('warning', '') if has_empty else ''
                self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',), image=img)

                self.items[iid] = copy.deepcopy(apply_record_metadata(res, tasks[i].get('metadata_record')))
                res['is_user_specified_structure'] = '/' in res['label']
                self.tree_panel.update_item_icon(iid)
                matched_count += 1
            else:
                suffix = " (未匹配)" if match_mode == 'fuzzy' else " (缺失)"
                iid = f"missing_{res['label']}_{id(res)}"
                self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + suffix, tags=('item',))
                item_data = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': [], 'import_index': i}
                self.items[iid] = apply_record_metadata(item_data, tasks[i].get('metadata_record'))

        self.stop_loading(f"并行处理完成: {matched_count}/{total}")
        self.tree_panel.select_first_item()
        self._maybe_refresh_formants_after_import()


    def process_batch_with_wordlist(self, raw_text, match_mode='order', wordlist_document=None):
        groups, flat_words, metadata_records = self._prepare_wordlist_payload(raw_text, wordlist_document)
        if not flat_words: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行匹配独立音频..."))
            self.root.after(0, self.tree_panel.clear_all)
            total = len(flat_words)

            tasks = self._build_batch_tasks(groups, match_mode, metadata_records=metadata_records)

            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()

            results = self._process_batch_tasks(tasks, params, trim)

            self._fix_batch_mismatches(tasks, results)

            def finalize():
                self._finalize_batch_processing(tasks, results, match_mode, params, total)

            self.root.after(0, finalize)
        threading.Thread(target=run, daemon=True).start()


    def process_batch_with_textgrid(self, tg_paths):
        if not self.pending_batch_paths:
            return messagebox.showwarning("提示", "请先选择多个音频文件。")
        if not tg_paths: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行匹配并分析 TextGrid..."))
            self.root.after(0, self.tree_panel.clear_all)

            audio_paths = self.pending_batch_paths

            import re
            def natural_sort_key(s):
                return [int(text) if text.isdigit() else text.lower()
                        for text in re.split('([0-9]+)', s)]

            sorted_audios = sorted(audio_paths, key=natural_sort_key)
            sorted_tgs = sorted(tg_paths, key=natural_sort_key)

            matched_audio_to_tg = {}
            matched_tg_to_audio = {}

            # 1. Exact match
            tg_base_map = {os.path.splitext(os.path.basename(tp))[0].lower(): tp for tp in sorted_tgs}
            for ap in sorted_audios:
                abase = os.path.splitext(os.path.basename(ap))[0].lower()
                if abase in tg_base_map:
                    tp = tg_base_map[abase]
                    matched_audio_to_tg[ap] = tp
                    matched_tg_to_audio[tp] = ap

            # 2. Fuzzy match
            for ap in sorted_audios:
                if ap in matched_audio_to_tg: continue
                abase = os.path.splitext(os.path.basename(ap))[0].lower()
                for tp in sorted_tgs:
                    if tp in matched_tg_to_audio: continue
                    tbase = os.path.splitext(os.path.basename(tp))[0].lower()
                    if abase in tbase or tbase in abase:
                        matched_audio_to_tg[ap] = tp
                        matched_tg_to_audio[tp] = ap
                        break

            # 3. Order match
            remaining_audios = [ap for ap in sorted_audios if ap not in matched_audio_to_tg]
            remaining_tgs = [tp for tp in sorted_tgs if tp not in matched_tg_to_audio]
            for ap, tp in zip(remaining_audios, remaining_tgs):
                matched_audio_to_tg[ap] = tp
                matched_tg_to_audio[tp] = ap

            total = len(sorted_audios)
            results = [None] * total

            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()

            futures = {}
            for i, ap in enumerate(sorted_audios):
                tp = matched_audio_to_tg.get(ap)
                if tp:
                    futures[self.executor.submit(batch_process_worker_with_textgrid, ap, tp, params, trim)] = i
                else:
                    word_lbl = os.path.splitext(os.path.basename(ap))[0]
                    futures[self.executor.submit(batch_process_worker, ap, params, trim, word_lbl)] = i

            total_futures = len(futures) if futures else 1
            done_count = 0
            if futures:
                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        results[orig_idx] = res
                    except Exception as e:
                        ap = sorted_audios[orig_idx]
                        lbl = os.path.splitext(os.path.basename(ap))[0]
                        results[orig_idx] = {'label': lbl, 'group': '导入内容', 'success': False, 'error': str(e), 'path': ap}
                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))

            def finalize():
                self.tree_panel.clear_all()
                self.items.clear()
                matched_count = 0

                unique_groups = []
                for res in results:
                    if res:
                        g = res.get('group', '导入内容')
                        if g not in unique_groups:
                            unique_groups.append(g)

                for g in unique_groups:
                    if g not in self.tree_panel.project_groups:
                        self.tree_panel.project_groups.append(g)
                    iid_grp = f"group_node_{g}"
                    self.tree_panel.tree.insert("", tk.END, iid=iid_grp, text=g, tags=('group',), open=True)
                    self.tree_panel.group_nodes[g] = iid_grp

                for res in results:
                    if not res: continue
                    grp_name = res.get('group', '导入内容')
                    gid = self.tree_panel.group_nodes.get(grp_name)
                    if res.get('success'):
                        res['group'] = grp_name
                        if 'pitch_floor' not in res:
                            res['pitch_floor'] = params['pitch_floor']
                            res['pitch_ceiling'] = params['pitch_ceiling']
                            res['voicing_threshold'] = params['voicing_threshold']
                        self._stamp_formant_params_on_item(res, params)

                        tp = matched_audio_to_tg.get(res['path'])
                        if tp:
                            display = f"{res['label']} ← {os.path.basename(tp)}"
                        else:
                            display = res['label']

                        iid = f"batch_tg_{res['label']}_{id(res)}"
                        has_empty = res.get('has_empty_data', False)
                        img = self.tk_icons.get('warning', '') if has_empty else ''
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=display, tags=('item',), image=img)
                        self.items[iid] = res
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        lbl = res.get('label') or os.path.splitext(os.path.basename(res.get('path', '')))[0] or "未知条目"
                        iid = f"missing_{lbl}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=lbl + " (失败)", tags=('item',))
                        self.items[iid] = {'label': lbl, 'group': grp_name, 'snd': None, 'start': None, 'end': None, 'inner_splits': [], 'error': res.get('error', '')}

                self.stop_loading(f"TextGrid 导入并匹配完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)

        threading.Thread(target=run, daemon=True).start()


    def process_long_with_textgrid(self, tg_intervals):
        if not tg_intervals: return

        def run():
            self.root.after(0, lambda: self.start_loading("正在并行分析 TextGrid 音段..."))
            self.root.after(0, self.tree_panel.clear_all)

            snd = self.pending_long_snd
            # Compute global pitch once
            global_pitch = snd.to_pitch_ac(time_step=None, pitch_floor=self.last_params['pitch_floor'], pitch_ceiling=self.last_params['pitch_ceiling'], voicing_threshold=self.last_params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)

            total = len(tg_intervals)
            results = []

            params = self._build_worker_params()
            trim = self.switch_trim_silence.get()
            pitch_xs = global_pitch.xs()
            pitch_freqs = global_pitch.selected_array['frequency']

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

                    import numpy as np
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

            import concurrent.futures
            from modules.audio_core import process_single_long_word

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        results.append({'label': t['word'], 'group': t['group'], 'success': False, 'missing': True})
                        continue

                    future = executor.submit(
                        process_single_long_word,
                        t['snd_values'], t['snd_sf'], t['word'], t['ms'], t['me'],
                        params, trim, t['sliced_xs'], t['sliced_freqs'], t['ref_splits'], t['ref_chars_bounds']
                    )
                    futures[future] = (i, t['group'], t['word'])

                temp_results = [None] * len(tasks)
                for future in concurrent.futures.as_completed(futures):
                    idx, grp, w = futures[future]
                    try:
                        res = future.result()
                        res['group'] = grp
                        res['missing'] = False
                        temp_results[idx] = res
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error(f"处理 '{w}' 时出错: {e}", exc_info=True)
                        temp_results[idx] = {'label': w, 'group': grp, 'success': False, 'missing': True}

            # Override macro_segments for next workflow
            self.current_macro_segments = [(t['ms'], t['me']) for t in tasks if not t.get('missing')]

            def finalize():
                self.tree_panel.clear_all()
                self.items.clear()
                matched_count = 0

                # Pre-register the groups from the tasks
                unique_groups = []
                for t in tasks:
                    g = t.get('group', '导入内容')
                    if g not in unique_groups:
                        unique_groups.append(g)

                for g in unique_groups:
                    if g not in self.tree_panel.project_groups:
                        self.tree_panel.project_groups.append(g)
                    iid_grp = f"group_node_{g}"
                    self.tree_panel.tree.insert("", tk.END, iid=iid_grp, text=g, tags=('group',), open=True)
                    self.tree_panel.group_nodes[g] = iid_grp

                for idx, res in enumerate(temp_results):
                    gid = self.tree_panel.group_nodes.get(res['group'])
                    if res and res.get('success'):
                        iid = f"item_{res['label']}_{id(res)}"
                        res['snd'] = self.pending_long_snd
                        task_t = tasks[idx]
                        macro_s = task_t.get('ms', res.get('start', 0.0))
                        macro_e = task_t.get('me', res.get('end', snd.get_total_duration()))
                        res['macro_start'] = macro_s
                        res['macro_end'] = macro_e
                        win_s = max(0.0, float(macro_s) - 0.05)
                        win_e = float(macro_e) + 0.05
                        mask = (pitch_xs >= win_s) & (pitch_xs <= win_e)
                        res['pitch_data'] = {
                            'xs': pitch_xs[mask].copy(),
                            'freqs': pitch_freqs[mask].copy()
                        }
                        res['_dirty_pitch'] = True
                        res['pitch'] = global_pitch
                        res['pitch_floor'] = params['pitch_floor']
                        res['pitch_ceiling'] = params['pitch_ceiling']
                        res['voicing_threshold'] = params['voicing_threshold']
                        self._stamp_formant_params_on_item(res, params)
                        res['import_index'] = idx

                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'], tags=('item',))
                        self.items[iid] = res
                        self.tree_panel.update_item_icon(iid)
                        matched_count += 1
                    else:
                        iid = f"missing_{res['label']}_{id(res)}"
                        self.tree_panel.tree.insert(gid, tk.END, iid=iid, text=res['label'] + " (失败)", tags=('item',))
                        self.items[iid] = {'label': res['label'], 'group': res['group'], 'snd': None, 'start': None, 'end': None, 'inner_splits': [], 'import_index': idx}

                self.stop_loading(f"TextGrid 导入完成: {matched_count}/{total}")
                self.tree_panel.select_first_item()
                self._maybe_refresh_formants_after_import()

            self.root.after(0, finalize)

        import threading
        threading.Thread(target=run, daemon=True).start()


