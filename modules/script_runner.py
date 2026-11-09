# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本运行器（薄门面重导出）
"""

from .scripting.meta_parser import strip_markdown_code_fence
from .scripting.sandbox_runner import (
    ALLOWED_BUILTINS,
    ALLOWED_IMPORT_ROOTS,
    GAUSSIAN_KDE_ERROR_MSG,
    SAFE_BUILTINS,
    _ALLOWED_IMPORT_ROOTS,
    _AST_FORBIDDEN_FUNCS,
    _AST_FORBIDDEN_NAMES,
    _AST_FORBIDDEN_OUTPUT_CALLS,
    _AST_FORBIDDEN_PANDAS_IO,
    _AST_FORBIDDEN_PRIVATE_ATTRS,
    _FORBIDDEN_IMPORT_ROOTS,
    _PARAM_TYPE_ALIASES,
    _SAFE_BUILTINS,
    _SafeOSNamespace,
    _SafeOSPathNamespace,
    _coerce_single_param_value,
    _safe_import,
    check_script_safety,
    coerce_script_params,
    extract_script_params_schema,
    get_default_script_params,
    run_custom_script,
    validate_ast,
)

__all__ = [
    "ALLOWED_BUILTINS",
    "ALLOWED_IMPORT_ROOTS",
    "GAUSSIAN_KDE_ERROR_MSG",
    "SAFE_BUILTINS",
    "_ALLOWED_IMPORT_ROOTS",
    "_AST_FORBIDDEN_FUNCS",
    "_AST_FORBIDDEN_NAMES",
    "_AST_FORBIDDEN_OUTPUT_CALLS",
    "_AST_FORBIDDEN_PANDAS_IO",
    "_AST_FORBIDDEN_PRIVATE_ATTRS",
    "_FORBIDDEN_IMPORT_ROOTS",
    "_PARAM_TYPE_ALIASES",
    "_SAFE_BUILTINS",
    "_SafeOSNamespace",
    "_SafeOSPathNamespace",
    "_coerce_single_param_value",
    "_safe_import",
    "check_script_safety",
    "coerce_script_params",
    "extract_script_params_schema",
    "get_default_script_params",
    "run_custom_script",
    "strip_markdown_code_fence",
    "validate_ast",
]
