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
        "to_file",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
        "writestr",
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
            if node.attr.startswith("_"):
                raise ValueError(f"安全限制: 禁止访问私有或双下划线属性 '{node.attr}'")
            if node.attr == "gaussian_kde":
                raise ValueError(GAUSSIAN_KDE_ERROR_MSG)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split('.')[0]
                if name not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"安全检查拦截：禁止在第一版脚本中导入库 '{alias.name}'")
                if "gaussian_kde" in alias.name.split('.'):
                    raise ValueError(GAUSSIAN_KDE_ERROR_MSG)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split('.')[0]
                if name not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"安全检查拦截：禁止在第一版脚本中导入库 '{node.module}'")
                if node.module.startswith("parselmouth.praat"):
                    raise ValueError("安全检查拦截：第一版脚本不允许导入 parselmouth.praat；请使用 ctx.load_item_sound 和 Sound.to_spectrogram 等受控接口。")
                if "gaussian_kde" in node.module.split('.'):
                    raise ValueError(GAUSSIAN_KDE_ERROR_MSG)
            for alias in node.names:
                if alias.name == "gaussian_kde":
                    raise ValueError(GAUSSIAN_KDE_ERROR_MSG)
        elif isinstance(node, ast.Call):
            name = call_name(node.func)
            short_name = name.split(".")[-1] if name else ""
            if short_name == "gaussian_kde" or "gaussian_kde" in name.split("."):
                raise ValueError(GAUSSIAN_KDE_ERROR_MSG)
            if short_name in forbidden_funcs:
                raise ValueError(f"安全检查拦截：禁止在第一版脚本中调用系统函数 '{short_name}'")
            if short_name in forbidden_output_calls:
                raise ValueError(f"安全检查拦截：禁止在脚本中直接调用输出/写入函数 '{short_name}'；请通过 ctx.figure(...) 或 ctx.table(...) 返回结果。")

validate_ast = check_script_safety


def run_custom_script(code, dataset_items, timeout=30, cancel_event=None, teproj_path=None):
    """
    运行自定义 Python 脚本。
    :param code: 脚本源码
    :param dataset_items: 只读数据集条目列表
    :param timeout: 超时时间（秒）
    :param cancel_event: 线程取消事件对象（可选）
    :param teproj_path: 当前工程文件路径（可选，仅供 ctx 受控读取工程内音频）
    :return: (result, logs, error_message)
    """
    logs = []
    code = strip_markdown_code_fence(code)

    # 1. 静态安全检查
    try:
        check_script_safety(code)
    except Exception as e:
        return None, [f"安全检查失败: {e}"], str(e)

    # 2. 构造只读上下文
    from .script_api import ScriptContext
    ctx = ScriptContext(dataset_items, cancel_event=cancel_event, teproj_path=teproj_path)

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
        try:
            # 切换 matplotlib 到 Agg 后端以防止弹窗
            import matplotlib
            matplotlib.use("Agg", force=True)

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
