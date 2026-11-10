# -*- coding: utf-8 -*-
"""
modules.chart_engine.domain_port
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
无头声学图表数据端口与数据源适配器协议层。
定义 ProjectDataPort 协议、HeadlessProjectDataSource 与 GuiTreeDataSourceAdapter，
使得图表引擎完全解耦于具体的 GUI 控件与树形组件（ProjectTreePanel）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Protocol, runtime_checkable
import numpy as np

from modules.data_utils import split_into_syllables


@runtime_checkable
class ProjectDataPort(Protocol):
    """
    声学图表引擎数据源协议接口。
    凡是向 AcousticChartExporter 提供工程条目、基频/共振峰数据的对象（树组件、CLI适配器或纯数据源）
    均需满足此接口契约。
    """
    app_state_params: Dict[str, Any]
    items: Dict[str, Any]

    def _get_items_by_group_for_dict(self, items_dict: Dict[str, Any]) -> List[Tuple[str, List[str]]]: ...
    def _ensure_item_loaded(self, item: Dict[str, Any]) -> None: ...
    def _extract_syl_data(self, item: Dict[str, Any], num_points: int) -> Tuple[float, List[Any]]: ...
    def _get_pitch_arrays_for_item(self, item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]: ...
    def _get_syllables_and_bounds(self, item: Dict[str, Any]) -> Tuple[List[str], List[List[float]]]: ...
    def _extract_kde_contour(
        self,
        p_xs: Any,
        p_freqs: Any,
        c_s: float,
        c_e: float,
        n_dense: int,
    ) -> Optional[np.ndarray]: ...


class HeadlessProjectDataSource:
    """
    纯无头工程数据源适配器。
    实现 ProjectDataPort 协议，收敛自 cli.py:AcousticChartCLIAdapter，
    用于无 GUI 环境下支撑声学图表渲染与批量导出。
    """
    def __init__(self, cli_instance=None, items: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None):
        self.cli = cli_instance
        if cli_instance is not None:
            self.app_state_params = getattr(cli_instance, "params", {})
            self.items = getattr(cli_instance, "items", {})
        else:
            self.app_state_params = params if params is not None else {}
            self.items = items if items is not None else {}

    def _get_items_by_group_for_dict(self, items_dict: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
        groups: Dict[str, List[str]] = {}
        for k, v in items_dict.items():
            if isinstance(v, dict) and v.get("is_excluded", False):
                continue
            g = v.get("group", "导入内容") if isinstance(v, dict) else "导入内容"
            if g not in groups:
                groups[g] = []
            groups[g].append(k)
        return [(g, groups[g]) for g in groups]

    def _ensure_item_loaded(self, item: Dict[str, Any]) -> None:
        if self.cli is not None and hasattr(self.cli, "_ensure_item_loaded"):
            self.cli._ensure_item_loaded(item)

    def _extract_syl_data(self, item: Dict[str, Any], num_points: int) -> Tuple[float, List[Any]]:
        if self.cli is not None and hasattr(self.cli, "_extract_syl_data"):
            return self.cli._extract_syl_data(item, num_points)

        if item.get("start") is None or (not item.get("snd") and not item.get("pitch_data") and not item.get("pitch")):
            return 0.0, []

        pitch_data = item.get("pitch_data")
        if pitch_data:
            p_xs = pitch_data.get("xs")
            p_freqs = pitch_data.get("freqs")
        else:
            pitch = item.get("pitch")
            if not pitch:
                return 0.0, []
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array["frequency"]

        t_s, t_e = item.get("start"), item.get("end")
        if t_s is None or t_e is None or t_e <= t_s:
            return 0.0, []

        label = item.get("label", "")
        inner_splits = item.get("inner_splits", [])
        syls = split_into_syllables(label)
        chars_bounds = item.get("chars_bounds", [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_s, t_e]
            bounds = [[splits[i], splits[i + 1]] for i in range(len(splits) - 1)]

        syl_data = []
        for c_s, c_e in bounds:
            if c_e <= c_s:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0] * num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0] * num_points))

        total_dur = sum(d for d, _ in syl_data)
        return total_dur, syl_data

    def _get_pitch_arrays_for_item(self, item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if item.get("pitch_data"):
            p_xs = item["pitch_data"].get("xs")
            p_freqs = item["pitch_data"].get("freqs")
            if p_xs is None or p_freqs is None:
                return None, None
            return np.asarray(p_xs), np.asarray(p_freqs)
        if item.get("pitch"):
            pitch = item["pitch"]
            try:
                p_xs = np.asarray(pitch.xs())
                p_freqs = np.asarray(pitch.selected_array["frequency"])
            except (TypeError, KeyError, AttributeError):
                return None, None
            if p_xs.ndim != 1 or p_freqs.ndim != 1 or len(p_xs) != len(p_freqs):
                return None, None
            return p_xs, p_freqs
        return None, None

    def _get_syllables_and_bounds(self, item: Dict[str, Any]) -> Tuple[List[str], List[List[float]]]:
        t_s, t_e = item.get("start"), item.get("end")
        if t_s is None or t_e is None or t_e <= t_s:
            return [], []

        label = item.get("label", "")
        syls = split_into_syllables(label)
        if not syls and label:
            syls = [label]

        chars_bounds = item.get("chars_bounds", [])
        if chars_bounds and len(chars_bounds) == len(syls):
            return syls, [[float(s), float(e)] for s, e in chars_bounds]

        inner_splits = item.get("inner_splits", [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
            if not syls:
                syls = [label]

        return syls, [[splits[i], splits[i + 1]] for i in range(len(splits) - 1)]

    def _extract_kde_contour(
        self,
        p_xs: Any,
        p_freqs: Any,
        c_s: float,
        c_e: float,
        n_dense: int,
    ) -> Optional[np.ndarray]:
        p_xs = np.asarray(p_xs) if p_xs is not None else np.array([])
        p_freqs = np.asarray(p_freqs) if p_freqs is not None else np.array([])
        if c_e <= c_s or len(p_xs) == 0 or len(p_freqs) == 0:
            return None

        valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs) & (p_freqs > 0))[0]
        if len(valid_idx) < 3:
            return None

        seg_xs = np.asarray(p_xs[valid_idx], dtype=float)
        seg_ys = np.asarray(p_freqs[valid_idx], dtype=float)
        order = np.argsort(seg_xs)
        seg_xs = seg_xs[order]
        seg_ys = seg_ys[order]

        v_s, v_e = seg_xs[0], seg_xs[-1]
        if v_e <= v_s:
            return None

        gap_threshold = 0.025
        smoothed = seg_ys.copy()
        try:
            from scipy.signal import savgol_filter
            breaks = np.where(np.diff(seg_xs) > gap_threshold)[0] + 1
            run_ranges = np.split(np.arange(len(seg_xs)), breaks)
            for run in run_ranges:
                run_len = len(run)
                if run_len < 5:
                    continue
                win = min(9, run_len if run_len % 2 == 1 else run_len - 1)
                if win >= 5:
                    smoothed[run] = savgol_filter(seg_ys[run], win, 2)
        except Exception:
            pass

        dense_times = np.linspace(v_s, v_e, n_dense)
        y_dense = np.interp(dense_times, seg_xs, smoothed)

        nearest_right = np.searchsorted(seg_xs, dense_times, side="left")
        nearest_left = np.clip(nearest_right - 1, 0, len(seg_xs) - 1)
        nearest_right = np.clip(nearest_right, 0, len(seg_xs) - 1)
        nearest_dist = np.minimum(np.abs(dense_times - seg_xs[nearest_left]), np.abs(dense_times - seg_xs[nearest_right]))
        y_dense[nearest_dist > gap_threshold] = np.nan
        return y_dense


class GuiTreeDataSourceAdapter:
    """
    GUI 树控件数据适配器。
    封装对 ProjectTreePanel 的数据访问，作为 ProjectDataPort 的具体实现，
    让 AcousticChartExporter 统一通过数据端口与数据交互。
    """
    def __init__(self, tree_panel: Any):
        self._tree = tree_panel

    @property
    def app_state_params(self) -> Dict[str, Any]:
        return getattr(self._tree, "app_state_params", {})

    @app_state_params.setter
    def app_state_params(self, val: Dict[str, Any]) -> None:
        self._tree.app_state_params = val

    @property
    def items(self) -> Dict[str, Any]:
        return getattr(self._tree, "items", {})

    @items.setter
    def items(self, val: Dict[str, Any]) -> None:
        self._tree.items = val

    def _get_items_by_group_for_dict(self, items_dict: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
        if hasattr(self._tree, "_get_items_by_group_for_dict"):
            return self._tree._get_items_by_group_for_dict(items_dict)
        groups: Dict[str, List[str]] = {}
        for k, v in items_dict.items():
            if isinstance(v, dict) and v.get("is_excluded", False):
                continue
            g = v.get("group", "导入内容") if isinstance(v, dict) else "导入内容"
            if g not in groups:
                groups[g] = []
            groups[g].append(k)
        return [(g, groups[g]) for g in groups]

    def _ensure_item_loaded(self, item: Dict[str, Any]) -> None:
        if hasattr(self._tree, "_ensure_item_loaded"):
            self._tree._ensure_item_loaded(item)

    def _extract_syl_data(self, item: Dict[str, Any], num_points: int) -> Tuple[float, List[Any]]:
        if hasattr(self._tree, "_extract_syl_data"):
            return self._tree._extract_syl_data(item, num_points)
        return 0.0, []

    def _get_pitch_arrays_for_item(self, item: Dict[str, Any]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if hasattr(self._tree, "_get_pitch_arrays_for_item"):
            return self._tree._get_pitch_arrays_for_item(item)
        return None, None

    def _get_syllables_and_bounds(self, item: Dict[str, Any]) -> Tuple[List[str], List[List[float]]]:
        if hasattr(self._tree, "_get_syllables_and_bounds"):
            return self._tree._get_syllables_and_bounds(item)
        return [], []

    def _extract_kde_contour(
        self,
        p_xs: Any,
        p_freqs: Any,
        c_s: float,
        c_e: float,
        n_dense: int,
    ) -> Optional[np.ndarray]:
        if hasattr(self._tree, "_extract_kde_contour"):
            return self._tree._extract_kde_contour(p_xs, p_freqs, c_s, c_e, n_dense)
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tree, name)


# 向下兼容别名
AcousticChartCLIAdapter = HeadlessProjectDataSource
