"""Acoustic Probe Mixin: 基频范围智能探测与共振峰多档位参数全局推荐."""
import os
import threading
import tkinter as tk
from tkinter import messagebox
# pyrefly: ignore [missing-import]
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault("parselmouth", parselmouth)
    _sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import numpy as np

from modules.audio_core import load_compatible_sound


class AppAcousticProbeMixin:
    """提供发音人基频范围自适应探测与共振峰提取参数全局搜索推荐."""

    def on_detect_f0_clicked(self):
        if not self.items:
            messagebox.showwarning("提示", "请先导入音频文件以进行检测。")
            return

        curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
        detect_current_only = False
        if curr_item:
            resp = messagebox.askyesnocancel("检测范围", "是否仅针对当前选中的音频条目进行 F0 参数估算？\n\n- 选择【是】：仅针对当前条目估算本条建议参数\n- 选择【否】：对项目内的代表性音频进行全局参数估算\n- 选择【取消】：取消本次操作")
            if resp is None:
                return
            detect_current_only = resp

        if detect_current_only:
            snd = curr_item.get('snd')
            if snd is None and curr_item.get('path'):
                try:
                    import parselmouth
                    snd = load_compatible_sound(curr_item['path'])
                except Exception as e:
                    messagebox.showerror("错误", f"加载当前项音频失败: {e}")
                    return
            if snd is None:
                messagebox.showwarning("提示", "未找到已加载的音频。")
                return

            self.start_loading("正在估算本条 F0 分布...")

            params_temp = {
                'pitch_floor': 50,
                'pitch_ceiling': 700,
                'voicing_threshold': self.last_params.get('voicing_threshold', 0.25),
                'very_accurate': False
            }

            def run_current_detection():
                try:
                    macro_bounds = self._get_valid_macro_bounds(curr_item)
                    is_long = macro_bounds is not None and snd.get_total_duration() > 15.0
                    if is_long:
                        m_s, m_e = macro_bounds
                        padding = 0.1
                        from_t = max(0.0, m_s - padding)
                        to_t = min(snd.get_total_duration(), m_e + padding)
                        part = snd.extract_part(from_time=from_t, to_time=to_t)
                        pitch_data = extract_f0(part, params_temp)
                        pitch_times = pitch_data['xs'] + from_t
                        pitch_freqs = pitch_data['freqs']
                        mask = (pitch_times >= m_s) & (pitch_times <= m_e)
                        item_times = pitch_times[mask]
                        item_freqs = pitch_freqs[mask]
                        all_stable_f0 = self.extract_stable_f0_values(item_times, item_freqs)
                    else:
                        pitch_data = extract_f0(snd, params_temp)
                        all_stable_f0 = self.extract_stable_f0_values(pitch_data['xs'], pitch_data['freqs'])

                    if len(all_stable_f0) < 20:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("有效语音帧不足", "当前条目有效有声数据太少（少于 20 帧），无法提取稳定 F0。"))
                        return

                    import numpy as np
                    p5 = float(np.percentile(all_stable_f0, 5))
                    p10 = float(np.percentile(all_stable_f0, 10))
                    p50 = float(np.percentile(all_stable_f0, 50))
                    p90 = float(np.percentile(all_stable_f0, 90))
                    p95 = float(np.percentile(all_stable_f0, 95))

                    med = p50
                    w = (med - 120.0) / 120.0
                    w = max(0.0, min(1.0, w))

                    mult_cons_floor = 0.66 * (1.0 - w) + 0.58 * w
                    mult_cons_ceil = 1.94 * (1.0 - w) + 1.61 * w

                    mult_reco_floor = 0.78 * (1.0 - w) + 0.76 * w
                    mult_reco_ceil = 1.67 * (1.0 - w) + 1.45 * w

                    mult_fine_floor = 0.89 * (1.0 - w) + 0.88 * w
                    mult_fine_ceil = 1.44 * (1.0 - w) + 1.35 * w

                    cons_floor = p5 * mult_cons_floor
                    cons_ceil = p95 * mult_cons_ceil

                    reco_floor = p5 * mult_reco_floor
                    reco_ceil = p95 * mult_reco_ceil

                    fine_floor = p5 * mult_fine_floor
                    fine_ceil = p95 * mult_fine_ceil

                    def round_to_25(val):
                        return int(round(val / 25.0) * 25.0)

                    cons_floor = max(40, round_to_25(cons_floor))
                    cons_ceil = min(1000, round_to_25(cons_ceil))

                    reco_floor = max(40, round_to_25(reco_floor))
                    reco_ceil = min(1000, round_to_25(reco_ceil))

                    fine_floor = max(40, round_to_25(fine_floor))
                    fine_ceil = min(1000, round_to_25(fine_ceil))

                    voiced_duration = len(all_stable_f0) * 0.01

                    def show_result_dialog():
                        self.stop_loading()
                        from modules.f0_detection_dialog import F0DetectionDialog
                        F0DetectionDialog(
                            parent=self.root,
                            app=self,
                            p5=p5,
                            p10=p10,
                            p50=p50,
                            p90=p90,
                            p95=p95,
                            stable_count=len(all_stable_f0),
                            stable_duration=voiced_duration,
                            cons_range=(cons_floor, cons_ceil),
                            reco_range=(reco_floor, reco_ceil),
                            fine_range=(fine_floor, fine_ceil),
                            apply_scope="current"
                        )

                    self.root.after(0, show_result_dialog)
                except Exception as e:
                    error_message = f"检测过程中发生错误: {e}"
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))

            import threading
            threading.Thread(target=run_current_detection, daemon=True).start()
            return

        self.start_loading("正在估算发音人 F0 分布...")

        # 在主线程中捕获 UI 和 Tkinter 状态，保证线程安全
        tab_mode = self.tabview.get()
        pending_long_snd = self.pending_long_snd
        long_audio_path = self.long_audio_path

        # 快照常规属性，避免工作线程直接读取或操作主线程的 self.items 数据字典
        items_snapshot = []
        for item in self.items.values():
            if item.get('is_excluded', False):
                continue
            items_snapshot.append({
                'macro_start': item.get('macro_start'),
                'macro_end': item.get('macro_end'),
                'snd': item.get('snd'),
                'path': item.get('path'),
                'label': item.get('label')
            })

        # 抽样至多 15 个样本进行代表性估算，兼顾精度与计算开销
        import numpy as np
        if len(items_snapshot) > 15:
            indices = np.linspace(0, len(items_snapshot) - 1, 15, dtype=int)
            items_snapshot = [items_snapshot[idx] for idx in indices]

        params_temp = {
            'pitch_floor': 50,
            'pitch_ceiling': 700,
            'voicing_threshold': self.last_params.get('voicing_threshold', 0.25),
            'very_accurate': False  # 快速估算模式
        }

        def run_detection():
            try:
                import parselmouth
                from modules.audio_core import extract_f0
                import concurrent.futures

                all_stable_f0 = []

                if tab_mode == "单条长音频":
                    snd = pending_long_snd
                    if snd is None and long_audio_path and os.path.exists(long_audio_path):
                        snd = load_compatible_sound(long_audio_path)
                    if snd is None:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "未找到已加载的长音频。"))
                        return

                    # 提取抽样有声片段的 F0（并发），而非全量提取整段长音频
                    valid_items = [it for it in items_snapshot if self._get_valid_macro_bounds(it) is not None]
                    if not valid_items:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "未找到有效发音段。"))
                        return

                    def process_long_item(item):
                        macro_bounds = self._get_valid_macro_bounds(item)
                        if macro_bounds is None:
                            return []
                        m_s, m_e = macro_bounds
                        padding = 0.1
                        from_t = max(0.0, m_s - padding)
                        to_t = min(snd.get_total_duration(), m_e + padding)
                        try:
                            part = snd.extract_part(from_time=from_t, to_time=to_t)
                            pitch_data = extract_f0(part, params_temp)
                            pitch_times = pitch_data['xs'] + from_t
                            pitch_freqs = pitch_data['freqs']
                            mask = (pitch_times >= m_s) & (pitch_times <= m_e)
                            item_times = pitch_times[mask]
                            item_freqs = pitch_freqs[mask]
                            return self.extract_stable_f0_values(item_times, item_freqs)
                        except Exception:
                            return []

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_items), 8)) as executor:
                        results = list(executor.map(process_long_item, valid_items))
                    for stable_f0 in results:
                        all_stable_f0.extend(stable_f0)

                elif tab_mode == "多条独立音频":
                    valid_items = [it for it in items_snapshot if it.get('snd') or (it.get('path') and os.path.exists(it['path']))]
                    if not valid_items:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "没有有效的独立音频文件。"))
                        return

                    def process_independent_item(idx_item):
                        idx, item = idx_item
                        self.root.after(0, lambda v=((idx + 1) / len(valid_items)): self.set_progress(v))
                        self.root.after(0, lambda name=item['label'], cur_idx=idx+1, tot=len(valid_items): self.set_status(f"正在分析 F0 ({cur_idx}/{tot}): {name}...", "#2563EB", "status_loading"))

                        item_snd = item.get('snd')
                        if item_snd is None:
                            try:
                                item_snd = load_compatible_sound(item['path'])
                            except Exception:
                                return []

                        try:
                            pitch_data = extract_f0(item_snd, params_temp)
                            return self.extract_stable_f0_values(pitch_data['xs'], pitch_data['freqs'])
                        except Exception:
                            return []

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_items), 8)) as executor:
                        results = list(executor.map(process_independent_item, enumerate(valid_items)))
                    for stable_f0 in results:
                        all_stable_f0.extend(stable_f0)

                # 判断有效有声帧是否足够（例如，每帧 10ms，至少需 50 帧，即 0.5 秒）
                if len(all_stable_f0) < 50:
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda: messagebox.showwarning("有效语音帧不足", "有效有声数据太少，无法进行可靠建议。请先导入更多音频，或确保当前音频包含足够稳定发音段。"))
                    return

                # 计算百分位数
                p5 = float(np.percentile(all_stable_f0, 5))
                p10 = float(np.percentile(all_stable_f0, 10))
                p50 = float(np.percentile(all_stable_f0, 50))
                p90 = float(np.percentile(all_stable_f0, 90))
                p95 = float(np.percentile(all_stable_f0, 95))

                # 基于 P50 (中位数) 决定插值权重
                med = p50
                w = (med - 120.0) / 120.0
                w = max(0.0, min(1.0, w))

                # 插值系数
                mult_cons_floor = 0.66 * (1.0 - w) + 0.58 * w
                mult_cons_ceil = 1.94 * (1.0 - w) + 1.61 * w

                mult_reco_floor = 0.78 * (1.0 - w) + 0.76 * w
                mult_reco_ceil = 1.67 * (1.0 - w) + 1.45 * w

                mult_fine_floor = 0.89 * (1.0 - w) + 0.88 * w
                mult_fine_ceil = 1.44 * (1.0 - w) + 1.35 * w

                # 计算建议范围
                cons_floor = p5 * mult_cons_floor
                cons_ceil = p95 * mult_cons_ceil

                reco_floor = p5 * mult_reco_floor
                reco_ceil = p95 * mult_reco_ceil

                fine_floor = p5 * mult_fine_floor
                fine_ceil = p95 * mult_fine_ceil

                # 四舍五入到 5 Hz 和 10 Hz
                def round_to_nearest(val, base):
                    return int(round(val / base) * base)

                cons_floor = max(40, round_to_nearest(cons_floor, 5))
                cons_ceil = min(1000, round_to_nearest(cons_ceil, 10))

                reco_floor = max(40, round_to_nearest(reco_floor, 5))
                reco_ceil = min(1000, round_to_nearest(reco_ceil, 10))

                fine_floor = max(40, round_to_nearest(fine_floor, 5))
                fine_ceil = min(1000, round_to_nearest(fine_ceil, 10))

                # 估算稳定的有声时长 (dt 估计为 10ms = 0.01s)
                voiced_duration = len(all_stable_f0) * 0.01

                def show_result_dialog():
                    self.stop_loading()
                    from modules.f0_detection_dialog import F0DetectionDialog
                    F0DetectionDialog(
                        parent=self.root,
                        app=self,
                        p5=p5,
                        p10=p10,
                        p50=p50,
                        p90=p90,
                        p95=p95,
                        stable_count=len(all_stable_f0),
                        stable_duration=voiced_duration,
                        cons_range=(cons_floor, cons_ceil),
                        reco_range=(reco_floor, reco_ceil),
                        fine_range=(fine_floor, fine_ceil)
                    )

                self.root.after(0, show_result_dialog)

            except Exception as e:
                error_message = f"检测过程中发生错误: {e}"
                self.root.after(0, self.stop_loading)
                self.root.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))

        import threading
        threading.Thread(target=run_detection, daemon=True).start()


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


    def apply_f0_bounds(self, floor, ceiling, scope="global"):
        self.entry_pitch_floor.delete(0, tk.END)
        self.entry_pitch_floor.insert(0, str(int(floor)))
        if hasattr(self.entry_pitch_floor, '_last_val'):
            self.entry_pitch_floor._last_val = str(int(floor))

        self.entry_pitch_ceiling.delete(0, tk.END)
        self.entry_pitch_ceiling.insert(0, str(int(ceiling)))
        if hasattr(self.entry_pitch_ceiling, '_last_val'):
            self.entry_pitch_ceiling._last_val = str(int(ceiling))

        if scope == "current":
            try:
                voicing_threshold = float(self.entry_voicing_threshold.get())
            except Exception:
                voicing_threshold = self.last_params.get('voicing_threshold', 0.25)
            self.recalculate_current_item(
                recompute_pitch=True,
                preserve_bounds=True,
                completion_text="当前条目 F0 范围已应用",
                param_overrides={
                    'pitch_floor': int(floor),
                    'pitch_ceiling': int(ceiling),
                    'voicing_threshold': voicing_threshold
                }
            )
        else:
            # [P1 优化]：触发参数变化逻辑，并传入 only_pitch_changed=True 以保护所有非手动切分边界不被重写
            self.on_param_change(recalculate_current=False)
            self.recalculate_all_audio(recompute_pitch=True, only_pitch_changed=True)


    def _collect_formant_snippets(self, tab_mode, pending_long_snd, long_audio_path, items_snapshot):
        import parselmouth
        import os
        snippets = []
        if tab_mode == "单条长音频":
            snd = pending_long_snd
            if snd is None and long_audio_path and os.path.exists(long_audio_path):
                snd = load_compatible_sound(long_audio_path)
            if snd:
                for item in items_snapshot:
                    macro_bounds = self._get_valid_macro_bounds(item)
                    if macro_bounds:
                        m_s, m_e = macro_bounds
                    else:
                        continue
                    if m_e > m_s + 0.06:
                        try:
                            part = snd.extract_part(from_time=m_s + 0.025, to_time=m_e - 0.025)
                            snippets.append(part)
                        except Exception:
                            pass
        else: # "多条独立音频"
            for item in items_snapshot:
                item_snd = item.get('snd')
                if item_snd is None and item.get('path') and os.path.exists(item['path']):
                    try:
                        item_snd = load_compatible_sound(item['path'])
                    except Exception:
                        pass
                if item_snd:
                    t_s = item.get('start', 0.0)
                    t_e = item.get('end', item_snd.get_total_duration())
                    if t_e > t_s + 0.06:
                        try:
                            part = item_snd.extract_part(from_time=t_s + 0.025, to_time=t_e - 0.025)
                            snippets.append(part)
                        except Exception:
                            pass
                    else:
                        try:
                            snippets.append(item_snd)
                        except Exception:
                            pass
        return snippets


    def _crop_snippets_for_formant_analysis(self, snippets):
        import numpy as np
        # 共振峰上限对元音类型敏感；保留更多分散样本，避免少量抽样漏掉极端元音。
        if len(snippets) > 24:
            indices = np.linspace(0, len(snippets) - 1, 24, dtype=int)
            snippets = [snippets[idx] for idx in indices]

        cropped_snippets = []
        for s in snippets:
            try:
                dur = s.get_total_duration()
                if dur > 0.150:
                    mid = dur / 2.0
                    cropped_snippets.append(s.extract_part(from_time=mid - 0.075, to_time=mid + 0.075))
                else:
                    cropped_snippets.append(s)
            except Exception:
                cropped_snippets.append(s)
        return cropped_snippets


    def _evaluate_configs_parallel(self, snippets, configs):
        import concurrent.futures
        import numpy as np
        from modules.formant_optimizer import score_config
        results_dict = {}
        def eval_single(cfg):
            h, win, pre = cfg
            try:
                score, vr, m_f1, m_f2 = score_config(snippets, h, win, pre)
                return cfg, (score, vr, m_f1, m_f2)
            except Exception:
                return cfg, (0.0, 0.0, np.nan, np.nan)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(configs), 8)) as executor:
            mapped_results = list(executor.map(eval_single, configs))
        for cfg, res in mapped_results:
            results_dict[cfg] = res
        return results_dict


    def _search_best_formant_config(self, snippets):
        import numpy as np

        # 3. Coarse search (并行计算)
        # Praat 官方说明儿童或较短声道发音人可能需要约 8000 Hz 的上限。
        coarse_candidates = [3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 8500]
        coarse_configs = [(h, 0.025, 50) for h in coarse_candidates]
        coarse_eval_results = self._evaluate_configs_parallel(snippets, coarse_configs)

        coarse_results = {}
        for h in coarse_candidates:
            score, vr, m_f1, m_f2 = coarse_eval_results[(h, 0.025, 50)]
            coarse_results[h] = {
                'score': score,
                'valid_rate': vr,
                'med_f1': m_f1,
                'med_f2': m_f2
            }

        # Apply consistency drift penalty
        final_coarse_scores = {}
        for i, h in enumerate(coarse_candidates):
            res = coarse_results[h]
            score = res['score']
            med_f1 = res['med_f1']
            med_f2 = res['med_f2']
            drift_penalty = 1.0

            if i > 0:
                left_res = coarse_results[coarse_candidates[i-1]]
                if np.isfinite(med_f1) and np.isfinite(left_res['med_f1']) and abs(med_f1 - left_res['med_f1']) > 100:
                    drift_penalty *= 0.85
                if np.isfinite(med_f2) and np.isfinite(left_res['med_f2']) and abs(med_f2 - left_res['med_f2']) > 200:
                    drift_penalty *= 0.85

            if i < len(coarse_candidates) - 1:
                right_res = coarse_results[coarse_candidates[i+1]]
                if np.isfinite(med_f1) and np.isfinite(right_res['med_f1']) and abs(med_f1 - right_res['med_f1']) > 100:
                    drift_penalty *= 0.85
                if np.isfinite(med_f2) and np.isfinite(right_res['med_f2']) and abs(med_f2 - right_res['med_f2']) > 200:
                    drift_penalty *= 0.85

            final_coarse_scores[h] = score * drift_penalty

        best_coarse_h = max(coarse_candidates, key=lambda h: final_coarse_scores[h])

        # 4. Fine search (并行计算所有候选参数)
        fine_h_candidates = [
            best_coarse_h - 500,
            best_coarse_h - 250,
            best_coarse_h,
            best_coarse_h + 250,
            best_coarse_h + 500
        ]
        fine_h_candidates = [h for h in fine_h_candidates if 3000 <= h <= 9000]

        win_candidates = [0.020, 0.025, 0.030, 0.035]
        pre_candidates = [50, 80, 100]

        fine_configs = []
        for h in fine_h_candidates:
            for win in win_candidates:
                for pre in pre_candidates:
                    fine_configs.append((h, win, pre))

        fine_eval_results = self._evaluate_configs_parallel(snippets, fine_configs)

        best_score = -1.0
        best_config = None
        all_fine_results = []

        total_steps = len(fine_configs)
        for step_idx, cfg in enumerate(fine_configs):
            h, win, pre = cfg
            score, vr, _, _ = fine_eval_results[cfg]
            all_fine_results.append((h, win, pre, score))
            if score > best_score:
                best_score = score
                best_config = (h, 5, win, pre, score)

            if (step_idx + 1) % 5 == 0 or (step_idx + 1) == total_steps:
                self.root.after(0, lambda v=(step_idx + 1)/total_steps: self.set_progress(v))

        return best_score, best_config, all_fine_results


    def _generate_formant_gears(self, best_score, best_config, all_fine_results):
        # 1. Recommended
        reco_params = best_config

        # 2. Anti-misalignment (lower max_hz, longer window)
        anti_candidates = [
            cfg for cfg in all_fine_results
            if cfg[1] >= 0.030 and cfg[0] <= reco_params[0] - 250 and cfg[3] >= best_score - 0.18
        ]
        if not anti_candidates:
            anti_candidates = [
                cfg for cfg in all_fine_results
                if cfg[1] >= 0.025 and cfg[0] <= reco_params[0] and cfg[3] >= best_score - 0.25
            ]
        if anti_candidates:
            best_anti = max(anti_candidates, key=lambda c: (c[3], -c[0], c[1]))
            anti_params = (best_anti[0], 5, best_anti[1], best_anti[2], best_anti[3])
        else:
            anti_params = (reco_params[0], 5, max(0.025, reco_params[2]), reco_params[3], reco_params[4] * 0.9)

        # 3. High-resolution (shorter window, score within 0.05 of best)
        fine_candidates = [
            cfg for cfg in all_fine_results
            if cfg[1] <= 0.025 and cfg[3] >= best_score - 0.08
        ]
        if fine_candidates:
            best_fine = sorted(fine_candidates, key=lambda c: (c[1], -c[3]))[0]
            fine_params = (best_fine[0], 5, best_fine[1], best_fine[2], best_fine[3])
        else:
            fine_params = (reco_params[0], 5, min(0.025, reco_params[2]), reco_params[3], reco_params[4] * 0.9)

        return reco_params, anti_params, fine_params


    def _run_formant_detection_task(self, tab_mode, pending_long_snd, long_audio_path, items_snapshot):
        try:
            snippets = self._collect_formant_snippets(tab_mode, pending_long_snd, long_audio_path, items_snapshot)

            if not snippets:
                self.root.after(0, self.stop_loading)
                self.root.after(0, lambda: messagebox.showwarning("提示", "没有有效发音片段可进行分析。"))
                return

            snippets = self._crop_snippets_for_formant_analysis(snippets)

            best_score, best_config, all_fine_results = self._search_best_formant_config(snippets)

            if best_config is None:
                self.root.after(0, self.stop_loading)
                self.root.after(0, lambda: messagebox.showwarning("提示", "未找到有效的最佳配置。"))
                return

            voiced_duration = 0.0
            for s in snippets:
                try:
                    voiced_duration += float(s.get_total_duration())
                except Exception:
                    pass
            insufficient_data = voiced_duration < 0.5

            reco_params, anti_params, fine_params = self._generate_formant_gears(best_score, best_config, all_fine_results)

            def show_result_dialog():
                self.stop_loading()
                from modules.formant_detection_dialog import FormantDetectionDialog
                FormantDetectionDialog(
                    parent=self.root,
                    app=self,
                    voiced_duration=voiced_duration,
                    insufficient_data=insufficient_data,
                    reco_params=reco_params,
                    anti_params=anti_params,
                    fine_params=fine_params
                )

            self.root.after(0, show_result_dialog)

        except Exception as e:
            error_message = f"检测过程中发生错误: {e}"
            self.root.after(0, self.stop_loading)
            self.root.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))


    def on_detect_formant_clicked(self):
        if not self.items:
            messagebox.showwarning("提示", "请先导入音频文件以进行检测。")
            return

        curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
        detect_current_only = False
        if curr_item:
            resp = messagebox.askyesnocancel("检测范围", "是否仅针对当前选中的音频条目进行共振峰参数估算？\n\n- 选择【是】：仅针对当前条目估算本条建议参数\n- 选择【否】：对项目内的代表性音频进行全局参数估算\n- 选择【取消】：取消本次操作")
            if resp is None:
                return
            detect_current_only = resp

        if detect_current_only:
            snd = curr_item.get('snd')
            if snd is None and curr_item.get('path'):
                try:
                    import parselmouth
                    snd = load_compatible_sound(curr_item['path'])
                except Exception as e:
                    messagebox.showerror("错误", f"加载当前项音频失败: {e}")
                    return
            if snd is None:
                messagebox.showwarning("提示", "未找到已加载的音频。")
                return

            self.start_loading("正在分析本条共振峰最佳参数...")

            def run_current_detection():
                try:
                    macro_bounds = self._get_valid_macro_bounds(curr_item)
                    is_long = macro_bounds is not None and snd.get_total_duration() > 15.0
                    snippets = []
                    if is_long:
                        m_s, m_e = macro_bounds
                        if m_e > m_s:
                            part = snd.extract_part(from_time=m_s, to_time=m_e)
                            snippets.append(part)
                    else:
                        s_t = curr_item.get('start', 0.0)
                        e_t = curr_item.get('end', snd.get_total_duration())
                        from_t = s_t + 0.025
                        to_t = e_t - 0.025
                        if to_t > from_t + 0.01:
                            part = snd.extract_part(from_time=from_t, to_time=to_t)
                        else:
                            part = snd.extract_part(from_time=s_t, to_time=e_t)
                        snippets.append(part)

                    if not snippets:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "当前条目没有有效发音片段可进行分析。"))
                        return

                    voiced_duration = 0.0
                    for s in snippets:
                        try:
                            voiced_duration += float(s.get_total_duration())
                        except Exception: pass
                    insufficient_data = voiced_duration < 0.5

                    best_score, best_config, all_fine_results = self._search_best_formant_config(snippets)

                    if best_config is None:
                        self.root.after(0, self.stop_loading)
                        self.root.after(0, lambda: messagebox.showwarning("提示", "未找到有效的最佳配置。"))
                        return

                    reco_params, anti_params, fine_params = self._generate_formant_gears(best_score, best_config, all_fine_results)

                    def show_result_dialog():
                        self.stop_loading()
                        from modules.formant_detection_dialog import FormantDetectionDialog
                        FormantDetectionDialog(
                            parent=self.root,
                            app=self,
                            voiced_duration=voiced_duration,
                            insufficient_data=insufficient_data,
                            reco_params=reco_params,
                            anti_params=anti_params,
                            fine_params=fine_params,
                            apply_scope="current"
                        )

                    self.root.after(0, show_result_dialog)
                except Exception as e:
                    error_message = f"检测过程中发生错误: {e}"
                    self.root.after(0, self.stop_loading)
                    self.root.after(0, lambda msg=error_message: messagebox.showerror("错误", msg))

            import threading
            threading.Thread(target=run_current_detection, daemon=True).start()
            return

        self.start_loading("正在分析共振峰最佳参数...")

        tab_mode = self.tabview.get()
        pending_long_snd = self.pending_long_snd
        long_audio_path = self.long_audio_path

        items_snapshot = []
        for item in self.items.values():
            if item.get('is_excluded', False):
                continue
            items_snapshot.append({
                'macro_start': item.get('macro_start'),
                'macro_end': item.get('macro_end'),
                'snd': item.get('snd'),
                'path': item.get('path'),
                'label': item.get('label'),
                'start': item.get('start'),
                'end': item.get('end')
            })

        import threading
        threading.Thread(
            target=self._run_formant_detection_task,
            args=(tab_mode, pending_long_snd, long_audio_path, items_snapshot),
            daemon=True
        ).start()


    def apply_formant_params(self, max_hz, count, window_length, pre_emphasis, scope="global"):
        # Update entry fields UI
        if hasattr(self, 'entry_formant_max_hz') and self.entry_formant_max_hz:
            self.entry_formant_max_hz.delete(0, tk.END)
            self.entry_formant_max_hz.insert(0, str(float(max_hz)))
            if hasattr(self.entry_formant_max_hz, '_last_val'):
                self.entry_formant_max_hz._last_val = str(float(max_hz))

        if hasattr(self, 'entry_formant_count') and self.entry_formant_count:
            self.entry_formant_count.delete(0, tk.END)
            self.entry_formant_count.insert(0, str(int(count)))
            if hasattr(self.entry_formant_count, '_last_val'):
                self.entry_formant_count._last_val = str(int(count))

        if hasattr(self, 'entry_formant_window_length') and self.entry_formant_window_length:
            self.entry_formant_window_length.delete(0, tk.END)
            self.entry_formant_window_length.insert(0, f"{float(window_length):.3f}")
            if hasattr(self.entry_formant_window_length, '_last_val'):
                self.entry_formant_window_length._last_val = f"{float(window_length):.3f}"

        if hasattr(self, 'entry_formant_pre_emphasis') and self.entry_formant_pre_emphasis:
            self.entry_formant_pre_emphasis.delete(0, tk.END)
            self.entry_formant_pre_emphasis.insert(0, str(float(pre_emphasis)))
            if hasattr(self.entry_formant_pre_emphasis, '_last_val'):
                self.entry_formant_pre_emphasis._last_val = str(float(pre_emphasis))

        if scope == "current":
            curr_item = getattr(self, 'spectrogram_panel', None) and self.spectrogram_panel.current_item
            self.recalculate_current_item(
                recompute_formant_only=True,
                completion_text="当前条目共振峰参数已应用",
                param_overrides={
                    'formant_max_hz': float(max_hz),
                    'formant_count': int(count),
                    'formant_window_length': float(window_length),
                    'formant_pre_emphasis': float(pre_emphasis),
                    'formant_sample_strategy': (curr_item or {}).get('formant_sample_strategy', self.last_params.get('formant_sample_strategy', '整段11点')),
                    'analysis_mode': (curr_item or {}).get('analysis_mode', self.last_params.get('analysis_mode', 'f0')),
                    'show_f3': (curr_item or {}).get('show_f3', self.last_params.get('show_f3', False))
                }
            )
        else:
            # Sync to self.last_params
            self.last_params['formant_max_hz'] = float(max_hz)
            self.last_params['formant_count'] = int(count)
            self.last_params['formant_window_length'] = float(window_length)
            self.last_params['formant_pre_emphasis'] = float(pre_emphasis)
            self.recalculate_all_formants()


    def recalculate_all_formants(self):
        if hasattr(self, 'spectrogram_panel') and self.spectrogram_panel:
            self.spectrogram_panel.discard_eraser_changes()
        if not self.items: return

        # Ensure last params is synced
        try:
            self.on_param_change(recalculate_current=False)
        except Exception:
            pass

        items_snapshot = list(self.items.items())
        total = len(items_snapshot)

        def run():
            self.root.after(0, lambda: self.start_loading("正在重新计算共振峰..."))

            import parselmouth
            import os
            from modules.audio_core import _sample_formants_helper

            params = {
                'formant_max_hz': self.last_params.get('formant_max_hz', 5500.0),
                'formant_count': self.last_params.get('formant_count', 5),
                'formant_window_length': self.last_params.get('formant_window_length', 0.025),
                'formant_pre_emphasis': self.last_params.get('formant_pre_emphasis', 50.0),
                'formant_sample_strategy': self.last_params.get('formant_sample_strategy', '整段11点'),
                'pts': self.last_params.get('pts', 11),
                'analysis_mode': 'formant',
                'show_f3': self.last_params.get('show_f3', False)
            }

            for i, (iid, item) in enumerate(items_snapshot):
                if item.get('is_excluded', False):
                    continue
                snd = item.get('snd')
                if not snd and item.get('path') and os.path.exists(item['path']):
                    try:
                        snd = load_compatible_sound(item['path'])
                        item['snd'] = snd
                    except Exception:
                        pass

                if snd and item.get('start') is not None and item.get('end') is not None:
                    try:
                        mic_s = item['start']
                        mic_e = item['end']

                        macro_bounds = self._get_valid_macro_bounds(item)
                        is_long = macro_bounds is not None and snd.get_total_duration() > 15.0

                        if is_long:
                            ms, me = macro_bounds
                            valid_ms = max(0, ms)
                            valid_me = min(snd.get_total_duration(), me)

                            padding = 1.0
                            seg_start = max(0.0, valid_ms - padding)
                            seg_end = min(snd.get_total_duration(), valid_me + padding)
                            part_snd = snd.extract_part(from_time=seg_start, to_time=seg_end)

                            rel_s = mic_s - seg_start
                            rel_e = mic_e - seg_start

                            formant_data, preview_formants = _sample_formants_helper(part_snd, rel_s, rel_e, params)
                            formant_data['xs'] = formant_data['xs'] + seg_start
                        else:
                            formant_data, preview_formants = _sample_formants_helper(snd, mic_s, mic_e, params)

                        item['formant_data'] = formant_data
                        item['_dirty_formant'] = True
                        item['preview_formants'] = preview_formants

                        item['formant_max_hz'] = params['formant_max_hz']
                        item['formant_count'] = params['formant_count']
                        item['formant_window_length'] = params['formant_window_length']
                        item['formant_pre_emphasis'] = params['formant_pre_emphasis']
                        item['formant_sample_strategy'] = params['formant_sample_strategy']
                        item['show_f3'] = params['show_f3']

                        if item.get('path'):
                            cache_item = self._get_audio_cache(item['path'], params)
                            if cache_item is not None:
                                cache_item['formant_data'] = formant_data
                                cache_item['_dirty_formant'] = True
                                cache_item['preview_formants'] = preview_formants
                                cache_item['formant_max_hz'] = params['formant_max_hz']
                                cache_item['formant_count'] = params['formant_count']
                                cache_item['formant_window_length'] = params['formant_window_length']
                                cache_item['formant_pre_emphasis'] = params['formant_pre_emphasis']
                                cache_item['formant_sample_strategy'] = params['formant_sample_strategy']
                                cache_item['show_f3'] = params['show_f3']
                                self._set_audio_cache(item['path'], cache_item, params)
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error(f"Error recalculating formant: {e}")

                if i % 5 == 0 or i == total - 1:
                    self.root.after(0, lambda v=(i + 1) / total: self.set_progress(v))

            def finalize():
                if self.spectrogram_panel.current_item:
                    self.spectrogram_panel.plot_item_spectrogram()
                    self.spectrogram_panel.update_ui_times()

                for iid in list(self.items.keys()):
                    self.tree_panel.update_item_icon(iid)

                self.tree_panel.update_preview()
                self.mark_modified()
                self.stop_loading("共振峰参数已应用，重算完成")

            self.root.after(0, finalize)

        import threading
        threading.Thread(target=run, daemon=True).start()


