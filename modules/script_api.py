# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本 API 上下文定义
"""

import os
import zipfile
import json
import math
import numpy as np


def configure_matplotlib_chinese_font():
    """
    为脚本生成的 Matplotlib 图表配置中文字体。
    Windows 打包环境优先使用 Microsoft YaHei，其他平台按常见 CJK 字体兜底。
    """
    try:
        import matplotlib
        from matplotlib import font_manager

        candidates = [
            "Microsoft YaHei",
            "SimHei",
            "SimSun",
            "DengXian",
            "Noto Sans CJK SC",
            "Noto Sans CJK JP",
            "Arial Unicode MS",
            "PingFang SC",
            "WenQuanYi Micro Hei",
        ]
        installed = {font.name for font in font_manager.fontManager.ttflist}
        preferred = [name for name in candidates if name in installed]
        if preferred:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = preferred + ["DejaVu Sans", "sans-serif"]
        else:
            matplotlib.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans", "sans-serif"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass


class FigureResult:
    """
    表示脚本生成的图表结果。
    """
    def __init__(self, fig, filename="custom_chart.png", title="自定义图表"):
        self.fig = fig
        self.filename = filename
        self.title = title

class TableResult:
    """
    表示脚本生成的数据表格结果。
    """
    def __init__(self, rows, columns, title="自定义表格"):
        self.rows = rows
        self.columns = columns
        self.title = title

class DatasetSnapshot:
    """
    只读数据快照包装器。
    """
    def __init__(self, items):
        self.items = items  # list of dicts

    def groups(self):
        """返回所有不重复且非空的分组列表（排序后）"""
        grps = set()
        for item in self.items:
            g = item.get("group")
            if g:
                grps.add(g)
        return sorted(list(grps))

    def speakers(self):
        """返回所有不重复的发音人姓名列表（排序后）"""
        spks = set()
        for item in self.items:
            s = item.get("speaker_name")
            if s:
                spks.add(s)
        return sorted(list(spks))

    def included_items(self):
        """返回所有未排除的分析条目"""
        return [item for item in self.items if not item.get("is_excluded", False)]

    def pitch_points(self, item):
        """获取指定条目的基频点数据"""
        return item.get("pitch", {"xs": [], "freqs": [], "t_values": []})

    def formant_points(self, item):
        """获取指定条目的共振峰点数据"""
        return item.get("formant", {"xs": [], "f1": [], "f2": []})

class ScriptContext:
    """
    传递给脚本 run(ctx) 函数的上下文对象。
    """
    def __init__(self, dataset_items, cancel_event=None):
        import numpy as np
        import matplotlib.pyplot as plt
        import scipy

        configure_matplotlib_chinese_font()
        self.dataset = DatasetSnapshot(dataset_items)
        self.np = np
        self.plt = plt
        self.scipy = scipy
        self._cancel_event = cancel_event
        self._logs = []

    def log(self, message):
        """记录一条运行日志"""
        self._logs.append(str(message))

    def figure(self, fig, filename="custom_chart.png", title="自定义图表"):
        """返回图表结果对象"""
        return FigureResult(fig, filename, title)

    def table(self, rows, columns, title="自定义表格"):
        """返回表格结果对象"""
        return TableResult(rows, columns, title)

    def is_cancelled(self):
        """脚本长循环中可调用，用于协作式响应用户取消。"""
        return bool(self._cancel_event and self._cancel_event.is_set())


def build_dataset_snapshot(teproj_path):
    """
    解析 .teproj 文件（ZIP格式）中的 project.json 和缓存数据，
    构造脚本所需的只读快照数据。
    """
    if not teproj_path or not os.path.exists(teproj_path):
        return []

    try:
        with zipfile.ZipFile(teproj_path, 'r') as zf:
            try:
                raw = zf.read("project.json")
            except KeyError:
                return []
            project_data = json.loads(raw.decode("utf-8"))

            # Create a lookup mapping from decoded (correct) filenames to original ZIP members
            namelist_map = {}
            for name in zf.namelist():
                try:
                    decoded = name.encode('cp437').decode('gbk')
                except Exception:
                    try:
                        decoded = name.encode('cp437').decode('utf-8')
                    except Exception:
                        decoded = name
                decoded_norm = decoded.replace("\\", "/")
                namelist_map[decoded_norm] = name
                namelist_map[name.replace("\\", "/")] = name

            speakers = project_data.get("speakers", {})
            items_snapshot = []

            # 1. 遍历发音人，计算发音人级别的 F0 分布（用于计算 T 值）
            for spk_id, spk in speakers.items():
                spk_name = spk.get("name", "发音人")
                items = spk.get("items", {})

                # 收集当前发音人所有有效的 F0 频率，用于计算 5% 与 95% 分位数
                f0_pool = []
                loaded_pitches = {}
                loaded_formants = {}

                # 批量读取并预载 npz 缓存
                for item_id, item in items.items():
                    pitch_file = item.get("pitch_data_file")
                    if pitch_file:
                        pitch_file_norm = pitch_file.replace("\\", "/")
                        real_pitch_file = namelist_map.get(pitch_file_norm)
                        if real_pitch_file:
                            try:
                                with zf.open(real_pitch_file) as pf:
                                    data = np.load(pf)
                                    xs = data["xs"].tolist()
                                    freqs = data["freqs"].tolist()
                                    loaded_pitches[item_id] = (xs, freqs)
                                    f0_pool.extend([f for f in freqs if f > 0])
                            except Exception:
                                pass

                    formant_file = item.get("formant_data_file")
                    if formant_file:
                        formant_file_norm = formant_file.replace("\\", "/")
                        real_formant_file = namelist_map.get(formant_file_norm)
                        if real_formant_file:
                            try:
                                with zf.open(real_formant_file) as ff:
                                    data = np.load(ff)
                                    xs = data["xs"].tolist()
                                    f1 = data["f1"].tolist()
                                    f2 = data["f2"].tolist()
                                    loaded_formants[item_id] = (xs, f1, f2)
                            except Exception:
                                pass

                # 计算基准
                if f0_pool:
                    s_min = np.percentile(f0_pool, 5.0)
                    s_max = np.percentile(f0_pool, 95.0)
                else:
                    s_min, s_max = 75.0, 600.0

                if s_max > s_min and s_min > 0:
                    log_s_min = math.log10(s_min)
                    log_s_max_min = math.log10(s_max) - log_s_min
                else:
                    log_s_min = 0.0
                    log_s_max_min = 1.0

                # 2. 填充条目明细快照
                for item_id, item in items.items():
                    start = item.get("start", 0.0)
                    end = item.get("end", 0.0)
                    duration = end - start

                    # 计算 F0 的 T 值
                    xs_p, freqs = loaded_pitches.get(item_id, ([], []))
                    t_values = []
                    for f in freqs:
                        if f > 0:
                            if s_max > s_min and s_min > 0:
                                t = 1.0 + 4.0 * (math.log10(f) - log_s_min) / log_s_max_min
                                t = max(1.0, min(5.0, t))
                            else:
                                t = 3.0
                            t_values.append(t)
                        else:
                            t_values.append(np.nan)

                    # 共振峰数据
                    xs_f, f1, f2 = loaded_formants.get(item_id, ([], [], []))
                    f1_clean = [f if not np.isnan(f) else np.nan for f in f1]
                    f2_clean = [f if not np.isnan(f) else np.nan for f in f2]

                    # 提取每个音节的 F0 数据 (syl_data)、T值数据 (syl_t_values) 与共振峰数据 (syl_formants)
                    syl_data = []
                    syl_t_values = []
                    syl_formants = []
                    try:
                        from modules.data_utils import get_item_syllable_bounds, split_into_syllables, sample_formant_points_by_bounds
                        
                        # 构造临时字典用于获取音节边界和单字
                        tmp_item = {
                            "start": start,
                            "end": end,
                            "label": item.get("label", ""),
                            "chars_bounds": item.get("chars_bounds", []),
                            "inner_splits": item.get("inner_splits", [])
                        }
                        bounds = get_item_syllable_bounds(tmp_item)
                        syls = split_into_syllables(tmp_item["label"])

                        # 1. 提取 F0 syl_data & syl_t_values
                        if xs_p and freqs:
                            p_xs_arr = np.asarray(xs_p)
                            p_freqs_arr = np.asarray(freqs)
                            p_t_arr = np.asarray(t_values)
                            for c_s, c_e in bounds:
                                if c_e <= c_s:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                valid_idx = np.where((p_xs_arr >= c_s) & (p_xs_arr <= c_e) & (p_freqs_arr > 0))[0]
                                if len(valid_idx) >= 2:
                                    v_s, v_e = p_xs_arr[valid_idx[0]], p_xs_arr[valid_idx[-1]]
                                    seg_xs = p_xs_arr[valid_idx]
                                    seg_ys = p_freqs_arr[valid_idx]
                                    seg_ts = p_t_arr[valid_idx]
                                else:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                dur = v_e - v_s
                                if dur <= 0:
                                    syl_data.append((0.0, [0.0]*11))
                                    syl_t_values.append([np.nan]*11)
                                    continue
                                times_p = np.linspace(v_s, v_e, 11)
                                f0s = np.interp(times_p, seg_xs, seg_ys).tolist()
                                ts_vals = np.interp(times_p, seg_xs, seg_ts).tolist()
                                for j, t in enumerate(times_p):
                                    if np.min(np.abs(seg_xs - t)) > 0.025:
                                        f0s[j] = 0.0
                                        ts_vals[j] = np.nan
                                syl_data.append((dur, f0s))
                                syl_t_values.append(ts_vals)

                        # 2. 提取共振峰 syl_formants
                        if xs_f and f1_clean and f2_clean:
                            f_data_mock = {
                                "xs": np.asarray(xs_f),
                                "f1": np.asarray(f1_clean),
                                "f2": np.asarray(f2_clean)
                            }
                            mock_item = {
                                "start": start,
                                "end": end,
                                "label": item.get("label", ""),
                                "chars_bounds": item.get("chars_bounds", []),
                                "inner_splits": item.get("inner_splits", []),
                                "formant_data": f_data_mock
                            }
                            strategy = item.get("formant_sample_strategy", spk.get("last_params", {}).get("formant_sample_strategy", "整段11点"))
                            times_f, f1_vals, f2_vals = sample_formant_points_by_bounds(mock_item, bounds, 11, strategy)
                            for idx_syl, (c_s, c_e) in enumerate(bounds):
                                char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                                s_idx = idx_syl * 11
                                e_idx = s_idx + 11
                                s_times = times_f[s_idx:e_idx]
                                s_f1 = f1_vals[s_idx:e_idx]
                                s_f2 = f2_vals[s_idx:e_idx]
                                syl_formants.append({
                                    "syllable_index": idx_syl,
                                    "char": char,
                                    "bounds": [c_s, c_e],
                                    "times": s_times,
                                    "f1": s_f1,
                                    "f2": s_f2
                                })
                    except Exception as ex:
                        print(f"Error extracting syllable data in snapshot: {ex}")

                    snapshot_item = {
                        "speaker_id": spk_id,
                        "speaker_name": spk_name,
                        "item_id": item_id,
                        "label": item.get("label", ""),
                        "group": item.get("group", "默认组"),
                        "is_excluded": item.get("is_excluded", False),
                        "analysis_mode": item.get("analysis_mode", spk.get("last_params", {}).get("analysis_mode", "f0")),
                        "start": start,
                        "end": end,
                        "duration": duration,
                        "pitch": {
                            "xs": xs_p,
                            "freqs": freqs,
                            "t_values": t_values
                        },
                        "formant": {
                            "xs": xs_f,
                            "f1": f1_clean,
                            "f2": f2_clean
                        },
                        "syl_data": syl_data,
                        "syl_t_values": syl_t_values,
                        "syl_formants": syl_formants
                    }
                    items_snapshot.append(snapshot_item)

            return items_snapshot
    except Exception as e:
        print(f"Error building dataset snapshot: {e}")
        return []
