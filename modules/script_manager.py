# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本管理器
"""

import os
import json
import uuid
import shutil

# 默认内置示例脚本
DEFAULT_SCRIPTS = [
    {
        "id": "builtin_f0_group_mean",
        "name": "F0 分组均值折线图 (示例)",
        "description": "计算并绘制不同声调分组在 11 点归一化时间上的 F0 均值曲线，并附带标准差阴影。",
        "type": "chart",
        "code": '''def run(ctx):
    # 1. 获取所有纳入分析的条目
    items = ctx.dataset.included_items()
    if not items:
        # 如果没有数据，生成空图并写日志
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        ctx.log("警告: 数据集为空，绘制空图。")
        return ctx.figure(fig, filename="empty_chart.png", title="无数据提示")

    # 2. 按 group 分组
    groups = {}
    for item in items:
        g = item.get("group", "默认组")
        if g not in groups:
            groups[g] = []
        groups[g].append(item)

    # 找出最大音节数
    max_syls = 1
    for item in items:
        s_data = item.get("syl_data", [])
        if len(s_data) > max_syls:
            max_syls = len(s_data)

    num_pts = 11
    total_pts = max_syls * num_pts
    colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

    figs = []
    # 分别生成 Hz 均值曲线和 T值 均值曲线
    for scale_mode in ["Hz", "T值"]:
        fig, ax = ctx.plt.subplots(figsize=(8, 5.5))
        ax.set_facecolor("#F8FAFC")
        ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

        plotted_count = 0
        for g_idx, (g_name, g_items) in enumerate(sorted(groups.items())):
            color = colors[g_idx % len(colors)]
            
            syl_curves = [[] for _ in range(max_syls)]
            for item in g_items:
                s_data = item.get("syl_data", [])
                s_t_data = item.get("syl_t_values", [])
                for s_idx in range(max_syls):
                    if s_idx < len(s_data):
                        if scale_mode == "T值":
                            syl_t_vals = s_t_data[s_idx] if s_idx < len(s_t_data) else [ctx.np.nan]*num_pts
                            syl_curves[s_idx].append(syl_t_vals)
                        else:
                            dur, f0s = s_data[s_idx]
                            syl_curves[s_idx].append(f0s)

            mean_all = []
            std_all = []
            for s_idx in range(max_syls):
                curves = syl_curves[s_idx]
                if not curves:
                    mean_all.extend([ctx.np.nan] * num_pts)
                    std_all.extend([ctx.np.nan] * num_pts)
                    continue
                
                # 过滤无效值 (<=0 或 NaN)
                curves_clean = []
                for c in curves:
                    c_clean = [f if (f is not None and not ctx.np.isnan(f) and f > 0) else ctx.np.nan for f in c]
                    curves_clean.append(c_clean)
                
                # 检查是否有任何有效值，防止 nanmean/nanstd 在全 NaN 数据上引发 RuntimeWarning 警告
                has_valid = False
                for c in curves_clean:
                    for val in c:
                        if val is not None and not ctx.np.isnan(val) and val > 0:
                            has_valid = True
                            break
                    if has_valid:
                        break

                if has_valid:
                    with ctx.np.errstate(all='ignore'):
                        mean_syl = ctx.np.nanmean(curves_clean, axis=0)
                        std_syl = ctx.np.nanstd(curves_clean, axis=0)
                else:
                    mean_syl = ctx.np.full(num_pts, ctx.np.nan)
                    std_syl = ctx.np.full(num_pts, ctx.np.nan)
                    
                mean_all.extend(mean_syl.tolist())
                std_all.extend(std_syl.tolist())

            x_pts = ctx.np.arange(1, total_pts + 1)
            mean_all = ctx.np.array(mean_all)
            std_all = ctx.np.array(std_all)

            # 仅在非全空时绘制折线图
            if not ctx.np.all(ctx.np.isnan(mean_all)):
                ax.plot(x_pts, mean_all, "-o", color=color, linewidth=2.5, markersize=5, label=g_name, zorder=5)
                # 忽略 nan 带来的警告并绘制置信区间
                with ctx.np.errstate(all='ignore'):
                    ax.fill_between(x_pts, mean_all - std_all, mean_all + std_all, color=color, alpha=0.15, zorder=4)
                plotted_count += 1

        if max_syls > 1:
            for k in range(1, max_syls):
                ax.axvline(k * num_pts + 0.5, color='gray', linestyle='--', alpha=0.5, zorder=3)

        ax.set_title(f"各声调组 F0 均值曲线图 ({scale_mode})", fontsize=14, fontweight="bold", pad=15)
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

        if scale_mode == "T值":
            ax.set_ylabel("T 值 (0-5 标度)", fontsize=12)
            ax.set_ylim(-0.2, 5.2)
            ax.set_yticks([0, 1, 2, 3, 4, 5])
        else:
            ax.set_ylabel("基频 F0 (Hz)", fontsize=12)

        if plotted_count > 0:
            ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E5E7EB")

        fig.tight_layout()
        figs.append(fig)

    fig_hz, fig_t = figs
    ctx.log(f"成功绘制 {plotted_count} 个分组的 Hz 和 T值 均值曲线。")
    return [
        ctx.figure(fig_hz, filename="f0_group_means_hz.png", title="F0 分组均值图 (Hz)"),
        ctx.figure(fig_t, filename="f0_group_means_t.png", title="F0 分组均值图 (T值)"),
    ]
'''
    },
    {
        "id": "builtin_vowel_space",
        "name": "F1/F2 元音空间图 (示例)",
        "description": "按分组（如声调、实验组）提取并绘制 F1 和 F2 共振峰分布（反转坐标轴），展示不同组别的元音空间和置信椭圆。",
        "type": "chart",
        "code": '''def run(ctx):
    # 1. 获取所有纳入分析的条目
    items = ctx.dataset.included_items()
    if not items:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty_chart.png", title="无数据提示")

    # 2. 收集各分组下的共振峰数据
    vowel_data = {}
    for item in items:
        syl_formants = item.get("syl_formants", [])
        if not syl_formants:
            continue
            
        f_xs = item.get("formant", {}).get("xs", [])
        f_f1 = item.get("formant", {}).get("f1", [])
        f_f2 = item.get("formant", {}).get("f2", [])
        
        if len(f_xs) == 0 or len(f_f1) == 0 or len(f_f2) == 0:
            continue
            
        f_xs_arr = ctx.np.asarray(f_xs)
        f_f1_arr = ctx.np.asarray(f_f1)
        f_f2_arr = ctx.np.asarray(f_f2)
        
        # 按照组来分
        g_name = item.get("group", "默认组")
        if not g_name:
            g_name = "默认组"
            
        for syl in syl_formants:
            c_s, c_e = syl.get("bounds", [0.0, 0.0])
            
            mask = (f_xs_arr >= c_s) & (f_xs_arr <= c_e) & ctx.np.isfinite(f_f1_arr) & ctx.np.isfinite(f_f2_arr) & (f_f2_arr > f_f1_arr)
            s_f1 = f_f1_arr[mask]
            s_f2 = f_f2_arr[mask]
            
            if len(s_f1) == 0:
                continue
                
            if g_name not in vowel_data:
                vowel_data[g_name] = {"f1": [], "f2": []}
                
            vowel_data[g_name]["f1"].append(s_f1)
            vowel_data[g_name]["f2"].append(s_f2)

    if not vowel_data:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "未找到有效的共振峰分析数据", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty_chart.png", title="无有效共振峰数据提示")

    # 3. 绘图
    fig, ax = ctx.plt.subplots(figsize=(8.6, 7.2))
    ax.set_facecolor("#F8FAFC")
    ax.grid(True, linestyle="--", alpha=0.25, linewidth=0.8)

    # 配色方案
    categories = sorted(list(vowel_data.keys()))
    cmap = ctx.plt.get_cmap('tab10')
    cat_colors = {cat: cmap(i % 10) for i, cat in enumerate(categories)}

    # 置信椭圆绘制辅助函数
    def draw_confidence_ellipse(x, y, ax, n_std=1.0, facecolor='none', **kwargs):
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
            return ax.add_patch(ellipse)
        except Exception:
            return None

    all_f1_plotted = []
    all_f2_plotted = []

    # 绘制原始散点 (底层)
    for cat in categories:
        color = cat_colors[cat]
        f1_concat = ctx.np.concatenate(vowel_data[cat]["f1"])
        f2_concat = ctx.np.concatenate(vowel_data[cat]["f2"])
        vowel_data[cat]["f1_concat"] = f1_concat
        vowel_data[cat]["f2_concat"] = f2_concat
        
        all_f1_plotted.append(f1_concat)
        all_f2_plotted.append(f2_concat)
        
        ax.scatter(f2_concat, f1_concat, color=color, s=14, alpha=0.15, edgecolors='none', zorder=3)

    # 绘制置信椭圆与均值中心点 (顶层)
    for cat in categories:
        color = cat_colors[cat]
        f1_concat = vowel_data[cat]["f1_concat"]
        f2_concat = vowel_data[cat]["f2_concat"]
        
        mean_f1 = ctx.np.mean(f1_concat)
        mean_f2 = ctx.np.mean(f2_concat)
        
        # 绘制 1-sigma 置信椭圆
        draw_confidence_ellipse(f2_concat, f1_concat, ax, n_std=1.0, edgecolor=color, linestyle='--', linewidth=1.5, zorder=4)
        
        # 绘制大星中心点
        ax.scatter(mean_f2, mean_f1, color=color, s=150, marker='o', edgecolors='black', linewidth=1.2, zorder=6, label=cat)
        
        # 绘制分组标签
        ax.text(mean_f2, mean_f1 - 15, cat, fontsize=11, fontweight='bold', color='#111827', ha='center', va='bottom', zorder=7,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.8, lw=1))

    # 传统元音空间图需要反转 X 轴 (F2) 和 Y 轴 (F1)
    ax.invert_xaxis()
    ax.invert_yaxis()

    # 设置坐标轴范围
    if all_f1_plotted and all_f2_plotted:
        limit_f1 = ctx.np.concatenate(all_f1_plotted)
        limit_f2 = ctx.np.concatenate(all_f2_plotted)
        f1_p1, f1_p99 = ctx.np.percentile(limit_f1, 1.0), ctx.np.percentile(limit_f1, 99.0)
        f2_p1, f2_p99 = ctx.np.percentile(limit_f2, 1.0), ctx.np.percentile(limit_f2, 99.0)
        
        f1_pad = (f1_p99 - f1_p1) * 0.15 if f1_p99 > f1_p1 else 100.0
        f2_pad = (f2_p99 - f2_p1) * 0.15 if f2_p99 > f2_p1 else 150.0
        
        ax.set_ylim(f1_p99 + f1_pad, max(50.0, f1_p1 - f1_pad))
        ax.set_xlim(f2_p99 + f2_pad, max(500.0, f2_p1 - f2_pad))

    ax.set_title("F1/F2 元音空间分布图", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("F2 频率 (Hz)", fontsize=12, fontweight='bold', labelpad=10)
    ax.set_ylabel("F1 频率 (Hz)", fontsize=12, fontweight='bold', labelpad=10)

    if vowel_data:
        ax.legend(loc="upper right")

    ctx.log(f"成功绘制 {len(vowel_data)} 个不同分组的元音空间分布图。")
    return ctx.figure(fig, filename="vowel_space.png", title="元音空间分布图")
'''
    }
]

def get_scripts_dir():
    """获取脚本存储目录路径"""
    path = os.path.join(os.path.expanduser("~"), ".phon_tracer", "scripts")
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def load_all_scripts():
    """加载本地所有脚本"""
    s_dir = get_scripts_dir()

    # 自动更新最新版内置的两个示例脚本
    for ds in DEFAULT_SCRIPTS:
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
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if os.path.exists(f_path):
        os.remove(f_path)
        return True
    return False

def copy_script(script_id):
    """复制现有脚本"""
    s_dir = get_scripts_dir()
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError(f"找不到要复制的脚本。")

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
    new_f_path = os.path.join(s_dir, f"{new_id}.json")
    with open(new_f_path, 'w', encoding='utf-8') as file:
        json.dump(s_dict, file, ensure_ascii=False, indent=2)

    return s_dict

def export_script(script_id, dest_file_path):
    """导出脚本到目标路径"""
    s_dir = get_scripts_dir()
    f_path = os.path.join(s_dir, f"{script_id}.json")
    if not os.path.exists(f_path):
        raise FileNotFoundError("找不到要导出的脚本。")

    shutil.copy2(f_path, dest_file_path)
    return True
