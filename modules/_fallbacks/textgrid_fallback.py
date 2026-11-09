# -*- coding: utf-8 -*-
"""
无原生 textgrid 依赖环境下的轻量 TextGrid 解析与写入回退实现。
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import sys
import types
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union


def _install_textgrid_fallback_if_missing() -> None:
    if "textgrid" in sys.modules or importlib.util.find_spec("textgrid") is not None:
        return

    tg_mod = types.ModuleType("textgrid")

    class Interval:
        def __init__(self, minTime: float, maxTime: float, mark: str = ""):
            self.minTime = float(minTime)
            self.maxTime = float(maxTime)
            self.mark = "" if mark is None else str(mark)

        def __repr__(self) -> str:
            return f"Interval({self.minTime}, {self.maxTime}, {self.mark!r})"

    class Point:
        def __init__(self, time: float, mark: str = ""):
            self.time = float(time)
            self.mark = "" if mark is None else str(mark)

    class IntervalTier:
        def __init__(self, name: str = "", minTime: float = 0.0, maxTime: float = 1.0):
            self.name = str(name or "")
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.intervals: List[Interval] = []

        def add(self, minTime: float, maxTime: float, mark: str = "") -> None:
            iv = Interval(minTime, maxTime, mark)
            self.intervals.append(iv)
            self.intervals.sort(key=lambda x: (x.minTime, x.maxTime))
            if iv.maxTime > self.maxTime:
                self.maxTime = iv.maxTime

        def addInterval(self, interval: Interval) -> None:
            self.add(interval.minTime, interval.maxTime, interval.mark)

        def __iter__(self):
            return iter(self.intervals)

        def __len__(self) -> int:
            return len(self.intervals)

        def __getitem__(self, idx: int) -> Interval:
            return self.intervals[idx]

    class PointTier:
        def __init__(self, name: str = "", minTime: float = 0.0, maxTime: float = 1.0):
            self.name = str(name or "")
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.points: List[Point] = []

        def add(self, time: float, mark: str = "") -> None:
            self.points.append(Point(time, mark))
            self.points.sort(key=lambda p: p.time)

        def __iter__(self):
            return iter(self.points)

        def __len__(self) -> int:
            return len(self.points)

        def __getitem__(self, idx: int) -> Point:
            return self.points[idx]

    class TextGrid:
        def __init__(self, name: Optional[str] = None, minTime: float = 0.0, maxTime: float = 1.0):
            self.name = name
            self.minTime = float(minTime or 0.0)
            self.maxTime = float(maxTime or 0.0)
            self.tiers: List[Any] = []

        def append(self, tier: Any) -> None:
            self.tiers.append(tier)
            t_max = getattr(tier, "maxTime", None)
            if t_max is not None and float(t_max) > self.maxTime:
                self.maxTime = float(t_max)

        def __iter__(self):
            return iter(self.tiers)

        def __len__(self) -> int:
            return len(self.tiers)

        def __getitem__(self, idx: int) -> Any:
            return self.tiers[idx]

        def write(self, f: Union[str, os.PathLike, io.TextIOBase]) -> None:
            lines = [
                'File type = "ooTextFile"',
                'Object class = "TextGrid"',
                "",
                f"xmin = {self.minTime}",
                f"xmax = {self.maxTime}",
                "tiers? <exists> ",
                f"size = {len(self.tiers)} ",
                "item []: ",
            ]
            for idx, tier in enumerate(self.tiers, start=1):
                lines.append(f"    item [{idx}]:")
                if isinstance(tier, IntervalTier):
                    lines.append('        class = "IntervalTier" ')
                    t_name = str(tier.name).replace('"', '""')
                    lines.append(f'        name = "{t_name}" ')
                    lines.append(f"        xmin = {tier.minTime} ")
                    lines.append(f"        xmax = {tier.maxTime} ")
                    lines.append(f"        intervals: size = {len(tier.intervals)} ")
                    for j, iv in enumerate(tier.intervals, start=1):
                        mark_esc = str(iv.mark).replace('"', '""')
                        lines.append(f"        intervals [{j}]:")
                        lines.append(f"            xmin = {iv.minTime} ")
                        lines.append(f"            xmax = {iv.maxTime} ")
                        lines.append(f'            text = "{mark_esc}" ')
                else:
                    lines.append('        class = "TextTier" ')
                    t_name = str(tier.name).replace('"', '""')
                    lines.append(f'        name = "{t_name}" ')
                    lines.append(f"        xmin = {tier.minTime} ")
                    lines.append(f"        xmax = {tier.maxTime} ")
                    pts = getattr(tier, "points", [])
                    lines.append(f"        points: size = {len(pts)} ")
                    for j, pt in enumerate(pts, start=1):
                        mark_esc = str(pt.mark).replace('"', '""')
                        lines.append(f"        points [{j}]:")
                        lines.append(f"            number = {pt.time} ")
                        lines.append(f'            mark = "{mark_esc}" ')
            content = "\n".join(lines) + "\n"
            if hasattr(f, "write"):
                f.write(content)
            else:
                with open(os.fspath(f), "w", encoding="utf-8") as fp:
                    fp.write(content)

        @classmethod
        def fromFile(cls, f: Union[str, os.PathLike, io.TextIOBase]) -> "TextGrid":
            if hasattr(f, "read"):
                raw_text = f.read()
            else:
                raw_bytes = Path(os.fspath(f)).read_bytes()
                for enc in ("utf-8-sig", "utf-16", "utf-8", "gb18030", "latin-1"):
                    try:
                        raw_text = raw_bytes.decode(enc)
                        break
                    except Exception:
                        continue
            return cls._parse_textgrid_text(raw_text)

        @classmethod
        def _parse_textgrid_text(cls, text: str) -> "TextGrid":
            # 提取所有字符串字面量或数值 token，兼容长格式与短格式 Praat TextGrid
            token_re = re.compile(r'"((?:[^"]|"")*)"|([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)')
            lines = text.splitlines()
            cleaned_lines = []
            for line in lines:
                s = line.strip()
                if not s or s.startswith("File type") or s.startswith("Object class"):
                    continue
                if "tiers?" in s or "item []:" in s:
                    continue
                cleaned_lines.append(s)

            body = "\n".join(cleaned_lines)
            # 移除 item [n]: / intervals [n]: / points [n]: 中的索引用括号以免干扰数字提取
            body = re.sub(r"\b(?:item|intervals|points)\s*\[\s*\d+\s*\]\s*:", "", body)

            tokens: List[Tuple[str, Any]] = []
            for m in token_re.finditer(body):
                if m.group(1) is not None:
                    tokens.append(("str", m.group(1).replace('""', '"')))
                elif m.group(2) is not None:
                    tokens.append(("num", float(m.group(2))))

            if len(tokens) < 3:
                return cls()

            pos = 0

            def next_num(default: float = 0.0) -> float:
                nonlocal pos
                while pos < len(tokens):
                    kind, val = tokens[pos]
                    pos += 1
                    if kind == "num":
                        return float(val)
                return default

            def next_str(default: str = "") -> str:
                nonlocal pos
                while pos < len(tokens):
                    kind, val = tokens[pos]
                    pos += 1
                    if kind == "str":
                        return str(val)
                return default

            tg_min = next_num(0.0)
            tg_max = next_num(1.0)
            n_tiers = int(round(next_num(0.0)))
            tg = cls(minTime=tg_min, maxTime=tg_max)

            for _ in range(max(0, n_tiers)):
                tier_class = next_str("IntervalTier")
                tier_name = next_str("")
                t_min = next_num(tg_min)
                t_max = next_num(tg_max)
                n_items = int(round(next_num(0.0)))
                if "interval" in tier_class.lower():
                    tier = IntervalTier(name=tier_name, minTime=t_min, maxTime=t_max)
                    for _j in range(max(0, n_items)):
                        iv_min = next_num(0.0)
                        iv_max = next_num(0.0)
                        iv_txt = next_str("")
                        tier.intervals.append(Interval(iv_min, iv_max, iv_txt))
                    tg.append(tier)
                else:
                    tier = PointTier(name=tier_name, minTime=t_min, maxTime=t_max)
                    for _j in range(max(0, n_items)):
                        pt_time = next_num(0.0)
                        pt_txt = next_str("")
                        tier.points.append(Point(pt_time, pt_txt))
                    tg.append(tier)

            return tg

    tg_mod.TextGrid = TextGrid
    tg_mod.IntervalTier = IntervalTier
    tg_mod.PointTier = PointTier
    tg_mod.Interval = Interval
    tg_mod.Point = Point
    sys.modules["textgrid"] = tg_mod

__all__ = ["_install_textgrid_fallback_if_missing"]
