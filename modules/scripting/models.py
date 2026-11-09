# -*- coding: utf-8 -*-
"""
自定义脚本产物数据模型（FigureResult / TableResult / ProjectPatchResult）与基础辅助函数。
"""

import numpy as np


def configure_matplotlib_chinese_font():
    """
    为脚本生成的 Matplotlib 图表配置中文字体与国际音标 (IPA) 回退链，并开启 PDF Type 42 矢量字体内嵌。
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
            "PingFang SC",
            "WenQuanYi Micro Hei",
            "Charis SIL",
            "Doulos SIL",
            "Segoe UI",
            "Arial Unicode MS",
        ]
        installed = {font.name for font in font_manager.fontManager.ttflist}
        preferred = [name for name in candidates if name in installed]
        fallback_tail = ["Charis SIL", "Doulos SIL", "Segoe UI", "Arial Unicode MS", "DejaVu Sans", "sans-serif"]
        if preferred:
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = preferred + [f for f in fallback_tail if f not in preferred]
        else:
            matplotlib.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans", "sans-serif"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
    except Exception:
        pass


def get_f0_normalization_bounds(f0_values):
    from modules.data_utils import compute_speaker_f0_bounds
    return compute_speaker_f0_bounds(f0_values, method="p5_p95_iqr")


class FigureResult:
    """
    表示脚本生成的图表结果。
    支持 formats=("png", "pdf", "svg") 多格式同步导出与自定义 dpi。
    """
    def __init__(self, fig, filename="custom_chart.png", title="自定义图表", formats=None, dpi=None):
        self.fig = fig
        self.filename = filename
        self.title = title
        if formats is not None:
            if isinstance(formats, str):
                formats = [formats]
            clean_fmts = []
            for fmt in formats:
                f_str = str(fmt or "").strip().lower().lstrip(".")
                if f_str in ("png", "pdf", "svg", "jpg", "jpeg") and f_str not in clean_fmts:
                    clean_fmts.append(f_str)
            self.formats = tuple(clean_fmts) if clean_fmts else None
        else:
            self.formats = None
        if dpi is not None:
            try:
                val = int(dpi)
                self.dpi = val if val > 0 else None
            except (TypeError, ValueError):
                self.dpi = None
        else:
            self.dpi = None


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


def _hz_to_bark_arr(arr):
    a = np.asarray(arr, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    v = np.isfinite(a) & (a > 0)
    if np.any(v):
        out[v] = (26.81 * a[v]) / (1960.0 + a[v]) - 0.53
    return out


def _hz_to_erb_arr(arr):
    a = np.asarray(arr, dtype=float)
    out = np.full(a.shape, np.nan, dtype=float)
    v = np.isfinite(a) & (a > 0)
    if np.any(v):
        out[v] = 21.4 * np.log10(1.0 + 0.00437 * a[v])
    return out


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
