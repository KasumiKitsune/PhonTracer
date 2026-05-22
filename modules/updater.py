import os
import json
import urllib.request
import urllib.error
import threading
import logging
from modules.version import __version__

logger = logging.getLogger(__name__)

# 默认检测地址：GitHub API。如果是国内环境，此地址可能较慢，但作为开源项目是标准做法。
DEFAULT_UPDATE_URL = "https://api.github.com/repos/KasumiKitsune/Tone_extractor/releases/latest"
SETTINGS_FILE = os.path.expanduser("~/.tone_extractor_settings.json")

def get_ignored_version():
    """获取用户选择忽略的版本"""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("ignored_version", "")
        except Exception as e:
            logger.error(f"读取设置文件失败: {e}")
    return ""

def save_ignored_version(version_str):
    """保存用户忽略的版本"""
    try:
        data = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except Exception:
                    pass
        data["ignored_version"] = version_str
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存设置文件失败: {e}")

def is_new_version(current, latest):
    """对比版本号判定是否有更新"""
    # 过滤掉前导的 'v' 或 'V'
    c_str = current.lower().lstrip('v')
    l_str = latest.lower().lstrip('v')
    
    try:
        c_parts = [int(x) for x in c_str.split(".")]
        l_parts = [int(x) for x in l_str.split(".")]
        
        # 补齐长度差异，用0填充
        max_len = max(len(c_parts), len(l_parts))
        c_parts += [0] * (max_len - len(c_parts))
        l_parts += [0] * (max_len - len(l_parts))
        
        return l_parts > c_parts
    except ValueError:
        # 如果版本格式不规则，回退到普通字符串对比
        return c_str != l_str

def fetch_latest_release(url=DEFAULT_UPDATE_URL, timeout=5):
    """从网络请求获取最新的Release信息"""
    try:
        # GitHub API 必须包含 User-Agent 头，否则返回 403
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ToneExtractorUpdater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            # 解析 GitHub API 返回结构
            tag_name = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            body = data.get("body", "")
            published_at = data.get("published_at", "")
            
            # 如果是自定义 JSON，可能包含不同的字段，做一下兼容
            latest_version = tag_name or data.get("latest_version", "")
            download_url = html_url or data.get("download_url", "")
            changelog = body or data.get("changelog", "")
            if isinstance(changelog, list):
                changelog = "\n".join(changelog)
                
            return {
                "latest_version": latest_version,
                "download_url": download_url,
                "changelog": changelog,
                "publish_date": published_at[:10] if published_at else ""
            }
    except Exception as e:
        logger.error(f"获取最新版本信息失败: {e}")
        return None

def check_for_updates_async(root, on_update_found=None, on_no_update=None, on_error=None, is_manual=False):
    """
    异步检查更新主入口。
    
    :param root: tkinter.Tk 根实例，用于跨线程调用 root.after
    :param on_update_found: 发现新版本时的回调，参数为 (latest_info)
    :param on_no_update: 未发现新版本时的回调，无参数
    :param on_error: 检测失败时的回调，参数为 (error_message)
    :param is_manual: 是否为手动触发。若为自动触发，会判断用户是否忽略了此版本。
    """
    def worker():
        info = fetch_latest_release()
        if not info:
            if on_error:
                root.after(0, lambda: on_error("无法连接到更新服务器，请稍后重试。"))
            return
            
        latest_ver = info.get("latest_version", "")
        if not latest_ver:
            if on_error:
                root.after(0, lambda: on_error("解析更新数据失败。"))
            return
            
        # 比对版本
        if is_new_version(__version__, latest_ver):
            # 如果是自动检测，判断是否被用户忽略
            if not is_manual and latest_ver == get_ignored_version():
                if on_no_update:
                    root.after(0, on_no_update)
                return
                
            if on_update_found:
                root.after(0, lambda: on_update_found(info))
        else:
            if on_no_update:
                root.after(0, on_no_update)
                
    threading.Thread(target=worker, daemon=True).start()
