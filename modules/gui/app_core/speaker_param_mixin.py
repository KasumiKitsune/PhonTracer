"""Speaker & Param Mixin: 发音人管理、参数校验联动、共振峰点采样与模式切换."""
import os
import copy
import threading
import numpy as np
import tkinter as tk
from tkinter import messagebox

from modules.audio_core import load_compatible_sound, extract_f0, auto_split_inner_word
from modules.data_utils import split_into_syllables
from modules.gui.app_core.legacy_text_dialog import RenameSpeakerDialog


class AppSpeakerParamMixin:
    """提供发音人增删改、声学参数联动校验、分析模式切换与共振峰采样."""

    def on_tree_item_selected(self, iid):
        item = self.items[iid]

        if item.get('snd') and (item.get('pitch_data') or item.get('pitch')):
            self._sync_ui_and_plot(item)
            return

        long_snd = getattr(self, 'long_sound', None) or getattr(self, 'pending_long_snd', None)
        has_path = bool(item.get('path') and os.path.exists(item['path']))
        has_long = (long_snd is not None) and (item.get('macro_start') is not None)

        if not has_path and not has_long:
            return

        def run():
            try:
                self.root.after(0, lambda: self.set_status(f"正在读取音频: {item['label']}...", "#2563EB", "status_loading"))

                if has_path:
                    snd = load_compatible_sound(item['path'])
                    pitch_data = extract_f0(snd, self.last_params)
                elif has_long:
                    snd = long_snd
                    total_dur = snd.get_total_duration()
                    macro_start = float(item.get('macro_start', 0.0))
                    macro_end = float(item.get('macro_end', total_dur))
                    ms = max(0.0, macro_start - 0.05)
                    me = min(total_dur, macro_end + 0.05)
                    if me > ms and total_dur > (me - ms) + 0.5:
                        part_snd = snd.extract_part(from_time=ms, to_time=me)
                        part_pitch = extract_f0(part_snd, self.last_params)
                        pitch_data = {
                            'xs': (part_pitch['xs'] + ms).copy(),
                            'freqs': part_pitch['freqs'].copy(),
                            'engine': part_pitch.get('engine', 'praat')
                        }
                    else:
                        full_pitch = extract_f0(snd, self.last_params)
                        xs = full_pitch['xs']
                        freqs = full_pitch['freqs']
                        mask = (xs >= ms) & (xs <= me)
                        pitch_data = {'xs': xs[mask].copy(), 'freqs': freqs[mask].copy()}
                else:
                    return

                def done():
                    item['snd'] = snd
                    item['pitch_data'] = pitch_data
                    item['_dirty_pitch'] = True
                    if 'pitch' in item:
                        del item['pitch']
                    item['pitch_floor'] = self.last_params['pitch_floor']
                    item['pitch_ceiling'] = self.last_params['pitch_ceiling']
                    item['voicing_threshold'] = self.last_params.get('voicing_threshold', 0.25)

                    self.set_status("就绪", "#10B981", "status_success")
                    self._sync_ui_and_plot(item)

                self.root.after(0, done)
            except Exception as e:
                error_message = f"加载失败: {e}"
                self.root.after(0, lambda msg=error_message: self.set_status(msg, "#EF4444", "status_error"))

        threading.Thread(target=run, daemon=True).start()


    def _sync_ui_and_plot(self, item):
        # 同步侧边栏 UI 参数为当前选中项的独立参数（所见即所得）
        def _set_entry_if_changed(entry, target_str):
            if entry.get() != target_str:
                entry.delete(0, tk.END)
                entry.insert(0, target_str)
            entry._last_val = target_str

        if hasattr(self, 'entry_pitch_floor'):
            pitch_floor = item.get('pitch_floor', self.last_params.get('pitch_floor', 75))
            _set_entry_if_changed(self.entry_pitch_floor, str(int(pitch_floor)))
        if hasattr(self, 'entry_pitch_ceiling'):
            pitch_ceiling = item.get('pitch_ceiling', self.last_params.get('pitch_ceiling', 600))
            _set_entry_if_changed(self.entry_pitch_ceiling, str(int(pitch_ceiling)))
        if hasattr(self, 'entry_voicing_threshold'):
            voicing_threshold = item.get('voicing_threshold', self.last_params.get('voicing_threshold', 0.25))
            _set_entry_if_changed(self.entry_voicing_threshold, str(float(voicing_threshold)))
        if hasattr(self, 'entry_formant_max_hz'):
            formant_max_hz = item.get('formant_max_hz', self.last_params.get('formant_max_hz', 5500.0))
            _set_entry_if_changed(self.entry_formant_max_hz, str(float(formant_max_hz)))
        if hasattr(self, 'entry_formant_count'):
            formant_count = item.get('formant_count', self.last_params.get('formant_count', 5))
            _set_entry_if_changed(self.entry_formant_count, str(int(formant_count)))
        if hasattr(self, 'entry_formant_window_length'):
            formant_window_length = item.get('formant_window_length', self.last_params.get('formant_window_length', 0.025))
            _set_entry_if_changed(self.entry_formant_window_length, f"{float(formant_window_length):.3f}")
        if hasattr(self, 'entry_formant_pre_emphasis'):
            formant_pre_emphasis = item.get('formant_pre_emphasis', self.last_params.get('formant_pre_emphasis', 50.0))
            _set_entry_if_changed(self.entry_formant_pre_emphasis, str(float(formant_pre_emphasis)))

        if hasattr(self, 'option_formant_sample_strategy'):
            strategy = item.get('formant_sample_strategy', self.last_params.get('formant_sample_strategy', '整段11点'))
            self.option_formant_sample_strategy.set(strategy)

        if hasattr(self, 'switch_show_f3'):
            show_f3 = item.get('show_f3', self.last_params.get('show_f3', False))
            self.var_show_f3.set(show_f3)
            if show_f3:
                self.switch_show_f3.select()
            else:
                self.switch_show_f3.deselect()

        self.spectrogram_panel.load_item(item)


    def on_spectrogram_time_changed(self, item):
        # Time has been manually changed. We need to clear the cached preview_f0
        # so that _check_item_has_empty_data in project_tree will properly recalculate
        # and not rely on stale preview_f0 values.
        if 'preview_f0' in item:
            item.pop('preview_f0')
        if 'preview_formants' in item:
            item.pop('preview_formants')
        if 'has_empty_data' in item:
            item.pop('has_empty_data')

        self.mark_modified()
        self.tree_panel.update_preview()
        cur_iid = getattr(self.spectrogram_panel, 'current_item_iid', None) if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel else None
        if cur_iid and self.items.get(cur_iid) is item:
            self.tree_panel.update_item_icon(cur_iid)
        else:
            for iid, it in list(self.items.items()):
                if it is item:
                    self.tree_panel.update_item_icon(iid)
                    break


    def on_export_callback(self):
        self.tree_panel.export_project()


    def on_clear_canvas_callback(self):
        self.spectrogram_panel.clear_canvas()


    def on_spectrogram_auto_detect(self):
        item = self.spectrogram_panel.current_item
        if not item: return
        if self.spectrogram_panel:
            self.spectrogram_panel.discard_eraser_changes()
        snd = item['snd']
        mac_s, mac_e = self._get_item_base_boundaries(item)
        params_snapshot = dict(self.last_params)

        def run():
            try:
                self.root.after(0, lambda: self.start_loading("正在智能识别..."))
                from modules.audio_core import extract_f0, extract_formants, auto_split_to_chars_bounds

                total_dur = snd.get_total_duration()
                is_long_slice = total_dur > (mac_e - mac_s) + 1.0 and mac_e > mac_s
                if is_long_slice:
                    seg_start = max(0.0, mac_s - 0.15)
                    seg_end = min(total_dur, mac_e + 0.15)
                    work_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)
                else:
                    seg_start = 0.0
                    work_snd = snd

                # 重新提取 F0 / Formant，以还原橡皮擦抹去的数据点
                try:
                    p_data = extract_f0(work_snd, params_snapshot)
                    if seg_start > 0.0:
                        p_data['xs'] = p_data['xs'] + seg_start
                    item['pitch_data'] = p_data
                    item['_dirty_pitch'] = True
                    if 'pitch' in item:
                        del item['pitch']
                except Exception:
                    pass
                try:
                    f_data = extract_formants(work_snd, params_snapshot)
                    if seg_start > 0.0:
                        f_data['xs'] = f_data['xs'] + seg_start
                    item['formant_data'] = f_data
                    item['_dirty_formant'] = True
                    self._stamp_formant_params_on_item(item, params_snapshot)
                except Exception:
                    pass
                item.pop('preview_f0', None)
                item.pop('preview_formants', None)
                item.pop('has_empty_data', None)

                pitch = item.get('pitch_data', item.get('pitch'))
                mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(snd, pitch, mac_s, mac_e)

                label = item['label'].replace(" (缺失)", "")
                syls = split_into_syllables(label)
                split_warnings = []
                split_confidence = 1.0
                if len(syls) > 1:
                    meta = {}
                    inner_splits = auto_split_inner_word(snd, raw_s, raw_e, len(syls), pitch_data=pitch, output_meta=meta)
                    split_warnings = meta.get('split_warnings', [])
                    split_confidence = meta.get('split_confidence', 1.0)
                    chars_bounds = auto_split_to_chars_bounds(snd, raw_s, raw_e, inner_splits, len(syls), params_snapshot, pitch_data=pitch)
                    if chars_bounds:
                        mic_s = chars_bounds[0][0]
                        mic_e = chars_bounds[-1][1]
                else:
                    inner_splits = []
                    chars_bounds = [[mic_s, mic_e]]

                def update_ui():
                    item['start'] = mic_s
                    item['end'] = mic_e
                    item['raw_start'] = raw_s
                    item['raw_end'] = raw_e
                    item['inner_splits'] = inner_splits
                    item['chars_bounds'] = chars_bounds
                    item['split_warnings'] = split_warnings
                    item['split_confidence'] = split_confidence
                    item['has_empty_data'] = item.get('has_empty_data', False) or len(split_warnings) > 0
                    item['analysis_mode'] = self.last_params.get('analysis_mode', 'f0')

                    if item.get('snd') and item.get('formant_data'):
                        pts = int(self.last_params.get('pts', 11))
                        strategy = self.last_params.get('formant_sample_strategy', '整段11点')
                        show_f3 = bool(item.get("show_f3", self.last_params.get("show_f3", False)))
                        if show_f3:
                            _, preview_f1, preview_f2, preview_f3 = self.sample_formant_points_with_f3(item, pts, strategy)
                            item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                        else:
                            _, preview_f1, preview_f2 = self.sample_formant_points(item, pts, strategy)
                            item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}

                    if self.spectrogram_panel and self.spectrogram_panel.current_item is item:
                        self.spectrogram_panel.var_t_start.set(f"{mic_s:.3f}")
                        self.spectrogram_panel.var_t_end.set(f"{mic_e:.3f}")
                        self.spectrogram_panel.plot_item_spectrogram()
                        self.spectrogram_panel.update_ui_times()
                    else:
                        self.tree_panel.update_preview()
                        for iid, it in list(self.items.items()):
                            if it is item:
                                self.tree_panel.update_item_icon(iid)
                                break
                    self.mark_modified()
                    self.stop_loading("识别完成，可点击 F0/共振峰检测获取建议参数")
                self.root.after(0, update_ui)
            except Exception as e:
                error_message = f"识别失败: {e}"
                self.root.after(0, lambda msg=error_message: self.set_status(msg, "#EF4444", "status_error"))
                self.root.after(0, self.stop_loading)
        threading.Thread(target=run, daemon=True).start()


    def _on_slider_change(self, value, entry, key):
        if key == 'pts':
            ival = int(value)
            entry.delete(0, tk.END)
            entry.insert(0, str(ival))
        else:
            entry.delete(0, tk.END)
            entry.insert(0, f"{value:.2f}")
        self._debounce_apply_params(key)


    def _debounce_apply_params(self, key):
        if self.debounce_timer:
            self.root.after_cancel(self.debounce_timer)
        self.debounce_timer = self.root.after(400, lambda: self.apply_params_from_entry(key))


    def apply_params_from_entry(self, key):
        try:
            if key == 'pts':
                val = int(self.entry_points.get())
                if val != self.last_params['pts']:
                    self.last_params['pts'] = val
                    self.slider_pts.set(val)

                    def update_icons_bg():
                        self.root.after(0, lambda: self.start_loading("正在重新检测图标..."))
                        keys = list(self.items.keys())

                        def update_batch(start_idx):
                            end_idx = min(start_idx + 10, len(keys))
                            for iid in keys[start_idx:end_idx]:
                                self.tree_panel.update_item_icon(iid)

                            self.set_progress(end_idx / max(1, len(keys)))

                            if end_idx < len(keys):
                                self.root.after(5, lambda: update_batch(end_idx))
                            else:
                                self.tree_panel.update_preview()
                                self.stop_loading()

                        self.root.after(10, lambda: update_batch(0))
                    update_icons_bg()
            elif key == 'db':
                val = float(self.entry_drop_db.get())
                if val != self.last_params['db']:
                    self.last_params['db'] = val
                    self.slider_db.set(val)
                    self.recalculate_current_item()
            elif key == 'skip_front':
                val = float(self.entry_min_dur.get())
                if val != self.last_params['skip_front']:
                    self.last_params['skip_front'] = val
                    self.slider_dur.set(val)
                    self.recalculate_current_item()
            elif key == 'pitch_floor':
                val = int(self.entry_pitch_floor.get())
                if val != self.last_params['pitch_floor']:
                    self.last_params['pitch_floor'] = val
                    self.recalculate_current_item(recompute_pitch=True)
            elif key == 'pitch_ceiling':
                val = int(self.entry_pitch_ceiling.get())
                if val != self.last_params['pitch_ceiling']:
                    self.last_params['pitch_ceiling'] = val
                    self.recalculate_current_item(recompute_pitch=True)
        except Exception: pass


    def _make_audio_cache_key(self, path, params=None):
        if not path:
            return None
        p = params or self.last_params
        sig = (
            int(p.get('pitch_floor', 75)),
            int(p.get('pitch_ceiling', 600)),
            round(float(p.get('voicing_threshold', 0.25)), 4),
            round(float(p.get('formant_max_hz', 5500.0)), 1),
            int(p.get('formant_count', 5)),
            round(float(p.get('formant_window_length', 0.025)), 4),
            round(float(p.get('formant_pre_emphasis', 50.0)), 1),
            str(p.get('formant_sample_strategy', '整段11点')),
            str(p.get('analysis_mode', 'f0')),
            bool(self.switch_trim_silence.get() if hasattr(self, 'switch_trim_silence') else True),
        )
        return (os.path.abspath(path), sig)


    def _get_audio_cache(self, path, params=None):
        k = self._make_audio_cache_key(path, params)
        if k and k in self.audio_cache:
            return copy.deepcopy(self.audio_cache[k])
        return None


    def _set_audio_cache(self, path, res, params=None):
        k = self._make_audio_cache_key(path, params)
        if k:
            self.audio_cache[k] = copy.deepcopy(res)


    def on_add_speaker(self):
        idx = len(self.speaker_manager.get_all_speakers()) + 1
        while True:
            name = f"发音人 {idx}"
            if not any(s.name == name for s in self.speaker_manager.get_all_speakers()):
                break
            idx += 1
        new_speaker = self.speaker_manager.add_speaker(name)
        new_speaker.tab_mode = self.tabview.get()
        self._update_speaker_dropdown()
        self.speaker_option_var.set(new_speaker.name)
        self.on_speaker_changed(new_speaker.name)
        self.mark_modified()


    def on_rename_speaker(self):
        current_name = self.speaker_option_var.get()
        dialog = RenameSpeakerDialog(self.root, current_name=current_name)
        new_name = dialog.result
        if new_name and new_name.strip():
            new_name = new_name.strip()
            active_id = self.speaker_manager.active_speaker_id
            other_names = {s.name for s in self.speaker_manager.get_all_speakers() if s.id != active_id}
            if new_name in other_names:
                base = new_name
                cnt = 2
                while f"{base} ({cnt})" in other_names:
                    cnt += 1
                new_name = f"{base} ({cnt})"
            self.speaker_manager.rename_speaker(active_id, new_name)
            self._update_speaker_dropdown()
            self.speaker_option_var.set(new_name)
            self.mark_modified()


    def on_delete_speaker(self):
        if len(self.speaker_manager.speakers) <= 1:
            messagebox.showwarning("提示", "必须至少保留一个发音人。")
            return
        if messagebox.askyesno("确认", f"确定要删除发音人 '{self.active_speaker.name}' 吗？其所有数据将丢失。"):
            self.speaker_manager.remove_speaker(self.speaker_manager.active_speaker_id)
            self._update_speaker_dropdown()
            self.speaker_option_var.set(self.active_speaker.name)
            self.on_speaker_changed(self.active_speaker.name)
            self.mark_modified()


    def _update_speaker_dropdown(self):
        speakers = self.speaker_manager.get_all_speakers()
        seen = {}
        values = []
        self._speaker_dropdown_map = {}
        for s in speakers:
            c = seen.get(s.name, 0)
            seen[s.name] = c + 1
            display = s.name if c == 0 else f"{s.name} ({c + 1})"
            values.append(display)
            self._speaker_dropdown_map[display] = s.id
        self.speaker_dropdown.configure(values=values)


    def on_speaker_changed(self, selected_name):
        target_id = getattr(self, '_speaker_dropdown_map', {}).get(selected_name)
        if not target_id:
            for s in self.speaker_manager.get_all_speakers():
                if s.name == selected_name:
                    target_id = s.id
                    break
        if target_id and target_id in self.speaker_manager.speakers:
            if hasattr(self, 'active_speaker') and self.active_speaker:
                self.active_speaker.tab_mode = self.tabview.get()
                if hasattr(self, 'tree_panel') and hasattr(self.tree_panel, 'project_groups'):
                    self.active_speaker.project_groups = list(self.tree_panel.project_groups)
            self.speaker_manager.set_active_speaker(target_id)
            self._refresh_ui_for_speaker()


    def _refresh_ui_for_speaker(self):
        if hasattr(self.active_speaker, 'tab_mode'):
            try: self.tabview.set(self.active_speaker.tab_mode)
            except ValueError: pass
        self.slider_pts.set(self.last_params['pts'])
        self.entry_points.delete(0, tk.END)
        self.entry_points.insert(0, str(self.last_params['pts']))
        self.slider_db.set(self.last_params['db'])
        self.var_drop_db.set(str(self.last_params['db']))
        self.slider_dur.set(self.last_params['skip_front'])
        self.var_min_dur.set(f"{self.last_params['skip_front']:.2f}")
        self.entry_pitch_ceiling.delete(0, tk.END)
        self.entry_pitch_ceiling.insert(0, str(self.last_params['pitch_ceiling']))
        self.entry_pitch_floor.delete(0, tk.END)
        self.entry_pitch_floor.insert(0, str(self.last_params['pitch_floor']))
        self.entry_voicing_threshold.delete(0, tk.END)
        self.entry_voicing_threshold.insert(0, f"{self.last_params['voicing_threshold']:.2f}")
        if hasattr(self, 'mode_button') and self.mode_button:
            initial_mode = "声调/F0" if self.last_params.get('analysis_mode', 'f0') == 'f0' else "共振峰/F1-F2"
            self.mode_button.set(initial_mode)
            self._update_mode_button_text_colors()
            self.update_param_containers_visibility()

        if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
            self.entry_formant_max_hz.delete(0, tk.END)
            self.entry_formant_max_hz.insert(0, str(self.last_params.get('formant_max_hz', 5500.0)))
            self.entry_formant_count.delete(0, tk.END)
            self.entry_formant_count.insert(0, str(self.last_params.get('formant_count', 5)))
            self.entry_formant_window_length.delete(0, tk.END)
            self.entry_formant_window_length.insert(0, str(self.last_params.get('formant_window_length', 0.025)))
            self.entry_formant_pre_emphasis.delete(0, tk.END)
            self.entry_formant_pre_emphasis.insert(0, str(self.last_params.get('formant_pre_emphasis', 50.0)))
            self.option_formant_sample_strategy.set(self.last_params.get('formant_sample_strategy', '整段11点'))
            if hasattr(self, 'switch_show_f3'):
                show_f3 = self.last_params.get('show_f3', False)
                self.var_show_f3.set(show_f3)
                if show_f3:
                    self.switch_show_f3.select()
                else:
                    self.switch_show_f3.deselect()

        if hasattr(self, 'active_speaker') and self.active_speaker:
            if getattr(self.active_speaker, 'pending_long_snd', None):
                self.pending_long_snd = self.active_speaker.pending_long_snd
                self.long_sound = self.pending_long_snd
                for it in getattr(self, 'items', {}).values():
                    if it.get('snd') is None and not it.get('path'):
                        it['snd'] = self.pending_long_snd

        if self.pending_long_snd: self.lbl_long_file.configure(text="已加载音频", text_color="#2563EB")
        else: self.lbl_long_file.configure(text="未选择", text_color="#6B7280")
        if self.pending_batch_paths: self.lbl_batch_files.configure(text=f"已选 {len(self.pending_batch_paths)} 个文件", text_color="#2563EB")
        else: self.lbl_batch_files.configure(text="未选择", text_color="#6B7280")

        if hasattr(self, 'tree_panel'):
            self.tree_panel.items = self.items
            self.tree_panel.app_state_params = self.last_params
            self.tree_panel.clear_ui_only()

            spk_groups = getattr(self.active_speaker, 'project_groups', None)
            if spk_groups and isinstance(spk_groups, list):
                ordered_groups = list(spk_groups)
                for item in self.items.values():
                    g = item.get('group', '导入内容')
                    if g not in ordered_groups:
                        ordered_groups.append(g)
            else:
                seen_g = set()
                ordered_groups = []
                for item in self.items.values():
                    g = item.get('group', '导入内容')
                    if g not in seen_g:
                        seen_g.add(g)
                        ordered_groups.append(g)
                if not ordered_groups:
                    ordered_groups = ['导入内容']

            self.active_speaker.project_groups = ordered_groups
            for g in ordered_groups:
                self.tree_panel.ensure_group(g)

            self.tree_panel.rebuild_tree()

            if hasattr(self.active_speaker, 'last_selected_iid') and self.active_speaker.last_selected_iid in self.items:
                try:
                    iid_to_select = self.active_speaker.last_selected_iid
                    if not self.tree_panel.tree.exists(iid_to_select):
                        warning_iid = f"warning_{iid_to_select}"
                        if self.tree_panel.tree.exists(warning_iid):
                            iid_to_select = warning_iid
                    self.tree_panel.tree.selection_set(iid_to_select)
                    self.tree_panel.on_tree_select(None)
                except tk.TclError:
                    self.tree_panel.select_first_item()
            else:
                self.tree_panel.select_first_item()

        self.root.after(100, self._maybe_refresh_formants_after_import)


    def _build_worker_params(self):
        return {
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
            'show_f3': self.last_params.get('show_f3', False),
        }


    def _stamp_formant_params_on_item(self, item, params=None):
        params = params or self._build_worker_params()
        item['analysis_mode'] = params.get('analysis_mode', self.last_params.get('analysis_mode', 'f0'))
        item['formant_max_hz'] = params.get('formant_max_hz', self.last_params.get('formant_max_hz', 5500.0))
        item['formant_count'] = params.get('formant_count', self.last_params.get('formant_count', 5))
        item['formant_window_length'] = params.get('formant_window_length', self.last_params.get('formant_window_length', 0.025))
        item['formant_pre_emphasis'] = params.get('formant_pre_emphasis', self.last_params.get('formant_pre_emphasis', 50.0))
        item['formant_sample_strategy'] = params.get('formant_sample_strategy', self.last_params.get('formant_sample_strategy', '整段11点'))
        item['show_f3'] = params.get('show_f3', self.last_params.get('show_f3', False))


    def _maybe_refresh_formants_after_import(self):
        analysis_mode = self.last_params.get('analysis_mode', 'f0')
        if analysis_mode == 'formant':
            for item in self.items.values():
                if item.get('snd') and item.get('start') is not None and item.get('end') is not None and not item.get('formant_data'):
                    self.root.after(50, self.recalculate_all_formants)
                    return
        else:
            for item in self.items.values():
                if item.get('snd') and item.get('start') is not None and item.get('end') is not None and not item.get('pitch_data'):
                    self.root.after(50, lambda: self.recalculate_all_audio(recompute_pitch=True, only_pitch_changed=True))
                    return


    def _check_general_params(self):
        changed_algo = False
        new_pts = None
        try:
            new_db = float(self.var_drop_db.get())
            new_skip = float(self.var_min_dur.get())
            new_pts_val = int(self.entry_points.get())

            if new_db != self.last_params['db']:
                self.last_params['db'] = new_db
                changed_algo = True

            if new_skip != self.last_params['skip_front']:
                self.last_params['skip_front'] = new_skip
                changed_algo = True

            new_pts = new_pts_val
        except ValueError:
            pass
        return changed_algo, new_pts


    def _check_f0_params(self, curr_item):
        recompute_pitch = False
        if not (hasattr(self, 'entry_pitch_floor') and self.entry_pitch_floor):
            return recompute_pitch

        try:
            new_floor = int(self.entry_pitch_floor.get())
            if new_floor != self.last_params.get('pitch_floor', 75):
                self.last_params['pitch_floor'] = new_floor
                recompute_pitch = True
            elif curr_item and new_floor != curr_item.get('pitch_floor', self.last_params.get('pitch_floor', 75)):
                recompute_pitch = True

            new_ceiling = int(self.entry_pitch_ceiling.get())
            if new_ceiling != self.last_params.get('pitch_ceiling', 600):
                self.last_params['pitch_ceiling'] = new_ceiling
                recompute_pitch = True
            elif curr_item and new_ceiling != curr_item.get('pitch_ceiling', self.last_params.get('pitch_ceiling', 600)):
                recompute_pitch = True

            new_voicing = float(self.entry_voicing_threshold.get())
            if new_voicing != self.last_params.get('voicing_threshold', 0.25):
                self.last_params['voicing_threshold'] = new_voicing
                recompute_pitch = True
            elif curr_item and new_voicing != curr_item.get('voicing_threshold', self.last_params.get('voicing_threshold', 0.25)):
                recompute_pitch = True
        except ValueError:
            pass

        return recompute_pitch


    def _check_formant_params(self, curr_item):
        recompute_formant = False
        if not (hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz):
            return recompute_formant

        try:
            new_formant_max = float(self.entry_formant_max_hz.get())
            if new_formant_max != self.last_params.get('formant_max_hz', 5500.0):
                self.last_params['formant_max_hz'] = new_formant_max
                recompute_formant = True
            elif curr_item and new_formant_max != curr_item.get('formant_max_hz', self.last_params.get('formant_max_hz', 5500.0)):
                recompute_formant = True
        except ValueError: pass

        try:
            new_formant_count = int(self.entry_formant_count.get())
            if new_formant_count != self.last_params.get('formant_count', 5):
                self.last_params['formant_count'] = new_formant_count
                recompute_formant = True
            elif curr_item and new_formant_count != curr_item.get('formant_count', self.last_params.get('formant_count', 5)):
                recompute_formant = True
        except ValueError: pass

        try:
            new_formant_window = float(self.entry_formant_window_length.get())
            if new_formant_window != self.last_params.get('formant_window_length', 0.025):
                self.last_params['formant_window_length'] = new_formant_window
                recompute_formant = True
            elif curr_item and new_formant_window != curr_item.get('formant_window_length', self.last_params.get('formant_window_length', 0.025)):
                recompute_formant = True
        except ValueError: pass

        try:
            new_formant_pre = float(self.entry_formant_pre_emphasis.get())
            if new_formant_pre != self.last_params.get('formant_pre_emphasis', 50.0):
                self.last_params['formant_pre_emphasis'] = new_formant_pre
                recompute_formant = True
            elif curr_item and new_formant_pre != curr_item.get('formant_pre_emphasis', self.last_params.get('formant_pre_emphasis', 50.0)):
                recompute_formant = True
        except ValueError: pass

        return recompute_formant


    def on_param_change(self, event=None, recalculate_current=True):
        changed_algo, new_pts = self._check_general_params()

        curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item

        recompute_pitch = self._check_f0_params(curr_item)
        recompute_formant = self._check_formant_params(curr_item)

        need_recompute = (changed_algo or recompute_pitch or recompute_formant)
        if recalculate_current and need_recompute:
            if recompute_formant and not recompute_pitch and not changed_algo:
                self.recalculate_current_item(recompute_formant_only=True)
            else:
                self.recalculate_current_item(recompute_pitch=True)

        if new_pts is not None and new_pts != self.last_params.get('pts', -1):
            self.last_params['pts'] = new_pts
            for iid in list(self.items.keys()):
                self.tree_panel.update_item_icon(iid)
            self.tree_panel.update_preview()


    def on_trim_silence_toggle(self):
        self.recalculate_current_item(only_trim_silence=True)


    def on_formant_strategy_change(self, value):
        self.last_params['formant_sample_strategy'] = value
        self.on_param_change()


    def on_show_f3_toggle(self):
        val = bool(self.var_show_f3.get())
        self.last_params["show_f3"] = val
        curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
        if curr_item:
            curr_item["show_f3"] = val
        self.mark_modified()
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            self.spectrogram_panel.plot_item_spectrogram()


    def _mode_state_key(self, mode):
        return f"_mode_state_{mode}"


    @staticmethod
    def _finite_float_or_none(value):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(number):
            return None
        return number


    def _first_finite_item_value(self, item, keys, default=None):
        for key in keys:
            number = self._finite_float_or_none(item.get(key))
            if number is not None:
                return number
        return default


    def _get_valid_macro_bounds(self, item):
        macro_start = self._finite_float_or_none(item.get('macro_start'))
        macro_end = self._finite_float_or_none(item.get('macro_end'))
        if macro_start is None or macro_end is None or macro_end <= macro_start:
            return None
        return macro_start, macro_end


    def _get_item_base_boundaries(self, item):
        base_start = self._first_finite_item_value(item, ('macro_start', 'start', 'raw_start'), 0.0)
        base_end = self._first_finite_item_value(item, ('macro_end', 'end', 'raw_end'), base_start + 0.01)
        if base_end <= base_start:
            base_start = self._first_finite_item_value(item, ('start', 'raw_start'), base_start)
            base_end = self._first_finite_item_value(item, ('end', 'raw_end'), base_start + 0.01)
        if base_end <= base_start:
            base_end = base_start + 0.01
        return base_start, base_end


    def _save_item_mode_state(self, item, mode):
        fields = ('start', 'end', 'raw_start', 'raw_end', 'inner_splits', 'chars_bounds', 'split_warnings', 'split_confidence', 'is_manual_edited')
        state = {}
        for field in fields:
            if field in item:
                state[field] = copy.deepcopy(item[field])
        if state:
            item[self._mode_state_key(mode)] = state


    def _load_item_mode_state(self, item, mode):
        fields = ('start', 'end', 'raw_start', 'raw_end', 'inner_splits', 'chars_bounds', 'split_warnings', 'split_confidence', 'is_manual_edited')
        state = item.get(self._mode_state_key(mode))
        if state:
            for field in fields:
                if field in state:
                    item[field] = copy.deepcopy(state[field])
            item.pop('preview_f0', None)
            item.pop('preview_formants', None)
            item.pop('has_empty_data', None)
            item.pop('warnings', None)
            item['analysis_mode'] = mode
            return True
        return False


    def _reset_item_mode_boundaries(self, item, mode):
        base_start, base_end = self._get_item_base_boundaries(item)

        label = item.get('label', '').replace(" (缺失)", "")
        syls = split_into_syllables(label)
        syl_count = max(1, len(syls))
        splits = np.linspace(base_start, base_end, syl_count + 1).tolist()
        chars_bounds = [[splits[i], splits[i + 1]] for i in range(syl_count)]
        inner_splits = splits[1:-1] if syl_count > 1 else []

        item['start'] = chars_bounds[0][0]
        item['end'] = chars_bounds[-1][1]
        item['raw_start'] = item['start']
        item['raw_end'] = item['end']
        item['chars_bounds'] = chars_bounds
        item['inner_splits'] = inner_splits
        item['split_warnings'] = []
        item['split_confidence'] = 1.0
        item['is_manual_edited'] = False
        item['analysis_mode'] = mode
        item.pop('preview_f0', None)
        item.pop('preview_formants', None)
        item.pop('has_empty_data', None)
        item.pop('warnings', None)


    def on_analysis_mode_change(self, value):
        self.flush_eraser_changes()
        old_mode = self.last_params.get('analysis_mode', 'f0')
        mode = 'f0' if ("声调" in value or "F0" in value) else 'formant'
        if mode == old_mode:
            return
        self.last_params['analysis_mode'] = mode
        for item in self.items.values():
            if item.get('is_excluded', False):
                continue
            self._save_item_mode_state(item, old_mode)
            if not self._load_item_mode_state(item, mode):
                has_valid_bounds = (
                    item.get('start') is not None
                    and item.get('end') is not None
                    and item['end'] > item['start']
                )
                if item.get('is_manual_edited') or has_valid_bounds:
                    item['analysis_mode'] = mode
                    item.pop('preview_f0', None)
                    item.pop('preview_formants', None)
                    item.pop('has_empty_data', None)
                    item.pop('warnings', None)
                else:
                    self._reset_item_mode_boundaries(item, mode)
        self._update_mode_button_text_colors()
        self.update_param_containers_visibility()
        # Refresh current spectrogram panel erasure button text
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            self.spectrogram_panel.update_eraser_button_text()
        if mode == 'formant':
            self.recalculate_all_formants()
        else:
            if self.spectrogram_panel and self.spectrogram_panel.current_item:
                self.spectrogram_panel.plot_item_spectrogram()
                self.spectrogram_panel.update_ui_times()
            self.tree_panel.update_preview()
        self.mark_modified()


    def _update_tabview_colors_and_icons(self):
        try:
            current_tab = self.tabview.get()
            if hasattr(self.tabview, "_segmented_button") and hasattr(self.tabview._segmented_button, "_buttons_dict"):
                icons_map = {
                    "单条长音频": ("tab_single_white", "tab_single_dark"),
                    "多条独立音频": ("tab_batch_white", "tab_batch_dark"),
                }
                for name, btn in self.tabview._segmented_button._buttons_dict.items():
                    white_key, dark_key = icons_map.get(name, ("tab_single_white", "tab_single_dark"))
                    if name == current_tab:
                        btn.configure(
                            text_color="white",
                            image=self.icons.get(white_key) or self.icons.get("tab_single")
                        )
                    else:
                        btn.configure(
                            text_color=("#334155", "#E2E8F0"),
                            image=self.icons.get(dark_key) or self.icons.get("tab_batch")
                        )
        except Exception:
            pass


    def _update_mode_button_text_colors(self):
        current_val = self.mode_button.get()
        if hasattr(self.mode_button, "_buttons_dict"):
            for val, btn in self.mode_button._buttons_dict.items():
                if val == current_val:
                    btn.configure(text_color="white")
                else:
                    btn.configure(text_color=("#334155", "#E2E8F0"))


    def update_param_containers_visibility(self):
        mode = self.last_params.get('analysis_mode', 'f0')
        if mode == 'formant':
            self.f0_params_container.pack_forget()
            self.row_trim.pack_forget()
            self.row_export_rule.pack_forget()

            self.formant_params_container.pack(fill=tk.X)
            self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
            self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))
        else:
            self.formant_params_container.pack_forget()
            self.row_trim.pack_forget()
            self.row_export_rule.pack_forget()

            self.f0_params_container.pack(fill=tk.X)
            self.row_trim.pack(fill=tk.X, padx=15, pady=(10, 15))
            self.row_export_rule.pack(fill=tk.X, padx=15, pady=(0, 15))


    def sample_formant_points(self, item, pts=11, strategy='整段11点'):
        start = item['start']
        end = item['end']
        preview_times = np.linspace(start, end, pts)

        f_data = item.get('formant_data')
        if not f_data or 'xs' not in f_data or 'f1' not in f_data or 'f2' not in f_data:
            nan_list = [np.nan] * pts
            return preview_times, nan_list, nan_list

        xs = f_data['xs']
        f1_arr = f_data['f1']
        f2_arr = f_data['f2']

        if strategy == '中段均值':
            duration = end - start
            m_start = start + duration / 3.0
            m_end = start + 2.0 * duration / 3.0

            mask = (xs >= m_start) & (xs <= m_end)
            f1_slice = f1_arr[mask]
            f2_slice = f2_arr[mask]

            f1_vals = f1_slice[~np.isnan(f1_slice)]
            f2_vals = f2_slice[~np.isnan(f2_slice)]

            mean_f1 = np.nanmean(f1_vals) if len(f1_vals) > 0 else np.nan
            mean_f2 = np.nanmean(f2_vals) if len(f2_vals) > 0 else np.nan

            preview_f1 = [mean_f1] * pts
            preview_f2 = [mean_f2] * pts
        else:
            preview_f1 = []
            preview_f2 = []

            f1_valid_idx = np.where(~np.isnan(f1_arr))[0]
            f2_valid_idx = np.where(~np.isnan(f2_arr))[0]

            for t in preview_times:
                # F1
                if len(f1_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f1.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f1_valid_idx] - t))
                    if np.abs(xs[f1_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f1.append(np.nan)
                    else:
                        preview_f1.append(float(np.interp(t, xs[f1_valid_idx], f1_arr[f1_valid_idx])))
                # F2
                if len(f2_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f2.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f2_valid_idx] - t))
                    if np.abs(xs[f2_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f2.append(np.nan)
                    else:
                        preview_f2.append(float(np.interp(t, xs[f2_valid_idx], f2_arr[f2_valid_idx])))

        return preview_times.tolist(), preview_f1, preview_f2


    def sample_formant_points_with_f3(self, item, pts=11, strategy='整段11点'):
        from modules.data_utils import sample_formant_points_by_bounds_extended
        start = item['start']
        end = item['end']
        sampled = sample_formant_points_by_bounds_extended(
            item,
            [[start, end]],
            pts=pts,
            strategy=strategy,
            include_f3=True,
        )
        return sampled["times"], sampled["f1"], sampled["f2"], sampled["f3"]


