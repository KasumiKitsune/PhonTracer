# -*- coding: utf-8 -*-
"""
Thin facade for modules.speaker_manager.
All implementations live in modules.storage.speaker_state.
"""

from .storage.speaker_state import (
    SpeakerState,
    SpeakerManager,
)

__all__ = [
    "SpeakerState",
    "SpeakerManager",
]
