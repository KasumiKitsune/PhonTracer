# -*- coding: utf-8 -*-
"""
Thin facade for modules.formant_optimizer.
All implementations live in modules.dsp.formant_optimizer.
"""

from .dsp.formant_optimizer import (
    clipped_score,
    adjacent_jump_rate,
    fragmentation_penalty,
    score_config,
    FormantOptimizer,
)

__all__ = [
    "clipped_score",
    "adjacent_jump_rate",
    "fragmentation_penalty",
    "score_config",
    "FormantOptimizer",
]
