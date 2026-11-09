import json
import os
import shutil
import time


def is_pid_alive(pid):
    """只读探测目标 PID 是否存活。
    注意：在 Windows 下绝对禁止调用 os.kill(pid, 0)，因为 0 即 CTRL_C_EVENT，
    Windows CPython 的 os.kill 会向控制台广播信号或调用 TerminateProcess 强杀进程！
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            ERROR_ACCESS_DENIED = 5
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle or handle == 0:
                return ctypes.get_last_error() == ERROR_ACCESS_DENIED
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (OSError, ProcessLookupError):
            return False


_is_pid_alive = is_pid_alive


class WorkspaceLockMixin:
    """工作区进程互斥锁与孤立工作区隔离分配混入类。"""

    _is_pid_alive = staticmethod(is_pid_alive)
    _pid_exists = _is_pid_alive

    def allocate_isolated_workspace(self):
        """当默认工作区被其他存活实例占用时，分配独立工作区目录（workspace_{pid}）并获取锁。"""
        self._default_workspace_contended = True
        new_ws = os.path.join(self.base_dir, f"workspace_{os.getpid()}")
        self._fallback_workspace_dir = new_ws
        self.workspace_dir = new_ws
        self.backup_path = os.path.join(self.workspace_dir, "auto_save_backup.teproj")
        os.makedirs(self.workspace_dir, exist_ok=True)
        self._lock_acquired = self.acquire_workspace_lock()
        self.auto_save_enabled = False
        return self.workspace_dir

    def acquire_workspace_lock(self):
        """尝试获取工作区锁文件。若被其他存活进程或实例占用则返回 False，否则写入当前 PID 与实例 ID 并返回 True。"""
        if not os.path.exists(self.workspace_dir):
            try:
                os.makedirs(self.workspace_dir, exist_ok=True)
            except Exception:
                pass
        lock_path = os.path.join(self.workspace_dir, "workspace.lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                existing_pid = info.get("pid")
                existing_inst = info.get("instance_id")
                current_inst = getattr(self, "_instance_id", None)
                if existing_pid:
                    if existing_pid != os.getpid():
                        if self._is_pid_alive(existing_pid):
                            return False
                    else:
                        if existing_inst and current_inst and existing_inst != current_inst:
                            return False
            except Exception:
                pass
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump({
                    "pid": os.getpid(),
                    "instance_id": getattr(self, "_instance_id", None),
                    "timestamp": time.time(),
                    "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
                }, f)
            self._lock_acquired = True
            return True
        except Exception:
            return False

    def release_workspace_lock(self):
        """释放当前进程与实例的工作区锁，并清理回退隔离工作区（若有）。"""
        lock_path = os.path.join(self.workspace_dir, "workspace.lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                current_inst = getattr(self, "_instance_id", None)
                if info.get("pid") == os.getpid() and (not current_inst or not info.get("instance_id") or info.get("instance_id") == current_inst):
                    os.remove(lock_path)
            except Exception:
                pass
        fallback_dir = getattr(self, "_fallback_workspace_dir", None)
        if fallback_dir and os.path.exists(fallback_dir):
            try:
                shutil.rmtree(fallback_dir, ignore_errors=True)
            except Exception:
                pass
            self._fallback_workspace_dir = None


__all__ = [
    "is_pid_alive",
    "_is_pid_alive",
    "WorkspaceLockMixin",
]
