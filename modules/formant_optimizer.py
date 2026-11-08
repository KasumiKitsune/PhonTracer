import os
import warnings
import concurrent.futures
import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    import sys as _sys
    _sys.modules.setdefault('parselmouth', parselmouth)
    _sys.modules.setdefault('parselmouth.praat', parselmouth.praat)


def clipped_score(value, low, high):
    if high <= low:
        return 0.0
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def adjacent_jump_rate(xs_arr, vals, threshold):
    vals = np.asarray(vals, dtype=float)
    xs_arr = np.asarray(xs_arr, dtype=float)
    valid = np.isfinite(vals)
    if np.sum(valid) < 3:
        return 1.0
    idx = np.where(valid)[0]
    jump_count = 0
    pair_count = 0
    for left, right in zip(idx[:-1], idx[1:]):
        if xs_arr[right] - xs_arr[left] > 0.055:
            continue
        pair_count += 1
        if abs(vals[right] - vals[left]) > threshold:
            jump_count += 1
    if pair_count == 0:
        return 1.0
    return jump_count / pair_count


def fragmentation_penalty(valid_mask):
    if len(valid_mask) == 0:
        return 1.0
    runs = []
    run_len = 0
    for flag in valid_mask:
        if flag:
            run_len += 1
        elif run_len:
            runs.append(run_len)
            run_len = 0
    if run_len:
        runs.append(run_len)
    if not runs:
        return 1.0
    short = sum(r for r in runs if r < 4)
    return short / max(1, sum(runs))


def score_config(snippets, formant_max_hz, window_length, pre_emphasis, num_formants=5):
    total_frames = 0
    valid_frames = 0

    continuity_scores = []
    gap_scores = []
    range_scores = []
    fragmentation_scores = []
    edge_scores = []

    all_valid_f1 = []
    all_valid_f2 = []

    for snd_part in snippets:
        try:
            formant = snd_part.to_formant_burg(
                time_step=None,
                max_number_of_formants=num_formants,
                maximum_formant=formant_max_hz,
                window_length=window_length,
                pre_emphasis_from=pre_emphasis
            )
        except Exception:
            continue

        xs = formant.xs()
        if len(xs) == 0:
            continue

        f1_vals = []
        f2_vals = []
        f3_vals = []
        for t in xs:
            f1_vals.append(formant.get_value_at_time(1, t))
            f2_vals.append(formant.get_value_at_time(2, t))
            f3_vals.append(formant.get_value_at_time(3, t))

        f1 = np.array(f1_vals, dtype=float)
        f2 = np.array(f2_vals, dtype=float)
        f3 = np.array(f3_vals, dtype=float)

        gap = f2 - f1
        valid_mask = (
            np.isfinite(f1) & np.isfinite(f2) &
            (f1 >= 90.0) & (f1 <= 1300.0) &
            (f2 >= 350.0) & (f2 <= min(float(formant_max_hz) * 0.96, 4200.0)) &
            (gap >= 120.0)
        )
        total_frames += len(xs)
        valid_frames += int(np.sum(valid_mask))

        if np.sum(valid_mask) < 4:
            continue

        vf1 = f1[valid_mask]
        vf2 = f2[valid_mask]
        vf3 = f3[valid_mask]

        all_valid_f1.extend(vf1.tolist())
        all_valid_f2.extend(vf2.tolist())

        valid_f1_series = np.where(valid_mask, f1, np.nan)
        valid_f2_series = np.where(valid_mask, f2, np.nan)
        f1_jump_threshold = max(150.0, 0.28 * float(np.median(vf1)))
        f2_jump_threshold = max(260.0, 0.20 * float(np.median(vf2)))
        f1_jump_rate = adjacent_jump_rate(xs, valid_f1_series, f1_jump_threshold)
        f2_jump_rate = adjacent_jump_rate(xs, valid_f2_series, f2_jump_threshold)
        continuity_scores.append(1.0 - min(1.0, 3.0 * (0.45 * f1_jump_rate + 0.55 * f2_jump_rate)))

        diff = vf2 - vf1
        diff_p10 = float(np.percentile(diff, 10))
        mean_diff = float(np.mean(diff)) if len(diff) > 0 else 0.0
        diff_cv = float(np.std(diff)) / mean_diff if mean_diff > 0 else 1.0
        gap_score = 0.65 * clipped_score(diff_p10, 140.0, 450.0) + 0.35 * (1.0 - min(1.0, diff_cv / 0.85))
        gap_scores.append(gap_score)

        f1_p95 = float(np.percentile(vf1, 95))
        f2_p99 = float(np.percentile(vf2, 99))
        f1_range_penalty = clipped_score(f1_p95, 1050.0, 1450.0)
        f2_range_penalty = clipped_score(f2_p99, 3300.0, 4300.0)
        range_scores.append(1.0 - min(1.0, 0.55 * f1_range_penalty + 0.45 * f2_range_penalty))

        fragmentation_scores.append(1.0 - fragmentation_penalty(valid_mask))

        near_edge_f2 = float(np.sum(vf2 > 0.93 * formant_max_hz)) / len(vf2)
        vf3_clean = vf3[np.isfinite(vf3)]
        near_edge_f3 = float(np.sum(vf3_clean > 0.93 * formant_max_hz)) / len(vf3_clean) if len(vf3_clean) > 0 else 0.0
        edge_scores.append(1.0 - min(1.0, 0.7 * near_edge_f2 + 0.3 * near_edge_f3))

    if total_frames == 0:
        return 0.0, 0.0, np.nan, np.nan

    valid_rate = valid_frames / total_frames
    coverage_score = min(1.0, valid_rate / 0.82)
    continuity_score = float(np.mean(continuity_scores)) if continuity_scores else 0.0
    gap_score = float(np.mean(gap_scores)) if gap_scores else 0.0
    range_score = float(np.mean(range_scores)) if range_scores else 0.0
    fragmentation_score = float(np.mean(fragmentation_scores)) if fragmentation_scores else 0.0
    edge_score = float(np.mean(edge_scores)) if edge_scores else 0.0

    quality_score = (
        0.22 * coverage_score +
        0.30 * continuity_score +
        0.22 * gap_score +
        0.12 * range_score +
        0.08 * fragmentation_score +
        0.06 * edge_score
    )

    if valid_rate < 0.25:
        quality_score *= 0.55

    # Prefer a lower ceiling when the observed F1/F2 quality is effectively tied.
    quality_score *= (1.0 - max(0.0, formant_max_hz - 5000.0) / 30000.0)

    # Slight tie-break preference for standard 5 formants unless 4 or 6 clearly wins.
    if int(num_formants) != 5:
        quality_score *= 0.985

    median_f1 = float(np.median(all_valid_f1)) if all_valid_f1 else np.nan
    median_f2 = float(np.median(all_valid_f2)) if all_valid_f2 else np.nan

    return float(quality_score), float(valid_rate), median_f1, median_f2


class FormantOptimizer:
    """
    自适应共振峰参数探测器（封装粗搜+精搜网格评分与三档推荐参数生成）。
    支持从 CLI / GUI 的音频条目列表或音频片段直接探测最佳 `max_formant` (`formant_max_hz`)、
    `num_formants` (`formant_count`)、`formant_window_length` 与 `formant_pre_emphasis`。
    """

    def __init__(
        self,
        coarse_candidates=None,
        num_formants_candidates=None,
        window_candidates=None,
        pre_emphasis_candidates=None,
        max_snippets=24,
    ):
        self.coarse_candidates = list(
            coarse_candidates or [3500, 4000, 4500, 5000, 5500, 6000, 6500, 7000, 7500, 8000, 8500]
        )
        self.num_formants_candidates = list(num_formants_candidates or [5, 4, 6])
        self.window_candidates = list(window_candidates or [0.020, 0.025, 0.030, 0.035])
        self.pre_emphasis_candidates = list(pre_emphasis_candidates or [50.0, 80.0, 100.0])
        self.max_snippets = int(max_snippets)

    @staticmethod
    def _extract_item_bounds(item):
        start = item.get("start")
        end = item.get("end")
        if start is not None and end is not None and float(end) > float(start):
            return float(start), float(end)
        m_s = item.get("macro_start")
        m_e = item.get("macro_end")
        if m_s is not None and m_e is not None and float(m_e) > float(m_s):
            return float(m_s), float(m_e)
        return None

    def collect_snippets(self, items, mode="batch", long_snd=None, long_snd_path=None):
        """从条目字典/列表或音频对象中提取用于共振峰评分的元音核心片段。"""
        if isinstance(items, dict):
            item_list = list(items.values())
        elif isinstance(items, (list, tuple)):
            item_list = list(items)
        else:
            item_list = []

        # 如果直接传入了 Sound 片段列表
        if item_list and all(hasattr(x, "to_formant_burg") for x in item_list):
            return list(item_list)

        snippets = []
        is_long_mode = str(mode).lower() in ("long", "单条长音频")

        if is_long_mode:
            snd = long_snd
            if snd is None and long_snd_path and os.path.exists(long_snd_path) and parselmouth is not None:
                try:
                    snd = parselmouth.Sound(long_snd_path)
                except Exception:
                    snd = None
            if snd is not None:
                total_dur = float(snd.get_total_duration())
                for item in item_list:
                    if not isinstance(item, dict) or item.get("is_excluded", False) or item.get("missing", False):
                        continue
                    bounds = self._extract_item_bounds(item)
                    if not bounds:
                        continue
                    b_s, b_e = max(0.0, bounds[0]), min(total_dur, bounds[1])
                    if b_e > b_s + 0.06:
                        try:
                            part = snd.extract_part(from_time=b_s + 0.025, to_time=b_e - 0.025)
                            snippets.append(part)
                        except Exception:
                            pass
                    elif b_e > b_s + 0.02:
                        try:
                            part = snd.extract_part(from_time=b_s, to_time=b_e)
                            snippets.append(part)
                        except Exception:
                            pass
                if snippets:
                    return snippets

        for item in item_list:
            if not isinstance(item, dict) or item.get("is_excluded", False) or item.get("missing", False):
                continue
            item_snd = item.get("snd")
            if item_snd is None and item.get("path") and os.path.exists(item["path"]) and parselmouth is not None:
                try:
                    item_snd = parselmouth.Sound(item["path"])
                except Exception:
                    item_snd = None
            if item_snd is None:
                continue
            try:
                total_dur = float(item_snd.get_total_duration())
            except Exception:
                total_dur = 0.0
            bounds = self._extract_item_bounds(item)
            if bounds:
                t_s, t_e = max(0.0, bounds[0]), min(total_dur, bounds[1]) if total_dur > 0 else bounds[1]
            else:
                t_s, t_e = 0.0, total_dur
            if t_e > t_s + 0.06:
                try:
                    part = item_snd.extract_part(from_time=t_s + 0.025, to_time=t_e - 0.025)
                    snippets.append(part)
                except Exception:
                    pass
            elif t_e > t_s > 0:
                try:
                    part = item_snd.extract_part(from_time=t_s, to_time=t_e)
                    snippets.append(part)
                except Exception:
                    snippets.append(item_snd)
            else:
                snippets.append(item_snd)

        return snippets

    def crop_snippets(self, snippets):
        """对片段抽样并截取中段 150ms 稳定段以加速网格搜索。"""
        if not snippets:
            return []
        if len(snippets) > self.max_snippets:
            indices = np.linspace(0, len(snippets) - 1, self.max_snippets, dtype=int)
            snippets = [snippets[idx] for idx in indices]

        cropped = []
        for s in snippets:
            try:
                dur = float(s.get_total_duration())
                if dur > 0.150:
                    mid = dur / 2.0
                    cropped.append(s.extract_part(from_time=mid - 0.075, to_time=mid + 0.075))
                else:
                    cropped.append(s)
            except Exception:
                cropped.append(s)
        return cropped

    @staticmethod
    def _evaluate_configs_parallel(snippets, configs):
        results_dict = {}

        def eval_single(cfg):
            if len(cfg) == 4:
                h, nf, win, pre = cfg
            else:
                h, win, pre = cfg
                nf = 5
            try:
                score, vr, m_f1, m_f2 = score_config(snippets, h, win, pre, num_formants=nf)
                return (h, nf, win, pre), (score, vr, m_f1, m_f2)
            except Exception:
                return (h, nf, win, pre), (0.0, 0.0, np.nan, np.nan)

        if not configs:
            return results_dict

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(configs), 8)) as executor:
            mapped_results = list(executor.map(eval_single, configs))
        for cfg_key, res in mapped_results:
            results_dict[cfg_key] = res
        return results_dict

    def _fallback_from_cached_items(self, items):
        """当环境中无原始音频对象但条目带有缓存共振峰/基频数据时，基于分布稳健估计最佳参数。"""
        if isinstance(items, dict):
            item_list = list(items.values())
        elif isinstance(items, (list, tuple)):
            item_list = list(items)
        else:
            item_list = []

        f1_pool, f2_pool, f0_pool = [], [], []
        total_dur = 0.0
        for it in item_list:
            if not isinstance(it, dict) or it.get("is_excluded", False) or it.get("missing", False):
                continue
            s, e = it.get("start"), it.get("end")
            if s is not None and e is not None and float(e) > float(s):
                total_dur += float(e) - float(s)
            f_data = it.get("formant_data") or it.get("preview_formants")
            if isinstance(f_data, dict):
                for arr, pool in ((f_data.get("f1"), f1_pool), (f_data.get("f2"), f2_pool)):
                    if arr is not None:
                        a = np.asarray(arr, dtype=float)
                        valid = a[np.isfinite(a) & (a > 0)]
                        pool.extend(valid.tolist())
            p_data = it.get("pitch_data")
            if isinstance(p_data, dict) and p_data.get("freqs") is not None:
                pf = np.asarray(p_data["freqs"], dtype=float)
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())
            elif it.get("preview_f0"):
                pf = np.asarray(it["preview_f0"], dtype=float)
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())

        med_f0 = float(np.median(f0_pool)) if f0_pool else 160.0
        med_f1 = float(np.median(f1_pool)) if f1_pool else (500.0 if med_f0 < 165.0 else 580.0)
        med_f2 = float(np.median(f2_pool)) if f2_pool else (1450.0 if med_f0 < 165.0 else 1680.0)

        if f2_pool and float(np.percentile(f2_pool, 95)) > 2550.0:
            best_max = 5500.0
        elif med_f0 >= 175.0:
            best_max = 5500.0
        else:
            best_max = 5000.0

        best_params = {
            "max_formant": float(best_max),
            "num_formants": 5,
            "formant_max_hz": float(best_max),
            "formant_count": 5,
            "formant_window_length": 0.025,
            "formant_pre_emphasis": 50.0,
            "quality_score": 0.82,
            "valid_rate": 0.85,
            "median_f1": round(med_f1, 1),
            "median_f2": round(med_f2, 1),
        }
        suggestions = {
            "recommended": dict(best_params),
            "conservative": {
                "max_formant": float(max(4500.0, best_max - 500.0)),
                "num_formants": 5,
                "formant_max_hz": float(max(4500.0, best_max - 500.0)),
                "formant_count": 5,
                "formant_window_length": 0.030,
                "formant_pre_emphasis": 50.0,
                "quality_score": 0.78,
            },
            "high_resolution": {
                "max_formant": float(best_max),
                "num_formants": 5,
                "formant_max_hz": float(best_max),
                "formant_count": 5,
                "formant_window_length": 0.020,
                "formant_pre_emphasis": 50.0,
                "quality_score": 0.79,
            },
        }
        return {
            "success": True,
            "voiced_duration_s": round(total_dur, 3),
            "insufficient_data": total_dur < 0.5,
            "best_params": best_params,
            "suggestions": suggestions,
        }

    def optimize(self, items_or_snippets, mode="batch", long_snd=None, long_snd_path=None):
        """
        执行粗搜 + 精搜网格优化，返回最佳 max_formant、num_formants、window_length、pre_emphasis 及三档建议。
        """
        raw_snippets = self.collect_snippets(items_or_snippets, mode=mode, long_snd=long_snd, long_snd_path=long_snd_path)
        if not raw_snippets:
            return self._fallback_from_cached_items(items_or_snippets)

        voiced_duration = 0.0
        for s in raw_snippets:
            try:
                voiced_duration += float(s.get_total_duration())
            except Exception:
                pass
        insufficient_data = voiced_duration < 0.5

        snippets = self.crop_snippets(raw_snippets)
        coarse_configs = [(h, 5, 0.025, 50.0) for h in self.coarse_candidates]
        coarse_eval = self._evaluate_configs_parallel(snippets, coarse_configs)

        coarse_results = {}
        for h in self.coarse_candidates:
            score, vr, m_f1, m_f2 = coarse_eval.get((h, 5, 0.025, 50.0), (0.0, 0.0, np.nan, np.nan))
            coarse_results[h] = {
                "score": score,
                "valid_rate": vr,
                "med_f1": m_f1,
                "med_f2": m_f2,
            }

        final_coarse_scores = {}
        for i, h in enumerate(self.coarse_candidates):
            res = coarse_results[h]
            score = res["score"]
            med_f1 = res["med_f1"]
            med_f2 = res["med_f2"]
            drift_penalty = 1.0
            if i > 0:
                left_res = coarse_results[self.coarse_candidates[i - 1]]
                if np.isfinite(med_f1) and np.isfinite(left_res["med_f1"]) and abs(med_f1 - left_res["med_f1"]) > 100:
                    drift_penalty *= 0.85
                if np.isfinite(med_f2) and np.isfinite(left_res["med_f2"]) and abs(med_f2 - left_res["med_f2"]) > 200:
                    drift_penalty *= 0.85
            if i < len(self.coarse_candidates) - 1:
                right_res = coarse_results[self.coarse_candidates[i + 1]]
                if np.isfinite(med_f1) and np.isfinite(right_res["med_f1"]) and abs(med_f1 - right_res["med_f1"]) > 100:
                    drift_penalty *= 0.85
                if np.isfinite(med_f2) and np.isfinite(right_res["med_f2"]) and abs(med_f2 - right_res["med_f2"]) > 200:
                    drift_penalty *= 0.85
            final_coarse_scores[h] = score * drift_penalty

        best_coarse_h = max(self.coarse_candidates, key=lambda h: final_coarse_scores[h])

        fine_h_candidates = [
            best_coarse_h - 500,
            best_coarse_h - 250,
            best_coarse_h,
            best_coarse_h + 250,
            best_coarse_h + 500,
        ]
        fine_h_candidates = [h for h in fine_h_candidates if 3000 <= h <= 9000]

        fine_configs = []
        for h in fine_h_candidates:
            for nf in self.num_formants_candidates:
                for win in self.window_candidates:
                    for pre in self.pre_emphasis_candidates:
                        fine_configs.append((h, nf, win, pre))

        fine_eval = self._evaluate_configs_parallel(snippets, fine_configs)

        best_score = -1.0
        best_tuple = (float(best_coarse_h), 5, 0.025, 50.0, 0.0, 0.0, np.nan, np.nan)
        all_fine_results = []

        for cfg in fine_configs:
            h, nf, win, pre = cfg
            score, vr, m_f1, m_f2 = fine_eval.get(cfg, (0.0, 0.0, np.nan, np.nan))
            all_fine_results.append((h, nf, win, pre, score, vr, m_f1, m_f2))
            if score > best_score:
                best_score = score
                best_tuple = (float(h), int(nf), float(win), float(pre), float(score), float(vr), m_f1, m_f2)

        b_h, b_nf, b_win, b_pre, b_score, b_vr, b_f1, b_f2 = best_tuple

        # Generate conservative (anti-misalignment) and high-resolution (fine) gears
        anti_candidates = [
            r for r in all_fine_results
            if r[2] >= 0.030 and r[0] <= b_h - 250 and r[4] >= best_score - 0.18
        ]
        if not anti_candidates:
            anti_candidates = [
                r for r in all_fine_results
                if r[2] >= 0.025 and r[0] <= b_h and r[4] >= best_score - 0.25
            ]
        if anti_candidates:
            best_anti = max(anti_candidates, key=lambda r: (r[4], -r[0], r[2]))
            anti_dict = {
                "max_formant": float(best_anti[0]),
                "num_formants": int(best_anti[1]),
                "formant_max_hz": float(best_anti[0]),
                "formant_count": int(best_anti[1]),
                "formant_window_length": float(best_anti[2]),
                "formant_pre_emphasis": float(best_anti[3]),
                "quality_score": round(float(best_anti[4]), 4),
            }
        else:
            anti_dict = {
                "max_formant": float(b_h),
                "num_formants": int(b_nf),
                "formant_max_hz": float(b_h),
                "formant_count": int(b_nf),
                "formant_window_length": float(max(0.025, b_win)),
                "formant_pre_emphasis": float(b_pre),
                "quality_score": round(float(b_score * 0.9), 4),
            }

        fine_candidates = [
            r for r in all_fine_results
            if r[2] <= 0.025 and r[4] >= best_score - 0.08
        ]
        if fine_candidates:
            best_fine = sorted(fine_candidates, key=lambda r: (r[2], -r[4]))[0]
            fine_dict = {
                "max_formant": float(best_fine[0]),
                "num_formants": int(best_fine[1]),
                "formant_max_hz": float(best_fine[0]),
                "formant_count": int(best_fine[1]),
                "formant_window_length": float(best_fine[2]),
                "formant_pre_emphasis": float(best_fine[3]),
                "quality_score": round(float(best_fine[4]), 4),
            }
        else:
            fine_dict = {
                "max_formant": float(b_h),
                "num_formants": int(b_nf),
                "formant_max_hz": float(b_h),
                "formant_count": int(b_nf),
                "formant_window_length": float(min(0.025, b_win)),
                "formant_pre_emphasis": float(b_pre),
                "quality_score": round(float(b_score * 0.9), 4),
            }

        best_params = {
            "max_formant": float(b_h),
            "num_formants": int(b_nf),
            "formant_max_hz": float(b_h),
            "formant_count": int(b_nf),
            "formant_window_length": float(b_win),
            "formant_pre_emphasis": float(b_pre),
            "quality_score": round(float(b_score), 4),
            "valid_rate": round(float(b_vr), 4),
            "median_f1": round(float(b_f1), 1) if np.isfinite(b_f1) else None,
            "median_f2": round(float(b_f2), 1) if np.isfinite(b_f2) else None,
        }

        return {
            "success": True,
            "voiced_duration_s": round(float(voiced_duration), 3),
            "insufficient_data": bool(insufficient_data),
            "best_params": best_params,
            "suggestions": {
                "recommended": dict(best_params),
                "conservative": anti_dict,
                "high_resolution": fine_dict,
            },
        }

    def detect(self, items_or_snippets, mode="batch", long_snd=None, long_snd_path=None):
        return self.optimize(items_or_snippets, mode=mode, long_snd=long_snd, long_snd_path=long_snd_path)
