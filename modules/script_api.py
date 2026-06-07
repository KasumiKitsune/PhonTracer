# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本 API 上下文定义
"""

import os
import zipfile
import json
import math
import numpy as np

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
                    if pitch_file and pitch_file in zf.namelist():
                        try:
                            with zf.open(pitch_file) as pf:
                                data = np.load(pf)
                                xs = data["xs"].tolist()
                                freqs = data["freqs"].tolist()
                                loaded_pitches[item_id] = (xs, freqs)
                                f0_pool.extend([f for f in freqs if f > 0])
                        except Exception:
                            pass

                    formant_file = item.get("formant_data_file")
                    if formant_file and formant_file in zf.namelist():
                        try:
                            with zf.open(formant_file) as ff:
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
                                t = 5.0 * (math.log10(f) - log_s_min) / log_s_max_min
                                t = max(0.0, min(5.0, t))
                            else:
                                t = 3.0
                            t_values.append(t)
                        else:
                            t_values.append(np.nan)

                    # 共振峰数据
                    xs_f, f1, f2 = loaded_formants.get(item_id, ([], [], []))
                    f1_clean = [f if not np.isnan(f) else np.nan for f in f1]
                    f2_clean = [f if not np.isnan(f) else np.nan for f in f2]

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
                        }
                    }
                    items_snapshot.append(snapshot_item)

            return items_snapshot
    except Exception as e:
        print(f"Error building dataset snapshot: {e}")
        return []
