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

    # 3. 初始化画板
    fig, ax = ctx.plt.subplots(figsize=(8, 5))
    ax.grid(True, linestyle="--", alpha=0.5)

    # 4. 遍历分组进行数据对齐与统计
    num_pts = 11
    plotted_count = 0

    for g_name, g_items in groups.items():
        all_curves = []
        for item in g_items:
            pitch = ctx.dataset.pitch_points(item)
            freqs = pitch.get("freqs", [])
            valid_freqs = [f for f in freqs if f > 0]
            if len(valid_freqs) >= 2:
                all_curves.append(valid_freqs)

        if not all_curves:
            continue

        # 对齐到 11 点
        aligned_curves = []
        for freqs in all_curves:
            x_old = ctx.np.linspace(0, 1, len(freqs))
            x_new = ctx.np.linspace(0, 1, num_pts)
            iy = ctx.np.interp(x_new, x_old, freqs)
            aligned_curves.append(iy)

        # 计算均值和标准差
        mean_y = ctx.np.mean(aligned_curves, axis=0)
        std_y = ctx.np.std(aligned_curves, axis=0)

        x_pts = ctx.np.linspace(0, 100, num_pts)

        # 绘图
        ax.plot(x_pts, mean_y, "-o", label=g_name, linewidth=2)
        ax.fill_between(x_pts, mean_y - std_y, mean_y + std_y, alpha=0.1)
        plotted_count += 1

    ax.set_title("各声调组 F0 均值曲线图", fontsize=14, fontweight="bold")
    ax.set_xlabel("归一化时间 (%)", fontsize=12)
    ax.set_ylabel("基频 F0 (Hz)", fontsize=12)

    if plotted_count > 0:
        ax.legend(loc="upper right")

    ctx.log(f"成功绘制 {plotted_count} 个分组的均值曲线。")
    return ctx.figure(fig, filename="f0_group_means.png", title="F0 分组均值图")
'''
    },
    {
        "id": "builtin_vowel_space",
        "name": "F1/F2 元音空间图 (示例)",
        "description": "提取纳入分析的条目的 F1 和 F2 共振峰数据（取中点），绘制元音空间散点图（反转坐标轴）。",
        "type": "chart",
        "code": '''def run(ctx):
    # 1. 获取所有纳入分析的条目
    items = ctx.dataset.included_items()
    if not items:
        fig, ax = ctx.plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "没有符合条件的分析数据\\n请先加载工程", ha="center", va="center", fontsize=12, color="red")
        ax.axis("off")
        return ctx.figure(fig, filename="empty_chart.png", title="无数据提示")

    # 2. 收集 F1/F2 中点值
    vowel_data = {}
    for item in items:
        # 检查是否为共振峰模式
        formant = ctx.dataset.formant_points(item)
        f1_vals = formant.get("f1", [])
        f2_vals = formant.get("f2", [])

        # 提取有效值
        valid_f1 = [f for f in f1_vals if f > 0]
        valid_f2 = [f for f in f2_vals if f > 0]

        if valid_f1 and valid_f2:
            # 获取中点位置的值
            mid_idx = len(valid_f1) // 2
            f1_mid = valid_f1[mid_idx]
            f2_mid = valid_f2[mid_idx]

            lbl = item.get("label", "元音")
            # 过滤掉标签中的数字（比如 ma1 -> ma）
            import re
            clean_lbl = re.sub(r'\\d+', '', lbl)

            if clean_lbl not in vowel_data:
                vowel_data[clean_lbl] = {"f1": [], "f2": []}
            vowel_data[clean_lbl]["f1"].append(f1_mid)
            vowel_data[clean_lbl]["f2"].append(f2_mid)

    # 3. 绘图
    fig, ax = ctx.plt.subplots(figsize=(7, 6))
    ax.grid(True, linestyle="--", alpha=0.5)

    colors = ["#2563EB", "#DC2626", "#16A34A", "#9333EA", "#EA580C", "#0891B2"]
    for idx, (lbl, coords) in enumerate(vowel_data.items()):
        f1s = coords["f1"]
        f2s = coords["f2"]
        color = colors[idx % len(colors)]

        # 绘制散点
        ax.scatter(f2s, f1s, label=lbl, color=color, alpha=0.6, edgecolors="none", s=50)

        # 绘制均值中心点
        mean_f1 = ctx.np.mean(f1s)
        mean_f2 = ctx.np.mean(f2s)
        ax.scatter(mean_f2, mean_f1, color=color, s=150, marker="*", edgecolors="black", linewidths=1.5)
        ax.text(mean_f2 + 15, mean_f1 + 10, lbl, fontsize=12, fontweight="bold", color="black")

    ax.set_title("F1/F2 元音空间散点图 (取中点)", fontsize=14, fontweight="bold")
    ax.set_xlabel("F2 频率 (Hz)", fontsize=12)
    ax.set_ylabel("F1 频率 (Hz)", fontsize=12)

    # 传统元音空间图需要反转 X 轴 (F2) 和 Y 轴 (F1)
    ax.invert_xaxis()
    ax.invert_yaxis()

    if vowel_data:
        ax.legend(loc="upper right")

    ctx.log(f"成功绘制 {len(vowel_data)} 个不同元音的元音空间分布图。")
    return ctx.figure(fig, filename="vowel_space.png", title="元音空间图")
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

    # 如果本地没有任何自定义脚本，则自动导入默认的内置脚本
    if not scripts:
        for ds in DEFAULT_SCRIPTS:
            save_script(ds["id"], ds["name"], ds["description"], ds["type"], ds["code"])
            scripts.append(ds)

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
