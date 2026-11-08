# Modules package initialization
import sys as _sys

try:
    import parselmouth as _parselmouth  # noqa: F401
except ImportError:
    from . import _parselmouth_fallback as _parselmouth
    _sys.modules.setdefault("parselmouth", _parselmouth)
    _sys.modules.setdefault("parselmouth.praat", _parselmouth.praat)
