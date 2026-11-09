# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本管理器
"""

import os
import json
import uuid
import shutil
from modules.script_runner import (
    extract_script_params_schema,
    get_default_script_params,
    coerce_script_params,
)

# 默认内置高阶与示例脚本库
DEFAULT_SCRIPTS = [
    {
        "id": "builtin_f0_group_mean",
        "name": "F0 分组均值折线图 (示例)",
        "description": "利用预展开长表计算并绘制不同声调分组在 11 点归一化时间上的 F0 均值曲线（支持 Hz / T值 / 半音 与置信阴影切换）。",
        "type": "chart",
        "code": '''# 脚本名称：F0 分组均值折线图 (示例)
# 功能说明：计算并绘制不同声调分组在 11 点归一化时间上的 F0 均值曲线，并附带标准差阴影。
PARAMS = [
    {"key": "scale_mode", "label": "声学标度", "type": "choice", "choices": ["Hz + T值双图", "仅 T值 (0-5)", "仅 Hz", "仅半音 (Semitone)"], "default": "Hz + T值双图"},
    {"key": "show_band", "label": "显示标准差阴影", "type": "bool", "default": True},
    {"key": "line_width", "label": "线条粗细", "type": "float", "default": 2.4, "min": 1.0, "max": 5.0},
    {"key": "export_table", "label": "同步导出汇总表", "type": "bool", "default": False},
]

def run(ctx):
    df = ctx.dataset.to_point_table(included_only=True, as_df=True)
    if df is None or len(df) == 0:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        ctx.log("警告: 数据集为空，绘制空图。")
        return ctx.figure(fig, filename="empty_chart.png", title="无数据提示")

    scale_opt = ctx.params.get("scale_mode", "Hz + T值双图")
    show_band = bool(ctx.params.get("show_band", True))
    lw = float(ctx.params.get("line_width", 2.4))
    export_table = bool(ctx.params.get("export_table", False))

    if scale_opt == "仅 T值 (0-5)":
        modes = [("T值", "t_value")]
    elif scale_opt == "仅 Hz":
        modes = [("Hz", "f0_hz")]
    elif scale_opt == "仅半音 (Semitone)":
        modes = [("半音", "semitone")]
    else:
        modes = [("Hz", "f0_hz"), ("T值", "t_value")]

    df = df.copy()
    df["global_pt"] = df["syl_idx"] * 11 + df["point_idx"]
    max_syls = max(1, int(df["syl_idx"].max()) + 1)
    num_pts = 11
    total_pts = max_syls * num_pts
    colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

    outputs = []
    plotted_count = 0
    for mode_label, col_name in modes:
        fig, ax = ctx.plt.subplots(figsize=(8.0, 5.2))
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        plotted_count = 0
        for g_idx, (g_name, g_df) in enumerate(sorted(df.groupby("group"), key=lambda x: str(x[0]))):
            color = colors[g_idx % len(colors)]
            stats = g_df.groupby("global_pt")[col_name].agg(["mean", "std", "count"]).reindex(range(1, total_pts + 1))
            mean_arr = stats["mean"].to_numpy(dtype=float)
            std_arr = stats["std"].fillna(0.0).to_numpy(dtype=float)
            x_pts = ctx.np.arange(1, total_pts + 1)

            if not ctx.np.all(ctx.np.isnan(mean_arr)):
                n_items = int(g_df["item_id"].nunique())
                ax.plot(x_pts, mean_arr, "-o", color=color, linewidth=lw, markersize=4.8, label=f"{g_name} (n={n_items})", zorder=5)
                if show_band:
                    with ctx.np.errstate(all='ignore'):
                        ax.fill_between(x_pts, mean_arr - std_arr, mean_arr + std_arr, color=color, alpha=0.15, zorder=4)
                plotted_count += 1

        if max_syls > 1:
            for k in range(1, max_syls):
                ax.axvline(k * num_pts + 0.5, color='gray', linestyle='--', alpha=0.5, zorder=3)

        ax.set_title(f"各声调组 F0 均值曲线图 ({mode_label})", fontsize=14, fontweight="bold", pad=15)
        ax.set_xlabel("音节测量点 (时序展开)", fontsize=12)
        ax.set_xticks(ctx.np.arange(1, total_pts + 1))

        x_labels = []
        for s_idx in range(max_syls):
            for p_idx in range(num_pts):
                if p_idx == 0:
                    x_labels.append(f"音节{s_idx+1}_点1")
                elif p_idx == num_pts // 2:
                    x_labels.append(f"点{p_idx+1}")
                elif p_idx == num_pts - 1:
                    x_labels.append(f"点{num_pts}")
                else:
                    x_labels.append("")
        ax.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=9)

        if mode_label == "T值":
            ax.set_ylabel("T 值 (0-5 标度)", fontsize=12)
            ax.set_ylim(-0.2, 5.2)
            ax.set_yticks([0, 1, 2, 3, 4, 5])
        elif mode_label == "半音":
            ax.set_ylabel("半音 (Semitone, re P5)", fontsize=12)
        else:
            ax.set_ylabel("基频 F0 (Hz)", fontsize=12)

        if plotted_count > 0:
            ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E5E7EB")

        fig.tight_layout()
        fname = f"f0_group_means_{col_name}.png"
        outputs.append(ctx.figure(fig, filename=fname, title=f"F0 分组均值图 ({mode_label})"))

    if export_table:
        summary_df = (
            df.groupby(["group", "syl_idx", "point_idx"])[["f0_hz", "t_value", "semitone"]]
            .agg(["mean", "std", "count"])
            .reset_index()
        )
        summary_df.columns = [
            f"{a}_{b}" if b else str(a) for a, b in summary_df.columns.to_flat_index()
        ]
        outputs.append(ctx.table(summary_df.round(3), title="F0 分组逐点均值统计表"))

    ctx.log(f"成功绘制 {plotted_count} 个分组的均值曲线（模式: {scale_opt}）。")
    return outputs[0] if len(outputs) == 1 else outputs
'''
    },
    {
        "id": "builtin_vowel_space",
        "name": "F1/F2 元音空间图 (示例)",
        "description": "按分组提取并绘制 F1/F2 元音空间分布（支持 Hz / Lobanov / Bark 归一化、置信椭圆与双元音轨迹箭头）。",
        "type": "chart",
        "code": '''# 脚本名称：F1/F2 元音空间图 (示例)
# 功能说明：按分组提取并绘制 F1 和 F2 共振峰分布（反转坐标轴），展示不同组别的元音空间和置信椭圆。
PARAMS = [
    {"key": "norm_method", "label": "频率归一化", "type": "choice", "choices": ["raw_hz", "lobanov", "bark", "erb", "nearey1"], "default": "raw_hz"},
    {"key": "ellipse_sigma", "label": "置信椭圆倍数(σ)", "type": "float", "default": 1.0, "min": 0.5, "max": 3.0},
    {"key": "show_arrows", "label": "叠加20%→80%动程箭头", "type": "bool", "default": True},
    {"key": "export_table", "label": "同步输出元音汇总表", "type": "bool", "default": True},
]

def run(ctx):
    df_syl = ctx.dataset.to_syllable_table(included_only=True, as_df=True)
    if df_syl is None or len(df_syl) == 0:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty_chart.png", title="无数据提示")

    valid_df = df_syl.dropna(subset=["f1_mid", "f2_mid"]).copy()
    valid_df = valid_df[(valid_df["f1_mid"] > 0) & (valid_df["f2_mid"] > valid_df["f1_mid"])]
    if len(valid_df) == 0:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "未找到有效的共振峰分析数据", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty_chart.png", title="无有效共振峰数据提示")

    norm_method = str(ctx.params.get("norm_method", "raw_hz"))
    ellipse_sigma = float(ctx.params.get("ellipse_sigma", 1.0))
    show_arrows = bool(ctx.params.get("show_arrows", True))
    export_table = bool(ctx.params.get("export_table", True))

    if norm_method != "raw_hz":
        valid_df = ctx.normalize_formants(valid_df, method=norm_method)
        col_f1, col_f2 = "f1_norm", "f2_norm"
        x_label, y_label = f"F2 ({norm_method})", f"F1 ({norm_method})"
    else:
        col_f1, col_f2 = "f1_mid", "f2_mid"
        x_label, y_label = "F2 频率 (Hz)", "F1 频率 (Hz)"

    fig, ax = ctx.plt.subplots(figsize=(8.4, 6.8))
    ax.set_facecolor("#F8FAFC")
    ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

    categories = sorted(valid_df["group"].astype(str).unique().tolist())
    cmap = ctx.plt.get_cmap('tab10')
    cat_colors = {cat: cmap(i % 10) for i, cat in enumerate(categories)}

    def draw_confidence_ellipse(x, y, ax_obj, n_std=1.0, facecolor='none', **kwargs):
        if len(x) < 3:
            return None
        from matplotlib.patches import Ellipse
        try:
            x = ctx.np.asarray(x, dtype=float)
            y = ctx.np.asarray(y, dtype=float)
            cov = ctx.np.cov(x, y)
            if ctx.np.any(ctx.np.isnan(cov)) or ctx.np.any(ctx.np.isinf(cov)):
                return None
            vals, vecs = ctx.np.linalg.eigh(cov)
            order = vals.argsort()[::-1]
            vals, vecs = vals[order], vecs[:, order]
            theta = ctx.np.degrees(ctx.np.arctan2(*vecs[:, 0][::-1]))
            width, height = 2 * n_std * ctx.np.sqrt(ctx.np.maximum(vals, 0))
            ellipse = Ellipse(xy=(ctx.np.mean(x), ctx.np.mean(y)), width=width, height=height,
                              angle=theta, facecolor=facecolor, **kwargs)
            return ax_obj.add_patch(ellipse)
        except Exception:
            return None

    summary_rows = []
    for cat in categories:
        sub = valid_df[valid_df["group"].astype(str) == cat]
        f1_vals = sub[col_f1].to_numpy(dtype=float)
        f2_vals = sub[col_f2].to_numpy(dtype=float)
        color = cat_colors[cat]

        ax.scatter(f2_vals, f1_vals, color=color, s=28, alpha=0.35, edgecolors='none', zorder=3)
        draw_confidence_ellipse(f2_vals, f1_vals, ax, n_std=ellipse_sigma, edgecolor=color, linestyle='--', linewidth=1.6, zorder=4)

        m_f1 = float(ctx.np.nanmean(f1_vals))
        m_f2 = float(ctx.np.nanmean(f2_vals))
        ax.scatter(m_f2, m_f1, color=color, s=140, marker='o', edgecolors='black', linewidth=1.2, zorder=6, label=f"{cat} (n={len(sub)})")
        ax.annotate(
            cat,
            xy=(m_f2, m_f1),
            xytext=(0, 8),
            textcoords="offset points",
            fontsize=10.5,
            fontweight="bold",
            color="#111827",
            ha="center",
            va="bottom",
            zorder=7,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.85, lw=1),
        )

        if show_arrows and norm_method == "raw_hz":
            s_20_80 = sub.dropna(subset=["f1_20", "f2_20", "f1_80", "f2_80"])
            if len(s_20_80) > 0:
                f1_20_m = float(s_20_80["f1_20"].mean())
                f2_20_m = float(s_20_80["f2_20"].mean())
                f1_80_m = float(s_20_80["f1_80"].mean())
                f2_80_m = float(s_20_80["f2_80"].mean())
                if abs(f2_80_m - f2_20_m) + abs(f1_80_m - f1_20_m) > 15.0:
                    ax.annotate(
                        "",
                        xy=(f2_80_m, f1_80_m),
                        xytext=(f2_20_m, f1_20_m),
                        arrowprops=dict(arrowstyle="->", color=color, lw=1.8, alpha=0.8),
                        zorder=5,
                    )

        summary_rows.append([
            cat,
            int(len(sub)),
            round(float(sub["f1_mid"].mean()), 1),
            round(float(sub["f1_mid"].std(ddof=0)), 1),
            round(float(sub["f2_mid"].mean()), 1),
            round(float(sub["f2_mid"].std(ddof=0)), 1),
            round(float(sub["tl_hz"].mean()), 1) if sub["tl_hz"].notna().any() else 0.0,
        ])

    ax.invert_xaxis()
    ax.invert_yaxis()
    ax.set_title(f"F1/F2 元音空间分布图 ({norm_method})", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel(x_label, fontsize=12, fontweight='bold', labelpad=10)
    ax.set_ylabel(y_label, fontsize=12, fontweight='bold', labelpad=10)
    ax.legend(loc="upper right")
    fig.tight_layout()

    fig_res = ctx.figure(fig, filename="vowel_space.png", title="元音空间分布图")
    ctx.log(f"成功绘制 {len(categories)} 个分组的元音空间分布图（归一化: {norm_method}）。")
    if export_table:
        tbl_res = ctx.table(
            summary_rows,
            ["分组", "音节数", "F1均值(Hz)", "F1标准差", "F2均值(Hz)", "F2标准差", "平均动程TL(Hz)"],
            title="元音空间分组声学统计表",
        )
        return [fig_res, tbl_res]
    return fig_res
'''
    },
    {
        "id": "builtin_voice_quality_profile",
        "name": "嗓音发声态多维分析与箱线图 (内置)",
        "description": "提取各分组嗓音发声态指标（H1-H2 / CPP / HNR / Jitter / Shimmer），绘制多维箱线对比图并导出统计表。",
        "type": "chart",
        "code": '''# 脚本名称：嗓音发声态多维分析与箱线图 (内置)
# 功能说明：提取各分组嗓音发声态指标（H1-H2 / CPP / HNR / Jitter / Shimmer），绘制多维箱线对比图并导出统计表。
PARAMS = [
    {"key": "syl_target", "label": "目标音节", "type": "choice", "choices": ["首音节 (syl 0)", "末音节", "整词综合"], "default": "首音节 (syl 0)"},
    {"key": "max_items", "label": "最大分析词数", "type": "int", "default": 60, "min": 5, "max": 500},
    {"key": "show_jitter_shimmer", "label": "包含微扰指标(Jitter/Shimmer)", "type": "bool", "default": True},
    {"key": "export_vector", "label": "同步导出PDF/SVG矢量图", "type": "bool", "default": False},
]

def run(ctx):
    items = ctx.dataset.included_items()
    if not items:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="voice_quality_empty.png", title="无数据提示")

    syl_target = str(ctx.params.get("syl_target", "首音节 (syl 0)"))
    max_items = int(ctx.params.get("max_items", 60))
    show_js = bool(ctx.params.get("show_jitter_shimmer", True))
    export_vector = bool(ctx.params.get("export_vector", False))

    rows = []
    for item in items[:max_items]:
        if ctx.is_cancelled():
            break
        syl_data = item.get("syl_data") or []
        if syl_target == "首音节 (syl 0)":
            s_idx = 0
        elif syl_target == "末音节":
            s_idx = max(0, len(syl_data) - 1)
        else:
            s_idx = None

        vq = ctx.extract_voice_quality(item, syl_idx=s_idx)
        rows.append({
            "speaker_name": str(item.get("speaker_name") or "默认发音人"),
            "item_id": str(item.get("item_id") or ""),
            "word": str(item.get("label") or item.get("word") or ""),
            "group": str(item.get("group") or "默认组"),
            "h1_h2_db": vq.get("h1_h2_db", ctx.np.nan),
            "cpp_db": vq.get("cpp_db", ctx.np.nan),
            "hnr_db": vq.get("hnr_db", ctx.np.nan),
            "jitter_local": vq.get("jitter_local", ctx.np.nan),
            "shimmer_local": vq.get("shimmer_local", ctx.np.nan),
            "h1_a1_db": vq.get("h1_a1_db", ctx.np.nan),
            "h1_a3_db": vq.get("h1_a3_db", ctx.np.nan),
        })

    df = ctx.pd.DataFrame(rows)
    metrics = [
        ("h1_h2_db", "H1-H2 谐波差 (dB)"),
        ("cpp_db", "CPP 倒谱峰值突出度 (dB)"),
        ("hnr_db", "HNR 谐噪比 (dB)"),
    ]
    if show_js:
        metrics.extend([
            ("jitter_local", "Jitter 基频微扰 (%)"),
            ("shimmer_local", "Shimmer 振幅微扰 (%)"),
            ("h1_a3_db", "H1-A3 频谱倾斜 (dB)"),
        ])

    n_panels = len(metrics)
    ncols = 3
    nrows = int(ctx.np.ceil(n_panels / ncols))
    fig, axes = ctx.plt.subplots(nrows, ncols, figsize=(3.8 * ncols, 3.6 * nrows), squeeze=False)
    axes_flat = [ax for row in axes for ax in row]

    groups = sorted(df["group"].unique().tolist())
    palette = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#4F46E5']

    for idx, (m_key, m_title) in enumerate(metrics):
        ax = axes_flat[idx]
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, axis="y", linestyle="--", alpha=0.3)
        data_by_grp = []
        valid_labels = []
        for g in groups:
            vals = df[df["group"] == g][m_key].dropna().to_numpy(dtype=float)
            vals = vals[ctx.np.isfinite(vals)]
            if vals.size > 0:
                data_by_grp.append(vals)
                valid_labels.append(g)
        if data_by_grp:
            bp = ax.boxplot(data_by_grp, tick_labels=valid_labels, patch_artist=True, widths=0.5)
            for b_i, patch in enumerate(bp["boxes"]):
                c = palette[b_i % len(palette)]
                patch.set_facecolor(c)
                patch.set_alpha(0.35)
                patch.set_edgecolor(c)
                patch.set_linewidth(1.4)
            for b_i, vals in enumerate(data_by_grp):
                c = palette[b_i % len(palette)]
                jitter_x = ctx.np.full(vals.size, b_i + 1.0) + ctx.np.linspace(-0.12, 0.12, vals.size)
                ax.scatter(jitter_x, vals, color=c, s=18, alpha=0.7, zorder=4)
        ax.set_title(m_title, fontsize=11, fontweight="bold")
        ax.tick_params(axis="x", rotation=25, labelsize=9)

    for extra_ax in axes_flat[n_panels:]:
        extra_ax.axis("off")

    fig.suptitle(f"嗓音发声态 (Voice Quality) 多维分组对比 ({syl_target})", fontsize=14, fontweight="bold", y=0.99)
    fig.tight_layout()

    fmts = ("png", "pdf", "svg") if export_vector else ("png",)
    fig_res = ctx.figure(
        fig,
        filename="voice_quality_profile.png",
        title="嗓音发声态多维对比箱线图",
        formats=fmts,
        dpi=300,
    )
    tbl_res = ctx.table(df.round(3), title="嗓音发声态逐词测量明细表")
    ctx.log(f"已完成 {len(df)} 条词项的嗓音发声态提取与多维对比图绘制。")
    return [fig_res, tbl_res]
'''
    },
    {
        "id": "builtin_fricative_spectral_moments",
        "name": "擦音/塞擦音频谱四阶矩分析 (内置)",
        "description": "提取擦音与塞擦音频谱四阶矩（COG 频谱重心、SD 标准差、Skewness 偏度、Kurtosis 峰度），绘制 COG-偏度分布与组间统计表。",
        "type": "chart",
        "code": '''# 脚本名称：擦音/塞擦音频谱四阶矩分析 (内置)
# 功能说明：提取辅音/擦音频谱前四阶中心矩（COG 频谱重心、标准差、偏度、峰度），输出双联对比图与统计表。
PARAMS = [
    {"key": "pre_emphasis", "label": "预加重截止频率(Hz)", "type": "choice", "choices": [50.0, 80.0, 100.0, 0.0], "default": 80.0},
    {"key": "window_portion", "label": "分析时间窗位置", "type": "choice", "choices": ["前35%辅音段", "中段50%稳态", "整词范围"], "default": "前35%辅音段"},
    {"key": "max_items", "label": "最大分析词数", "type": "int", "default": 80, "min": 5, "max": 500},
]

def run(ctx):
    items = ctx.dataset.included_items()
    if not items:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="spectral_moments_empty.png", title="无数据提示")

    pre_emp = float(ctx.params.get("pre_emphasis", 80.0))
    win_mode = str(ctx.params.get("window_portion", "前35%辅音段"))
    max_items = int(ctx.params.get("max_items", 80))

    records = []
    for item in items[:max_items]:
        if ctx.is_cancelled():
            break
        dur = float(item.get("duration") or (float(item.get("end", 0.25)) - float(item.get("start", 0.0))) or 0.25)
        dur = max(0.05, dur)
        if win_mode == "前35%辅音段":
            tw = (0.0, dur * 0.35)
        elif win_mode == "中段50%稳态":
            tw = (dur * 0.25, dur * 0.75)
        else:
            tw = None

        sm = ctx.extract_spectral_moments(item, time_window=tw, pre_emphasis=pre_emp)
        records.append({
            "speaker_name": str(item.get("speaker_name") or "默认发音人"),
            "item_id": str(item.get("item_id") or ""),
            "word": str(item.get("label") or item.get("word") or ""),
            "group": str(item.get("group") or "默认组"),
            "cog_hz": sm.get("cog_hz", ctx.np.nan),
            "sd_hz": sm.get("sd_hz", ctx.np.nan),
            "skewness": sm.get("skewness", ctx.np.nan),
            "kurtosis": sm.get("kurtosis", ctx.np.nan),
        })

    df = ctx.pd.DataFrame(records).dropna(subset=["cog_hz"])
    fig, (ax_scatter, ax_bar) = ctx.plt.subplots(1, 2, figsize=(11.2, 4.8))
    ax_scatter.set_facecolor("#F8FAFC")
    ax_bar.set_facecolor("#F8FAFC")
    ax_scatter.grid(True, linestyle="--", alpha=0.3)
    ax_bar.grid(True, axis="y", linestyle="--", alpha=0.3)

    groups = sorted(df["group"].unique().tolist()) if len(df) > 0 else []
    palette = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#4F46E5']

    cog_means = []
    cog_stds = []
    for idx, g in enumerate(groups):
        sub = df[df["group"] == g]
        c = palette[idx % len(palette)]
        ax_scatter.scatter(
            sub["cog_hz"],
            sub["skewness"],
            color=c,
            s=42,
            alpha=0.75,
            label=f"{g} (n={len(sub)})",
        )
        m_cog = float(sub["cog_hz"].mean())
        m_skew = float(sub["skewness"].mean())
        ax_scatter.scatter([m_cog], [m_skew], color=c, s=150, marker="D", edgecolors="black", linewidth=1.2, zorder=5)
        cog_means.append(m_cog)
        cog_stds.append(float(sub["cog_hz"].std(ddof=0)) if len(sub) > 1 else 0.0)

    ax_scatter.set_title("频谱重心 (COG) vs 频谱偏度 (Skewness)", fontsize=12, fontweight="bold")
    ax_scatter.set_xlabel("频谱重心 COG (Hz)", fontsize=11)
    ax_scatter.set_ylabel("三阶频谱偏度 Skewness", fontsize=11)
    if groups:
        ax_scatter.legend(loc="best", fontsize=9)

    if groups:
        x_pos = ctx.np.arange(len(groups))
        bars = ax_bar.bar(x_pos, cog_means, yerr=cog_stds, capsize=4, color=[palette[i % len(palette)] for i in range(len(groups))], alpha=0.8)
        ax_bar.set_xticks(x_pos)
        ax_bar.set_xticklabels(groups, rotation=25, ha="right", fontsize=9)
        for bar, val in zip(bars, cog_means):
            ax_bar.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height(), f"{val:.0f}Hz", ha="center", va="bottom", fontsize=8.5)
    ax_bar.set_title("各分组平均频谱重心 (COG ± SD)", fontsize=12, fontweight="bold")
    ax_bar.set_ylabel("COG 频率 (Hz)", fontsize=11)

    fig.suptitle(f"擦音/塞擦音频谱四阶矩分析 ({win_mode}, 预加重={pre_emp:g}Hz)", fontsize=13.5, fontweight="bold")
    fig.tight_layout()

    fig_res = ctx.figure(fig, filename="fricative_spectral_moments.png", title="频谱四阶矩分析图")
    tbl_res = ctx.table(df.round(3), title="频谱四阶矩测量统计表")
    ctx.log(f"已完成 {len(df)} 条词项的频谱四阶矩（COG/SD/Skewness/Kurtosis）分析。")
    return [fig_res, tbl_res]
'''
    },
    {
        "id": "builtin_octave_jump_auto_clean",
        "name": "基频八度跳变自动诊断与重算补丁 (内置)",
        "description": "自动检测基频八度跳变与离群突变词条，联合返回清洗前后对比诊断图、异常明细表与自适应调域重算补丁（Figure + Table + ProjectPatch 三合一）。",
        "type": "data_process",
        "code": '''# 脚本名称：基频八度跳变自动诊断与重算补丁 (内置)
# 功能说明：自动诊断基频八度跳变词条，联合生成清洗前后对比图、异常词条明细表与自适应重算工程补丁。
PARAMS = [
    {"key": "jump_ratio_threshold", "label": "相邻点跳变倍率阈值", "type": "float", "default": 1.45, "min": 1.15, "max": 2.5},
    {"key": "margin_ratio", "label": "重算调域收窄余量", "type": "float", "default": 0.25, "min": 0.05, "max": 0.8},
    {"key": "mark_note", "label": "为异常词条写入复核备注", "type": "bool", "default": True},
]

def run(ctx):
    items = ctx.dataset.included_items()
    jump_thresh = float(ctx.params.get("jump_ratio_threshold", 1.45))
    margin_ratio = float(ctx.params.get("margin_ratio", 0.25))
    mark_note = bool(ctx.params.get("mark_note", True))

    if not items:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return [
            ctx.figure(fig, filename="octave_clean_empty.png", title="八度跳变诊断图"),
            ctx.table([], ["发音人", "词条ID", "词项", "异常原因", "建议Floor(Hz)", "建议Ceiling(Hz)"], title="异常词条明细表"),
            ctx.project_patch([], title="八度跳变自动清洗补丁", description="无待处理词条"),
        ]

    # 1. 按发音人估计稳健基频调域 [s_min, s_max]
    spk_pools = {}
    for it in items:
        spk = str(it.get("speaker_name") or it.get("speaker_id") or "默认发音人")
        spk_pools.setdefault(spk, [])
        for _, pts in (it.get("syl_data") or []):
            for v in (pts or []):
                if v is not None and ctx.np.isfinite(v) and v > 0:
                    spk_pools[spk].append(float(v))
        for v in ((it.get("pitch") or {}).get("freqs") or []):
            if v is not None and ctx.np.isfinite(v) and v > 0:
                spk_pools[spk].append(float(v))

    spk_bounds = {}
    for spk, pool in spk_pools.items():
        s_min, s_max = ctx.get_f0_bounds(pool)
        floor_rec = max(55.0, round(float(s_min) * (1.0 - margin_ratio), 1))
        ceil_rec = min(550.0, round(float(s_max) * (1.0 + margin_ratio), 1))
        if ceil_rec <= floor_rec + 40.0:
            ceil_rec = floor_rec + 120.0
        spk_bounds[spk] = (float(s_min), float(s_max), floor_rec, ceil_rec)

    # 2. 扫描每个词条的曲线是否存在八度跳变或超界尖峰
    fig, (ax_raw, ax_clean) = ctx.plt.subplots(1, 2, figsize=(11.0, 4.6), sharey=True)
    ax_raw.set_facecolor("#F8FAFC")
    ax_clean.set_facecolor("#F8FAFC")
    ax_raw.grid(True, linestyle="--", alpha=0.3)
    ax_clean.grid(True, linestyle="--", alpha=0.3)

    anomaly_rows = []
    operations = []
    normal_count = 0

    for it in items:
        spk = str(it.get("speaker_name") or it.get("speaker_id") or "默认发音人")
        s_min, s_max, floor_rec, ceil_rec = spk_bounds.get(spk, (85.0, 320.0, 70.0, 380.0))
        pts_all = []
        for _, pts in (it.get("syl_data") or []):
            for v in (pts or []):
                pts_all.append(float(v) if (v is not None and ctx.np.isfinite(v) and v > 0) else ctx.np.nan)
        if not pts_all:
            p_freqs = (it.get("pitch") or {}).get("freqs") or []
            if len(p_freqs) > 0:
                arr_p = ctx.np.asarray(p_freqs, dtype=float)
                arr_p = ctx.np.where(ctx.np.isfinite(arr_p) & (arr_p > 0), arr_p, ctx.np.nan)
                idx_sample = ctx.np.linspace(0, len(arr_p) - 1, 11).astype(int)
                pts_all = arr_p[idx_sample].tolist()

        if not pts_all:
            continue

        arr = ctx.np.asarray(pts_all, dtype=float)
        valid = arr[ctx.np.isfinite(arr)]
        if valid.size < 2:
            continue

        ratios = ctx.np.maximum(valid[1:] / valid[:-1], valid[:-1] / valid[1:])
        max_ratio = float(ctx.np.max(ratios)) if ratios.size > 0 else 1.0
        out_of_bounds = bool(ctx.np.any((valid < floor_rec * 0.85) | (valid > ceil_rec * 1.15)))
        is_anomalous = (max_ratio >= jump_thresh) or out_of_bounds

        x_idx = ctx.np.arange(1, len(arr) + 1)
        if is_anomalous:
            ax_raw.plot(x_idx, arr, "-o", color="#DC2626", alpha=0.75, linewidth=1.8, markersize=4)
            # 构造中值平滑/限幅后的预览参考曲线
            clean_arr = arr.copy()
            med_val = float(ctx.np.nanmedian(valid))
            bad_mask = ctx.np.isfinite(clean_arr) & ((clean_arr < floor_rec * 0.85) | (clean_arr > ceil_rec * 1.15) | (ctx.np.abs(clean_arr - med_val) > 0.45 * med_val))
            clean_arr[bad_mask] = med_val
            ax_clean.plot(x_idx, clean_arr, "-o", color="#16A34A", alpha=0.8, linewidth=1.8, markersize=4)

            reason_str = f"最大相邻跳变比={max_ratio:.2f}x" + ("，超出稳健调域" if out_of_bounds else "")
            anomaly_rows.append([
                spk,
                str(it.get("item_id") or ""),
                str(it.get("label") or it.get("word") or ""),
                str(it.get("group") or ""),
                round(max_ratio, 2),
                reason_str,
                floor_rec,
                ceil_rec,
            ])
            if it.get("speaker_id") and it.get("item_id"):
                if mark_note:
                    old_note = str(it.get("note") or it.get("item_note") or "").strip()
                    new_note = f"{old_note}; 自动诊断八度跳变({max_ratio:.2f}x)".strip("; ")
                    operations.append(ctx.set_item_fields(it, {"item_note": new_note}, reason=reason_str))
                operations.append(ctx.recompute_pitch(
                    it,
                    params={"pitch_floor": floor_rec, "pitch_ceiling": ceil_rec},
                    reason=f"八度跳变自适应收窄调域重算 [{floor_rec}, {ceil_rec}] Hz",
                ))
        else:
            normal_count += 1
            if normal_count <= 25:
                ax_raw.plot(x_idx, arr, "-", color="#94A3B8", alpha=0.35, linewidth=1.0)
                ax_clean.plot(x_idx, arr, "-", color="#2563EB", alpha=0.35, linewidth=1.0)

    # 若无异常词条但希望生成示范重算操作，则对最大跳变比词条给出温和校准建议
    if not operations and items:
        first_it = items[0]
        if first_it.get("speaker_id") and first_it.get("item_id"):
            spk0 = str(first_it.get("speaker_name") or first_it.get("speaker_id") or "默认发音人")
            _, _, fl0, cl0 = spk_bounds.get(spk0, (85.0, 320.0, 75.0, 360.0))
            operations.append(ctx.recompute_pitch(
                first_it,
                params={"pitch_floor": fl0, "pitch_ceiling": cl0},
                reason=f"稳健调域基准校准 [{fl0}, {cl0}] Hz",
            ))
            anomaly_rows.append([
                spk0,
                str(first_it.get("item_id") or ""),
                str(first_it.get("label") or first_it.get("word") or ""),
                str(first_it.get("group") or ""),
                1.0,
                "未检出显著跳变（稳健调域基准校准）",
                fl0,
                cl0,
            ])

    ax_raw.set_title(f"清洗前原始基频轨迹 (红线=检出跳变, 共 {len(anomaly_rows)} 条)", fontsize=11.5, fontweight="bold")
    ax_clean.set_title("自适应调域约束与去跳变预期轨迹", fontsize=11.5, fontweight="bold")
    ax_raw.set_xlabel("归一化测量点")
    ax_clean.set_xlabel("归一化测量点")
    ax_raw.set_ylabel("基频 F0 (Hz)")
    fig.suptitle("基频八度跳变自动诊断与自适应调域重算预览", fontsize=13.5, fontweight="bold")
    fig.tight_layout()

    fig_res = ctx.figure(fig, filename="octave_jump_diagnosis.png", title="八度跳变清洗前后对比图")
    tbl_res = ctx.table(
        anomaly_rows,
        ["发音人", "词条ID", "词项", "分组", "最大跳变比", "诊断结论", "建议Floor(Hz)", "建议Ceiling(Hz)"],
        title="八度跳变异常诊断明细表",
    )
    patch_res = ctx.project_patch(
        operations,
        title="八度跳变自适应重算补丁",
        description=f"共扫描 {len(items)} 条词项，生成 {len(operations)} 项自适应调域重算与备注操作。",
    )
    ctx.log(f"扫描完成：共检测 {len(items)} 条词项，生成 {len(operations)} 项工程修补操作。")
    return [fig_res, tbl_res, patch_res]
'''
    },
]

# 旧版无 PARAMS 内置脚本特征（用于在不覆盖用户自定义修改的前提下无缝升级旧版默认内置脚本）
_LEGACY_BUILTIN_SIGNATURES = {
    "builtin_f0_group_mean": 'return [\n        ctx.figure(fig_hz, filename="f0_group_means_hz.png", title="F0 分组均值图 (Hz)"),\n        ctx.figure(fig_t, filename="f0_group_means_t.png", title="F0 分组均值图 (T值)"),\n    ]',
    "builtin_vowel_space": 'return ctx.figure(fig, filename="vowel_space.png", title="元音空间分布图")',
}


def get_scripts_dir():
    """获取脚本存储目录路径"""
    path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "scripts")
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def reset_builtin_scripts():
    """重置所有内置示例脚本为默认版本"""
    for ds in DEFAULT_SCRIPTS:
        save_script(ds["id"], ds["name"], ds["description"], ds["type"], ds["code"])


def load_all_scripts(force_reset_builtins=False):
    """加载本地所有脚本（仅当磁盘上不存在内置脚本文件、为旧版未修改内置模板或显式请求重置时才写入默认内置脚本，避免覆盖用户修改）"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)

    for ds in DEFAULT_SCRIPTS:
        builtin_path = os.path.join(s_dir, f"{ds['id']}.json")
        should_write = force_reset_builtins or not os.path.exists(builtin_path)
        if not should_write and ds["id"] in _LEGACY_BUILTIN_SIGNATURES:
            try:
                with open(builtin_path, "r", encoding="utf-8") as bf:
                    existing = json.load(bf)
                existing_code = str(existing.get("code") or "")
                if (
                    existing.get("name") == ds["name"]
                    and "PARAMS" not in existing_code
                    and _LEGACY_BUILTIN_SIGNATURES[ds["id"]] in existing_code
                ):
                    should_write = True
            except Exception:
                pass
        if should_write:
            save_script(ds["id"], ds["name"], ds["description"], ds["type"], ds["code"])

    scripts = []

    # 获取目录下的所有 .json 文件
    files = [f for f in os.listdir(s_dir) if f.endswith(".json")]

    for f in files:
        f_path = os.path.join(s_dir, f)
        try:
            with open(f_path, 'r', encoding='utf-8') as file:
                s_dict = json.load(file)
                if "id" in s_dict:
                    scripts.append(s_dict)
        except Exception as e:
            print(f"Error loading script file {f}: {e}")

    # 按名字排序
    scripts.sort(key=lambda x: x.get("name", ""))
    return scripts


def save_script(script_id, name, description, script_type, code):
    """保存或更新脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    if not script_id:
        script_id = str(uuid.uuid4())

    s_dict = {
        "id": script_id,
        "name": name,
        "description": description,
        "type": script_type,
        "code": code
    }

    f_path = os.path.join(s_dir, f"{script_id}.json")
    with open(f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return script_id


def delete_script(script_id):
    """删除脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if os.path.exists(f_path):
        os.remove(f_path)
        return True
    return False


def copy_script(script_id):
    """复制现有脚本"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError("找不到要复制的脚本。")

    with open(f_path, 'r', encoding='utf-8') as file:
        s_dict = json.load(file)

    new_id = str(uuid.uuid4())
    s_dict["id"] = new_id
    s_dict["name"] = f"{s_dict['name']} - 副本"

    new_f_path = os.path.join(s_dir, f"{new_id}.json")
    with open(new_f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return s_dict


def import_script(file_path):
    """导入脚本文件"""
    with open(file_path, 'r', encoding='utf-8') as file:
        s_dict = json.load(file)

    # 简单校验字段
    if not isinstance(s_dict, dict) or "name" not in s_dict or "code" not in s_dict:
        raise ValueError("非法的脚本文件格式：必须包含 'name' 和 'code' 属性。")

    new_id = str(uuid.uuid4())
    s_dict["id"] = new_id

    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    new_f_path = os.path.join(s_dir, f"{new_id}.json")
    with open(new_f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return s_dict


def export_script(script_id, dest_file_path):
    """导出脚本到目标路径"""
    s_dir = get_scripts_dir()
    os.makedirs(s_dir, exist_ok=True)
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError("找不到要导出的脚本。")

    shutil.copy2(f_path, dest_file_path)
    return True
