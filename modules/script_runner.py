# -*- coding: utf-8 -*-
"""
PhonTracer / Tone Extractor 自定义脚本运行器
"""

import ast
import builtins
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
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "object": object,
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
    "zip": zip,
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0:
        raise ImportError("安全检查拦截：禁止使用相对导入")
    root_name = str(name).split(".")[0]
    if root_name not in ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"安全检查拦截：禁止在第一版脚本中导入库 '{name}'")
    return builtins.__import__(name, globals, locals, fromlist, level)

def check_script_safety(code):
    """
    通过 AST 抽象语法树检查脚本是否包含危险的 import 或函数调用。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"脚本语法错误：{e}")

    forbidden_funcs = {
        "__import__",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
        "vars",
    }
    forbidden_expensive_calls = {
        "gaussian_kde": (
            "第一版脚本运行器暂不允许调用 scipy.stats.gaussian_kde。"
            "该函数在数据点或网格较多时很容易长时间占用后台 Python 线程，"
            "请改用散点图、分箱均值曲线、hexbin/hist2d，或先将每组数据降采样到很小规模。"
        ),
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
        if isinstance(node, ast.Import):
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
            if short_name in forbidden_output_calls:
                raise ValueError(f"安全检查拦截：禁止在脚本中直接调用输出/写入函数 '{short_name}'；请通过 ctx.figure(...) 或 ctx.table(...) 返回结果。")
            if short_name in forbidden_expensive_calls:
                raise ValueError(f"安全检查拦截：{forbidden_expensive_calls[short_name]}")


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
    globals_dict = {
        "ctx": ctx,
        "print": custom_print,
        "__builtins__": {
            **ALLOWED_BUILTINS,
            "__import__": _safe_import,
            "print": custom_print,
        },
    }

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
