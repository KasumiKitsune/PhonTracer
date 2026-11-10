"""
Anomaly detection and statistics calculation mixin for ProjectTreePanel.
"""
import re
import numpy as np
import math
import logging
from modules.data_utils import split_into_syllables
from modules.anomaly_detection import detect_pitch_anomaly_points
from .tree_dialogs import (
    TREE_INFO_MAX_CHARS,
    EXCLUSION_REASON_SHORT_LABELS,
    EXCLUSION_REASON_CATEGORIES,
    EXCLUSION_REASON_CUSTOM_CATEGORY,
    EXCLUSION_REASON_DETAIL_PLACEHOLDER,
    format_exclusion_reason,
    parse_exclusion_reason,
)

logger = logging.getLogger(__name__)


class AnomalyStatsMixin:

    def _extract_item_features(self, item):
        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return None

        duration = t_e - t_s
        p_xs, p_freqs = self._get_pitch_arrays_for_item(item)
        if p_xs is None or p_freqs is None or len(p_xs) == 0:
            if item.get('preview_f0'):
                p_freqs = np.array(item['preview_f0'])
                active_freqs = p_freqs[~np.isnan(p_freqs) & (p_freqs > 0)]
                mean_f0 = float(np.mean(active_freqs)) if len(active_freqs) > 0 else 0.0
                f0_range = float(np.max(active_freqs) - np.min(active_freqs)) if len(active_freqs) > 0 else 0.0
                active_ratio = float(len(active_freqs) / len(p_freqs)) if len(p_freqs) > 0 else 0.0
                return {
                    'duration': duration,
                    'mean_f0': mean_f0,
                    'f0_range': f0_range,
                    'active_ratio': active_ratio
                }
            return None

        mask = (p_xs >= t_s) & (p_xs <= t_e)
        p_xs_slice = p_xs[mask]
        p_freqs_slice = p_freqs[mask]

        if len(p_xs_slice) == 0:
            return {
                'duration': duration,
                'mean_f0': 0.0,
                'f0_range': 0.0,
                'active_ratio': 0.0
            }

        active_freqs = p_freqs_slice[~np.isnan(p_freqs_slice) & (p_freqs_slice > 0)]
        if len(active_freqs) == 0:
            return {
                'duration': duration,
                'mean_f0': 0.0,
                'f0_range': 0.0,
                'active_ratio': 0.0
            }

        mean_f0 = float(np.mean(active_freqs))
        f0_range = float(np.max(active_freqs) - np.min(active_freqs))
        active_ratio = float(len(active_freqs) / len(p_xs_slice))

        return {
            'duration': duration,
            'mean_f0': mean_f0,
            'f0_range': f0_range,
            'active_ratio': active_ratio
        }

    def analyze_item_anomalies(self, item, group_stats=None, speaker_stats=None):
        if item and item.get('ignore_warnings'):
            return []
        warnings = []
        if not item or item.get('start') is None:
            warnings.append("[致命] 时间边界无效或缺失")
            return warnings

        if item.get('preview_segment_mismatch'):
            warnings.append("[致命] 子段数量与预览不匹配")

        mode = getattr(self, 'app_state_params', {}).get('analysis_mode', 'f0')
        if mode == 'formant':
            f_data = item.get('formant_data')
            if not f_data:
                # "未分析" (Not Analyzed) — No warnings!
                return []
            f_xs = f_data.get('xs', np.array([]))
            f1 = f_data.get('f1', np.array([]))
            f2 = f_data.get('f2', np.array([]))
            if len(f_xs) == 0:
                warnings.append("[致命] 共振峰数据为空")
                return warnings
            if self._check_item_has_empty_data(item):
                warnings.append("[警告] 共振峰存在明显缺失帧或无效帧，建议复核边界与参数")

            t_s, t_e = item.get('start'), item.get('end')
            label = item.get('label', '')
            syls = split_into_syllables(label)
            chars_bounds = item.get('chars_bounds', [])
            if chars_bounds and len(chars_bounds) == len(syls):
                bounds = chars_bounds
            else:
                inner_splits = item.get('inner_splits', [])
                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(syls) > 1 and len(splits) != len(syls) + 1:
                    splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                elif len(syls) <= 1:
                    splits = [t_s, t_e]
                bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

            for idx, (c_s, c_e) in enumerate(bounds):
                char = syls[idx] if idx < len(syls) else f"音节{idx+1}"
                dur = c_e - c_s
                if dur <= 0:
                    warnings.append(f"[致命] 音节 [{char}] 时间边界无效")
                    continue
                margin = dur * 0.125
                core_s = c_s + margin
                core_e = c_e - margin

                mask = (f_xs >= core_s) & (f_xs <= core_e)
                seg_xs = f_xs[mask]
                seg_f1 = f1[mask]
                seg_f2 = f2[mask]
                if len(seg_xs) == 0:
                    warnings.append(f"[致命] 音节 [{char}] 核心区间无共振峰数据")
                    continue
                valid_mask = ~np.isnan(seg_f1) & ~np.isnan(seg_f2) & (seg_f2 > seg_f1)
                ratio = np.sum(valid_mask) / len(seg_xs)
                if ratio < 0.30:
                    warnings.append(f"[致命] 音节 [{char}] 共振峰有效帧比例过低 ({ratio:.1%} < 30%)")
                elif ratio < 0.55:
                    warnings.append(f"[警告] 音节 [{char}] 共振峰有效帧比例偏低 ({ratio:.1%} < 55%)")

                finite_pair = np.isfinite(seg_f1) & np.isfinite(seg_f2)
                if np.any(finite_pair):
                    bad_order_ratio = float(np.sum(seg_f2[finite_pair] <= seg_f1[finite_pair])) / float(np.sum(finite_pair))
                    if bad_order_ratio >= 0.20:
                        warnings.append(f"[警告] 音节 [{char}] 出现较多 F2<=F1 的异常帧 ({bad_order_ratio:.1%})")

                # Track-level anomaly detection: catch sharp F1/F2 cliffs even when coverage ratio is high.
                if np.sum(valid_mask) >= 4:
                    v_f1 = seg_f1[valid_mask]
                    v_f2 = seg_f2[valid_mask]

                    f2_diff = np.abs(np.diff(v_f2))
                    med_f2 = float(np.nanmedian(v_f2)) if len(v_f2) > 0 else 0.0
                    if med_f2 > 0:
                        f2_rel = f2_diff / max(med_f2, 1e-9)
                        if np.any((f2_diff > 260.0) & (f2_rel > 0.20)):
                            max_jump = float(np.max(f2_diff))
                            warnings.append(f"[警告] 音节 [{char}] F2 轨迹跳变异常 (最大跳变 {max_jump:.0f}Hz)")

                    f1_diff = np.abs(np.diff(v_f1))
                    med_f1 = float(np.nanmedian(v_f1)) if len(v_f1) > 0 else 0.0
                    if med_f1 > 0:
                        f1_rel = f1_diff / max(med_f1, 1e-9)
                        if np.any((f1_diff > 180.0) & (f1_rel > 0.30)):
                            max_jump = float(np.max(f1_diff))
                            warnings.append(f"[提示] 音节 [{char}] F1 轨迹波动较大 (最大跳变 {max_jump:.0f}Hz)")

            split_warnings = item.get('split_warnings', [])
            for sw in split_warnings:
                if sw == 'tiny_segment':
                    warnings.append("[致命] 边界过短 (某个子段短于 80ms)")
                elif sw == 'imbalanced_duration':
                    warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
                elif sw == 'no_clear_valley':
                    warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
                elif sw == 'fallback_equal_split':
                    warnings.append("[提示] 采用等分兜底切割")
            # Keep warning panel readable: preserve order while removing duplicate messages.
            return list(dict.fromkeys(warnings))

        if self._check_item_has_empty_data(item):
            warnings.append("[致命] 基频数据含有0值 (F0 缺失)")

        feats = self._extract_item_features(item)
        if feats is not None:
            p_xs, p_freqs = self._get_pitch_arrays_for_item(item)
            if p_xs is not None and p_freqs is not None and len(p_xs) > 0:
                t_s, t_e = item.get('start'), item.get('end')
                mask = (p_xs >= t_s) & (p_xs <= t_e)
                p_xs_slice = p_xs[mask]
                p_freqs_slice = p_freqs[mask]

                _, bounds = self._get_syllables_and_bounds(item)
                if not bounds:
                    bounds = [[t_s, t_e]]

                anomaly_points = detect_pitch_anomaly_points(
                    p_xs_slice, p_freqs_slice, bounds=bounds, start=t_s, end=t_e
                )
                if len(anomaly_points) > 0:
                    jump_times = ", ".join([f"{t:.2f}s" for t, _ in anomaly_points[:5]])
                    suffix = "..." if len(anomaly_points) > 5 else ""
                    warnings.append(f"[警告] 疑似倍频/半频/噪声点 (发生在: {jump_times}{suffix})")

            split_warnings = item.get('split_warnings', [])
            for sw in split_warnings:
                if sw == 'tiny_segment':
                    warnings.append("[致命] 边界过短 (某个子段短于 80ms)")
                elif sw == 'imbalanced_duration':
                    warnings.append("[警告] 时长严重失衡 (子段时长比例不均)")
                elif sw == 'no_clear_valley':
                    warnings.append("[警告] 未能识别到能量谷 (子音节切分谷底不明显)")
                elif sw == 'fallback_equal_split':
                    warnings.append("[提示] 采用等分兜底切割")
                elif sw == 'low_f0_coverage':
                    warnings.append("[致命] F0 覆盖率低 (某子段有效基频点比例低于 30%)")

            g = (item.get('group', '导入内容'), len(item.get('chars_bounds', [[0, 1]])))
            if group_stats and (g in group_stats or item.get('group', '导入内容') in group_stats):
                g_feats = group_stats.get(g) or group_stats.get(item.get('group', '导入内容'))
                for key, (med, mad) in g_feats.items():
                    val = feats[key]
                    if mad > 0:
                        deviation = abs(val - med) / mad
                        if deviation > 4.0:
                            if key == 'duration':
                                warnings.append(f"[提示] 时长明显偏离同类项目 (当前 {val:.3f}s, 同类中位数 {med:.3f}s)")
                            elif key == 'mean_f0':
                                warnings.append(f"[提示] 基频均值明显偏离同类项目 (当前 {val:.1f}Hz, 同类中位数 {med:.1f}Hz)")
                            elif key == 'f0_range':
                                warnings.append(f"[提示] F0 波动范围明显偏离同类项目 (当前 {val:.1f}Hz, 同类中位数 {med:.1f}Hz)")
                            elif key == 'active_ratio' and val < med:
                                warnings.append(f"[提示] 有效点比例偏低 (当前 {val:.1%}, 同类中位数 {med:.1%})")

            if speaker_stats:
                spk_id = self.app.speaker_manager.active_speaker_id if self.app and hasattr(self.app, 'speaker_manager') else None
                if spk_id and spk_id in speaker_stats:
                    spk_info = speaker_stats[spk_id]
                    if spk_info.get('mean_f0_outlier'):
                        warnings.append(f"[提示] 建议检查 Pitch Floor/Ceiling 或录音质量 (发音人整体基频 {spk_info['mean_f0']:.1f}Hz 偏离其他发音人中位数)")
                    g_feats = group_stats.get(g) if group_stats else None
                    if g_feats and 'active_ratio' in g_feats:
                        g_active_med = g_feats['active_ratio'][0]
                        if g_active_med < 0.60:
                            warnings.append(f"[提示] 建议检查 Pitch Floor/Ceiling 或录音质量 (当前组平均有效基频点比例仅 {g_active_med:.1%})")

        return warnings

    def _shorten_warning(self, warning_msg):
        msg = warning_msg
        for prefix in ("[致命]", "[警告]", "[提示]"):
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()

        if "时间边界" in msg and "无效" in msg:
            return "边界无效"
        if "时间边界" in msg and "缺失" in msg:
            return "边界缺失"
        if "子段数量" in msg:
            return "子段不匹配"
        if "共振峰数据为空" in msg:
            return "无共振峰"
        if "共振峰存在明显缺失" in msg:
            return "共振峰缺失"
        if "核心区间无共振峰" in msg:
            return "核心无数据"
        if "共振峰有效帧" in msg and "过低" in msg:
            return "有效帧率低"
        if "共振峰有效帧" in msg and "偏低" in msg:
            return "有效帧率低"
        if "F2<=F1" in msg or "F2<=F1" in msg.replace(" ", ""):
            return "F2F1异常"
        if "F2 轨迹跳变" in msg:
            return "F2跳变"
        if "F1 轨迹波动" in msg:
            return "F1波动"
        if "边界过短" in msg:
            return "边界过短"
        if "时长严重失衡" in msg:
            return "时长失衡"
        if "未能识别到能量谷" in msg or "能量谷" in msg:
            return "无能量谷"
        if "等分兜底" in msg:
            return "等分兜底"
        if "基频数据含有0值" in msg or "F0 缺失" in msg:
            return "基频缺失"
        if "倍频/半频/噪声" in msg or "倍频" in msg or "半频" in msg or "噪声" in msg:
            return "疑似噪声"
        if "F0 覆盖率低" in msg or "F0覆盖率低" in msg:
            return "基频率低"
        if "时长明显偏离" in msg:
            return "时长偏离"
        if "基频均值明显偏离" in msg:
            return "基频偏离"
        if "波动范围明显偏离" in msg:
            return "波动偏离"
        if "有效点比例偏低" in msg:
            return "有效点低"
        if "Pitch Floor/Ceiling" in msg or "整体基频" in msg:
            return "检查设置"
        if "平均有效" in msg:
            return "有效率低"

        msg = msg.replace("[", "").replace("]", "").replace(" ", "")
        import re
        msg = re.sub(r"音节\[.*?\]", "", msg)
        msg = msg.split("(")[0]
        msg = msg.split("（")[0]

        if len(msg) > 6:
            return msg[:6]
        return msg

    def _compact_tree_info_text(self, text, max_chars=TREE_INFO_MAX_CHARS):
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        if max_chars <= 2:
            return text[:max_chars]
        return text[:max_chars - 2] + ".."

    def _format_tree_info_labels(self, labels):
        labels = [label for label in labels if label]
        if not labels:
            return ""
        if len(labels) == 1:
            return self._compact_tree_info_text(labels[0])
        suffix = f"+{len(labels) - 1}"
        first_max = max(2, TREE_INFO_MAX_CHARS - len(suffix))
        return f"{self._compact_tree_info_text(labels[0], first_max)}{suffix}"

    def _shorten_exclusion_reason(self, reason):
        reason = (reason or "").strip()
        if not reason:
            return "已忽略"
        category, detail, custom = parse_exclusion_reason(reason)
        if category == EXCLUSION_REASON_CUSTOM_CATEGORY:
            return self._compact_tree_info_text(custom)
        if detail and detail != EXCLUSION_REASON_DETAIL_PLACEHOLDER:
            return EXCLUSION_REASON_SHORT_LABELS.get(detail, self._compact_tree_info_text(detail))
        return EXCLUSION_REASON_SHORT_LABELS.get(category, self._compact_tree_info_text(category))

    def _get_item_info_text(self, item):
        if item.get('is_excluded', False):
            reason = item.get('exclusion_reason', '')
            return self._shorten_exclusion_reason(reason)

        warn_msgs = item.get('warnings', [])
        short_warns = []
        for w in warn_msgs:
            if w.startswith("[提示]"):
                continue
            short_w = self._shorten_warning(w)
            if short_w and short_w not in short_warns:
                short_warns.append(short_w)
        return self._format_tree_info_labels(short_warns)

    def _compute_group_and_speaker_stats(self):
        # 计算项目统计信息以用于离群值检测
        group_stats = {}
        groups_items_features = {}
        for iid, item in self.items.items():
            syl_count = len(item.get('chars_bounds', [[0, 1]]))
            g = (item.get('group', '导入内容'), syl_count)
            if g not in groups_items_features:
                groups_items_features[g] = []
            feats = self._extract_item_features(item)
            if feats is not None:
                groups_items_features[g].append(feats)

        # 计算每组的 median and robust spread。普通统计离群只作为轻提示，
        # 因此要求更多样本并使用较宽的下限，避免正常语速/声调差异刷屏。
        for g, feats_list in groups_items_features.items():
            if len(feats_list) >= 8:
                group_stats[g] = {}
                for key in ['duration', 'mean_f0', 'f0_range', 'active_ratio']:
                    if key in ('mean_f0', 'f0_range'):
                        vals = np.array([
                            f[key] for f in feats_list
                            if f.get('active_ratio', 0.0) >= 0.60 and f.get(key, 0.0) > 0
                        ])
                    else:
                        vals = np.array([f[key] for f in feats_list])
                    if len(vals) < 8:
                        continue
                    med = np.median(vals)
                    abs_dev = np.abs(vals - med)
                    mad = 1.4826 * np.median(abs_dev)
                    if key == 'duration':
                        min_spread = max(0.15, abs(med) * 0.25)
                    elif key == 'mean_f0':
                        min_spread = 35.0
                    elif key == 'f0_range':
                        min_spread = 40.0
                    else:
                        min_spread = 0.20
                    mad = max(float(mad), min_spread)
                    group_stats[g][key] = (med, mad)

        # 计算发音人统计信息以用于组级检测
        speaker_stats = {}
        sm = getattr(self.app, 'speaker_manager', None)
        if sm:
            all_speakers = sm.get_all_speakers()
            speaker_means = []
            for spk in all_speakers:
                spk_freqs = []
                spk_ratios = []
                for iid, item in spk.items.items():
                    feats = self._extract_item_features(item)
                    if feats is not None:
                        if feats['mean_f0'] > 0:
                            spk_freqs.append(feats['mean_f0'])
                        spk_ratios.append(feats['active_ratio'])
                mean_f0 = np.mean(spk_freqs) if spk_freqs else 0.0
                mean_ratio = np.mean(spk_ratios) if spk_ratios else 0.0
                speaker_stats[spk.id] = {
                    'mean_f0': mean_f0,
                    'mean_ratio': mean_ratio,
                    'mean_f0_outlier': False
                }
                if mean_f0 > 0:
                    speaker_means.append((spk.id, mean_f0))

            if len(speaker_means) >= 3:
                means = np.array([item[1] for item in speaker_means])
                med_spk = np.median(means)
                mad_spk = max(1.4826 * np.median(np.abs(means - med_spk)), 45.0)
                for spk_id, mean_f0 in speaker_means:
                    if abs(mean_f0 - med_spk) / mad_spk > 4.0:
                        speaker_stats[spk_id]['mean_f0_outlier'] = True

        return group_stats, speaker_stats

