"""
Interactive eraser tool and pitch/formant point modification mixin.
"""
import copy
import tkinter as tk
import numpy as np
import matplotlib.patches as mpatches


class EraserMixin:

    def _draw_animated_eraser_layers(self):
        if self.ax2 and self.erased_pitch_layer is not None:
            try:
                self.ax2.draw_artist(self.erased_pitch_layer)
            except Exception:
                pass
        if self.ax:
            for layer_name in ("erased_f1_layer", "erased_f2_layer", "erased_f3_layer"):
                layer = getattr(self, layer_name, None)
                if layer is not None:
                    try:
                        self.ax.draw_artist(layer)
                    except Exception:
                        pass

    def _reset_eraser_session(self):
        self.session_erased_pitch_indices = set()
        self.session_erased_formant_indices = {"f1": set(), "f2": set(), "f3": set()}
        self.session_erased_pitch_points = {}
        self.session_erased_formant_points = {"f1": {}, "f2": {}, "f3": {}}

    def _remember_erased_pitch_points(self, xs, freqs):
        points = getattr(self, 'session_erased_pitch_points', {})
        orig_freqs = (
            self.current_item.get('_orig_pitch_freqs')
            if self.current_item and '_orig_pitch_freqs' in self.current_item
            else freqs
        )
        for idx in self.session_erased_pitch_indices:
            if idx not in points and 0 <= idx < len(xs):
                val = orig_freqs[idx] if (orig_freqs is not None and idx < len(orig_freqs) and orig_freqs[idx] > 0) else freqs[idx]
                if val > 0:
                    points[idx] = (xs[idx], val)
        self.session_erased_pitch_points = points

    def _remember_erased_formant_points(self, xs, f1, f2, f3=None):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        orig_fd = self.current_item.get('_orig_formant_data', {}) if self.current_item else {}
        curves = [("f1", f1), ("f2", f2)]
        if f3 is not None:
            curves.append(("f3", f3))
        for curve, values in curves:
            curve_points = points.setdefault(curve, {})
            orig_arr = orig_fd.get(curve, values)
            for idx in self.session_erased_formant_indices.get(curve, set()):
                if idx not in curve_points and 0 <= idx < len(xs) and 0 <= idx < len(values):
                    val = orig_arr[idx] if (orig_arr is not None and idx < len(orig_arr) and not np.isnan(orig_arr[idx])) else values[idx]
                    if not np.isnan(val):
                        curve_points[idx] = (xs[idx], val)
        self.session_erased_formant_points = points

    def _update_erased_pitch_layer(self):
        if not hasattr(self, 'erased_pitch_layer') or not self.erased_pitch_layer:
            return
        points = getattr(self, 'session_erased_pitch_points', {})
        ordered_points = [points[idx] for idx in sorted(points) if idx in self.session_erased_pitch_indices]
        self.erased_pitch_layer.set_data(
            [point[0] for point in ordered_points],
            [point[1] for point in ordered_points],
        )

    def _update_erased_formant_layers(self):
        points = getattr(self, 'session_erased_formant_points', {"f1": {}, "f2": {}, "f3": {}})
        for curve, layer_name in (("f1", "erased_f1_layer"), ("f2", "erased_f2_layer"), ("f3", "erased_f3_layer")):
            layer = getattr(self, layer_name, None)
            if not layer:
                continue
            active_set = self.session_erased_formant_indices.get(curve, set())
            ordered_points = [
                points.get(curve, {})[idx]
                for idx in sorted(points.get(curve, {}))
                if idx in active_set
            ]
            offsets = np.array(ordered_points) if ordered_points else np.empty((0, 2))
            layer.set_offsets(offsets)

    def light_apply_eraser_changes(self):
        if not self.current_item:
            return

        applied_pitch = False
        applied_formant = False
        item = self.current_item

        # 提交共振峰剔除，同时保留原始坐标供红色图层持续显示
        if hasattr(self, 'session_erased_formant_indices') and (
            self.session_erased_formant_indices.get("f1")
            or self.session_erased_formant_indices.get("f2")
            or self.session_erased_formant_indices.get("f3")
        ):
            if item.get('formant_data'):
                f1 = item['formant_data']['f1']
                f2 = item['formant_data']['f2']
                f3 = item['formant_data'].get('f3')
                self._remember_erased_formant_points(item['formant_data']['xs'], f1, f2, f3)

                for curve in ("f1", "f2", "f3"):
                    arr = item["formant_data"].get(curve)
                    if arr is None:
                        continue
                    for idx in self.session_erased_formant_indices.get(curve, set()):
                        if 0 <= idx < len(arr) and not np.isnan(arr[idx]):
                            arr[idx] = np.nan
                            applied_formant = True

        # 提交 F0 擦除，同时保留原始坐标供红色图层持续显示，并实时断开蓝色 F0 主线
        if hasattr(self, 'session_erased_pitch_indices') and self.session_erased_pitch_indices:
            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy(),
                }

            if item.get('pitch_data'):
                self._remember_erased_pitch_points(item['pitch_data']['xs'], item['pitch_data']['freqs'])
                freqs = item['pitch_data']['freqs']
                for idx in self.session_erased_pitch_indices:
                    if 0 <= idx < len(freqs) and freqs[idx] != 0.0:
                        freqs[idx] = 0.0
                        applied_pitch = True
                self._refresh_pitch_main_line_ydata()

        if applied_pitch or applied_formant:
            item['is_manual_edited'] = True
            if applied_pitch:
                item['_dirty_pitch'] = True
            if applied_formant:
                item['_dirty_formant'] = True
            if 'preview_f0' in item:
                item.pop('preview_f0')
            if 'preview_formants' in item:
                item.pop('preview_formants')
            if 'has_empty_data' in item:
                item.pop('has_empty_data')

            if self.app:
                self.app.mark_modified()

                app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                if applied_formant:
                    pts = int(app_params.get('pts', 11))
                    strategy = app_params.get('formant_sample_strategy', '整段11点')
                    show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                    try:
                        if show_f3:
                            res = self.app.sample_formant_points_with_f3(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 4:
                                _, preview_f1, preview_f2, preview_f3 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                        else:
                            res = self.app.sample_formant_points(item, pts, strategy)
                            if isinstance(res, (tuple, list)) and len(res) == 3:
                                _, preview_f1, preview_f2 = res
                                item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}
                    except Exception:
                        pass

                panel = None
                if hasattr(self.app, 'tree_panel') and self.app.tree_panel:
                    panel = self.app.tree_panel
                elif hasattr(self.app, 'project_panel') and self.app.project_panel:
                    panel = self.app.project_panel
                if panel:
                    panel.update_item_icon(self.current_item_iid)
                    if hasattr(panel, 'update_preview'):
                        panel.update_preview()

    def apply_eraser_changes(self):
        self.light_apply_eraser_changes()
        self._reset_eraser_session()
        self.plot_item_spectrogram()

    def discard_eraser_changes(self):
        item = self.current_item
        restored_pitch = False
        restored_formant = False
        if item:
            pitch_points = getattr(self, 'session_erased_pitch_points', {}) or {}
            if pitch_points and item.get('pitch_data') and 'freqs' in item['pitch_data']:
                freqs = item['pitch_data']['freqs']
                for idx, (_t, orig_freq) in pitch_points.items():
                    if 0 <= idx < len(freqs):
                        freqs[idx] = orig_freq
                        restored_pitch = True

            formant_points = getattr(self, 'session_erased_formant_points', {}) or {}
            if formant_points and item.get('formant_data'):
                for curve, curve_points in formant_points.items():
                    arr = item['formant_data'].get(curve)
                    if arr is None or not curve_points:
                        continue
                    for idx, (_t, orig_val) in curve_points.items():
                        if 0 <= idx < len(arr):
                            arr[idx] = orig_val
                            restored_formant = True

            if restored_pitch or restored_formant:
                item.pop('preview_f0', None)
                item.pop('preview_formants', None)
                item.pop('has_empty_data', None)
                if self.app:
                    app_params = getattr(self.app, 'last_params', {}) if self.app else {}
                    if restored_formant:
                        pts = int(app_params.get('pts', 11))
                        strategy = app_params.get('formant_sample_strategy', '整段11点')
                        show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
                        try:
                            if show_f3:
                                res = self.app.sample_formant_points_with_f3(item, pts, strategy)
                                if isinstance(res, (tuple, list)) and len(res) == 4:
                                    _, preview_f1, preview_f2, preview_f3 = res
                                    item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2, "f3": preview_f3}
                            else:
                                res = self.app.sample_formant_points(item, pts, strategy)
                                if isinstance(res, (tuple, list)) and len(res) == 3:
                                    _, preview_f1, preview_f2 = res
                                    item['preview_formants'] = {"f1": preview_f1, "f2": preview_f2}
                        except Exception:
                            pass
                    panel = getattr(self.app, 'tree_panel', None) or getattr(self.app, 'project_panel', None)
                    if panel:
                        panel.update_item_icon(self.current_item_iid)
                        if hasattr(panel, 'update_preview'):
                            panel.update_preview()

        self._reset_eraser_session()

        # 清空待擦除点图层
        if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
            self.erased_pitch_layer.set_data([], [])
        if hasattr(self, 'erased_f1_layer') and self.erased_f1_layer:
            self.erased_f1_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f2_layer') and self.erased_f2_layer:
            self.erased_f2_layer.set_offsets(np.empty((0, 2)))
        if hasattr(self, 'erased_f3_layer') and self.erased_f3_layer:
            self.erased_f3_layer.set_offsets(np.empty((0, 2)))

        if restored_pitch or restored_formant:
            self.plot_item_spectrogram()
        else:
            self.canvas.draw_idle()

    def toggle_eraser_mode(self):
        self.eraser_mode = not self.eraser_mode
        if self.eraser_mode:
            self._reset_eraser_session()
            self.btn_eraser.configure(
                fg_color="#FEE2E2",
                text_color="#DC2626",
                hover_color="#FCA5A5",
            )
            self.canvas.get_tk_widget().config(cursor="crosshair")
            self.plot_item_spectrogram()
        else:
            self.apply_eraser_changes()
            self.btn_eraser.configure(
                fg_color="#E5E7EB",
                text_color="#1F2937",
                hover_color="#D1D5DB",
            )
            self.canvas.get_tk_widget().config(cursor="arrow")
            self.update_eraser_circle(None)

    def erase_points_near(self, event):
        item = self.current_item
        if not item:
            return

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))

        if mode == 'formant':
            if event.inaxes != self.ax:
                return
            if not item.get('formant_data'):
                return

            xs = item['formant_data']['xs']
            f1 = item['formant_data']['f1']
            f2 = item['formant_data']['f2']
            if len(xs) == 0:
                return

            show_f3 = bool(item.get("show_f3", app_params.get("show_f3", False)))
            f3 = item['formant_data'].get('f3')

            candidate_curves = [("f1", f1), ("f2", f2)]
            if show_f3 and f3 is not None:
                candidate_curves.append(("f3", f3))

            best_curve = None
            best_idx = -1
            best_dist = np.inf

            for curve, values in candidate_curves:
                temp = values.copy()
                for idx in self.session_erased_formant_indices.get(curve, set()):
                    if 0 <= idx < len(temp):
                        temp[idx] = np.nan

                clean = temp.copy()
                clean[np.isnan(clean)] = 0.0
                pixels = self.ax.transData.transform(np.column_stack((xs, clean)))
                dists = np.hypot(pixels[:, 0] - event.x, pixels[:, 1] - event.y)
                dists[np.isnan(temp)] = np.inf

                idx = int(np.argmin(dists)) if len(dists) > 0 else -1
                if idx >= 0 and dists[idx] < best_dist:
                    best_curve = curve
                    best_idx = idx
                    best_dist = dists[idx]

            if best_idx >= 0 and best_dist <= self.erase_radius:
                self.session_erased_formant_indices[best_curve].add(best_idx)
                self._remember_erased_formant_points(xs, f1, f2, f3 if show_f3 else None)
                self._update_erased_formant_layers()
                if not self._blit_cursor():
                    self.canvas.draw_idle()
        else:
            if event.inaxes != self.ax2:
                return

            if not item.get('pitch_data') and item.get('pitch'):
                pitch = item['pitch']
                item['pitch_data'] = {
                    'xs': pitch.xs(),
                    'freqs': pitch.selected_array['frequency'].copy(),
                }

            if not item.get('pitch_data'):
                return

            xs = item['pitch_data']['xs']
            freqs = item['pitch_data']['freqs']
            if len(xs) == 0:
                return

            pts_data = np.column_stack((xs, freqs))
            pts_pixels = self.ax2.transData.transform(pts_data)

            dists = np.hypot(pts_pixels[:, 0] - event.x, pts_pixels[:, 1] - event.y)

            erase_radius = self.erase_radius
            mask_to_erase = (dists <= erase_radius) & (freqs > 0)

            if np.any(mask_to_erase):
                erased_indices = np.where(mask_to_erase)[0]
                self.session_erased_pitch_indices.update(erased_indices)

                # 先保存原始点坐标，再实时断开蓝色 F0 主线并更新红色待擦除图层
                self._remember_erased_pitch_points(xs, freqs)
                self._refresh_pitch_main_line_ydata()
                if hasattr(self, 'erased_pitch_layer') and self.erased_pitch_layer:
                    self._update_erased_pitch_layer()
                if not self._blit_cursor():
                    self.canvas.draw_idle()

    def restore_points_near(self, event):
        item = self.current_item
        if not item or event.x is None or event.y is None:
            return

        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))

        if mode == 'formant':
            if event.inaxes != self.ax or not item.get('formant_data'):
                return
            xs = item['formant_data']['xs']
            orig_fd = item.get('_orig_formant_data', {})
            restored_any = False
            for curve in ("f1", "f2", "f3"):
                arr = item['formant_data'].get(curve)
                if arr is None:
                    continue
                orig_arr = orig_fd.get(curve)
                remembered = self.session_erased_formant_points.get(curve, {})
                cand_indices = set(self.session_erased_formant_indices.get(curve, set())) | set(remembered.keys())
                if orig_arr is not None:
                    cand_indices |= set(np.where(np.isnan(arr) & ~np.isnan(orig_arr))[0].tolist())
                for idx in list(cand_indices):
                    if not (0 <= idx < len(xs)):
                        continue
                    orig_val = remembered.get(idx, (None, None))[1]
                    if orig_val is None and orig_arr is not None and idx < len(orig_arr):
                        orig_val = orig_arr[idx]
                    if orig_val is None or np.isnan(orig_val):
                        continue
                    px, py = self.ax.transData.transform((xs[idx], orig_val))
                    if np.hypot(px - event.x, py - event.y) <= self.erase_radius:
                        arr[idx] = orig_val
                        self.session_erased_formant_indices.get(curve, set()).discard(idx)
                        remembered.pop(idx, None)
                        restored_any = True
            if restored_any:
                self._update_erased_formant_layers()
                self.canvas.draw_idle()
        else:
            if event.inaxes != self.ax2 or not item.get('pitch_data'):
                return
            xs = item['pitch_data']['xs']
            freqs = item['pitch_data']['freqs']
            orig_freqs = item.get('_orig_pitch_freqs')
            remembered = getattr(self, 'session_erased_pitch_points', {})
            cand_indices = set(self.session_erased_pitch_indices) | set(remembered.keys())
            if orig_freqs is not None and len(orig_freqs) == len(freqs):
                cand_indices |= set(np.where((freqs == 0) & (orig_freqs > 0))[0].tolist())
            restored_any = False
            for idx in list(cand_indices):
                if not (0 <= idx < len(xs)):
                    continue
                orig_val = remembered.get(idx, (None, None))[1]
                if orig_val is None and orig_freqs is not None and idx < len(orig_freqs):
                    orig_val = orig_freqs[idx]
                if orig_val is None or orig_val <= 0:
                    continue
                px, py = self.ax2.transData.transform((xs[idx], orig_val))
                if np.hypot(px - event.x, py - event.y) <= self.erase_radius:
                    freqs[idx] = orig_val
                    self.session_erased_pitch_indices.discard(idx)
                    remembered.pop(idx, None)
                    restored_any = True
            if restored_any:
                self._refresh_pitch_main_line_ydata()
                self._update_erased_pitch_layer()
                if not self._blit_cursor():
                    self.canvas.draw_idle()

    def update_eraser_circle(self, event=None):
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        mode = 'f0'
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        target_ax = self.ax if mode == 'formant' else self.ax2

        if not self.eraser_mode or not target_ax:
            if self.eraser_circle:
                try:
                    self.eraser_circle.remove()
                except Exception:
                    pass
                self.eraser_circle = None
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self._draw_animated_eraser_layers()
                        if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                            self.ax.draw_artist(self.cursor_line)
                        if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                            self.ax.draw_artist(self.cursor_text)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if event is None or event.x is None or event.y is None or event.inaxes != target_ax:
            if self.eraser_circle:
                self.eraser_circle.set_visible(False)
                if self.background is not None:
                    try:
                        self.canvas.restore_region(self.background)
                        self._draw_animated_eraser_layers()
                        if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                            self.ax.draw_artist(self.cursor_line)
                        if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                            self.ax.draw_artist(self.cursor_text)
                        self.canvas.blit(self.fig.bbox)
                    except Exception:
                        self.canvas.draw_idle()
            return

        if self.eraser_circle is None or self.eraser_circle not in target_ax.patches:
            from matplotlib.patches import Circle
            self.eraser_circle = Circle(
                (event.x, event.y),
                radius=self.erase_radius,
                fill=True,
                facecolor='#FEE2E2',
                edgecolor='#EF4444',
                alpha=0.4,
                linewidth=1.5,
                transform=None,
                zorder=100,
                animated=True,
            )
            target_ax.add_patch(self.eraser_circle)
        else:
            self.eraser_circle.set_center((event.x, event.y))
            self.eraser_circle.set_radius(self.erase_radius)
            self.eraser_circle.set_visible(True)

        if self.background is not None:
            try:
                self.canvas.restore_region(self.background)
                self._draw_animated_eraser_layers()
                if self.ax and self.cursor_line is not None and self.cursor_line.get_visible():
                    self.ax.draw_artist(self.cursor_line)
                if self.ax and self.cursor_text is not None and self.cursor_text.get_visible():
                    self.ax.draw_artist(self.cursor_text)
                target_ax.draw_artist(self.eraser_circle)
                self.canvas.blit(self.fig.bbox)
            except Exception:
                self.canvas.draw_idle()
        else:
            self.canvas.draw_idle()

    def update_eraser_button_text(self):
        if not hasattr(self, 'btn_eraser') or not self.btn_eraser:
            return
        app_params = getattr(self.app, 'last_params', {}) if self.app else {}
        if self.current_item is not None:
            mode = self.current_item.get('analysis_mode', app_params.get('analysis_mode', 'f0'))
        else:
            mode = app_params.get('analysis_mode', 'f0')
        if mode == 'formant':
            self.btn_eraser.configure(text="剔除点")
        else:
            self.btn_eraser.configure(text="橡皮擦")

