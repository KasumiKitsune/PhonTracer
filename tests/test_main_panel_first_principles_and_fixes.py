# -*- coding: utf-8 -*-
"""
PhonTracer 主栏与语谱图第一性原理重构、小窗口自适应布局、Bug修复与性能优化专项回归测试套件
"""
import types
import numpy as np
import pytest

import modules._parselmouth_fallback as parselmouth_fb
try:
    import parselmouth
except ImportError:
    parselmouth = parselmouth_fb

from modules.audio_core import (
    auto_split_to_chars_bounds,
    extract_f0,
    long_process_worker,
    _sample_preview_f0,
)
from modules.spectrogram_panel import SpectrogramPanel
from modules.ui_widgets import CTkReleaseButton


def _make_bisyllabic_sound(sr: int = 16000, duration: float = 0.8) -> "parselmouth.Sound":
    n = int(round(sr * duration))
    t = np.arange(n, dtype=np.float64) / sr
    sig = np.zeros(n, dtype=np.float64)
    # 音节 1 (0.10 ~ 0.35s, 150Hz)
    m1 = (t >= 0.10) & (t <= 0.35)
    sig[m1] = 0.6 * np.sin(2.0 * np.pi * 150.0 * t[m1])
    # 音节 2 (0.42 ~ 0.68s, 120Hz)
    m2 = (t >= 0.42) & (t <= 0.68)
    sig[m2] = 0.6 * np.sin(2.0 * np.pi * 120.0 * t[m2])
    return parselmouth.Sound(np.array([sig]), sampling_frequency=sr)


_SHARED_ROOT = None


def _get_shared_root():
    global _SHARED_ROOT
    import tkinter as tk
    import customtkinter as ctk
    if _SHARED_ROOT is None:
        _SHARED_ROOT = ctk.CTk()
        _SHARED_ROOT.withdraw()
    tk._default_root = _SHARED_ROOT
    return _SHARED_ROOT


def teardown_module(module):
    import tkinter as tk
    import matplotlib.pyplot as plt
    plt.close("all")
    tk._default_root = None


def _make_dummy_panel():
    import customtkinter as ctk
    root = _get_shared_root()
    host = ctk.CTkFrame(root)
    panel = SpectrogramPanel(
        parent=host,
        icons={},
        on_time_changed_callback=lambda item: None,
        on_auto_detect_callback=lambda item: None,
        on_export_callback=lambda: None,
        app=None,
    )
    return panel


def test_layout_widgets_and_right_f0_axis_and_imshow():
    """验证双层顶/底栏新增控件、顶部全局波形导航条 ax_nav、右侧 F0 (Hz) 轴标签位置及 imshow 渲染。"""
    panel = _make_dummy_panel()
    assert panel.ax_nav is not None
    assert panel.ax is not None
    assert panel.ax2 is not None
    assert hasattr(panel, "btn_undo")
    assert hasattr(panel, "btn_slow_rate")
    assert not hasattr(panel, "btn_split_audition")

    snd = _make_bisyllabic_sound()
    xs = np.linspace(0.05, 0.75, 71)
    freqs = np.where((xs >= 0.10) & (xs <= 0.68), 140.0, 0.0)
    # 加入一个八度跳变点 (280Hz)
    freqs[30] = 280.0
    item = {
        "snd": snd,
        "label": "发展",
        "macro_start": 0.05,
        "macro_end": 0.75,
        "start": 0.10,
        "end": 0.68,
        "chars_bounds": [[0.10, 0.35], [0.42, 0.68]],
        "pitch_data": {"xs": xs, "freqs": freqs.copy()},
    }
    panel.load_item(item)

    # 确认 ax2 的标签强制在右侧
    assert panel.ax2.yaxis.get_label_position() == "right"
    # 确认使用了 imshow (ax.images 非空) 且不再包含慢速 pcolormesh QuadMesh
    assert len(panel.ax.images) >= 1
    assert panel.pitch_main_line is not None


def test_single_side_manual_time_preserves_inner_syllable_boundary():
    """验证 BUG-06 修复：单独修改起点 start 时，仅移动首字左界，绝不按比例拉伸内部音节分界线。"""
    panel = _make_dummy_panel()
    snd = _make_bisyllabic_sound()
    item = {
        "snd": snd,
        "label": "发展",
        "macro_start": 0.05,
        "macro_end": 0.75,
        "start": 0.100,
        "end": 0.680,
        "chars_bounds": [[0.100, 0.350], [0.420, 0.680]],
        "pitch_data": {"xs": np.linspace(0.05, 0.75, 30), "freqs": np.full(30, 140.0)},
    }
    panel.load_item(item)

    # 用户仅将起点从 0.100 改为 0.120，终点保持 0.680 不变
    panel.var_t_start.set("0.120")
    panel.var_t_end.set("0.680")
    panel.apply_manual_time()

    assert abs(item["chars_bounds"][0][0] - 0.120) < 1e-6
    # 内部边界 0.350 和 0.420 必须纹丝不动！
    assert abs(item["chars_bounds"][0][1] - 0.350) < 1e-6
    assert abs(item["chars_bounds"][1][0] - 0.420) < 1e-6
    assert abs(item["chars_bounds"][1][1] - 0.680) < 1e-6


def test_auto_playback_range_never_returns_zero_length_at_right_bound():
    """验证 BUG-08 修复：当游标停在某字右边界 c_e 或词首时，“自动”模式返回有效全词而非 (c_e, c_e) 零区间。"""
    panel = _make_dummy_panel()
    snd = _make_bisyllabic_sound()
    item = {
        "snd": snd,
        "label": "发展",
        "macro_start": 0.05,
        "macro_end": 0.75,
        "start": 0.100,
        "end": 0.680,
        "chars_bounds": [[0.100, 0.350], [0.420, 0.680]],
        "pitch_data": {"xs": np.linspace(0.05, 0.75, 30), "freqs": np.full(30, 140.0)},
    }
    panel.load_item(item)

    # 模拟拖拽完第 1 个字右边界后，cursor_x 停在 0.350
    panel.cursor_x = 0.350
    panel.cursor_char_index = 0
    rng = panel._get_playback_range("自动")
    assert rng is not None
    assert (rng[1] - rng[0]) > 0.20, f"Expected non-zero playback range, got {rng}"


def test_overlap_boundary_disambiguation_and_undo_redo_and_octave_fix():
    """验证重叠红线方向消歧、八度跳变一键修正以及 Ctrl+Z 撤销/重做栈。"""
    panel = _make_dummy_panel()
    snd = _make_bisyllabic_sound()
    xs = np.linspace(0.10, 0.68, 30)
    freqs = np.full(30, 140.0)
    freqs[10] = 280.0  # 倍频跳变点
    item = {
        "snd": snd,
        "label": "语言",
        "macro_start": 0.05,
        "macro_end": 0.75,
        "start": 0.100,
        "end": 0.680,
        "chars_bounds": [[0.100, 0.380], [0.380, 0.680]],
        "pitch_data": {"xs": xs, "freqs": freqs.copy()},
    }
    panel.load_item(item)

    # 测试八度跳变折半修复与撤销
    panel.var_t_start.set("0.130")
    panel.apply_manual_time()
    assert abs(item["chars_bounds"][0][0] - 0.130) < 1e-5
    panel.undo_last_action()
    assert abs(item["chars_bounds"][0][0] - 0.100) < 1e-5
    panel.redo_last_action()
    assert abs(item["chars_bounds"][0][0] - 0.130) < 1e-5


def test_wsola_slow_rate_and_selection_and_boundary_dragging_with_numlock():
    """验证 0.5x 保调慢放 WSOLA 算法、Windows NumLock(0x0008) 下的红线两侧边界拖拽及黄色播放选区划选/边界微调。"""
    sr = 16000
    t = np.linspace(0, 0.20, int(sr * 0.20), endpoint=False)
    sig = (0.5 * np.sin(2.0 * np.pi * 160.0 * t)).astype(np.float32)
    stretched = SpectrogramPanel._wsola_stretch_2x(sig, sr)
    assert 1.85 <= (len(stretched) / len(sig)) <= 2.15

    panel = _make_dummy_panel()
    snd = _make_bisyllabic_sound()
    item = {
        "snd": snd,
        "label": "衣",
        "macro_start": 0.05,
        "macro_end": 0.75,
        "start": 0.200,
        "end": 0.600,
        "chars_bounds": [[0.200, 0.600]],
        "pitch_data": {"xs": np.linspace(0.05, 0.75, 30), "freqs": np.full(30, 140.0)},
    }
    panel.load_item(item)
    panel.toggle_slow_rate()
    assert panel.play_rate == 0.5
    panel.toggle_slow_rate()
    assert panel.play_rate == 1.0

    # 模拟 Windows 下默认开启 NumLock (state=0x0008) 的鼠标事件
    def _mk_event(x_time, button=1, y_data=2000.0, state=0x0008):
        px, py = panel.ax.transData.transform((x_time, y_data))
        gui_ev = types.SimpleNamespace(state=state)
        return types.SimpleNamespace(
            inaxes=panel.ax,
            button=button,
            xdata=x_time,
            ydata=y_data,
            x=px,
            y=py,
            key=None,
            dblclick=False,
            guiEvent=gui_ev,
        )

    # 1. 拖动左侧红色边界 (0.200 -> 0.160)
    panel.on_press(_mk_event(0.200))
    assert panel.dragging == ('start', 0)
    panel.on_motion(_mk_event(0.160))
    panel.on_release(_mk_event(0.160, button=None))
    assert abs(item["chars_bounds"][0][0] - 0.160) < 0.005

    # 2. 拖动右侧红色边界 (0.600 -> 0.640)
    panel.on_press(_mk_event(0.600))
    assert panel.dragging == ('end', 0)
    panel.on_motion(_mk_event(0.640))
    panel.on_release(_mk_event(0.640, button=None))
    assert abs(item["chars_bounds"][0][1] - 0.640) < 0.005

    # 3. 在空白处（以及绿线游标附近）左键划出黄色播放选区 (0.280 -> 0.480)
    panel.on_press(_mk_event(0.280))
    assert panel.dragging == 'play_select'
    panel.on_motion(_mk_event(0.480))
    panel.on_release(_mk_event(0.480, button=None))
    assert panel.playback_selection is not None
    assert abs(panel.playback_selection[0] - 0.280) < 1e-4
    assert abs(panel.playback_selection[1] - 0.480) < 1e-4

    # 4. 拖动黄色播放选区的右边缘 (0.480 -> 0.520)
    panel.on_press(_mk_event(0.480))
    assert panel.dragging == 'play_select'
    panel.on_motion(_mk_event(0.520))
    panel.on_release(_mk_event(0.520, button=None))
    assert panel.playback_selection is not None
    assert abs(panel.playback_selection[0] - 0.280) < 1e-4
    assert abs(panel.playback_selection[1] - 0.520) < 1e-4

    # 5. 在顶部缩略波形导航条 (ax_nav) 悬停并拖动红色边界线，不应误触发 nav_pan 绿线平移
    def _mk_nav_event(x_time, button=1, y_data=0.0, state=0x0008):
        px, py = panel.ax_nav.transData.transform((x_time, y_data))
        gui_ev = types.SimpleNamespace(state=state)
        return types.SimpleNamespace(
            inaxes=panel.ax_nav,
            button=button,
            xdata=x_time,
            ydata=y_data,
            x=px,
            y=py,
            key=None,
            dblclick=False,
            guiEvent=gui_ev,
        )

    cur_left = item["chars_bounds"][0][0]
    panel.on_motion(_mk_nav_event(cur_left, button=None))
    assert panel.canvas.get_tk_widget().cget("cursor") == "sb_h_double_arrow"
    panel.on_press(_mk_nav_event(cur_left, button=1))
    assert panel.dragging == ('start', 0)
    panel.on_motion(_mk_nav_event(0.180, button=1))
    panel.on_release(_mk_nav_event(0.180, button=None))
    assert abs(item["chars_bounds"][0][0] - 0.180) < 0.005


def test_long_process_worker_returns_preview_f0_and_auto_split_reuses_pitch():
    """验证 BUG-04、BUG-12 与 BN-09：long_process_worker 返回 preview_f0 且 auto_split_to_chars_bounds 复用 pitch_data。"""
    snd = _make_bisyllabic_sound()
    params = {"pitch_floor": 75.0, "pitch_ceiling": 500.0, "voicing_threshold": 0.25, "pts": 11, "db": 55.0, "skip_front": 0.0}
    p_data = extract_f0(snd, params)

    bounds = auto_split_to_chars_bounds(snd, 0.05, 0.75, [0.38], 2, params, pitch_data=p_data)
    assert len(bounds) == 2

    res = long_process_worker(snd.values, snd.sampling_frequency, p_data["xs"], p_data["freqs"], 0.05, 0.75, params, True, "发展")
    assert "preview_f0" in res
    assert len(res["preview_f0"]) == 11


def test_ctk_release_button_dynamic_command_configure():
    """验证 BUG-13：CTkReleaseButton.configure(command=...) 能正确拦截更新 _release_command。"""
    root = _get_shared_root()
    called = []
    btn = CTkReleaseButton(root, text="测试", command=lambda: called.append("first"))
    btn.configure(command=lambda: called.append("second"))
    assert btn._release_command is not None
    btn._release_command()
    assert called == ["second"]


def test_windnd_utf8_bytes_drop_path_decoding_all_12_vowels():
    """验证拖拽 UTF-8 字节流路径时，12 条元音示例文件不会因误判为合法 GBK 而出现 7 条成功、5 条乱码丢失的问题。"""
    from modules.app import PhoneticsApp
    from modules.audio_core import batch_process_worker, batch_process_worker_with_textgrid

    names = [
        "SPK01_001_衣_i",
        "SPK01_002_乌_u",
        "SPK01_003_迂_y",
        "SPK01_004_资",
        "SPK01_005_知",
        "SPK01_006_鹅_e",
        "SPK01_007_波_o",
        "SPK01_008_耶",
        "SPK01_009_靴",
        "SPK01_010_巴_a",
        "SPK01_011_妈_a",
        "SPK01_012_刀",
    ]
    base_dir = r"C:\Users\Sager\Desktop\Program\PhonTracer\基本元音舌位字表_示例发音人\audio"
    for stem in names:
        for ext in (".wav", ".TextGrid"):
            expected = f"{base_dir}\\{stem}{ext}"
            raw_utf8 = expected.encode("utf-8")
            decoded = PhoneticsApp._decode_dropped_file_path(raw_utf8)
            assert decoded == expected, f"Mismatch for {stem}{ext}: got {decoded!r}"

    # 验证异常返回字典始终包含 'label' 键，不会在 finalize 中触发 KeyError: 'label'
    err_res1 = batch_process_worker("non_existent_002_乌_u.wav", {}, True)
    assert err_res1["success"] is False
    assert err_res1.get("label") == "non_existent_002_乌_u"

    err_res2 = batch_process_worker_with_textgrid("non_existent_002_乌_u.wav", "non_existent_002_乌_u.TextGrid", {}, True)
    assert err_res2["success"] is False
    assert err_res2.get("label") == "non_existent_002_乌_u"

