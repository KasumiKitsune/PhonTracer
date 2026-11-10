"""
Chart formant settings builder mixin for acoustic chart export dialog.
Provides GUI controls and layout helpers for formant-related chart settings.
"""
from __future__ import annotations

import numpy as np

try:
    import tkinter as tk
    import customtkinter as ctk
except ImportError:
    tk = None
    ctk = None


class ChartFormantSettingsBuilderMixin:
    """GUI formant controls builder mixin extracted from AcousticChartExporter."""

    def _parse_custom_ratio_value(self, raw_val, default=1.5):
        if isinstance(raw_val, (int, float)):
            try:
                v = float(raw_val)
                if np.isfinite(v) and v > 0:
                    return max(0.3, min(4.0, v))
            except Exception:
                pass
            return default
        s = str(raw_val or "").strip().replace("：", ":").replace("/", ":")
        if not s:
            return default
        if ":" in s:
            parts = [p.strip() for p in s.split(":") if p.strip()]
            if len(parts) == 2:
                try:
                    w_v = float(parts[0])
                    h_v = float(parts[1])
                    if w_v > 0 and h_v > 0:
                        return max(0.3, min(4.0, w_v / h_v))
                except Exception:
                    pass
        try:
            v = float(s)
            if np.isfinite(v) and v > 0:
                return max(0.3, min(4.0, v))
        except Exception:
            pass
        return default


    def _make_checkbox(self, parent, text, variable=None, command=None, **kwargs):
        try:
            from modules.ui_widgets import install_dynamic_checkbox_patch
            install_dynamic_checkbox_patch()
        except Exception:
            pass
        primary = getattr(self, "ui_primary", "#2563EB")
        primary_hover = getattr(self, "ui_primary_hover", "#1D4ED8")
        options = {
            "text": text,
            "font": getattr(self, "font_small", None),
            "checkbox_width": 18,
            "checkbox_height": 18,
            "corner_radius": 1000,
            "border_width": 2,
            "fg_color": (primary, primary_hover),
            "hover_color": ("#9CA3AF", "#4B5563"),
            "border_color": ("#4B5563", "#9CA3AF"),
            "checkmark_color": "#FFFFFF",
            "text_color": ("#1F2937", "#E5E7EB"),
        }
        if variable is not None:
            options["variable"] = variable
        if command is not None:
            options["command"] = command
        options.update(kwargs)
        return ctk.CTkCheckBox(parent, **options)


    def _build_formant_space_settings(self):
        # Confidence Ellipse
        ctk.CTkLabel(self.dynamic_content_frame, text="置信椭圆 (Confidence Ellipse):", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_ellipse = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["95% 置信椭圆", "1-sigma 置信椭圆", "无置信椭圆"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_ellipse.set(self.var_formant_ellipse.get())
        self.combo_formant_ellipse.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_formant_ellipse)

        # Label Mode
        ctk.CTkLabel(self.dynamic_content_frame, text="标签显示模式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_label_mode = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["显示分组标签", "显示单字标签", "显示词语标签", "不显示标签"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_label_mode.set("显示分组标签")
        self.combo_formant_label_mode.pack(fill=tk.X, pady=2)
        self._apply_custom_arrow(self.combo_formant_label_mode)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位/归一化:", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_normalization)

        self.cb_show_convex_hull = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示元音空间外围凸包与面积 (VSA)",
            variable=self.var_show_convex_hull,
            command=self.update_preview
        )
        self.cb_show_convex_hull.pack(anchor="w", pady=(4, 4))

        self.cb_show_diphthong_arrows = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示复合元音 20%→80% 方向箭头轨迹 (TL)",
            variable=self.var_show_diphthong_arrows,
            command=self.update_preview
        )
        self.cb_show_diphthong_arrows.pack(anchor="w", pady=(4, 4))

        self.cb_formant_axis_lock = self._make_checkbox(
            self.dynamic_content_frame,
            text="固定横纵坐标范围 (跨组/跨页一致)",
            variable=self.var_formant_axis_lock,
            command=self.update_preview
        )
        self.cb_formant_axis_lock.pack(anchor="w", pady=(2, 6))

        self.cb_formant_show_raw = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示个体测量帧 (散点背景)",
            variable=self.var_formant_show_raw,
            command=self.update_preview
        )
        self.cb_formant_show_raw.pack(anchor="w", pady=(4, 4))

        self.var_formant_time_gradient.set(False)
        self.var_formant_density_overlay.set(False)

        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_density_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按字表组分面", "按音节位置分面", "按发音人分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs
        )
        self.combo_formant_density_facet.set(self.var_formant_density_facet.get())
        self.combo_formant_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_density_facet)


    def _on_formant_density_bw_changed(self, val):
        bw = float(val)
        self.var_formant_density_bw.set(bw)
        if hasattr(self, 'lbl_formant_density_bw'):
            self.lbl_formant_density_bw.configure(text=f"{bw:.2f}")
        self.trigger_preview_update()


    def _build_formant_density_settings(self):
        self.cb_formant_density_show_raw = self._make_checkbox(
            self.dynamic_content_frame,
            text="显示个体测量帧 (极淡散点)",
            variable=self.var_formant_density_show_raw,
            command=self.update_preview
        )
        self.cb_formant_density_show_raw.pack(anchor="w", pady=(10, 5))

        self.cb_formant_density_show_contours = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加等密度轮廓线",
            variable=self.var_formant_density_show_contours,
            command=self.update_preview
        )
        self.cb_formant_density_show_contours.pack(anchor="w", pady=(5, 5))

        ctk.CTkLabel(self.dynamic_content_frame, text="分面子图排版 (Facet):", font=self.font_small).pack(anchor="w", pady=(6, 2))
        self.combo_formant_density_facet = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["单图展示 (不分面)", "按字表组分面", "按音节位置分面", "按发音人分面"],
            command=lambda val: self._sync_facet_from_child(val),
            **self.dropdown_kwargs
        )
        self.combo_formant_density_facet.set(self.var_formant_density_facet.get())
        self.combo_formant_density_facet.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_density_facet)


    def _build_formant_trajectory_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="曲线展现形式:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_formant_traj_style = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["平均曲线 + 置信区间阴影", "仅平均曲线", "平均曲线 + 个体浅色细线"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_traj_style.set(self.var_formant_traj_style.get() or "仅平均曲线")
        self.combo_formant_traj_style.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_traj_style)

        ctk.CTkLabel(self.dynamic_content_frame, text="音节位置筛选:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_syllable_filter = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["全部音节", "第 1 音节", "第 2 音节"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_syllable_filter.set("全部音节")
        self.combo_formant_syllable_filter.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_syllable_filter)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 6))
        self._apply_custom_arrow(self.combo_formant_normalization)

        self.cb_show_f3 = self._make_checkbox(
            self.dynamic_content_frame,
            text="叠加 F3 第三共振峰轨迹 (点划线)",
            variable=self.var_show_f3,
            command=self.update_preview
        )
        self.cb_show_f3.pack(anchor="w", pady=(4, 8))


    def _build_formant_pillai_settings(self):
        ctk.CTkLabel(self.dynamic_content_frame, text="元音对立与分布子视图:", font=self.font_small).pack(anchor="w", pady=(5, 2))
        self.combo_pillai_subview = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["元音对立重叠度矩阵 (Pillai Score)", "共振峰与时长分布雨云图"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_pillai_subview.set("元音对立重叠度矩阵 (Pillai Score)")
        self.combo_pillai_subview.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_pillai_subview)

        ctk.CTkLabel(self.dynamic_content_frame, text="共振峰声学单位:", font=self.font_small).pack(anchor="w", pady=(4, 2))
        self.combo_formant_normalization = ctk.CTkOptionMenu(
            self.dynamic_content_frame,
            values=["原始频率 (Hz)", "Bark (听觉巴克)", "ERB (等效矩形带宽)", "Lobanov z-score (学术标准)"],
            command=lambda _: self.trigger_preview_update(),
            **self.dropdown_kwargs
        )
        self.combo_formant_normalization.set(self.var_formant_normalization.get())
        self.combo_formant_normalization.pack(fill=tk.X, pady=(2, 8))
        self._apply_custom_arrow(self.combo_formant_normalization)


    def locate_qc_item_in_project_tree(self, item_id, speaker_name=None, label=None):
        """
        阶段 A 质检交互闭环：在质检图表中发现异常词条后，直接定位并选中主程序工程树对应词条，
        支持跨发音人自动切换与分组展开。
        """
        pt = getattr(self, "project_tree", None)
        if pt is None:
            return False

        # 若指定了发音人且与当前发音人不同，尝试通过主程序切换发音人
        if speaker_name and getattr(self, "app", None) is not None:
            sm = getattr(self.app, "speaker_manager", None)
            speakers = list(getattr(self, "all_speakers", None) or [])
            if not speakers and sm is not None and hasattr(sm, "speakers"):
                speakers = list(sm.speakers.values())
            target_spk = next((s for s in speakers if getattr(s, "name", None) == speaker_name), None)
            if target_spk is not None and sm is not None:
                active_spk = sm.get_active_speaker() if hasattr(sm, "get_active_speaker") else None
                if active_spk is None or getattr(active_spk, "id", None) != getattr(target_spk, "id", None):
                    for switch_attr in ("switch_speaker", "select_speaker", "on_speaker_selected"):
                        fn = getattr(self.app, switch_attr, None) or getattr(sm, switch_attr, None)
                        if callable(fn):
                            try:
                                fn(target_spk.id)
                                break
                            except Exception:
                                pass

        items_dict = getattr(pt, "items", None) or {}
        real_iid = None
        if item_id and str(item_id) in items_dict:
            real_iid = str(item_id)
        elif label:
            clean_lbl = str(label).split(" (")[0].replace("[!] ", "").strip()
            for k, v in items_dict.items():
                if isinstance(v, dict) and v.get("label") in (label, clean_lbl):
                    real_iid = str(k)
                    break
        if real_iid is None and item_id:
            real_iid = str(item_id)

        if not real_iid:
            return False

        self._last_located_qc_item_id = real_iid
        try:
            pt.current_iid = real_iid
        except Exception:
            pass

        tree = getattr(pt, "tree", None)
        if tree is not None:
            try:
                target_node = None
                if hasattr(tree, "exists"):
                    if tree.exists(real_iid):
                        target_node = real_iid
                    elif tree.exists(f"warning_{real_iid}"):
                        target_node = f"warning_{real_iid}"
                if target_node is not None:
                    if hasattr(tree, "parent") and hasattr(tree, "item"):
                        parent_gid = tree.parent(target_node)
                        if parent_gid:
                            tree.item(parent_gid, open=True)
                    if hasattr(tree, "selection_set"):
                        tree.selection_set(target_node)
                    if hasattr(tree, "see"):
                        tree.see(target_node)
                    if hasattr(tree, "focus"):
                        tree.focus(target_node)
            except Exception:
                pass

        if callable(getattr(pt, "on_tree_select", None)):
            try:
                pt.on_tree_select(None)
            except Exception:
                pass
        elif callable(getattr(pt, "on_item_selected", None)):
            try:
                pt.on_item_selected(real_iid)
            except Exception:
                pass

        return real_iid in items_dict or bool(real_iid)

