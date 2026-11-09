# -*- coding: utf-8 -*-
"""
PhonTracer语音学标注与声学分析系统 V1.0 自定义脚本运行器
"""

import ast
import builtins
import os
import re
import threading
import traceback
import matplotlib
matplotlib.use("Agg", force=True)

ALLOWED_IMPORT_ROOTS = {
    "collections",
    "itertools",
    "math",
    "matplotlib",
    "numpy",
    "pandas",
    "parselmouth",
    "re",
    "scipy",
    "statistics",
    "time",
    "warnings",
}

ALLOWED_BUILTINS = {
    "ArithmeticError": ArithmeticError,
    "AssertionError": AssertionError,
    "AttributeError": AttributeError,
    "Exception": Exception,
    "False": False,
    "IndexError": IndexError,
    "KeyError": KeyError,
    "LookupError": LookupError,
    "None": None,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "True": True,
    "TypeError": TypeError,
    "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "callable": callable,
    "chr": chr,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "object": object,
    "ord": ord,
    "pow": pow,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}

# 明确移除 getattr / hasattr 以免通过反射逃逸沙箱
ALLOWED_BUILTINS.pop("getattr", None)
ALLOWED_BUILTINS.pop("hasattr", None)
SAFE_BUILTINS = dict(ALLOWED_BUILTINS)


class _SafeOSPathNamespace:
    """受控的只读/纯路径操作包装器，绝不暴露 underlying os 或 sys 模块"""
    join = staticmethod(os.path.join)
    basename = staticmethod(os.path.basename)
    dirname = staticmethod(os.path.dirname)
    splitext = staticmethod(os.path.splitext)
    split = staticmethod(os.path.split)
    normpath = staticmethod(os.path.normpath)
    normcase = staticmethod(os.path.normcase)
    abspath = staticmethod(os.path.abspath)
    expanduser = staticmethod(os.path.expanduser)
    commonpath = staticmethod(os.path.commonpath)
    commonprefix = staticmethod(os.path.commonprefix)
    exists = staticmethod(os.path.exists)
    isfile = staticmethod(os.path.isfile)
    isdir = staticmethod(os.path.isdir)
    isabs = staticmethod(os.path.isabs)
    relpath = staticmethod(os.path.relpath)
    sep = os.path.sep
    altsep = os.path.altsep
    extsep = os.path.extsep
    pathsep = os.path.pathsep

    def __getattribute__(self, name):
        if name.startswith("_") or name in ("os", "sys"):
            raise AttributeError(f"安全限制: 禁止访问 '{name}'")
        return super().__getattribute__(name)


class _SafeOSNamespace:
    """受控 os 命名空间，仅提供受限制的只读 path 操作"""
    def __init__(self, path=None):
        self.path = _SafeOSPathNamespace()

    def __getattribute__(self, name):
        if name == "path":
            return object.__getattribute__(self, "path")
        raise AttributeError(f"安全限制: 禁止通过 os 访问 '{name}'，仅允许受控 os.path")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0:
        raise ImportError("安全检查拦截：禁止使用相对导入")
    root_name = str(name).split(".")[0]
    if root_name not in ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"安全检查拦截：禁止在第一版脚本中导入库 '{name}'")
    return builtins.__import__(name, globals, locals, fromlist, level)


def strip_markdown_code_fence(code: str) -> str:
    """
    自动剥离首尾的 ```python ... ``` 或 ``` ... ``` Markdown 代码围栏。
    若包含自然语言开场白，且代码被包裹在 ```python ... ``` 中，亦自动提取代码块。
    """
    if not isinstance(code, str):
        return "" if code is None else str(code)
    text = code.strip()
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip("\r\n")
    if lines and lines[0].strip().startswith("```"):
        body = lines[1:]
        if body and body[-1].strip() == "```":
            body = body[:-1]
        return "\n".join(body).strip("\r\n")
    if lines and lines[-1].strip() == "```":
        return "\n".join(lines[:-1]).strip("\r\n")
    m = re.search(r"```(?:python)?\s*\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if m:
        extracted = m.group(1).strip("\r\n")
        try:
            compile(extracted, "<extracted_check>", "exec")
            return extracted
        except SyntaxError:
            pass
    return code


_PARAM_TYPE_ALIASES = {
    "choice": "choice",
    "select": "choice",
    "option": "choice",
    "enum": "choice",
    "int": "int",
    "integer": "int",
    "float": "float",
    "number": "float",
    "bool": "bool",
    "boolean": "bool",
    "str": "str",
    "string": "str",
    "text": "str",
}


def _coerce_single_param_value(raw_val, schema_item):
    p_type = schema_item.get("type", "str")
    default = schema_item.get("default")
    min_v = schema_item.get("min")
    max_v = schema_item.get("max")
    choices = schema_item.get("choices") or []

    if p_type == "choice":
        if not choices:
            return raw_val if raw_val is not None else default
        if raw_val in choices:
            return raw_val
        raw_str = str(raw_val).strip()
        for c in choices:
            if str(c).strip() == raw_str:
                return c
        return default if default in choices else choices[0]

    if p_type == "bool":
        if isinstance(raw_val, bool):
            return raw_val
        if isinstance(raw_val, (int, float)):
            return bool(raw_val)
        if isinstance(raw_val, str):
            s = raw_val.strip().lower()
            if s in ("true", "1", "yes", "y", "on", "是"):
                return True
            if s in ("false", "0", "no", "n", "off", "否", ""):
                return False
        return bool(default)

    if p_type == "int":
        try:
            val = int(round(float(raw_val)))
        except (TypeError, ValueError):
            val = int(default) if default is not None else 0
        if min_v is not None:
            val = max(int(min_v), val)
        if max_v is not None:
            val = min(int(max_v), val)
        return val

    if p_type == "float":
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            val = float(default) if default is not None else 0.0
        if min_v is not None:
            val = max(float(min_v), val)
        if max_v is not None:
            val = min(float(max_v), val)
        return val

    if raw_val is None:
        return str(default) if default is not None else ""
    return str(raw_val)


def extract_script_params_schema(code: str) -> list:
    """
    通过 AST 安全解析脚本顶部的 PARAMS = [...] 声明式参数列表。
    支持 type 为 "choice", "int", "float", "bool", "str"，
    提取并规范化 key, label, type, default, choices, min, max。
    """
    if not isinstance(code, str) or not code.strip():
        return []
    clean_code = strip_markdown_code_fence(code)
    try:
        tree = ast.parse(clean_code)
    except SyntaxError:
        return []

    raw_params_list = None
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PARAMS":
                    value_node = node.value
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "PARAMS":
                value_node = node.value
        if value_node is not None:
            try:
                evaluated = ast.literal_eval(value_node)
                if isinstance(evaluated, (list, tuple)):
                    raw_params_list = list(evaluated)
            except Exception:
                pass

    if not raw_params_list:
        return []

    schema = []
    seen_keys = set()
    for item in raw_params_list:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        label = str(item.get("label") or key).strip() or key
        raw_choices = item.get("choices")
        choices = list(raw_choices) if isinstance(raw_choices, (list, tuple)) and len(raw_choices) > 0 else None

        raw_type = str(item.get("type") or "").strip().lower()
        p_type = _PARAM_TYPE_ALIASES.get(raw_type)
        if not p_type:
            if choices:
                p_type = "choice"
            else:
                def_v = item.get("default")
                if isinstance(def_v, bool):
                    p_type = "bool"
                elif isinstance(def_v, int):
                    p_type = "int"
                elif isinstance(def_v, float):
                    p_type = "float"
                else:
                    p_type = "str"

        min_v = item.get("min")
        max_v = item.get("max")
        if p_type == "int":
            try:
                min_v = int(min_v) if min_v is not None else None
            except (TypeError, ValueError):
                min_v = None
            try:
                max_v = int(max_v) if max_v is not None else None
            except (TypeError, ValueError):
                max_v = None
        elif p_type == "float":
            try:
                min_v = float(min_v) if min_v is not None else None
            except (TypeError, ValueError):
                min_v = None
            try:
                max_v = float(max_v) if max_v is not None else None
            except (TypeError, ValueError):
                max_v = None
        else:
            min_v = None
            max_v = None

        raw_default = item.get("default")
        if p_type == "choice":
            if not choices:
                choices = [raw_default] if raw_default is not None else [""]
            default_val = raw_default if raw_default in choices else choices[0]
        elif p_type == "bool":
            default_val = bool(raw_default) if raw_default is not None else False
        elif p_type == "int":
            base_def = min_v if min_v is not None else 0
            try:
                default_val = int(round(float(raw_default))) if raw_default is not None else base_def
            except (TypeError, ValueError):
                default_val = base_def
            if min_v is not None:
                default_val = max(min_v, default_val)
            if max_v is not None:
                default_val = min(max_v, default_val)
        elif p_type == "float":
            base_def = min_v if min_v is not None else 0.0
            try:
                default_val = float(raw_default) if raw_default is not None else base_def
            except (TypeError, ValueError):
                default_val = base_def
            if min_v is not None:
                default_val = max(min_v, default_val)
            if max_v is not None:
                default_val = min(max_v, default_val)
        else:
            default_val = str(raw_default) if raw_default is not None else ""

        schema.append({
            "key": key,
            "label": label,
            "type": p_type,
            "default": default_val,
            "choices": choices,
            "min": min_v,
            "max": max_v,
        })

    return schema


def get_default_script_params(code: str) -> dict:
    """提取脚本顶部的 PARAMS 默认参数字典 {key: default}。"""
    schema = extract_script_params_schema(code)
    return {item["key"]: item["default"] for item in schema}


def coerce_script_params(code_or_schema, params=None) -> dict:
    """将用户传入的 params 与脚本 PARAMS 默认值合并并按声明类型安全转换。"""
    if isinstance(code_or_schema, str):
        schema = extract_script_params_schema(code_or_schema)
    elif isinstance(code_or_schema, list):
        schema = code_or_schema
    else:
        schema = []

    schema_map = {item["key"]: item for item in schema if isinstance(item, dict) and "key" in item}
    resolved = {k: item.get("default") for k, item in schema_map.items()}
    if isinstance(params, dict):
        for k, v in params.items():
            if k in schema_map:
                resolved[k] = _coerce_single_param_value(v, schema_map[k])
            else:
                resolved[k] = v
    return resolved


GAUSSIAN_KDE_ERROR_MSG = (
    "安全检查拦截：禁止使用 scipy.stats.gaussian_kde（点数较多时易长时间占用后台线程）；"
    "请改用 np.histogram + scipy.ndimage.gaussian_filter，或使用 ax.hist2d / ax.hexbin。"
)


def check_script_safety(code):
    """
    通过 AST 抽象语法树检查脚本是否包含危险的 import 或函数调用。
    """
    code = strip_markdown_code_fence(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"脚本语法错误：{e}")

    forbidden_funcs = {
        "__import__",
        "compile",
        "eval",
        "exec",
        "getattr",
        "hasattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "delattr",
        "vars",
    }
    forbidden_output_calls = {
        "dump",
        "imsave",
        "save",
        "savefig",
        "savetxt",
        "savez",
        "savez_compressed",
        "to_csv",
        "to_excel",
        "to_feather",
        "to_file",
        "to_hdf",
        "to_parquet",
        "to_pickle",
        "to_sql",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
        "writestr",
    }
    forbidden_pandas_io = {
        "read_csv",
        "read_excel",
        "read_feather",
        "read_fwf",
        "read_hdf",
        "read_html",
        "read_json",
        "read_orc",
        "read_parquet",
        "read_pickle",
        "read_sas",
        "read_spss",
        "read_sql",
        "read_sql_query",
        "read_sql_table",
        "read_stata",
        "read_table",
        "read_xml",
    }
    forbidden_private_attrs = {
        "_cancel_event",
        "_teproj_path",
        "_logs",
        "_long_sound_cache",
        "_project_json_cache",
        "_read_audio_resource",
        "_load_sound_from_resource",
        "_project_item_for_snapshot",
        "_frame",
        "_tb",
        "_getframe",
    }

    def call_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            is_dunder = node.attr.startswith("__") and node.attr.endswith("__")
            is_ctx_priv = isinstance(node.value, ast.Name) and node.value.id in ("ctx", "os") and node.attr.startswith("_")
            if is_dunder or is_ctx_priv or node.attr in forbidden_private_attrs:
                raise ValueError(f"安全限制: 禁止访问私有或双下划线属性 '{node.attr}'")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split('.')[0]
                if name not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"安全检查拦截：禁止在第一版脚本中导入库 '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split('.')[0]
                if name not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"安全检查拦截：禁止在第一版脚本中导入库 '{node.module}'")
                if node.module.startswith("parselmouth.praat"):
                    raise ValueError("安全检查拦截：第一版脚本不允许导入 parselmouth.praat；请使用 ctx.load_item_sound 和 Sound.to_spectrogram 等受控接口。")
        elif isinstance(node, ast.Call):
            name = call_name(node.func)
            short_name = name.split(".")[-1] if name else ""
            if short_name in forbidden_funcs:
                raise ValueError(f"安全检查拦截：禁止在第一版脚本中调用系统函数 '{short_name}'")
            if short_name in forbidden_pandas_io:
                raise ValueError(f"安全检查拦截：禁止在脚本中调用磁盘读取函数 '{short_name}'；请使用 ctx.dataset.to_point_table() 或 to_syllable_table()。")
            if short_name in forbidden_output_calls:
                # 放行对内存缓冲区 (如 bio / buf / buffer) 的 .write / .save
                if short_name in ("write", "save") and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id.lower() in {"bio", "sio", "buf", "buffer", "bytes_io", "string_io", "mem_buf"}:
                        continue
                raise ValueError(f"安全检查拦截：禁止在脚本中直接调用输出/写入函数 '{short_name}'；请通过 ctx.figure(...) 或 ctx.table(...) 返回结果。")

validate_ast = check_script_safety


def run_custom_script(code, dataset_items, timeout=30, cancel_event=None, teproj_path=None, params=None):
    """
    运行自定义 Python 脚本。
    :param code: 脚本源码
    :param dataset_items: 只读数据集条目列表
    :param timeout: 超时时间（秒）
    :param cancel_event: 线程取消事件对象（可选）
    :param teproj_path: 当前工程文件路径（可选，仅供 ctx 受控读取工程内音频）
    :param params: 运行时覆盖参数字典（可选，自动合并脚本顶部 PARAMS 默认值）
    :return: (result, logs, error_message)
    """
    logs = []
    code = strip_markdown_code_fence(code)

    # 1. 静态安全检查
    try:
        check_script_safety(code)
    except Exception as e:
        return None, [f"安全检查失败: {e}"], str(e)

    # 2. 解析并合并脚本参数，构造只读上下文
    resolved_params = coerce_script_params(code, params)
    from .script_api import ScriptContext
    ctx = ScriptContext(dataset_items, cancel_event=cancel_event, teproj_path=teproj_path, params=resolved_params)

    # 3. 构造重定向的 print 函数以捕获 stdout
    def custom_print(*args, sep=' ', end='\n'):
        msg = sep.join(str(a) for a in args) + end
        ctx.log(msg.rstrip('\r\n'))

    # 4. 执行作用域
    safe_globals = {
        "ctx": ctx,
        "print": custom_print,
        "os": _SafeOSNamespace(),
        "__builtins__": {
            **SAFE_BUILTINS,
            "__import__": _safe_import,
            "print": custom_print,
        },
    }
    globals_dict = safe_globals

    result_container = {}
    exception_container = {}
    thread_finished = threading.Event()

    def thread_target():
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
        figs_before = set(plt.get_fignums())
        try:
            # 编译并执行，首先在 globals 中定义脚本内容
            compiled = compile(code, "<custom_script>", "exec")
            exec(compiled, globals_dict)

            if "run" not in globals_dict:
                raise ValueError("脚本中未定义 `def run(ctx):` 函数入口。")

            run_func = globals_dict["run"]
            if not callable(run_func):
                raise ValueError("`run` 必须是可调用的函数。")

            # 运行入口函数
            res = run_func(ctx)
            result_container["result"] = res
        except SystemExit:
            exception_container["error"] = "用户取消或脚本运行超时"
        except Exception as e:
            exception_container["error"] = str(e)
            exception_container["traceback"] = traceback.format_exc()
        finally:
            # 清理脚本运行期间产生的、未通过 FigureResult 返回的孤儿 Figure，防止内存泄漏与并发竞态 (BUG-S07)
            try:
                from .script_api import FigureResult
                res_obj = result_container.get("result")
                res_list = res_obj if isinstance(res_obj, list) else ([res_obj] if res_obj is not None else [])
                kept_figs = {r.fig for r in res_list if isinstance(r, FigureResult) and getattr(r, "fig", None) is not None}
                if cancel_event and cancel_event.is_set():
                    kept_figs = set()
                for fnum in list(plt.get_fignums()):
                    if fnum not in figs_before:
                        fig_obj = plt.figure(fnum)
                        if fig_obj not in kept_figs:
                            plt.close(fig_obj)
            except Exception:
                pass
            thread_finished.set()

    # 5. 启动后台线程执行
    t = threading.Thread(target=thread_target)
    t.daemon = True
    t.start()

    # 6. 等待超时或取消
    check_interval = 0.1
    elapsed = 0.0
    cancelled = False

    while elapsed < timeout:
        if thread_finished.is_set():
            break
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break
        thread_finished.wait(check_interval)
        elapsed += check_interval

    if not thread_finished.is_set():
        if cancelled:
            return None, ctx._logs, "运行已请求取消。为了保护主程序稳定，第一版不会强制杀死正在执行的 Python 线程；请让脚本尽快自然结束。"
        else:
            return None, ctx._logs, f"运行超时：脚本执行时间超过了限时 {timeout} 秒。为了保护主程序稳定，第一版不会强制杀死正在执行的 Python 线程；请等待它自然结束，或重启工具箱以彻底释放这次脚本占用的计算资源。"

    if "error" in exception_container:
        tb = exception_container.get("traceback", "")
        err_msg = exception_container["error"]
        if tb:
            return None, ctx._logs, f"脚本运行出错：\n{err_msg}\n\n堆栈信息：\n{tb}"
        else:
            return None, ctx._logs, f"脚本运行出错：{err_msg}"

    return result_container.get("result"), ctx._logs, None
