# -*- coding: utf-8 -*-
"""
轻量测试回退桩 (pytest 缺失时的简易替代实现)。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from typing import Any, Optional

import numpy as np


def _install_pytest_fallback_if_missing() -> None:
    if "pytest" in sys.modules or importlib.util.find_spec("pytest") is not None:
        return

    pytest_mod = types.ModuleType("pytest")

    class Skipped(Exception):
        pass

    class _Approx:
        def __init__(self, expected: Any, rel: Optional[float] = None, abs: Optional[float] = None):
            self.expected = expected
            self.rel = 1e-6 if rel is None else float(rel)
            self.abs = 1e-12 if abs is None else float(abs)

        def __eq__(self, actual: Any) -> bool:
            try:
                a = np.asarray(actual, dtype=np.float64)
                e = np.asarray(self.expected, dtype=np.float64)
                return bool(np.allclose(a, e, rtol=self.rel, atol=self.abs, equal_nan=True))
            except Exception:
                return False

        def __repr__(self) -> str:
            return f"approx({self.expected!r}, rel={self.rel}, abs={self.abs})"

    class _RaisesContext:
        def __init__(self, expected_exc: Any, match: Optional[str] = None):
            self.expected_exc = expected_exc
            self.match = match
            self.value = None

        def __enter__(self) -> "_RaisesContext":
            return self

        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
            if exc_type is None:
                raise AssertionError(f"DID NOT RAISE {self.expected_exc}")
            if not issubclass(exc_type, self.expected_exc):
                return False
            self.value = exc_val
            if self.match is not None and not re.search(self.match, str(exc_val)):
                raise AssertionError(f"Regex pattern {self.match!r} did not match {str(exc_val)!r}")
            return True

    class _Mark:
        @staticmethod
        def skipif(condition: bool, reason: str = ""):
            def decorator(fn):
                fn._pytest_skipif = (bool(condition), reason)
                return fn
            return decorator

        @staticmethod
        def parametrize(*args: Any, **kwargs: Any):
            def decorator(fn):
                return fn
            return decorator

    def importorskip(modname: str, minversion: Optional[str] = None, reason: Optional[str] = None) -> Any:
        try:
            return __import__(modname)
        except ImportError as exc:
            raise Skipped(reason or f"could not import {modname!r}: {exc}")

    def skip(reason: str = "") -> None:
        raise Skipped(reason)

    def fail(reason: str = "") -> None:
        raise AssertionError(reason)

    pytest_mod.approx = lambda expected, rel=None, abs=None: _Approx(expected, rel=rel, abs=abs)
    pytest_mod.raises = lambda expected_exc, match=None: _RaisesContext(expected_exc, match=match)
    pytest_mod.importorskip = importorskip
    pytest_mod.skip = skip
    pytest_mod.fail = fail
    pytest_mod.mark = _Mark()
    pytest_mod.Skipped = Skipped
    sys.modules["pytest"] = pytest_mod

__all__ = ["_install_pytest_fallback_if_missing"]
