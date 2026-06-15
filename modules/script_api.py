# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本 API 上下文定义
"""

import os
import zipfile
import json
import math
import tempfile
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


def get_f0_normalization_bounds(f0_values):
    arr = np.asarray(f0_values, dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size == 0:
        return 75.0, 600.0

    robust_arr = arr
    if arr.size >= 8:
        q1, q3 = np.percentile(arr, [25.0, 75.0])
        iqr = q3 - q1
        if iqr > 0:
            low = q1 - 1.5 * iqr
            high = q3 + 1.5 * iqr
            filtered = arr[(arr >= low) & (arr <= high)]
            if filtered.size >= max(8, int(arr.size * 0.5)):
                robust_arr = filtered

    s_min = float(np.percentile(robust_arr, 5.0))
    s_max = float(np.percentile(robust_arr, 95.0))
    if s_max > s_min and s_min > 0:
        return s_min, s_max
    return 75.0, 600.0


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


class ProjectPatchResult:
    """
    表示脚本生成的工程数据处理操作清单。
    脚本不直接修改 .teproj，只返回受控操作，由 Toolkit 统一校验和写回。
    """
    ALLOWED_OPS = {
        "set_item_fields",
        "recompute_pitch",
        "recompute_formant",
        "trim_item_audio",
        "split_project",
        "import_csv_metadata",
    }

    def __init__(self, operations, title="数据处理脚本结果", description=""):
        if operations is None:
            operations = []
        if not isinstance(operations, list):
            raise ValueError("project_patch 的 operations 必须是列表。")

        clean_ops = []
        for idx, op in enumerate(operations, start=1):
            if not isinstance(op, dict):
                raise ValueError(f"第 {idx} 个数据处理操作不是字典。")
            op_type = op.get("op")
            if op_type not in self.ALLOWED_OPS:
                raise ValueError(f"第 {idx} 个数据处理操作类型不受支持：{op_type}")
            clean_ops.append(dict(op))

        self.operations = clean_ops
        self.title = title
        self.description = description

    @property
    def operation_count(self):
        return len(self.operations)


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


def _build_zip_member_lookup(zf):
    lookup = {}
    for name in zf.namelist():
        try:
            decoded = name.encode('cp437').decode('gbk')
        except Exception:
            try:
                decoded = name.encode('cp437').decode('utf-8')
            except Exception:
                decoded = name
        lookup[decoded.replace("\\", "/")] = name
        lookup[name.replace("\\", "/")] = name
    return lookup


def _normalize_audio_resource_path(path):
    if not path:
        return None
    norm = str(path).replace("\\", "/")
    parts = norm.split("/")
    if len(parts) < 2 or parts[0] != "audio":
        raise ValueError(f"工程音频资源路径不受支持：{path}")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"工程音频资源路径非法：{path}")
    return norm


def _coerce_time(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _item_display_label(item, label_field="auto"):
    if not isinstance(item, dict):
        return ""
    meta = item.get("item_meta") or {}
    if not isinstance(meta, dict):
        meta = {}

    if label_field and label_field != "auto":
        if label_field.startswith("item_meta."):
            value = meta.get(label_field.split(".", 1)[1])
        else:
            value = item.get(label_field)
        if value:
            return str(value)

    for key in ("IPA", "ipa", "音标", "国际音标"):
        value = meta.get(key)
        if value:
            return str(value)

    aliases = item.get("item_aliases") or []
    if isinstance(aliases, (list, tuple)) and aliases:
        return str(aliases[0])
    if isinstance(aliases, str) and aliases.strip():
        return aliases.strip()

    return str(item.get("label") or item.get("item_id") or "")


class ScriptContext:
    """
    传递给脚本 run(ctx) 函数的上下文对象。
    """
    def __init__(self, dataset_items, cancel_event=None, teproj_path=None):
        import numpy as np
        import matplotlib.pyplot as plt
        import scipy
        import parselmouth

        configure_matplotlib_chinese_font()
        self.dataset = DatasetSnapshot(dataset_items)
        self.np = np
        self.plt = plt
        self.scipy = scipy
        self.parselmouth = parselmouth
        self._teproj_path = teproj_path
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

    def _read_audio_resource(self, rel_path):
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        norm_path = _normalize_audio_resource_path(rel_path)
        with zipfile.ZipFile(self._teproj_path, "r") as zf:
            member_lookup = _build_zip_member_lookup(zf)
            member = member_lookup.get(norm_path)
            if not member:
                raise FileNotFoundError(f"工程中找不到音频资源：{norm_path}")
            return zf.read(member)

    def _load_sound_from_resource(self, rel_path):
        audio_bytes = self._read_audio_resource(rel_path)
        suffix = os.path.splitext(str(rel_path))[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(prefix="phontracer_script_audio_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            return self.parselmouth.Sound(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _project_item_for_snapshot(self, item):
        if not isinstance(item, dict):
            raise ValueError("load_item_sound 需要传入 ctx.dataset.items 中的条目字典。")
        if not self._teproj_path or not os.path.exists(self._teproj_path):
            raise ValueError("当前脚本上下文没有可用工程文件，无法读取受控音频资源。")

        speaker_id = item.get("speaker_id")
        item_id = item.get("item_id")
        with zipfile.ZipFile(self._teproj_path, "r") as zf:
            try:
                raw = zf.read("project.json")
            except KeyError as exc:
                raise ValueError("工程文件缺少 project.json，无法读取音频。") from exc
        project_data = json.loads(raw.decode("utf-8"))
        speakers = project_data.get("speakers", {})

        if speaker_id in speakers:
            spk = speakers[speaker_id]
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        for spk in speakers.values():
            if not isinstance(spk, dict):
                continue
            items = spk.get("items", {}) or {}
            if item_id in items:
                return spk, items[item_id]

        label = item.get("label", "")
        raise ValueError(f"工程中找不到目标条目：speaker_id={speaker_id}, item_id={item_id}, label={label}")

    def load_item_sound(self, item, padding=0.0):
        """
        从当前 .teproj 中受控读取条目音频，返回 parselmouth.Sound。
        独立音频优先读取条目 path；长音频条目会按 start/end 提取当前片段。
        """
        spk, project_item = self._project_item_for_snapshot(item)
        item_path = project_item.get("path")
        padding = max(0.0, _coerce_time(padding, 0.0))

        if item_path:
            snd = self._load_sound_from_resource(item_path)
            total = float(snd.get_total_duration())
            start = _coerce_time(project_item.get("start"), 0.0)
            end = _coerce_time(project_item.get("end"), total)
            if end > start and (start > 0.0 or end < total):
                from_time = max(0.0, start - padding)
                to_time = min(total, end + padding)
                if to_time > from_time:
                    return snd.extract_part(from_time=from_time, to_time=to_time)
            return snd

        long_audio_path = spk.get("long_audio_path")
        if not long_audio_path:
            raise ValueError(f"条目 {project_item.get('label', '')} 缺少可用音频资源。")

        snd = self._load_sound_from_resource(long_audio_path)
        total = float(snd.get_total_duration())
        start = _coerce_time(project_item.get("start"), 0.0)
        end = _coerce_time(project_item.get("end"), total)
        from_time = max(0.0, start - padding)
        to_time = min(total, end + padding)
        if to_time <= from_time:
            raise ValueError(f"条目 {project_item.get('label', '')} 的音频时间范围无效。")
        return snd.extract_part(from_time=from_time, to_time=to_time)

    def spectrogram_data(self, sound, max_frequency=5000.0, window_length=0.005, dynamic_range_db=50.0):
        """
        使用 Parselmouth 生成 dB 语谱图矩阵，返回 x/y/db/vmin/vmax 等绘图数据。
        """
        if sound is None:
            raise ValueError("spectrogram_data 需要传入 parselmouth.Sound。")
        max_frequency = _coerce_time(max_frequency, 5000.0)
        window_length = _coerce_time(window_length, 0.005)
        dynamic_range_db = max(1.0, _coerce_time(dynamic_range_db, 50.0))
        try:
            nyquist = float(sound.sampling_frequency) / 2.0
            if nyquist > 0:
                max_frequency = min(max_frequency, nyquist)
        except Exception:
            pass
        if max_frequency <= 0:
            max_frequency = 5000.0
        if window_length <= 0:
            window_length = 0.005

        spectrogram = sound.to_spectrogram(window_length=window_length, maximum_frequency=max_frequency)
        x_grid = spectrogram.x_grid()
        y_grid = spectrogram.y_grid()
        values = np.where(spectrogram.values > 0, spectrogram.values, 1e-10)
        db_values = 10 * np.log10(values)
        finite = db_values[np.isfinite(db_values)]
        vmax = float(np.max(finite)) if finite.size else 0.0
        return {
            "x": x_grid,
            "y": y_grid,
            "db": db_values,
            "vmin": vmax - dynamic_range_db,
            "vmax": vmax,
            "max_frequency": float(max_frequency),
            "window_length": float(window_length),
            "dynamic_range_db": float(dynamic_range_db),
        }

    def plot_spectrogram_grid(
        self,
        items,
        columns=4,
        max_items=8,
        show_formant_arrows=True,
        label_field="auto",
        max_frequency=4000.0,
    ):
        """
        生成参考图式多宫格灰度语谱图，返回 Matplotlib Figure。
        """
        items = list(items or [])[: max(1, int(max_items or 8))]
        columns = max(1, int(columns or 4))
        rows = max(1, int(math.ceil(len(items) / columns))) if items else 1
        fig, axes = self.plt.subplots(
            rows,
            columns,
            figsize=(max(3.0, columns * 2.2), max(2.2, rows * 1.8)),
            squeeze=False,
        )
        axes_flat = [ax for row in axes for ax in row]

        if not items:
            ax = axes_flat[0]
            ax.text(0.5, 0.5, "没有可用音频条目", ha="center", va="center")
            ax.axis("off")
            for extra_ax in axes_flat[1:]:
                extra_ax.axis("off")
            fig.tight_layout()
            return fig

        for ax, item in zip(axes_flat, items):
            if self.is_cancelled():
                ax.axis("off")
                continue
            try:
                sound = self.load_item_sound(item)
                spec = self.spectrogram_data(sound, max_frequency=max_frequency)
                ax.pcolormesh(
                    spec["x"],
                    spec["y"],
                    spec["db"],
                    vmin=spec["vmin"],
                    vmax=spec["vmax"],
                    cmap="Greys",
                    shading="auto",
                )
                duration = float(sound.get_total_duration())
                ax.set_xlim(0.0, duration)
                ax.set_ylim(0.0, spec["max_frequency"])
                ax.set_xticks([0.0, duration])
                ax.tick_params(labelsize=7, length=2)
                if show_formant_arrows:
                    self._draw_formant_arrows(ax, item, duration, spec["max_frequency"])
            except Exception as exc:
                ax.text(0.5, 0.5, f"无法绘制\n{exc}", ha="center", va="center", fontsize=8)
                ax.set_xticks([])
                ax.set_yticks([])

            label = _item_display_label(item, label_field=label_field)
            ax.set_xlabel(f"[ {label} ]" if label else "", fontsize=10)
            ax.grid(True, color="#D1D5DB", linewidth=0.45, alpha=0.65)

        for ax in axes_flat[len(items):]:
            ax.axis("off")

        for row_idx in range(rows):
            axes[row_idx][0].set_ylabel("Hz", fontsize=8)

        fig.tight_layout()
        return fig

    def _draw_formant_arrows(self, ax, item, duration, max_frequency):
        formant = item.get("formant") or {}
        values = []
        for key in ("f1", "f2", "f3"):
            arr = np.asarray(formant.get(key, []), dtype=float)
            arr = arr[np.isfinite(arr) & (arr > 0) & (arr <= max_frequency)]
            if arr.size:
                values.append(float(np.nanmedian(arr)))
        if not values:
            return
        x0 = max(0.0, duration * 0.02)
        x1 = max(duration * 0.16, x0 + 0.001)
        for freq in values[:3]:
            ax.annotate(
                "",
                xy=(x1, freq),
                xytext=(x0, freq),
                arrowprops={"arrowstyle": "-|>", "color": "black", "lw": 1.0},
                zorder=8,
            )

    def _item_target(self, item):
        if not isinstance(item, dict):
            raise ValueError("数据处理操作需要传入工程条目 item 字典。")
        target = {
            "speaker_id": item.get("speaker_id"),
            "speaker_name": item.get("speaker_name"),
            "item_id": item.get("item_id"),
            "label": item.get("label", ""),
        }
        if not target["speaker_id"] or not target["item_id"]:
            raise ValueError("条目缺少 speaker_id 或 item_id，无法生成可写回操作。")
        return target

    def set_item_fields(self, item, fields, reason=""):
        """生成条目字段修改操作。"""
        if not isinstance(fields, dict):
            raise ValueError("set_item_fields 的 fields 必须是字典。")
        return {
            "op": "set_item_fields",
            "target": self._item_target(item),
            "fields": dict(fields),
            "reason": str(reason or ""),
        }

    def recompute_pitch(self, item, params=None, reason=""):
        """生成重算条目 F0 缓存的操作。"""
        return {
            "op": "recompute_pitch",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def recompute_formant(self, item, params=None, reason=""):
        """生成重算条目共振峰缓存的操作。"""
        return {
            "op": "recompute_formant",
            "target": self._item_target(item),
            "params": dict(params or {}),
            "reason": str(reason or ""),
        }

    def trim_item_audio(self, item, start=None, end=None, padding=0.0, reason=""):
        """生成裁剪条目音频并替换引用的操作。"""
        return {
            "op": "trim_item_audio",
            "target": self._item_target(item),
            "start": start,
            "end": end,
            "padding": float(padding or 0.0),
            "reason": str(reason or ""),
        }

    def split_project(self, name, item_ids=None, speaker_ids=None, reason=""):
        """生成按发音人或条目拆出新工程的操作。"""
        return {
            "op": "split_project",
            "name": str(name or "拆分工程"),
            "item_ids": list(item_ids or []),
            "speaker_ids": list(speaker_ids or []),
            "reason": str(reason or ""),
        }

    def import_csv_metadata(self, rows, match_on="label", field_map=None, reason=""):
        """
        生成外部表格元数据合并操作。
        第一版不允许脚本读取文件；rows 必须是已经结构化的字典列表。
        """
        if not isinstance(rows, list):
            raise ValueError("import_csv_metadata 的 rows 必须是字典列表。")
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("import_csv_metadata 的每一行必须是字典。")
            clean_rows.append(dict(row))
        return {
            "op": "import_csv_metadata",
            "rows": clean_rows,
            "match_on": str(match_on or "label"),
            "field_map": dict(field_map or {}),
            "reason": str(reason or ""),
        }

    def project_patch(self, operations, title="数据处理脚本结果", description=""):
        """返回工程数据处理结果对象。"""
        return ProjectPatchResult(operations, title=title, description=description)

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
                                    f3 = data["f3"].tolist() if "f3" in data else []
                                    loaded_formants[item_id] = (xs, f1, f2, f3)
                            except Exception:
                                pass

                # 计算基准
                s_min, s_max = get_f0_normalization_bounds(f0_pool)

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
                                t = 2.5
                            t_values.append(t)
                        else:
                            t_values.append(np.nan)

                    # 共振峰数据
                    loaded_val = loaded_formants.get(item_id, ([], [], [], []))
                    if len(loaded_val) == 3:
                        xs_f, f1, f2 = loaded_val
                        f3 = []
                    else:
                        xs_f, f1, f2, f3 = loaded_val

                    f1_clean = [f if not np.isnan(f) else np.nan for f in f1]
                    f2_clean = [f if not np.isnan(f) else np.nan for f in f2]
                    f3_clean = [f if not np.isnan(f) else np.nan for f in f3]

                    # 提取每个音节的 F0 数据 (syl_data)、T值数据 (syl_t_values) 与共振峰数据 (syl_formants)
                    syl_data = []
                    syl_t_values = []
                    syl_formants = []
                    try:
                        from modules.data_utils import get_item_syllable_bounds, split_into_syllables, sample_formant_points_by_bounds_extended
                        
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
                            if f3_clean:
                                f_data_mock["f3"] = np.asarray(f3_clean)

                            mock_item = {
                                "start": start,
                                "end": end,
                                "label": item.get("label", ""),
                                "chars_bounds": item.get("chars_bounds", []),
                                "inner_splits": item.get("inner_splits", []),
                                "formant_data": f_data_mock
                            }
                            strategy = item.get("formant_sample_strategy", spk.get("last_params", {}).get("formant_sample_strategy", "整段11点"))
                            show_f3 = bool(item.get("show_f3", spk.get("last_params", {}).get("show_f3", False)))
                            res_samp = sample_formant_points_by_bounds_extended(mock_item, bounds, 11, strategy, include_f3=show_f3)
                            times_f = res_samp["times"]
                            f1_vals = res_samp["f1"]
                            f2_vals = res_samp["f2"]
                            f3_vals = res_samp["f3"]

                            for idx_syl, (c_s, c_e) in enumerate(bounds):
                                char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                                s_idx = idx_syl * 11
                                e_idx = s_idx + 11
                                s_times = times_f[s_idx:e_idx]
                                s_f1 = f1_vals[s_idx:e_idx]
                                s_f2 = f2_vals[s_idx:e_idx]
                                s_dict = {
                                    "syllable_index": idx_syl,
                                    "char": char,
                                    "bounds": [c_s, c_e],
                                    "times": s_times,
                                    "f1": s_f1,
                                    "f2": s_f2
                                }
                                if show_f3:
                                    s_dict["f3"] = f3_vals[s_idx:e_idx]
                                syl_formants.append(s_dict)
                    except Exception as ex:
                        print(f"Error extracting syllable data in snapshot: {ex}")

                    snapshot_item = {
                        "speaker_id": spk_id,
                        "speaker_name": spk_name,
                        "item_id": item_id,
                        "label": item.get("label", ""),
                        "group": item.get("group", "默认组"),
                        "wordlist_version": item.get("wordlist_version", "v1"),
                        "wordlist_title": item.get("wordlist_title", ""),
                        "item_note": item.get("item_note", ""),
                        "item_tags": item.get("item_tags", []) or [],
                        "item_aliases": item.get("item_aliases", []) or [],
                        "item_meta": item.get("item_meta", {}) or {},
                        "group_note": item.get("group_note", ""),
                        "group_tags": item.get("group_tags", []) or [],
                        "metadata_source": item.get("metadata_source", ""),
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
                            "f2": f2_clean,
                            "f3": f3_clean
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
