# -*- coding: utf-8 -*-
"""
Thin facade for modules.anomaly_detection.
All implementations live in modules.dsp.anomaly_detector.
"""

from .dsp.anomaly_detector import detect_pitch_anomaly_points

__all__ = [
    "detect_pitch_anomaly_points",
]
