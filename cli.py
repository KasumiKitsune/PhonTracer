import cmd
import shlex
import sys
import os
import multiprocessing

# Force sys.stdin, sys.stdout, sys.stderr to use UTF-8 encoding to avoid encoding issues (especially on Windows)
if sys.stdin and hasattr(sys.stdin, 'reconfigure'):
    try:
        sys.stdin.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import parselmouth
import numpy as np
import concurrent.futures
from collections import OrderedDict
import json

# Override json.dumps to output raw UTF-8 Chinese characters rather than Unicode escape sequences (\uXXXX)
_orig_dumps = json.dumps
def dumps_utf8(*args, **kwargs):
    if 'ensure_ascii' not in kwargs:
        kwargs['ensure_ascii'] = False
    return _orig_dumps(*args, **kwargs)
json.dumps = dumps_utf8

# Modify sys.path if necessary
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.audio_core import macroscopic_vad, core_microscopic_vowel_nucleus, auto_split_inner_word, auto_split_to_chars_bounds, batch_process_worker, recalculate_bounds_fast, extract_f0
from modules.speaker_manager import SpeakerManager
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, get_export_text_for_item, build_five_point_chart, split_into_syllables, make_textgrid_export_stem
from modules.project_manager import ProjectManager
from modules.version import APP_NAME, __version__
from modules.acoustic_exporter import AcousticChartExporter

class LoggerOut:
    def __init__(self, original_stdout, log_file):
        self.original_stdout = original_stdout
        self.log_file = log_file

    def write(self, message):
        self.original_stdout.write(message)
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self):
        self.original_stdout.flush()
        self.log_file.flush()

class AcousticChartCLIAdapter:
    def __init__(self, cli_instance):
        self.cli = cli_instance
        self.app_state_params = cli_instance.params  # Mapped from self.params in CLI
        self.items = cli_instance.items

    def _get_items_by_group_for_dict(self, items_dict):
        groups = {}
        for k, v in items_dict.items():
            g = v.get('group', '导入内容')
            if g not in groups: groups[g] = []
            groups[g].append(k)
        return [(g, groups[g]) for g in groups]

    def _ensure_item_loaded(self, item):
        self.cli._ensure_item_loaded(item)

    def _extract_syl_data(self, item, num_points):
        return self.cli._extract_syl_data(item, num_points)

    def _get_pitch_arrays_for_item(self, item):
        if item.get('pitch_data'):
            p_xs = item['pitch_data'].get('xs')
            p_freqs = item['pitch_data'].get('freqs')
            if p_xs is None or p_freqs is None:
                return None, None
            return np.asarray(p_xs), np.asarray(p_freqs)
        if item.get('pitch'):
            pitch = item['pitch']
            try:
                p_xs = np.asarray(pitch.xs())
                p_freqs = np.asarray(pitch.selected_array['frequency'])
            except (TypeError, KeyError, AttributeError):
                return None, None
            if p_xs.ndim != 1 or p_freqs.ndim != 1 or len(p_xs) != len(p_freqs):
                return None, None
            return p_xs, p_freqs
        return None, None

    def _get_syllables_and_bounds(self, item):
        t_s, t_e = item.get('start'), item.get('end')
        if t_s is None or t_e is None or t_e <= t_s:
            return [], []

        label = item.get('label', '')
        from modules.data_utils import split_into_syllables
        syls = split_into_syllables(label)
        if not syls and label:
            syls = [label]

        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            return syls, [[float(s), float(e)] for s, e in chars_bounds]

        inner_splits = item.get('inner_splits', [])
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(syls) > 1 and len(splits) != len(syls) + 1:
            splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
        elif len(syls) <= 1:
            splits = [t_s, t_e]
            if not syls:
                syls = [label]

        return syls, [[splits[i], splits[i + 1]] for i in range(len(splits) - 1)]

    def _extract_kde_contour(self, p_xs, p_freqs, c_s, c_e, n_dense):
        if c_e <= c_s:
            return None

        valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & np.isfinite(p_freqs) & (p_freqs > 0))[0]
        if len(valid_idx) < 3:
            return None

        seg_xs = np.asarray(p_xs[valid_idx], dtype=float)
        seg_ys = np.asarray(p_freqs[valid_idx], dtype=float)
        order = np.argsort(seg_xs)
        seg_xs = seg_xs[order]
        seg_ys = seg_ys[order]

        v_s, v_e = seg_xs[0], seg_xs[-1]
        if v_e <= v_s:
            return None

        gap_threshold = 0.025
        smoothed = seg_ys.copy()
        try:
            from scipy.signal import savgol_filter
            breaks = np.where(np.diff(seg_xs) > gap_threshold)[0] + 1
            run_ranges = np.split(np.arange(len(seg_xs)), breaks)
            for run in run_ranges:
                run_len = len(run)
                if run_len < 5:
                    continue
                win = min(9, run_len if run_len % 2 == 1 else run_len - 1)
                if win >= 5:
                    smoothed[run] = savgol_filter(seg_ys[run], win, 2)
        except Exception:
            pass

        dense_times = np.linspace(v_s, v_e, n_dense)
        y_dense = np.interp(dense_times, seg_xs, smoothed)

        nearest_right = np.searchsorted(seg_xs, dense_times, side='left')
        nearest_left = np.clip(nearest_right - 1, 0, len(seg_xs) - 1)
        nearest_right = np.clip(nearest_right, 0, len(seg_xs) - 1)
        nearest_dist = np.minimum(np.abs(dense_times - seg_xs[nearest_left]), np.abs(dense_times - seg_xs[nearest_right]))
        y_dense[nearest_dist > gap_threshold] = np.nan
        return y_dense

class PhonTracerCLI(cmd.Cmd):
    intro = f"""{APP_NAME} CLI v{__version__} - AI Agent Mode
你好，我是 PhonTracer 的命令行工作台。请 AI 优先只通过这里的 CLI 命令完成任务。
除非用户明确要求，不要直接改源码、搬文件、删文件或绕过 CLI 操作工程数据。
Type 'help' or '?' to list commands. Use 'agent_guide' for AI operating rules.
"""
    prompt = "(phontracer) "

    def __init__(self):
        super().__init__()
        self.speaker_manager = SpeakerManager()
        self.lang = 'en'
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
        self.log_file = None
        self.original_stdout = sys.stdout
        self.project_manager = ProjectManager(self)

    def _emit(self, success=True, message="", **payload):
        data = {"success": success}
        if message:
            data["message"] = message
        data.update(payload)
        print(json.dumps(data))

    def _after_state_change(self, action="updated"):
        if getattr(self.project_manager, "auto_save_enabled", False):
            self.project_manager.trigger_auto_save()

    def after(self, _delay, callback):
        callback()

    @property
    def current_speaker(self):
        return self.speaker_manager.get_active_speaker()

    @property
    def items(self):
        return self.current_speaker.items

    @property
    def params(self):
        if 'trim_silence' not in self.current_speaker.last_params:
            self.current_speaker.last_params['trim_silence'] = True
        return self.current_speaker.last_params

    @property
    def mode(self):
        tab = getattr(self.current_speaker, 'tab_mode', '多条独立音频')
        return 'long' if '单条' in tab else 'batch'

    @mode.setter
    def mode(self, value):
        if value == 'long':
            self.current_speaker.tab_mode = '单条长音频'
        else:
            self.current_speaker.tab_mode = '多条独立音频'

    @property
    def groups(self):
        if not hasattr(self.current_speaker, 'cli_groups'):
            seen = []
            for item in self.current_speaker.items.values():
                group = item.get('group', '导入内容')
                if group not in seen:
                    seen.append(group)
            self.current_speaker.cli_groups = seen
        return self.current_speaker.cli_groups

    @groups.setter
    def groups(self, val):
        self.current_speaker.cli_groups = val

    @property
    def long_snd(self):
        return self.current_speaker.pending_long_snd

    @long_snd.setter
    def long_snd(self, val):
        self.current_speaker.pending_long_snd = val

    @property
    def long_snd_path(self):
        if hasattr(self.current_speaker, 'cli_long_snd_path') and self.current_speaker.cli_long_snd_path:
            return self.current_speaker.cli_long_snd_path
        return getattr(self.current_speaker, 'long_audio_path', None)

    @long_snd_path.setter
    def long_snd_path(self, val):
        self.current_speaker.cli_long_snd_path = val
        self.current_speaker.long_audio_path = val

    @property
    def batch_paths(self):
        return self.current_speaker.pending_batch_paths

    @batch_paths.setter
    def batch_paths(self, val):
        self.current_speaker.pending_batch_paths = val

    @property
    def audio_cache(self):
        return self.current_speaker.audio_cache

    def precmd(self, line):
        if self.log_file:
            self.log_file.write(f"> {line}\n")
            self.log_file.flush()
        return line

    def default(self, line):
        line_stripped = line.strip()
        if not line_stripped:
            return

        is_external = False

        # 1. Normalize line: strip PowerShell leading operator '&'
        test_line = line_stripped
        if test_line.startswith('&'):
            test_line = test_line[1:].strip()

        # 2. Extract tokens
        import os
        import shlex
        try:
            parts = shlex.split(test_line)
        except Exception:
            parts = test_line.split()

        normalized_tokens = []
        for p in parts:
            p_clean = p.strip().lower().strip('\'"')
            if p_clean.startswith('.\\') or p_clean.startswith('./'):
                p_clean = p_clean[2:]
            normalized_tokens.append(p_clean)

        banned_commands = {
            "python", "py", "powershell", "pwsh", "cmd", "bash", "sh",
            "node", "rscript", "praat", "parselmouth", "start", "call"
        }
        banned_suffixes = ('.bat', '.cmd', '.ps1', '.py', '.r', '.js', '.vbs')

        if normalized_tokens:
            first_token = normalized_tokens[0]
            # Extract basename in case of paths (e.g. C:\Python312\python.exe)
            cmd_name = os.path.basename(first_token.replace('\\', '/'))
            if cmd_name.endswith('.exe'):
                cmd_name = cmd_name[:-4]

            if cmd_name in banned_commands:
                is_external = True

            for token in normalized_tokens:
                if token.endswith(banned_suffixes):
                    is_external = True
                    break

        # Double check with simple whitespace splitting to prevent shlex parsing bypasses
        raw_parts = line_stripped.split()
        if raw_parts:
            raw_first = raw_parts[0].strip().lower().strip('\'"')
            if raw_first.startswith('.\\') or raw_first.startswith('./'):
                raw_first = raw_first[2:]
            raw_cmd_name = os.path.basename(raw_first.replace('\\', '/'))
            if raw_cmd_name.endswith('.exe'):
                raw_cmd_name = raw_cmd_name[:-4]

            if raw_cmd_name in banned_commands:
                is_external = True

            for rp in raw_parts:
                rp_clean = rp.strip().lower().strip('\'"')
                if rp_clean.endswith(banned_suffixes):
                    is_external = True
                    break

        if is_external:
            print(json.dumps({
                "success": False,
                "error": f"Unknown CLI command: {line_stripped}",
                "message": "Detected an external shell/script style command (External automation attempt). PhonTracerCLI does not execute external commands directly; please use built-in CLI commands.",
                "next_steps": ["help", "agent_guide", "status"]
            }))
        else:
            print(json.dumps({
                "success": False,
                "error": f"Unknown CLI command: {line_stripped}",
                "message": "Use help or agent_guide to see supported PhonTracer CLI commands."
            }))

    def _check_item_has_empty_data(self, item):
        """Returns True if the item contains a 0Hz pitch in the 11-point preview."""
        if 'has_empty_data' in item:
            return item['has_empty_data']
        if item.get('preview_f0'):
            return any(hz == 0.0 for hz in item['preview_f0'])
        return False

    def do_set_params(self, arg):
        """
        Set analysis parameters.
        Usage: set_params key=value [key=value ...]
        Valid keys: pts, db, skip_front, pitch_floor, pitch_ceiling, voicing_threshold, trim_silence, analysis_mode, formant_max_hz, formant_count, formant_window_length, formant_pre_emphasis, formant_sample_strategy
        Example: set_params db=50.0 trim_silence=False analysis_mode=formant formant_max_hz=5500
        """
        args = shlex.split(arg)
        if not args:
            print(json.dumps({"success": True, "params": self.params}))
            return

        updated = False
        for kv in args:
            if '=' in kv:
                k, v = kv.split('=', 1)
                if k in self.params:
                    try:
                        if k == 'trim_silence':
                            self.params[k] = v.lower() in ('true', '1', 'yes')
                        elif k in ('pts', 'pitch_floor', 'pitch_ceiling', 'formant_count'):
                            self.params[k] = int(v)
                        elif k in ('analysis_mode', 'formant_sample_strategy'):
                            self.params[k] = str(v)
                        elif k in ('formant_max_hz', 'formant_window_length', 'formant_pre_emphasis'):
                            self.params[k] = float(v)
                        else:
                            self.params[k] = float(v)
                        updated = True
                    except ValueError:
                        print(json.dumps({"success": False, "error": f"Invalid value for {k}"}))
                        return
                else:
                    print(json.dumps({"success": False, "error": f"Unknown parameter {k}"}))
                    return

        print(json.dumps({"success": True, "message": "Parameters updated", "params": self.params}))

    def do_lang(self, arg):
        """
        Switch display language for messages and guides between English and Chinese.
        Usage: lang [zh|en]
        Example: lang zh
        """
        choice = arg.strip().lower()
        if choice in ('zh', 'cn', 'chinese', '中文'):
            self.lang = 'zh'
            self.prompt = "(phontracer)[中文] "
            print(json.dumps({"success": True, "message": "语言已切换为中文", "lang": "zh"}))
        elif choice in ('en', 'english', '英文'):
            self.lang = 'en'
            self.prompt = "(phontracer) "
            print(json.dumps({"success": True, "message": "Language switched to English", "lang": "en"}))
        else:
            print(json.dumps({"success": True, "lang": self.lang}))

    def do_help(self, arg):
        if not arg:
            if getattr(self, 'lang', 'en') == 'zh':
                print("""
================================================================================
                            PhonTracer 命令行手册 (AI Agent 与开发者指南)
================================================================================
AI 操作守则：优先只使用本 CLI。除非用户明确要求，不要直接编辑源码、移动/删除文件、
不要绕过 CLI 去操作工程数据。需要说明时，请先用 `status`、`list_items`、`agent_guide` 确认。

PhonTracer 是一款高精度的声学声调格局分析工具。

--- 工作流生命周期 ---
1. 导入音频：
   - 导入单条长音频（如整句朗读录音）：
     `load_long <音频路径>`
   - 导入批量独立短音频文件：
     `load_batch <路径1> <路径2> ...`
2. 导入字词表（进行音节切分/匹配）：
   - `apply_wordlist <字词表路径> [匹配模式]`
     * 字词表格式：支持按分组名、回车或Tab分隔（例如："组1\n演讲\n工作"）。
     * 匹配模式：'fuzzy'（模糊文件名匹配，默认）或 'order'（按物理顺序匹配）。
3. 微调边界或声学提取参数：
   - 精细修正某个音节的自动 VAD 时间边界：
     `modify_bounds <音节ID> <开始时间> <结束时间>`
   - 精细修正某个音节的 Parselmouth 独立基频提取参数（所见即所得）：
     `modify_params <音节ID> pitch_floor=30 voicing_threshold=0.20`
   - 使用最新修改的全局参数，重新批量计算整个项目的所有项：
     `recalculate`
4. 数据导出：
   - `export <格式> <输出路径> [规则] [目标范围] [高级参数=值 ...]`
     * 支持的导出格式：txt, xlsx, line_chart, kde, wav, merged_wav, textgrid, contour (声调轮廓图), distribution (声调分布图), density (时序密度图), quality (数据质量检查), overview_heatmap (声调组别概览图)
     * 共振峰专有导出格式：formant_table (共振峰数据表), formant_space (元音舌位图), formant_trajectory (共振峰时序轨迹图), formant_density (共振峰密度分布图), formant_overview_heatmap (共振峰组别概览图)

--- 声学参数与调优指南 ---
* analysis_mode: 分析模式，可选 'pitch' (基频模式) 或 'formant' (共振峰模式，默认：'pitch')
* pts: 等分插值采样点数（默认：11）
* db: VAD 切分能量落差阈值（默认：60.0）
* skip_front: 排除声母时长，避免声母辅音浊化干扰（默认：0.0）
* pitch_floor: 音高分析下限（默认：75 Hz） -> 通用甜点区：75
* pitch_ceiling: 音高分析上限（默认：600 Hz） -> 通用甜点区：600
* voicing_threshold: 浊音阈值（默认：0.25） -> 针对汉语三声等低频“气泡音/嘎裂声”，建议手动调低至 0.15 ~ 0.20
* trim_silence: 自动切除有效声学边界首尾低于 -50dB 的静音区（默认：True）
* formant_max_hz: 最大共振峰频率（如 5500 Hz，默认针对不同发音人设置）
* formant_count: 共振峰数量（默认：5）
* formant_window_length: 共振峰分析窗长（秒，默认：0.025）
* formant_pre_emphasis: 共振峰预加重系数（Hz，默认：50.0）
* formant_sample_strategy: 共振峰提取采样策略（可选 '整段11点', '中段均值'，默认：'整段11点'）

--- 核心命令速查表 ---
- `status`: 获取项目当前模式、状态指标、警告统计等。
- `list_items [all|warnings|组名]`: 列表打印当前已切分的数据项及警告状态。
- `set_params 键=值 ...`: 动态更新全局算法提取参数。
- `modify_bounds <音节ID> <开始秒数> <结束秒数>`: 手动重写音节声学边界。
- `modify_params <音节ID> 键=值 ...`: 手动指定单个音节专属的提取参数。
- `recalculate`: 基于全局最新参数，批量重算整个项目。
- `detect_f0 [apply_preset]`: 自动估算发音人的 F0 分布并给出保守/推荐/精细范围。可直接应用预设（conservative, recommended, fine）。
- `project_export <路径.teproj>` / `project_import <路径.teproj>`: 导出或导入完整工程。
- `autosave on|off|now`: 开启、关闭或立即执行工程自动保存。
- `tool_merge <输出.wav> <间隔秒> <音频1> <音频2> ...`: 拼接多个短音频。
- `tool_split <长音频> <字表.txt> <输出目录> [缓冲秒] [trim]`: 按字表拆分长音频。
- `tool_sort_batch <字表.txt> [音频路径...]`: 按字表模糊排序独立音频。
- `lang [zh|en]`: 切换命令行显示语言为中文或英文。
================================================================================
""")
            else:
                print("""
================================================================================
                            PhonTracer CLI MANUAL (Agent & Developer Guide)
================================================================================
AI operating rule: prefer this CLI as the control surface. Unless the user clearly
asks for it, do not edit source files, move/delete files, or bypass the CLI to
change project data. Use `status`, `list_items`, and `agent_guide` to orient first.

PhonTracer is a high-accuracy acoustic tone analysis tool.

--- WORKFLOW LIFECYCLE ---
1. Load Audio:
   - For single long audio files (e.g. continuous sentence speech):
     `load_long <filepath>`
   - For multiple isolated sound files:
     `load_batch <file1> <file2> ...`
2. Apply Wordlist (Syllable Segmenting):
   - `apply_wordlist <wordlist_filepath> [match_mode]`
     * Wordlist structure: Tab/Newline grouped words (e.g., "组1\n演讲\n工作").
     * match_mode: 'fuzzy' (fuzzy filename matching) or 'order' (strict order matching).
3. Fine-Tune boundaries or acoustic parameters:
   - To fine-tune VAD time boundaries of a specific syllable:
     `modify_bounds <item_id> <start> <end>`
   - To fine-tune Parselmouth acoustic parameters of a specific syllable (WYSIWYG):
     `modify_params <item_id> pitch_floor=30 voicing_threshold=0.20`
   - To recalculate all items globally with updated global parameters:
     `recalculate`
4. Export:
   - `export <format> <output_file> [rule] [target] [key=val ...]`
     * format: txt, xlsx, line_chart, kde, wav, merged_wav, textgrid, contour, distribution, density, quality, overview_heatmap
     * formant formats: formant_table (Excel table), formant_space (vowel chart), formant_trajectory (trajectory), formant_density (density), formant_overview_heatmap (group heatmap)

--- CURRENT CONFIG & SCHEMAS ---
- Global parameters (modifiable via `set_params`):
  * analysis_mode: Analysis mode, 'pitch' or 'formant' (default: 'pitch')
  * pts: Number of interpolation points (default: 11)
  * db: VAD energy threshold (default: 60.0)
  * skip_front: Avoid segmenting consonant onset duration (default: 0.0)
  * pitch_floor: Minimum F0 range (default: 75) -> Sweet spot: 75
  * pitch_ceiling: Maximum F0 range (default: 600) -> Sweet spot: 600
  * voicing_threshold: Voicing tolerance (default: 0.25) -> Adjust lower (0.15~0.20) for creaky voice
  * trim_silence: Cut prefix/suffix silence under -50dB (default: True)
  * formant_max_hz: Max formant frequency threshold in Hz (e.g. 5500)
  * formant_count: Number of formants to track (default: 5)
  * formant_window_length: Formant analysis window length in seconds (default: 0.025)
  * formant_pre_emphasis: Formant pre-emphasis filter value (default: 50.0)
  * formant_sample_strategy: Strategy to sample formant points (e.g., '整段11点', '中段均值')

--- ALL COMMANDS REFERENCE ---
- `status`: Show current project state, active parameters, item warnings.
- `list_items [all|warnings|group_name]`: List segmented items and warning flags.
- `set_params key=value ...`: Update global extraction parameters.
- `modify_bounds <item_id> <start> <end>`: Set manual time boundaries.
- `modify_params <item_id> key=value ...`: Set item-specific custom parameters.
- `recalculate`: Recalculate VAD boundaries & F0 curves globally.
- `detect_f0 [apply_preset]`: Estimate speaker F0 distribution and get conservative/recommended/fine range suggestions. Can apply preset directly (conservative, recommended, fine).
- `project_export <path.teproj>` / `project_import <path.teproj>`: Save or load a full project.
- `autosave on|off|now`: Enable, disable, or immediately run project autosave.
- `tool_merge <output.wav> <gap_sec> <audio1> <audio2> ...`: Merge short audios.
- `tool_split <long_audio> <wordlist.txt> <output_dir> [buffer_sec] [trim]`: Split long audio by wordlist.
- `tool_sort_batch <wordlist.txt> [audio_paths...]`: Fuzzy-sort batch audio paths by wordlist.
- `lang [zh|en]`: Switch CLI language between Chinese and English.
================================================================================
""")
        else:
            super().do_help(arg)

    def do_agent_guide(self, arg):
        """
        Show operating rules for AI agents.
        Usage: agent_guide
        """
        self._emit(
            True,
            "你不是来写自动化脚本的，只能作为 PhonTracer CLI 操作者工作。每次只能输入一个内置命令，读完结果再进行下一步。",
            rules=[
                "你不是来写自动化脚本的，只能作为 PhonTracer CLI 操作者工作。",
                "每次只能输入一个 PhonTracer CLI 内置命令。",
                "必须等待并阅读返回结果后再决定下一步，禁止提前生成命令清单或一次性灌入多条命令。",
                "禁止创建或运行任何额外脚本文件，包括 .bat、.cmd、.ps1、.py、.r、.praat、.js、.vbs 等任何外部脚本或命令。",
                "如果 CLI 返回 success:false、出现警告、路径不匹配、音频和字表无法确认对应关系，或当前 AI 工具无法稳定交互运行 CLI，必须立即停止并向用户说明情况，等待用户确认。",
                "最终必须先 project_export 导出完整 .teproj 工程包，确保可复现；然后再按用户要求导出 xlsx/TextGrid/wav/图表等结果文件。"
            ],
            next_steps=["status", "help", "list_items all"]
        )


    def do_speakers(self, arg):
        """
        List all speakers and show the active one.
        Usage: speakers
        """
        speakers = self.speaker_manager.get_all_speakers()
        active_id = self.speaker_manager.active_speaker_id
        res = []
        for s in speakers:
            res.append({
                "id": s.id,
                "name": s.name,
                "active": s.id == active_id,
                "items_count": len(s.items)
            })
        print(json.dumps({"success": True, "speakers": res}))

    def do_add_speaker(self, arg):
        """
        Add a new speaker.
        Usage: add_speaker <name>
        """
        name = arg.strip()
        if not name:
            print('{"success": False, "error": "Speaker name required"}')
            return
        new_speaker = self.speaker_manager.add_speaker(name)
        self._after_state_change("add_speaker")
        print(json.dumps({"success": True, "message": f"Speaker '{name}' added", "speaker_id": new_speaker.id}))

    def do_switch_speaker(self, arg):
        """
        Switch the active speaker.
        Usage: switch_speaker <name_or_id>
        """
        target = arg.strip()
        if not target:
            print('{"success": False, "error": "Speaker name or id required"}')
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if found_id:
            self.speaker_manager.set_active_speaker(found_id)
            print(json.dumps({"success": True, "message": f"Switched to speaker '{target}'"}))
        else:
            print(json.dumps({"success": False, "error": f"Speaker '{target}' not found"}))

    def do_remove_speaker(self, arg):
        """
        Remove a speaker. Cannot remove the last speaker.
        Usage: remove_speaker <name_or_id>
        """
        target = arg.strip()
        if not target:
            print('{"success": False, "error": "Speaker name or id required"}')
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if not found_id:
            print(json.dumps({"success": False, "error": f"Speaker '{target}' not found"}))
            return

        success = self.speaker_manager.remove_speaker(found_id)
        if success:
            self._after_state_change("remove_speaker")
            print(json.dumps({"success": True, "message": f"Speaker '{target}' removed"}))
        else:
            print(json.dumps({"success": False, "error": "Cannot remove the last speaker"}))

    def do_load_long(self, arg):
        """
        Load a single long audio file.
        Usage: load_long <filepath>
        """
        args = shlex.split(arg)
        if not args:
            print('{"success": False, "error": "Filepath required"}')
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            print(json.dumps({"success": False, "error": f"File not found: {filepath}"}))
            return

        try:
            self.long_snd = parselmouth.Sound(filepath)
            self.long_snd_path = filepath
            self.mode = 'long'
            self.batch_paths = []
            self.items.clear()
            self.groups.clear()
            self._after_state_change("load_long")
            self._emit(True, "长音频已加载。下一步通常是 apply_wordlist 或 apply_textgrid。", mode=self.mode, path=filepath)
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    def do_load_batch(self, arg):
        """
        Load multiple independent audio files.
        Usage: load_batch <file1> <file2> ...
        """
        args = shlex.split(arg)
        if not args:
            print('{"success": False, "error": "Filepaths required"}')
            return

        valid_paths = []
        for p in args:
            if os.path.exists(p):
                valid_paths.append(p)
            else:
                print(json.dumps({"success": False, "error": f"File not found: {p}"}))
                return

        self.batch_paths = valid_paths
        self.mode = 'batch'
        self.long_snd = None
        self.long_snd_path = None
        self.items.clear()
        self.groups.clear()
        self._after_state_change("load_batch")
        self._emit(True, f"已加载 {len(self.batch_paths)} 个独立音频。下一步通常是 apply_wordlist。", mode=self.mode, count=len(self.batch_paths))

    def do_apply_wordlist(self, arg):
        """
        Apply a wordlist (text file) to split/match the audio.
        Usage: apply_wordlist <wordlist_filepath> [match_mode]
        match_mode for batch: 'fuzzy' or 'order' (default 'fuzzy')
        """
        args = shlex.split(arg)
        if not args:
            print('{"success": False, "error": "Wordlist filepath required"}')
            return

        filepath = args[0]
        match_mode = args[1] if len(args) > 1 else 'fuzzy'

        if not os.path.exists(filepath):
            print(json.dumps({"success": False, "error": f"File not found: {filepath}"}))
            return

        with open(filepath, 'r', encoding='utf-8') as f:
            raw_text = f.read()

        groups, flat_words = parse_wordlist(raw_text)
        if not flat_words:
            print('{"success": False, "error": "No words found in wordlist"}')
            return

        self.groups = [g['group'] for g in groups]
        self.items.clear()

        if self.mode == 'long':
            self._process_long_wordlist(groups, flat_words)
        elif self.mode == 'batch':
            self._process_batch_wordlist(groups, flat_words, match_mode)
        else:
            print('{"success": False, "error": "No audio loaded. Use load_long or load_batch first."}')

    def do_apply_textgrid(self, arg):
        """
        Apply a TextGrid file to segment and match the active long audio.
        Usage: apply_textgrid <textgrid_filepath>
        """
        args = shlex.split(arg)
        if not args:
            print('{"success": False, "error": "TextGrid filepath required"}')
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            print(json.dumps({"success": False, "error": f"File not found: {filepath}"}))
            return

        if self.mode != 'long' or not self.long_snd:
            print('{"success": False, "error": "This command requires active long audio mode. Use load_long first."}')
            return

        try:
            import textgrid
            tg = textgrid.TextGrid.fromFile(filepath)

            words_tier = None
            chars_tier = None
            groups_tier = None
            for t in tg.tiers:
                if t.name == "words" and words_tier is None:
                    words_tier = t
                elif t.name == "chars" and chars_tier is None:
                    chars_tier = t
                elif t.name in ["groups", "group"] and groups_tier is None:
                    groups_tier = t

            if not words_tier:
                for t in tg.tiers:
                    if isinstance(t, textgrid.IntervalTier):
                        words_tier = t
                        break

            if not words_tier:
                print('{"success": False, "error": "No IntervalTier found in TextGrid"}')
                return

            tg_intervals = []
            for interval in words_tier:
                lbl = interval.mark.strip()
                if lbl:
                    grp_name = "导入内容"
                    if groups_tier:
                        center = (interval.minTime + interval.maxTime) / 2.0
                        for g_interval in groups_tier:
                            if g_interval.minTime <= center <= g_interval.maxTime:
                                g_lbl = g_interval.mark.strip()
                                if g_lbl:
                                    grp_name = g_lbl
                                    break

                    chars_bounds = []
                    inner_splits = []
                    if chars_tier:
                        overlapping_chars = []
                        for c_interval in chars_tier:
                            c_lbl = c_interval.mark.strip()
                            if c_lbl:
                                center = (c_interval.minTime + c_interval.maxTime) / 2.0
                                if interval.minTime <= center <= interval.maxTime:
                                    overlapping_chars.append(c_interval)

                        overlapping_chars.sort(key=lambda c: c.minTime)
                        if overlapping_chars:
                            for c in overlapping_chars:
                                chars_bounds.append([c.minTime, c.maxTime])
                            for j in range(len(overlapping_chars) - 1):
                                inner_splits.append(overlapping_chars[j].maxTime)

                    if not chars_bounds:
                        syls = split_into_syllables(lbl)
                        w_len = len(syls)
                        if w_len > 1:
                            splits = np.linspace(interval.minTime, interval.maxTime, w_len + 1).tolist()
                            chars_bounds = [[splits[j], splits[j+1]] for j in range(w_len)]
                            inner_splits = splits[1:-1]
                        else:
                            chars_bounds = [[interval.minTime, interval.maxTime]]
                            inner_splits = []

                    tg_intervals.append({
                        'start': interval.minTime,
                        'end': interval.maxTime,
                        'label': lbl,
                        'group': grp_name,
                        'inner_splits': inner_splits,
                        'chars_bounds': chars_bounds
                    })

            if not tg_intervals:
                print('{"success": False, "error": "No non-empty labeled intervals found in TextGrid"}')
                return

            self.items.clear()

            unique_groups = []
            for item in tg_intervals:
                g = item.get('group', '导入内容')
                if g not in unique_groups:
                    unique_groups.append(g)
            self.groups = unique_groups

            snd = self.long_snd
            pitch_data = extract_f0(snd, self.params)

            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']

            tasks = []
            for item in tg_intervals:
                ms = item['start']
                me = item['end']
                word = item['label']
                grp_name = item.get('group', '导入内容')
                ref_splits = item.get('inner_splits', [])

                valid_ms = max(0, ms)
                valid_me = min(snd.get_total_duration(), me)

                if valid_me > valid_ms:
                    part = snd.extract_part(from_time=valid_ms, to_time=valid_me)
                    snd_values = part.values
                    snd_sf = part.sampling_frequency

                    idx_start = np.searchsorted(pitch_xs, valid_ms)
                    idx_end = np.searchsorted(pitch_xs, valid_me)
                    sliced_xs = pitch_xs[idx_start:idx_end]
                    sliced_freqs = pitch_freqs[idx_start:idx_end]

                    tasks.append({
                        'word': word, 'group': grp_name, 'ms': ms, 'me': me,
                        'snd_values': snd_values, 'snd_sf': snd_sf,
                        'sliced_xs': sliced_xs, 'sliced_freqs': sliced_freqs,
                        'ref_splits': ref_splits,
                        'missing': False
                    })
                else:
                    tasks.append({'word': word, 'group': grp_name, 'missing': True})

            from modules.audio_core import process_single_long_word

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        continue

                    future = executor.submit(
                        process_single_long_word,
                        t['snd_values'], t['snd_sf'], t['word'], t['ms'], t['me'],
                        self.params, self.params['trim_silence'], t['sliced_xs'], t['sliced_freqs'], t['ref_splits']
                    )
                    futures[future] = i

                results = [None] * len(tasks)
                for i, t in enumerate(tasks):
                    if t.get('missing'):
                        results[i] = {'label': t['word'], 'group': t['group'], 'missing': True}

                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        res['group'] = tasks[orig_idx]['group']
                        res['missing'] = False
                        results[orig_idx] = res
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'error': str(e)}

            matched_count = 0
            for idx, res in enumerate(results):
                iid = f"item_{idx}"
                if res and res.get('success'):
                    res['snd'] = self.long_snd
                    res['pitch_data'] = pitch_data
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                    preview_times = np.linspace(res['start'], res['end'], 11)
                    preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
                    for j, t in enumerate(preview_times):
                        valid_indices = np.where(pitch_freqs > 0)[0]
                        if len(valid_indices) == 0:
                            preview_f0[j] = 0.0
                            continue
                        valid_xs = pitch_xs[valid_indices]
                        if np.min(np.abs(valid_xs - t)) > 0.025:
                            preview_f0[j] = 0.0
                    res['preview_f0'] = preview_f0
                    res['has_empty_data'] = any(f == 0.0 for f in res['preview_f0'])

                    self.items[iid] = res
                    matched_count += 1
                else:
                    self.items[iid] = {
                        'id': iid,
                        'label': res['label'],
                        'group': res['group'],
                        'missing': True
                    }

            self._after_state_change("apply_textgrid")
            self._emit(True, f"TextGrid 已应用：完成 {matched_count}/{len(results)} 项。", processed=matched_count, total=len(results))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    def _process_long_wordlist(self, groups, flat_words):
        try:
            pitch_data = extract_f0(self.long_snd, self.params)
            macro_segments = macroscopic_vad(self.long_snd, expected_count=len(flat_words))

            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']

            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    iid = f"item_{word_idx}"
                    if word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]

                        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                            self.long_snd, pitch_data, ms, me,
                            self.params['db'], self.params['skip_front'], self.params['trim_silence']
                        )

                        inner_splits = []
                        chars_bounds = []
                        split_warnings = []
                        split_confidence = 1.0
                        syls = split_into_syllables(word)
                        if len(syls) > 1:
                            meta = {}
                            inner_splits = auto_split_inner_word(self.long_snd, mic_s, mic_e, len(syls), pitch_data=pitch_data, output_meta=meta)
                            split_warnings = meta.get('split_warnings', [])
                            split_confidence = meta.get('split_confidence', 1.0)
                            chars_bounds = auto_split_to_chars_bounds(self.long_snd, mic_s, mic_e, inner_splits, len(syls), self.params)
                        else:
                            chars_bounds = [[mic_s, mic_e]]

                        # Preview
                        preview_times = np.linspace(mic_s, mic_e, 11)
                        preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
                        for j, t in enumerate(preview_times):
                            valid_indices = np.where(pitch_freqs > 0)[0]
                            if len(valid_indices) == 0:
                                preview_f0[j] = 0.0
                                continue
                            valid_xs = pitch_xs[valid_indices]
                            if np.min(np.abs(valid_xs - t)) > 0.025:
                                preview_f0[j] = 0.0
                        has_empty = any(f == 0.0 for f in preview_f0)

                        self.items[iid] = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'snd': self.long_snd, 'pitch_data': pitch_data,
                            'macro_start': ms, 'macro_end': me,
                            'start': mic_s, 'end': mic_e,
                            'raw_start': raw_s, 'raw_end': raw_e,
                            'inner_splits': inner_splits, 'chars_bounds': chars_bounds,
                            'split_warnings': split_warnings, 'split_confidence': split_confidence,
                            'preview_f0': preview_f0, 'has_empty_data': has_empty, 'missing': False,
                            'pitch_floor': self.params['pitch_floor'],
                            'pitch_ceiling': self.params['pitch_ceiling'],
                            'voicing_threshold': self.params.get('voicing_threshold', 0.25),
                        }
                    else:
                        self.items[iid] = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'missing': True, 'start': None, 'end': None
                        }
                    word_idx += 1
            self._after_state_change("apply_wordlist")
            self._emit(True, f"长音频已按字表处理，共 {len(flat_words)} 个词/字。", total=len(flat_words))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    def _process_batch_wordlist(self, groups, flat_words, match_mode):
        tasks = []
        if match_mode == 'fuzzy':
            import re
            def natural_sort_key(s):
                return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]
            sorted_paths = sorted(self.batch_paths, key=natural_sort_key)
            used_indices = []
            for grp in groups:
                for word in grp['items']:
                    idx = fuzzy_match_word_to_path(word, sorted_paths, used_indices)
                    if idx is not None:
                        path = sorted_paths[idx]
                        used_indices.append(idx)
                        tasks.append({'word': word, 'group': grp['group'], 'path': path, 'missing': False})
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True})
        else:
            path_idx = 0
            for grp in groups:
                for word in grp['items']:
                    if path_idx < len(self.batch_paths):
                        tasks.append({'word': word, 'group': grp['group'], 'path': self.batch_paths[path_idx], 'missing': False})
                        path_idx += 1
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True})

        futures = {}
        for i, t in enumerate(tasks):
            if not t['missing']:
                path = t['path']
                f = self.executor.submit(batch_process_worker, path, self.params, self.params['trim_silence'], t['word'])
                futures[f] = i

        results = [None] * len(tasks)
        for i, t in enumerate(tasks):
            if t['missing']:
                results[i] = {'label': t['word'], 'group': t['group'], 'missing': True}

        for future in concurrent.futures.as_completed(futures):
            orig_idx = futures[future]
            try:
                res = future.result()
                res['missing'] = False
                res['group'] = tasks[orig_idx]['group']
                res['label'] = tasks[orig_idx]['word']

                # Fix word splits if batch process used filename as label initially
                word = res['label']
                if res.get('success'):
                    syls = split_into_syllables(word)
                    if len(syls) > 1:
                        snd = parselmouth.Sound(res['path'])
                        meta = {}
                        p_data = res.get('pitch_data')
                        res['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(syls), pitch_data=p_data, output_meta=meta)
                        res['split_warnings'] = meta.get('split_warnings', [])
                        res['split_confidence'] = meta.get('split_confidence', 1.0)
                        res['chars_bounds'] = auto_split_to_chars_bounds(snd, res['start'], res['end'], res['inner_splits'], len(syls), self.params)
                    else:
                        res['split_warnings'] = []
                        res['split_confidence'] = 1.0
                results[orig_idx] = res
            except Exception as e:
                results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'error': str(e)}

        for i, res in enumerate(results):
            iid = f"item_{i}"
            if not res.get('missing') and res.get('success'):
                # Load sound and pitch data into memory for fast recalculation
                try:
                    snd = parselmouth.Sound(res['path'])
                    pitch_data = extract_f0(snd, self.params)
                    res['snd'] = snd
                    res['pitch_data'] = pitch_data
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                except Exception:
                    pass
            res['id'] = iid
            self.items[iid] = res

        self._after_state_change("apply_wordlist")
        self._emit(True, f"独立音频已按字表匹配处理，共 {len(tasks)} 个词/字。", total=len(tasks))

    def do_status(self, arg):
        """
        Show current global status.
        Usage: status
        """
        status = {
            "mode": self.mode,
            "params": self.params,
            "groups": self.groups,
            "total_items": len(self.items),
            "missing_items": sum(1 for it in self.items.values() if it.get('missing') or not it.get('success', True)),
            "warnings": sum(1 for it in self.items.values() if self._check_item_has_empty_data(it))
        }
        print(json.dumps({"success": True, "status": status}))

    def do_list_items(self, arg):
        """
        List details of items.
        Usage: list_items [all|warnings|group_name]
        """
        filter_type = arg.strip() if arg else "all"

        output = []
        for iid, item in self.items.items():
            if filter_type == 'warnings' and not self._check_item_has_empty_data(item):
                continue
            if filter_type not in ('all', 'warnings') and item.get('group') != filter_type:
                continue

            entry = {
                "id": iid,
                "label": item.get('label'),
                "group": item.get('group'),
            }
            if item.get('missing') or not item.get('success', True):
                entry['status'] = 'missing/error'
            else:
                entry['status'] = 'ok'
                entry['start'] = item.get('start')
                entry['end'] = item.get('end')
                entry['warning'] = self._check_item_has_empty_data(item)
            output.append(entry)

        print(json.dumps({"success": True, "items": output}))

    def do_modify_bounds(self, arg):
        """
        Modify time boundaries of an item manually. This overrides automatic VAD.
        Usage: modify_bounds <item_id> <start> <end>
        Example: modify_bounds item_0 1.25 1.85
        """
        args = shlex.split(arg)
        if len(args) != 3:
            print('{"success": False, "error": "Requires item_id, start, and end"}')
            return

        iid = args[0]
        try:
            new_s = float(args[1])
            new_e = float(args[2])
        except ValueError:
            print('{"success": False, "error": "start and end must be floats"}')
            return

        if iid not in self.items:
            print(json.dumps({"success": False, "error": f"Item {iid} not found"}))
            return

        item = self.items[iid]
        if 'snd' not in item or item['snd'] is None:
            print('{"success": False, "error": "Item has no loaded audio data"}')
            return

        item['start'] = new_s
        item['end'] = new_e
        item['raw_start'] = new_s
        item['raw_end'] = new_e

        # Re-split inner words if needed
        label = item['label']
        syls = split_into_syllables(label)
        if len(syls) > 1:
            meta = {}
            p_data = item.get('pitch_data')
            item['inner_splits'] = auto_split_inner_word(item['snd'], new_s, new_e, len(syls), pitch_data=p_data, output_meta=meta)
            item['split_warnings'] = meta.get('split_warnings', [])
            item['split_confidence'] = meta.get('split_confidence', 1.0)
            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], new_s, new_e, item['inner_splits'], len(syls), self.params)
        else:
            item['inner_splits'] = []
            item['split_warnings'] = []
            item['split_confidence'] = 1.0
            item['chars_bounds'] = [[new_s, new_e]]

        # Re-evaluate warnings
        if item.get('pitch_data') or item.get('pitch'):
            preview_times = np.linspace(new_s, new_e, 11)
            if item.get('pitch_data'):
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()
                for j, t in enumerate(preview_times):
                    valid_indices = np.where(p_freqs > 0)[0]
                    if len(valid_indices) == 0:
                        preview_f0[j] = 0.0
                        continue
                    valid_xs = p_xs[valid_indices]
                    if np.min(np.abs(valid_xs - t)) > 0.025:
                        preview_f0[j] = 0.0
            else:
                preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                preview_f0 = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
            item['preview_f0'] = preview_f0
            item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

        self._after_state_change("modify_bounds")
        self._emit(True, f"{iid} 的边界已更新。", item_id=iid, warning=item.get('has_empty_data', False))

    def do_modify_params(self, arg):
        """
        Modify analysis parameters of a specific item manually. This overrides global parameters.
        Usage: modify_params <item_id> key=value [key=value ...]
        Example: modify_params item_0 pitch_floor=30 voicing_threshold=0.20
        """
        args = shlex.split(arg)
        if len(args) < 2:
            print('{"success": False, "error": "Requires item_id and key=value pairs"}')
            return

        iid = args[0]
        if iid not in self.items:
            print(json.dumps({"success": False, "error": f"Item {iid} not found"}))
            return

        item = self.items[iid]
        if item.get('missing') or not item.get('success', True):
            print('{"success": False, "error": "Item has no loaded audio data"}')
            return

        # Initialize defaults if not present
        if 'pitch_floor' not in item:
            item['pitch_floor'] = self.params['pitch_floor']
            item['pitch_ceiling'] = self.params['pitch_ceiling']
            item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

        updated = False
        for kv in args[1:]:
            if '=' in kv:
                k, v = kv.split('=', 1)
                if k in ('pitch_floor', 'pitch_ceiling', 'voicing_threshold'):
                    try:
                        if k in ('pitch_floor', 'pitch_ceiling'):
                            item[k] = int(v)
                        else:
                            item[k] = float(v)
                        updated = True
                    except ValueError:
                        print(json.dumps({"success": False, "error": f"Invalid value for {k}"}))
                        return

        if updated and 'snd' in item and item['snd'] is not None:
            # Recompute pitch for this item
            try:
                item['pitch_data'] = extract_f0(item['snd'], {
                    'pitch_floor': item['pitch_floor'],
                    'pitch_ceiling': item['pitch_ceiling'],
                    'voicing_threshold': item['voicing_threshold']
                })

                # Recompute chars bounds with new params if word mode
                label = item['label']
                syls = split_into_syllables(label)
                if len(syls) > 1:
                    meta = {}
                    p_data = item.get('pitch_data')
                    item['inner_splits'] = auto_split_inner_word(item['snd'], item['start'], item['end'], len(syls), pitch_data=p_data, output_meta=meta)
                    item['split_warnings'] = meta.get('split_warnings', [])
                    item['split_confidence'] = meta.get('split_confidence', 1.0)
                    item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], item['start'], item['end'], item['inner_splits'], len(syls), item)
                else:
                    item['inner_splits'] = []
                    item['split_warnings'] = []
                    item['split_confidence'] = 1.0
                    item['chars_bounds'] = [[item['start'], item['end']]]

                # Re-evaluate warnings
                preview_times = np.linspace(item['start'], item['end'], 11)
                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()
                for j, t in enumerate(preview_times):
                    valid_indices = np.where(p_freqs > 0)[0]
                    if len(valid_indices) == 0:
                        preview_f0[j] = 0.0
                        continue
                    valid_xs = p_xs[valid_indices]
                    if np.min(np.abs(valid_xs - t)) > 0.025:
                        preview_f0[j] = 0.0
                item['preview_f0'] = preview_f0
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))
                return

        self._after_state_change("modify_params")
        print(json.dumps({
            "success": True,
            "message": f"{iid} 的专属参数已更新。",
            "item_params": {
                "pitch_floor": item.get('pitch_floor'),
                "pitch_ceiling": item.get('pitch_ceiling'),
                "voicing_threshold": item.get('voicing_threshold'),
            },
            "warning": item.get('has_empty_data', False),
            "split_warnings": item.get('split_warnings', []),
            "split_confidence": item.get('split_confidence', 1.0)
        }))

    def do_recalculate(self, arg):
        """
        Recalculate bounds and pitches for all items based on current params.
        Usage: recalculate
        """
        print('{"status": "processing", "message": "Recalculating... this may take a moment."}')

        if self.mode == 'long' and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']

            for iid, item in self.items.items():
                if item.get('missing') or not item.get('success', True):
                    continue

                item['pitch_data'] = pitch_data
                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                    item['snd'], item['pitch_data'], item['macro_start'], item['macro_end'],
                    self.params['db'], self.params['skip_front'], self.params['trim_silence']
                )

                label = item['label']
                syls = split_into_syllables(label)
                if len(syls) > 1:
                    meta = {}
                    item['inner_splits'] = auto_split_inner_word(
                        item['snd'], raw_s, raw_e, len(syls),
                        pitch_data=pitch_data, output_meta=meta
                    )
                    item['split_warnings'] = meta.get('split_warnings', [])
                    item['split_confidence'] = meta.get('split_confidence', 1.0)
                    item['chars_bounds'] = auto_split_to_chars_bounds(
                        item['snd'], raw_s, raw_e, item['inner_splits'], len(syls), self.params
                    )
                    if item['chars_bounds']:
                        mic_s = item['chars_bounds'][0][0]
                        mic_e = item['chars_bounds'][-1][1]
                else:
                    item['inner_splits'] = []
                    item['split_warnings'] = []
                    item['split_confidence'] = 1.0
                    item['chars_bounds'] = [[mic_s, mic_e]]

                item['start'], item['end'] = mic_s, mic_e
                item['raw_start'], item['raw_end'] = raw_s, raw_e

                preview_times = np.linspace(mic_s, mic_e, 11)
                preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
                for j, t in enumerate(preview_times):
                    valid_indices = np.where(pitch_freqs > 0)[0]
                    if len(valid_indices) == 0:
                        preview_f0[j] = 0.0
                        continue
                    valid_xs = pitch_xs[valid_indices]
                    if np.min(np.abs(valid_xs - t)) > 0.025:
                        preview_f0[j] = 0.0
                item['preview_f0'] = preview_f0
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

                if self.params.get('analysis_mode') == 'formant':
                    try:
                        from modules.audio_core import extract_formants
                        total_dur = item['snd'].get_total_duration()
                        if 'macro_start' in item and 'macro_end' in item and total_dur > 15.0:
                            padding = 1.0
                            seg_start = max(0.0, item['macro_start'] - padding)
                            seg_end = min(total_dur, item['macro_end'] + padding)
                            part_snd = item['snd'].extract_part(from_time=seg_start, to_time=seg_end)
                            part_formant_data = extract_formants(part_snd, self.params)
                            part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                            item['formant_data'] = part_formant_data
                        else:
                            item['formant_data'] = extract_formants(item['snd'], self.params)
                    except Exception:
                        pass

        elif self.mode == 'batch':
            tasks = []
            iids = []
            for iid, item in self.items.items():
                if item.get('missing') or not item.get('success', True):
                    continue
                tasks.append({'path': item['path'], 'label': item.get('label', '')})
                iids.append(iid)

            futures = {}
            for i, t in enumerate(tasks):
                f = self.executor.submit(
                    batch_process_worker,
                    t['path'], self.params, self.params['trim_silence'], t['label']
                )
                futures[f] = i

            for future in concurrent.futures.as_completed(futures):
                orig_idx = futures[future]
                iid = iids[orig_idx]
                try:
                    res = future.result()
                    if not res.get('success'):
                        continue

                    item = self.items[iid]
                    item['start'] = res['start']
                    item['end'] = res['end']
                    item['raw_start'] = res['raw_start']
                    item['raw_end'] = res['raw_end']
                    item['inner_splits'] = res.get('inner_splits', [])
                    item['chars_bounds'] = res.get('chars_bounds', [])
                    item['split_warnings'] = res.get('split_warnings', [])
                    item['split_confidence'] = res.get('split_confidence', 1.0)
                    item['preview_f0'] = res.get('preview_f0', [])
                    item['has_empty_data'] = res.get('has_empty_data', False)

                    item['snd'] = parselmouth.Sound(res['path'])
                    item['pitch_data'] = extract_f0(item['snd'], self.params)
                    item['pitch_floor'] = self.params['pitch_floor']
                    item['pitch_ceiling'] = self.params['pitch_ceiling']
                    item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                    if self.params.get('analysis_mode') == 'formant':
                        try:
                            from modules.audio_core import extract_formants
                            item['formant_data'] = extract_formants(item['snd'], self.params)
                        except Exception:
                            pass
                except Exception:
                    pass

        self._after_state_change("recalculate")
        print('{"success": True, "message": "重算完成。"}')

    def _recompute_pitch_only_all_items(self):
        """
        Recompute pitch-related data only, preserving existing boundaries.
        Returns a tuple: (updated_count, skipped_count)
        """
        updated = 0
        skipped = 0

        if self.mode == 'long' and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']

            for item in self.items.values():
                if item.get('missing') or not item.get('success', True):
                    skipped += 1
                    continue
                if not item.get('snd'):
                    skipped += 1
                    continue

                item['pitch_data'] = pitch_data
                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                start = item.get('start')
                end = item.get('end')
                if start is None or end is None or end <= start:
                    item['preview_f0'] = []
                    item['has_empty_data'] = False
                    updated += 1
                    continue

                preview_times = np.linspace(start, end, 11)
                preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
                for j, t in enumerate(preview_times):
                    valid_indices = np.where(pitch_freqs > 0)[0]
                    if len(valid_indices) == 0:
                        preview_f0[j] = 0.0
                        continue
                    valid_xs = pitch_xs[valid_indices]
                    if np.min(np.abs(valid_xs - t)) > 0.025:
                        preview_f0[j] = 0.0
                item['preview_f0'] = preview_f0
                item['has_empty_data'] = any(f == 0.0 for f in preview_f0)
                updated += 1
        else:
            for item in self.items.values():
                if item.get('missing') or not item.get('success', True):
                    skipped += 1
                    continue

                snd = item.get('snd')
                if snd is None and item.get('path'):
                    try:
                        snd = parselmouth.Sound(item['path'])
                        item['snd'] = snd
                    except Exception:
                        skipped += 1
                        continue
                if snd is None:
                    skipped += 1
                    continue

                try:
                    item['pitch_data'] = extract_f0(snd, self.params)
                except Exception:
                    skipped += 1
                    continue

                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                start = item.get('start')
                end = item.get('end')
                if start is None or end is None or end <= start:
                    item['preview_f0'] = []
                    item['has_empty_data'] = False
                    updated += 1
                    continue

                p_xs = item['pitch_data']['xs']
                p_freqs = item['pitch_data']['freqs']
                preview_times = np.linspace(start, end, 11)
                preview_f0 = np.interp(preview_times, p_xs, p_freqs).tolist()
                for j, t in enumerate(preview_times):
                    valid_indices = np.where(p_freqs > 0)[0]
                    if len(valid_indices) == 0:
                        preview_f0[j] = 0.0
                        continue
                    valid_xs = p_xs[valid_indices]
                    if np.min(np.abs(valid_xs - t)) > 0.025:
                        preview_f0[j] = 0.0
                item['preview_f0'] = preview_f0
                item['has_empty_data'] = any(f == 0.0 for f in preview_f0)
                updated += 1

        return updated, skipped

    def extract_stable_f0_values(self, xs, freqs):
        if len(xs) < 2:
            return []

        dt = xs[1] - xs[0]
        if dt <= 0:
            dt = 0.010

        # 1. 查找连续的有声帧 (freq > 0)
        voiced_runs = []
        current_run = []
        for i in range(len(freqs)):
            if freqs[i] > 0:
                current_run.append((xs[i], freqs[i]))
            else:
                if current_run:
                    voiced_runs.append(current_run)
                    current_run = []
        if current_run:
            voiced_runs.append(current_run)

        stable_values = []
        for run in voiced_runs:
            if len(run) < 2:
                continue

            # 2. 对每个有声片段，如果相邻帧 of F0 突变过大（相对跳变 > 20%），在跳变处切断
            sub_runs = []
            current_sub = [run[0]]
            for i in range(1, len(run)):
                f_prev = run[i-1][1]
                f_curr = run[i][1]
                if abs(f_curr - f_prev) / f_prev > 0.20:
                    if current_sub:
                        sub_runs.append(current_sub)
                    current_sub = [run[i]]
                else:
                    current_sub.append(run[i])
            if current_sub:
                sub_runs.append(current_sub)

            # 3. 对子片段进行时间筛选和边界裁剪
            for sub in sub_runs:
                sub_duration = len(sub) * dt
                if sub_duration < 0.10:
                    continue

                # 边界剔除：从首尾各剔除 30ms 的数据，以消除发音边界过渡带来的基频不稳/追踪错误
                trim_frames = int(round(0.030 / dt))
                if trim_frames < 1:
                    trim_frames = 1

                if len(sub) > 2 * trim_frames:
                    trimmed_sub = sub[trim_frames:-trim_frames]
                    for item in trimmed_sub:
                        stable_values.append(item[1])
        return stable_values

    def do_detect_f0(self, arg):
        """
        Estimate speaker F0 distribution and suggest floor/ceiling ranges.
        Usage: detect_f0 [apply_preset]
        Presets: conservative, recommended, fine
        Example: detect_f0
        Example: detect_f0 recommended
        """
        if not self.items:
            print(json.dumps({"success": False, "error": "No items loaded. Please load audio files first."}))
            return

        import os
        import numpy as np
        import parselmouth
        from modules.audio_core import extract_f0

        # We need to compute stable F0 using 50-700 Hz temporary range
        params_temp = {
            'pitch_floor': 50,
            'pitch_ceiling': 700,
            'voicing_threshold': self.params.get('voicing_threshold', 0.25)
        }

        all_stable_f0 = []

        # Determine mode: long or batch
        if self.mode == 'long':
            snd = self.long_snd
            if snd is None and self.long_snd_path and os.path.exists(self.long_snd_path):
                try:
                    snd = parselmouth.Sound(self.long_snd_path)
                    self.long_snd = snd
                except Exception:
                    pass
            if snd is None:
                print(json.dumps({"success": False, "error": "Active long audio file not loaded or not found"}))
                return

            pitch_data = extract_f0(snd, params_temp)
            times = pitch_data['xs']
            freqs = pitch_data['freqs']

            for item in self.items.values():
                macro_start = item.get('macro_start')
                macro_end = item.get('macro_end')
                # Ignore placeholder/missing items
                if macro_start is None or macro_end is None:
                    continue
                mask = (times >= macro_start) & (times <= macro_end)
                item_times = times[mask]
                item_freqs = freqs[mask]
                stable_f0 = self.extract_stable_f0_values(item_times, item_freqs)
                all_stable_f0.extend(stable_f0)

        else: # batch mode
            valid_items = [it for it in self.items.values() if it.get('snd') or (it.get('path') and os.path.exists(it['path']))]
            if not valid_items:
                print(json.dumps({"success": False, "error": "No valid independent audio files found in project"}))
                return

            for item in valid_items:
                item_snd = item.get('snd')
                if item_snd is None:
                    try:
                        item_snd = parselmouth.Sound(item['path'])
                    except Exception:
                        continue
                try:
                    pitch_data = extract_f0(item_snd, params_temp)
                    stable_f0 = self.extract_stable_f0_values(pitch_data['xs'], pitch_data['freqs'])
                    all_stable_f0.extend(stable_f0)
                except Exception:
                    continue

        if len(all_stable_f0) < 50:
            print(json.dumps({
                "success": False,
                "error": "Too little voiced speech data (< 0.5s) to reliably estimate F0. Please import more audio or ensure there is stable speech."
            }))
            return

        p5 = float(np.percentile(all_stable_f0, 5))
        p10 = float(np.percentile(all_stable_f0, 10))
        p50 = float(np.percentile(all_stable_f0, 50))
        p90 = float(np.percentile(all_stable_f0, 90))
        p95 = float(np.percentile(all_stable_f0, 95))

        # Weight interpolation based on median
        med = p50
        w = max(0.0, min(1.0, (med - 120.0) / 120.0))

        # Coefficients mapping male to female
        mult_cons_floor = 0.66 * (1.0 - w) + 0.58 * w
        mult_cons_ceil = 1.94 * (1.0 - w) + 1.61 * w

        mult_reco_floor = 0.78 * (1.0 - w) + 0.76 * w
        mult_reco_ceil = 1.67 * (1.0 - w) + 1.45 * w

        mult_fine_floor = 0.89 * (1.0 - w) + 0.88 * w
        mult_fine_ceil = 1.44 * (1.0 - w) + 1.35 * w

        def round_to_nearest(val, base):
            return int(round(val / base) * base)

        cons_floor = max(40, round_to_nearest(p5 * mult_cons_floor, 5))
        cons_ceil = min(1000, round_to_nearest(p95 * mult_cons_ceil, 10))

        reco_floor = max(40, round_to_nearest(p5 * mult_reco_floor, 5))
        reco_ceil = min(1000, round_to_nearest(p95 * mult_reco_ceil, 10))

        fine_floor = max(40, round_to_nearest(p5 * mult_fine_floor, 5))
        fine_ceil = min(1000, round_to_nearest(p95 * mult_fine_ceil, 10))

        voiced_duration = len(all_stable_f0) * 0.01

        presets = {
            "conservative": (cons_floor, cons_ceil),
            "recommended": (reco_floor, reco_ceil),
            "fine": (fine_floor, fine_ceil)
        }

        preset_arg = arg.strip().lower()
        applied = False
        applied_msg = ""
        if preset_arg:
            if preset_arg in presets:
                floor, ceiling = presets[preset_arg]
                self.params['pitch_floor'] = floor
                self.params['pitch_ceiling'] = ceiling
                applied = True
                updated_count, skipped_count = self._recompute_pitch_only_all_items()
                applied_msg = (
                    f"Applied '{preset_arg}' preset: pitch_floor={floor}, pitch_ceiling={ceiling}. "
                    f"Pitch-only refresh completed (updated={updated_count}, skipped={skipped_count}); boundaries preserved."
                )
                self._after_state_change("set_params")
            else:
                print(json.dumps({
                    "success": False,
                    "error": f"Invalid preset '{preset_arg}'. Choose from: conservative, recommended, fine"
                }))
                return

        response = {
            "success": True,
            "metrics": {
                "voiced_duration_s": voiced_duration,
                "stable_frames_count": len(all_stable_f0),
                "p5": p5,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "p95": p95
            },
            "suggestions": {
                "conservative": {"floor": cons_floor, "ceiling": cons_ceil},
                "recommended": {"floor": reco_floor, "ceiling": reco_ceil},
                "fine": {"floor": fine_floor, "ceiling": fine_ceil}
            },
            "applied": applied,
            "message": applied_msg if applied else f"F0 distribution estimated. Detected main distribution: {int(p5)}~{int(p95)} Hz."
        }
        print(json.dumps(response))

    def do_export(self, arg):
        """
        Export data to various formats.
        Usage: export <format> <output_file> [rule] [target] [key=val ...]
        Advanced charts: export <chart_type> <output_file_or_dir> [target] [key=val ...]
        Formats: txt, xlsx, line_chart, kde, wav, merged_wav, textgrid, contour, distribution, density, quality, overview_heatmap,
                 formant_table, formant_space, formant_trajectory, formant_density, formant_overview_heatmap
        Rule: continuous (default) or per_group (For 'wav' and 'merged_wav', rule can also be buffer_sec or gap_sec like 0.5)
        Target (Multi-speaker): active (default), separate (multiple files per speaker), integrated (merged T-value calculation)
        Example: export xlsx output.xlsx continuous integrated
        Example: export wav scratch/output_dir 0.1 separate
        Example: export contour output.svg integrated scale=hz groupby=label facet=group
        Example: export density output.png integrated normalization=speaker facet=group
        """
        args = shlex.split(arg)
        if len(args) < 2:
            print('{"success": False, "error": "Requires format and output_file"}')
            return

        fmt = args[0]
        out_file = args[1]

        # Scientific/Advanced Visualization Toolbox integration
        scientific_charts = {'contour', 'distribution', 'density', 'quality', 'overview_heatmap', 'formant_space', 'formant_trajectory', 'formant_density', 'formant_overview_heatmap'}
        if fmt in scientific_charts:
            # Parse parameters:
            # Positional arguments: target
            # Key-value arguments: key=value
            params = {
                'chart_type': fmt,
                'export_scope': 'active',  # default target
            }

            # Gather positional args and key-value pairs
            pos_args = []
            for item in args[2:]:
                if '=' in item:
                    k, v = item.split('=', 1)
                    k = k.strip().lower()
                    v = v.strip()
                    # Strip surrounding quotes if present
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    # Map some standard aliases for ease of use
                    if k == 'target':
                        k = 'export_scope'
                    elif k == 'facet':
                        if fmt == 'density':
                            k = 'density_facet'
                        elif fmt == 'contour':
                            k = 'contour_facet'
                    params[k] = v
                else:
                    pos_args.append(item)

            # Accept both the advanced syntax and the generic export syntax:
            # `export contour out.png integrated` and `export contour out.png continuous integrated`.
            for pos_arg in pos_args:
                scope_candidate = pos_arg.lower()
                if scope_candidate in ('active', 'separate', 'integrated'):
                    params['export_scope'] = scope_candidate
                    break

            scope = params.get('export_scope', 'active')

            # Let's handle parameter values translation or casting (like density_bw to float, etc.)
            float_params = {'density_bw', 'density_p_low', 'density_p_high', 'density_m_min', 'density_m_max'}
            for fp in float_params:
                if fp in params:
                    try:
                        params[fp] = float(params[fp])
                    except ValueError:
                        pass

            # Resolve file extension
            if scope == 'separate':
                ext = "." + params.get('format', 'png').lower()
            else:
                _, ext = os.path.splitext(out_file)
                if not ext:
                    # Fallback based on format param or default to .png
                    ext_param = params.get('format', 'png').lower()
                    ext = f".{ext_param}"
                    out_file = out_file + ext
                else:
                    params['format'] = ext[1:]

            # Instantiate adapter
            adapter = AcousticChartCLIAdapter(self)

            # Instantiate AcousticChartExporter
            all_speakers = self.speaker_manager.get_all_speakers()
            exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=all_speakers)
            exporter.params = params

            # Check if there are speakers and if they have data
            speakers_to_process = [self.current_speaker] if scope == 'active' else all_speakers
            if not any(len(s.items) > 0 for s in speakers_to_process):
                print('{"success": False, "error": "No items to export"}')
                return

            old_mode = self.params.get('analysis_mode')
            try:
                if fmt.startswith('formant_'):
                    self.params['analysis_mode'] = 'formant'
                    params['analysis_mode'] = 'formant'

                if scope == 'separate':
                    os.makedirs(out_file, exist_ok=True)
                    for speaker in all_speakers:
                        data = exporter._extract_active_data([speaker])
                        if data:
                            out_path = os.path.join(out_file, f"{speaker.name}_{fmt}{ext}")
                            exporter._export_dataset(data, out_path, ext)
                    print(json.dumps({"success": True, "message": f"Exported {fmt} for multiple speakers separately to {out_file}"}))
                else:
                    # active or integrated
                    data = exporter._get_current_data_entries()
                    if not data:
                        print('{"success": False, "error": "No valid data found for export"}')
                        return
                    exporter._export_dataset(data, out_file, ext)
                    print(json.dumps({"success": True, "message": f"Exported {fmt} ({scope}) to {out_file}"}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))
            finally:
                if old_mode is not None:
                    self.params['analysis_mode'] = old_mode
                elif 'analysis_mode' in self.params:
                    del self.params['analysis_mode']
            return

        rule = args[2] if len(args) > 2 else 'continuous'
        target = args[3] if len(args) > 3 else 'active'

        # Structure setup based on target
        # For 'active', we just use current speaker's items and groups.
        # For 'separate' or 'integrated', we need all speakers.
        speakers_to_process = [self.current_speaker] if target == 'active' else self.speaker_manager.get_all_speakers()

        if not any(len(s.items) > 0 for s in speakers_to_process):
            print('{"success": False, "error": "No items to export"}')
            return

        try:
            if fmt == 'formant_table':
                self._export_formant_table(out_file, speakers_to_process)
                print(json.dumps({"success": True, "message": f"Exported formant_table to {out_file}"}))
                return
            elif fmt == 'formant_space':
                self._export_vowel_space_chart(out_file, speakers_to_process)
                print(json.dumps({"success": True, "message": f"Exported formant_space to {out_file}"}))
                return

            if target == 'separate':
                # Export individually to out_file (treated as directory)
                os.makedirs(out_file, exist_ok=True)
                for s in speakers_to_process:
                    s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in getattr(s, 'cli_groups', [])]
                    s_out = os.path.join(out_file, f"{s.name}_{fmt}")

                    if fmt == 'txt':
                        self._export_txt(f"{s_out}.txt", s_struct, rule, s)
                    elif fmt == 'xlsx':
                        self._export_xlsx(f"{s_out}.xlsx", s_struct, rule, s)
                    elif fmt == 'line_chart':
                        self._export_line_chart(f"{s_out}.png", s_struct, s)
                    elif fmt == 'kde':
                        self._export_kde_heatmap(f"{s_out}.png", s_struct, s)
                    elif fmt == 'wav':
                        self._export_wav(s_out, s_struct, rule, s)
                    elif fmt == 'merged_wav':
                        self._export_merged_wav(f"{s_out}.wav", s_struct, rule, s)
                    elif fmt == 'textgrid':
                        if s.tab_mode == '多条独立音频':
                            self._export_textgrid(s_out, s_struct, s)
                        else:
                            self._export_textgrid(f"{s_out}.TextGrid", s_struct, s)
                    else:
                        print(f'{{"success": False, "error": "Unknown format: {fmt}"}}')
                        return
                print(json.dumps({"success": True, "message": f"Exported {fmt} for multiple speakers to {out_file}"}))

            elif target == 'integrated':
                if fmt not in ('txt', 'xlsx', 'line_chart', 'kde'):
                    print(f'{{"success": False, "error": "Format {fmt} does not support integrated multi-speaker export"}}')
                    return
                if fmt == 'txt':
                    self._export_txt_integrated(out_file, speakers_to_process, rule)
                elif fmt == 'xlsx':
                    self._export_xlsx_integrated(out_file, speakers_to_process, rule)
                elif fmt == 'line_chart':
                    self._export_line_chart_integrated(out_file, speakers_to_process)
                elif fmt == 'kde':
                    self._export_kde_heatmap_integrated(out_file, speakers_to_process)
                print(json.dumps({"success": True, "message": f"Exported integrated {fmt} to {out_file}"}))

            else: # active
                s = self.current_speaker
                structure = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in self.groups]
                if fmt == 'txt':
                    self._export_txt(out_file, structure, rule, s)
                elif fmt == 'xlsx':
                    self._export_xlsx(out_file, structure, rule, s)
                elif fmt == 'line_chart':
                    self._export_line_chart(out_file, structure, s)
                elif fmt == 'kde':
                    self._export_kde_heatmap(out_file, structure, s)
                elif fmt == 'wav':
                    self._export_wav(out_file, structure, rule, s)
                elif fmt == 'merged_wav':
                    self._export_merged_wav(out_file, structure, rule, s)
                elif fmt == 'textgrid':
                    self._export_textgrid(out_file, structure, s)
                else:
                    print(f'{{"success": False, "error": "Unknown format: {fmt}"}}')
                    return
                print(json.dumps({"success": True, "message": f"Exported to {out_file}"}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    # --- Export Implementation Helpers (Abstracted from GUI) ---
    def _export_merged_wav(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import numpy as np

        if ('batch' if '多条' in speaker.tab_mode else 'long') != 'batch':
            raise Exception("merged_wav export is only supported for batch audio mode")

        gap_sec = 0.5
        try:
            if rule and rule not in ('continuous', 'per_group'):
                gap_sec = float(rule)
        except ValueError:
            pass

        target_sr = 44100
        all_vals = []
        gap_samples = int(target_sr * gap_sec)
        gap_array = np.zeros(gap_samples)

        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('missing') or not item.get('success', True) or not item.get('path'):
                    continue

                try:
                    snd = parselmouth.Sound(item['path'])
                    if snd.sampling_frequency != target_sr:
                        snd = snd.resample(target_sr)
                    all_vals.append(snd.values[0])
                    all_vals.append(gap_array)
                except Exception:
                    continue

        if not all_vals:
            raise Exception("No valid audio files found to merge")

        merged_vals = np.concatenate(all_vals[:-1])
        merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
        merged_snd.save(out_file, "WAV")

    def _export_wav(self, out_dir, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import re
        if ('batch' if '多条' in speaker.tab_mode else 'long') != 'long' or not speaker.pending_long_snd:
            raise Exception("WAV export is only supported for long audio mode")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        buffer_sec = 0.1
        try:
            if rule and rule not in ('continuous', 'per_group'):
                buffer_sec = float(rule)
        except ValueError:
            pass

        snd = speaker.pending_long_snd
        do_trim = speaker.last_params.get('trim_silence', True)

        global_idx = 1
        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('missing') or not item.get('success', True): continue
                if 'macro_start' not in item or 'macro_end' not in item: continue

                s, e = item['macro_start'], item['macro_end']
                word = item['label']

                if do_trim:
                    part = snd.extract_part(from_time=s, to_time=e)
                    vals = part.values[0]
                    xs = part.xs()
                    threshold = 10 ** (-50 / 20)
                    valid_idx = np.where(np.abs(vals) > threshold)[0]
                    if len(valid_idx) > 0:
                        s = s + xs[valid_idx[0]]
                        e = s + xs[valid_idx[-1]]

                s = max(0, s - buffer_sec)
                e = min(snd.get_total_duration(), e + buffer_sec)

                if e > s:
                    extract = snd.extract_part(from_time=s, to_time=e)
                    safe_word = re.sub(r'[\\/*?:"<>|]', "", word)
                    out_file = os.path.join(out_dir, f"{str(global_idx).zfill(3)}_{safe_word}.wav")
                    extract.save(out_file, "WAV")
                    global_idx += 1

    def _export_txt(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        is_continuous = (rule == "continuous")
        with open(out_file, "w", encoding="utf-8-sig") as f:
            global_idx = 1
            for grp_name, children in structure:
                if not is_continuous: global_idx = 1
                if grp_name and grp_name.strip() and grp_name not in ("未分组", "导入内容"):
                    f.write(f"{grp_name}\n")
                for child in children:
                    item = speaker.items[child]
                    if item.get('start') is not None:
                        txt_data = get_export_text_for_item(item, global_idx, speaker.last_params['pts'], pitch_floor=speaker.last_params['pitch_floor'], pitch_ceiling=speaker.last_params['pitch_ceiling'], voicing_threshold=speaker.last_params.get('voicing_threshold', 0.25))
                        f.write(txt_data)
                        global_idx += 1

    def _ensure_item_loaded(self, item):
        if not item: return
        has_snd = item.get('snd') is not None
        has_pitch = (item.get('pitch') is not None) or (item.get('pitch_data') is not None)

        if not has_snd and item.get('path'):
            try:
                import parselmouth
                item['snd'] = parselmouth.Sound(item['path'])
                has_snd = True
            except Exception:
                pass

        if not has_snd:
            return

        if not has_pitch:
            try:
                from modules.audio_core import extract_f0
                pf = item.get('pitch_floor', self.params.get('pitch_floor', 75))
                pc = item.get('pitch_ceiling', self.params.get('pitch_ceiling', 600))
                vt = item.get('voicing_threshold', self.params.get('voicing_threshold', 0.25))
                item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=pf, pitch_ceiling=pc, voicing_threshold=vt, very_accurate=True, octave_jump_cost=0.9)
            except Exception:
                pass

        if self.params.get('analysis_mode') == 'formant' and not item.get('formant_data'):
            try:
                from modules.audio_core import extract_formants
                total_dur = item['snd'].get_total_duration()
                if 'macro_start' in item and 'macro_end' in item and total_dur > 15.0:
                    padding = 1.0
                    seg_start = max(0.0, item['macro_start'] - padding)
                    seg_end = min(total_dur, item['macro_end'] + padding)
                    part_snd = item['snd'].extract_part(from_time=seg_start, to_time=seg_end)
                    part_formant_data = extract_formants(part_snd, self.params)
                    part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                    item['formant_data'] = part_formant_data
                else:
                    item['formant_data'] = extract_formants(item['snd'], self.params)
            except Exception:
                pass

    def _extract_syl_data(self, item, num_points):
        if item.get('start') is None or not item.get('snd'): return 0, []

        pitch_data = item.get('pitch_data')
        if pitch_data:
            p_xs = pitch_data['xs']
            p_freqs = pitch_data['freqs']
        else:
            pitch = item.get('pitch')
            if not pitch: return 0, []
            p_xs = pitch.xs()
            p_freqs = pitch.selected_array['frequency']

        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []

        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])

        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(label) > 1 and len(splits) != len(label) + 1:
            splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
        elif len(label) <= 1:
            splits = [t_s, t_e]

        syl_data = []
        for i in range(len(splits) - 1):
            c_s, c_e = splits[i], splits[i+1]
            if c_e <= c_s:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                seg_xs = p_xs[valid_idx]
                seg_ys = p_freqs[valid_idx]
            else:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            dur = v_e - v_s
            if dur <= 0:
                syl_data.append((0.0, [0.0]*num_points))
                continue

            times = np.linspace(v_s, v_e, num_points)
            if len(seg_xs) >= 2:
                f0s = np.interp(times, seg_xs, seg_ys).tolist()
                for j, t in enumerate(times):
                    if np.min(np.abs(seg_xs - t)) > 0.025:
                        f0s[j] = 0.0
                syl_data.append((dur, f0s))
            else:
                syl_data.append((dur, [0.0]*num_points))

        return t_e - t_s, syl_data

    def _export_xlsx(self, out_file, structure, rule, speaker=None):
        speaker = speaker or self.current_speaker
        import xlsxwriter
        import numpy as np
        is_continuous = (rule == "continuous")
        num_points = speaker.last_params['pts']

        max_syls = 1
        for grp_name, children in structure:
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")

        headers = ["组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_T{i}(Hz)")
        for col, header in enumerate(headers): ws_data.write(0, col, header)

        global_idx = 1
        row_idx = 1
        dict_data = {}

        for grp_name, children in structure:
            if not is_continuous: global_idx = 1
            for child in children:
                item = speaker.items[child]
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                row = [grp_name, global_idx, item['label'], float(f"{total_dur:.6f}")]

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'syl_dur_sums': [0.0]*max_syls, 'syl_counts': [0]*max_syls,
                        'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                        'f0_counts': [[0]*num_points for _ in range(max_syls)]
                    }

                for k in range(max_syls):
                    if k < len(syl_data):
                        dur, f0s = syl_data[k]
                        row.append(float(f"{dur:.6f}"))
                        dict_data[grp_name]['syl_dur_sums'][k] += dur
                        dict_data[grp_name]['syl_counts'][k] += 1
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                row.append(float(f"{f0:.6f}"))
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1
                            else:
                                row.append("")
                    else:
                        row.append("")
                        for _ in range(num_points): row.append("")

                for col, val in enumerate(row):
                    ws_data.write(row_idx, col, val)

                row_idx += 1
                global_idx += 1

        all_avg_hz = []
        avg_points_map = {}
        import math

        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    avg_hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(avg_hz)
                    if avg_hz > 0: all_avg_hz.append(avg_hz)
                avg_points_map[grp].append(syl_avgs)
        if not all_avg_hz:
            workbook.close()
            return

        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        # 写入分析结果 Sheet（全部使用 Excel 公式引用数据表）
        from modules.data_utils import write_analysis_sheet_with_formulas
        group_list = list(dict_data.keys())
        last_data_row = row_idx - 1  # 0-indexed
        res_row, _, _ = write_analysis_sheet_with_formulas(
            workbook, ws_res, group_list, num_points, max_syls, last_data_row
        )

        # 自动嵌入五度标调散点连线图
        try:
            build_five_point_chart(
                workbook, ws_res, dict_data, avg_points_map,
                num_points, max_syls, min_hz, max_hz,
                insert_cell=f'A{res_row + 3}',
                chart_title='各声调平均基频五度标调图（保留真实时长）'
            )
        except Exception:
            pass  # CLI 下静默忽略图表错误

        workbook.close()

    def _collect_group_avg_data(self, structure, speaker=None):
        speaker = speaker or self.current_speaker
        num_points = speaker.last_params['pts']
        max_syls = 1
        dict_data = {}
        for grp_name, children in structure:
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
                item = speaker.items[child]
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                if grp_name not in dict_data:
                    dict_data[grp_name] = { 'f0_sums': [[0.0]*num_points for _ in range(20)], 'f0_counts': [[0]*num_points for _ in range(20)] }
                for k, (dur, f0s) in enumerate(syl_data):
                    for i, f0 in enumerate(f0s):
                        if not np.isnan(f0) and f0 > 0:
                            dict_data[grp_name]['f0_sums'][k][i] += f0
                            dict_data[grp_name]['f0_counts'][k][i] += 1

        all_avg_hz = []
        avg_points_map = {}
        for grp, st in dict_data.items():
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(hz)
                    if hz > 0: all_avg_hz.append(hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz: return None, 1
        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        import math
        result = {}
        for grp, syl_avgs_list in avg_points_map.items():
            flat_t_vals = []
            for syl_avgs in syl_avgs_list:
                for h in syl_avgs:
                    if h > 0 and max_hz > min_hz and min_hz > 0:
                        flat_t_vals.append(5 * (math.log10(h) - math.log10(min_hz)) / (math.log10(max_hz) - math.log10(min_hz)))
                    else: flat_t_vals.append(None)
            result[grp] = flat_t_vals

        return result, max_syls

    def _export_line_chart(self, out_file, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import matplotlib.pyplot as plt
        data, max_syls = self._collect_group_avg_data(structure, speaker)
        if not data:
            raise Exception("No valid data for charting")

        num_points = speaker.last_params['pts']
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(6 + 4 * max_syls, 6))
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        for i, (name, t_vals) in enumerate(data.items()):
            color = colors[i % len(colors)]
            label_added = False
            for k in range(max_syls):
                s_start = k * num_points
                s_end = (k + 1) * num_points
                s_t_vals = t_vals[s_start:s_end]
                s_x_vals = x_vals[s_start:s_end]
                
                valid_x = [x for x, v in zip(s_x_vals, s_t_vals) if v is not None]
                valid_y = [v for v in s_t_vals if v is not None]
                if valid_x:
                    lbl = name if not label_added else None
                    ax.plot(valid_x, valid_y, '-o', color=color, linewidth=2, markersize=5, label=lbl)
                    label_added = True

        ax.set_ylim(0, 5)
        ax.set_xlim(0.5, total_points + 0.5)
        ax.set_yticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])

        ax.set_xticks(range(1, total_points + 1))
        ax.set_xticklabels([(idx % num_points) + 1 for idx in range(total_points)])

        for k in range(1, max_syls):
            div_x = k * num_points + 0.5
            ax.axvline(div_x, color='gray', linestyle='--', alpha=0.5)

        ax.set_xlabel('Points')
        ax.set_ylabel('T-Value (0-5)')
        ax.set_title('Tone Pattern')
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _export_kde_heatmap(self, out_file, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import matplotlib.pyplot as plt
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter
        from scipy.stats import gaussian_kde
        import math

        N_DENSE = 100
        group_syl_contours = {}

        max_syls = 1
        for grp_name, children in structure:
            group_syl_contours[grp_name] = {}
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        for grp_name, children in structure:
            for child in children:
                item = speaker.items[child]
                if item.get('start') is None or not item.get('snd'): continue

                pitch_data = item.get('pitch_data')
                if pitch_data:
                    p_xs = pitch_data['xs']
                    p_freqs = pitch_data['freqs']
                else:
                    pitch = item.get('pitch')
                    if not pitch: continue
                    p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']

                t_s, t_e = item['start'], item['end']
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])

                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                if len(label) <= 1: splits = [t_s, t_e]

                for k in range(len(splits) - 1):
                    c_s, c_e = splits[k], splits[k+1]
                    valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
                    if len(valid_idx) >= 2:
                        v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                        mask = (p_xs >= v_s) & (p_xs <= v_e) & (p_freqs > 0)
                        valid_freqs = p_freqs[mask]
                        if len(valid_freqs) < 3: continue

                        win = len(valid_freqs) // 3
                        if win % 2 == 0: win += 1
                        win = max(win, 3)
                        smoothed = savgol_filter(valid_freqs, win, 2) if len(valid_freqs) > win else valid_freqs

                        x_orig = np.linspace(0, 1, len(smoothed))
                        f_interp = interp1d(x_orig, smoothed, kind='linear')
                        y_dense = f_interp(np.linspace(0, 1, N_DENSE))

                        if k not in group_syl_contours[grp_name]: group_syl_contours[grp_name][k] = []
                        group_syl_contours[grp_name][k].append(y_dense)

        all_mean_vals = []
        for name, syls_dict in group_syl_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    mean_contour = np.mean(y_arrays, axis=0)
                    all_mean_vals.extend(mean_contour.tolist())

        if not all_mean_vals:
            print('{"success": False, "error": "No valid data to plot"}')
            return

        min_f0, max_f0 = min(all_mean_vals), max(all_mean_vals)

        def hz_to_5_scale(hz):
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        group_norm_points = {}
        for name, syls_dict in group_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    X_all.extend(x_dense.tolist())
                    Y_all.extend([hz_to_5_scale(h) for h in y_arr])
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        groups_with_data = [g for g in getattr(speaker, 'cli_groups', []) if group_norm_points.get(g) and len(group_norm_points[g][0]) > 0]
        n_groups = len(groups_with_data)
        if n_groups == 0:
            raise Exception("No valid data for KDE Heatmap")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(groups_with_data):
            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

            positions = np.vstack([X_all, Y_all])
            try:
                kernel = gaussian_kde(positions, bw_method=0.15)
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception:
                pass

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle('KDE Heatmap', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)


    def _export_txt_integrated(self, out_file, speakers, rule):
        is_continuous = (rule == "continuous")
        with open(out_file, "w", encoding="utf-8-sig") as f:
            global_idx = 1
            for s in speakers:
                s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in getattr(s, 'cli_groups', [])]
                f.write(f"--- 发音人: {s.name} ---\n\n")
                if not is_continuous: global_idx = 1
                for grp_name, children in s_struct:
                    f.write(f"{grp_name}\n\n")
                    for child in children:
                        item = s.items[child]
                        if item.get('start') is not None:
                            from modules.data_utils import get_export_text_for_item
                            txt_data = get_export_text_for_item(item, global_idx, s.last_params['pts'], pitch_floor=s.last_params['pitch_floor'], pitch_ceiling=s.last_params['pitch_ceiling'], voicing_threshold=s.last_params.get('voicing_threshold', 0.25))
                            
                            syls = split_into_syllables(item.get('label', ''))
                            expected_sections = len(syls)
                            shown_sections = 0
                            if expected_sections > 1:
                                lines = txt_data.splitlines()
                                subsection_prefix = f"{global_idx}_"
                                single_prefix = f"{global_idx}."
                                shown_sections = sum(1 for line in lines if line.startswith(subsection_prefix))
                                if shown_sections == 0 and any(line.startswith(single_prefix) for line in lines):
                                    shown_sections = 1
                            
                            preview_mismatch = expected_sections > 1 and shown_sections == 1
                            if preview_mismatch:
                                txt_data = f"[致命] 检测到 {expected_sections} 个子段，但数据预览当前只显示 1 个。请检查该段边界或基频。\n\n{txt_data}"
                            
                            warnings = item.get('warnings', [])
                            if warnings:
                                warnings_text = "\n".join(warnings)
                                txt_data = f"{warnings_text}\n\n{txt_data}"
                            
                            f.write(txt_data + "\n\n")
                            global_idx += 1
                f.write("\n")

    def _export_xlsx_integrated(self, out_file, speakers, rule):
        import xlsxwriter
        import numpy as np
        is_continuous = (rule == "continuous")

        # Max syllables across all speakers
        max_syls = 1
        for s in speakers:
            for item in s.items.values():
                lbl = item.get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")

        # Assume uniform points from active speaker
        num_points = self.current_speaker.last_params['pts']

        headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_T{i}(Hz)")
        for col, header in enumerate(headers): ws_data.write(0, col, header)

        global_idx = 1
        row_idx = 1
        dict_data = {}
        all_groups = []

        for s in speakers:
            if not is_continuous: global_idx = 1
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in getattr(s, 'cli_groups', [])]
            for grp_name, children in s_struct:
                if grp_name not in all_groups: all_groups.append(grp_name)
                for child in children:
                    item = s.items[child]
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue

                    row = [s.name, grp_name, global_idx, item['label'], float(f"{total_dur:.6f}")]

                    if grp_name not in dict_data:
                        dict_data[grp_name] = {
                            'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                            'f0_counts': [[0]*num_points for _ in range(max_syls)]
                        }

                    for k in range(max_syls):
                        if k < len(syl_data):
                            dur, f0s = syl_data[k]
                            row.append(float(f"{dur:.6f}"))
                            for i, f0 in enumerate(f0s):
                                if not np.isnan(f0) and f0 > 0:
                                    row.append(float(f"{f0:.6f}"))
                                    dict_data[grp_name]['f0_sums'][k][i] += f0
                                    dict_data[grp_name]['f0_counts'][k][i] += 1
                                else:
                                    row.append("")
                        else:
                            row.append("")
                            for _ in range(num_points): row.append("")

                    for col, val in enumerate(row):
                        ws_data.write(row_idx, col, val)

                    row_idx += 1
                    global_idx += 1

        all_avg_hz = []
        avg_points_map = {}

        for grp in dict_data:
            st = dict_data[grp]
            avg_points_map[grp] = []
            for k in range(max_syls):
                syl_avgs = []
                for i in range(num_points):
                    cnt = st['f0_counts'][k][i]
                    avg_hz = st['f0_sums'][k][i] / cnt if cnt > 0 else 0
                    syl_avgs.append(avg_hz)
                    if avg_hz > 0: all_avg_hz.append(avg_hz)
                avg_points_map[grp].append(syl_avgs)

        if not all_avg_hz:
            workbook.close()
            return

        min_hz, max_hz = min(all_avg_hz), max(all_avg_hz)

        from modules.data_utils import write_analysis_sheet_with_formulas, build_five_point_chart
        last_data_row = row_idx - 1
        res_row, _, _ = write_analysis_sheet_with_formulas(
            workbook, ws_res, all_groups, num_points, max_syls, last_data_row, speaker_col='A'
        )

        try:
            build_five_point_chart(
                workbook, ws_res, dict_data, avg_points_map,
                num_points, max_syls, min_hz, max_hz,
                insert_cell=f'A{res_row + 3}',
                chart_title='各声调平均基频五度标调图（保留真实时长, 多发音人）'
            )
        except Exception:
            pass

        workbook.close()

    def _export_line_chart_integrated(self, out_file, speakers):
        import matplotlib.pyplot as plt
        import math

        num_points = self.current_speaker.last_params['pts']
        max_syls = 1
        combined_points_map = {}
        all_groups = []

        for s in speakers:
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in getattr(s, 'cli_groups', [])]
            data, m_syls = self._collect_group_avg_data(s_struct, speaker=s)
            if not data: continue
            if m_syls > max_syls: max_syls = m_syls

            for grp, t_vals in data.items():
                if grp not in all_groups: all_groups.append(grp)
                if grp not in combined_points_map: combined_points_map[grp] = []
                combined_points_map[grp].append(t_vals)

        if not combined_points_map:
            raise Exception("No valid data for charting across speakers")

        final_data = {}
        for grp, t_arrays in combined_points_map.items():
            valid_len = max([len(arr) for arr in t_arrays])
            avg_arr = []
            for i in range(valid_len):
                col_vals = [arr[i] for arr in t_arrays if i < len(arr) and arr[i] is not None]
                if col_vals:
                    avg_arr.append(sum(col_vals) / len(col_vals))
                else:
                    avg_arr.append(None)
            final_data[grp] = avg_arr

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(6 + 4 * max_syls, 6))
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        for i, grp in enumerate(all_groups):
            if grp not in final_data: continue
            t_vals = final_data[grp]
            color = colors[i % len(colors)]
            label_added = False
            for k in range(max_syls):
                s_start = k * num_points
                s_end = (k + 1) * num_points
                s_t_vals = t_vals[s_start:s_end]
                s_x_vals = x_vals[s_start:s_end]
                
                valid_x = [x for x, v in zip(s_x_vals, s_t_vals) if v is not None]
                valid_y = [v for v in s_t_vals if v is not None]
                if valid_x:
                    lbl = grp if not label_added else None
                    ax.plot(valid_x, valid_y, '-o', color=color, linewidth=2, markersize=5, label=lbl)
                    label_added = True

        ax.set_ylim(0, 5)
        ax.set_xlim(0.5, total_points + 0.5)
        ax.set_yticks([0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5])
        ax.set_xticks(range(1, total_points + 1))
        ax.set_xticklabels([(idx % num_points) + 1 for idx in range(total_points)])

        for k in range(1, max_syls):
            div_x = k * num_points + 0.5
            ax.axvline(div_x, color='gray', linestyle='--', alpha=0.5)

        ax.set_xlabel('Points')
        ax.set_ylabel('T-Value (0-5)')
        ax.set_title(f'Integrated Tone Pattern ({len(speakers)} Speakers)')
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _process_kde_item(self, item, N_DENSE):
        from scipy.interpolate import interp1d
        from scipy.signal import savgol_filter
        import numpy as np

        if item.get('start') is None or not item.get('snd'): return None

        pitch_data = item.get('pitch_data')
        if pitch_data:
            p_xs = pitch_data['xs']
            p_freqs = pitch_data['freqs']
        else:
            pitch = item.get('pitch')
            if not pitch: return None
            p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']

        t_s, t_e = item['start'], item['end']
        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])

        splits = [t_s] + [s_split for s_split in inner_splits if t_s < s_split < t_e] + [t_e]
        if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
        if len(label) <= 1: splits = [t_s, t_e]

        y_denses = {}
        for k in range(len(splits) - 1):
            c_s, c_e = splits[k], splits[k+1]
            valid_idx = np.where((p_xs >= c_s) & (p_xs <= c_e) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_s, v_e = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
                mask = (p_xs >= v_s) & (p_xs <= v_e) & (p_freqs > 0)
                valid_freqs = p_freqs[mask]
                if len(valid_freqs) < 3: continue

                win = len(valid_freqs) // 3
                if win % 2 == 0: win += 1
                win = max(win, 3)
                smoothed = savgol_filter(valid_freqs, win, 2) if len(valid_freqs) > win else valid_freqs

                x_orig = np.linspace(0, 1, len(smoothed))
                f_interp = interp1d(x_orig, smoothed, kind='linear')
                y_dense = f_interp(np.linspace(0, 1, N_DENSE))

                y_denses[k] = y_dense
        return y_denses

    def _get_kde_max_syls(self, speakers):
        max_syls = 1
        for s in speakers:
            for item in s.items.values():
                lbl = item.get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
        return max_syls

    def _extract_kde_heatmap_contours(self, speakers, N_DENSE):
        group_syl_contours = {}
        for s in speakers:
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp]) for grp in getattr(s, 'cli_groups', [])]
            for grp_name, children in s_struct:
                if grp_name not in group_syl_contours:
                    group_syl_contours[grp_name] = {}
                for child in children:
                    item = s.items[child]
                    y_denses = self._process_kde_item(item, N_DENSE)
                    if not y_denses: continue
                    for k, y_dense in y_denses.items():
                        if k not in group_syl_contours[grp_name]: group_syl_contours[grp_name][k] = []
                        group_syl_contours[grp_name][k].append(y_dense)
        return group_syl_contours

    def _calculate_kde_f0_bounds(self, group_syl_contours):
        import numpy as np
        all_mean_vals = []
        for name, syls_dict in group_syl_contours.items():
            for k, y_arrays in syls_dict.items():
                if y_arrays:
                    mean_contour = np.mean(y_arrays, axis=0)
                    all_mean_vals.extend(mean_contour.tolist())

        if not all_mean_vals:
            return None, None
        return min(all_mean_vals), max(all_mean_vals)

    def _normalize_kde_points(self, group_syl_contours, min_f0, max_f0, N_DENSE):
        import numpy as np
        def hz_to_5_scale(hz):
            if max_f0 == min_f0: return 3.0
            return 5 * (np.log(hz) - np.log(min_f0)) / (np.log(max_f0) - np.log(min_f0))

        group_norm_points = {}
        for name, syls_dict in group_syl_contours.items():
            X_all, Y_all = [], []
            for k, y_arrays in syls_dict.items():
                x_dense = np.linspace(k * 100, (k + 1) * 100, N_DENSE)
                for y_arr in y_arrays:
                    X_all.extend(x_dense.tolist())
                    Y_all.extend([hz_to_5_scale(h) for h in y_arr])
            group_norm_points[name] = (np.array(X_all), np.array(Y_all))
        return group_norm_points

    def _draw_kde_heatmap_integrated(self, out_file, speakers, group_norm_points, max_syls):
        import matplotlib.pyplot as plt
        from scipy.stats import gaussian_kde
        import numpy as np
        import math

        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        all_groups_with_data = []
        for s in speakers:
            for g in getattr(s, 'cli_groups', []):
                if g not in all_groups_with_data and group_norm_points.get(g) and len(group_norm_points[g][0]) > 0:
                    all_groups_with_data.append(g)

        n_groups = len(all_groups_with_data)
        if n_groups == 0:
            raise Exception("No valid data for KDE Heatmap")

        n_cols = min(2, n_groups)
        n_rows = math.ceil(n_groups / n_cols)
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * max_syls * n_cols, 5 * n_rows), squeeze=False, sharex=True, sharey=True)
        axes_flat = axes.flatten()

        for idx, grp_name in enumerate(all_groups_with_data):
            ax = axes_flat[idx]
            X_all, Y_all = group_norm_points[grp_name]

            xmin, xmax = 0, max_syls * 100
            ymin, ymax = -1, 6

            positions = np.vstack([X_all, Y_all])
            try:
                kernel = gaussian_kde(positions, bw_method=0.15)
                xi, yi = np.mgrid[xmin:xmax:200j, ymin:ymax:100j]
                zi = kernel(np.vstack([xi.flatten(), yi.flatten()]))
                zi = zi.reshape(xi.shape)

                vmax = zi.max()
                if vmax > 0:
                    levels = np.linspace(vmax * 0.05, vmax, 30)
                    ax.contourf(xi, yi, zi, levels=levels, cmap="YlOrRd", extend='neither')
            except Exception:
                pass

            for k in range(1, max_syls):
                ax.axvline(k * 100, color='gray', linestyle='--', alpha=0.8)

            ax.set_title(grp_name, fontsize=16)
            ax.set_ylim(-1, 6)
            ax.set_xlim(0, max_syls * 100)

        for idx in range(n_groups, len(axes_flat)): axes_flat[idx].set_visible(False)

        fig.suptitle(f'Integrated KDE Heatmap ({len(speakers)} Speakers)', fontsize=20, fontweight='bold', y=1.05)
        fig.tight_layout()
        fig.savefig(out_file, dpi=300, bbox_inches='tight')
        plt.close(fig)

    def _export_kde_heatmap_integrated(self, out_file, speakers):
        N_DENSE = 100

        max_syls = self._get_kde_max_syls(speakers)
        group_syl_contours = self._extract_kde_heatmap_contours(speakers, N_DENSE)
        min_f0, max_f0 = self._calculate_kde_f0_bounds(group_syl_contours)

        if min_f0 is None or max_f0 is None:
            print('{"success": False, "error": "No valid data to plot"}')
            return

        group_norm_points = self._normalize_kde_points(group_syl_contours, min_f0, max_f0, N_DENSE)
        self._draw_kde_heatmap_integrated(out_file, speakers, group_norm_points, max_syls)

    def _sync_cli_state_after_project_load(self):
        for speaker in self.speaker_manager.get_all_speakers():
            if getattr(speaker, 'long_audio_path', None):
                speaker.cli_long_snd_path = speaker.long_audio_path
            seen = []
            for item in speaker.items.values():
                group = item.get('group', '导入内容')
                if group not in seen:
                    seen.append(group)
            speaker.cli_groups = seen

    def _process_speaker_batch_items(self, spk, sorted_audios, matched_audio_to_tg, params):
        from modules.audio_core import batch_process_worker_with_textgrid, batch_process_worker, extract_f0
        import parselmouth
        import concurrent.futures
        import os

        trim = params.get('trim_silence', True)

        futures = {}
        for i, ap in enumerate(sorted_audios):
            tp = matched_audio_to_tg.get(ap)
            if tp:
                futures[self.executor.submit(batch_process_worker_with_textgrid, ap, tp, params, trim)] = i
            else:
                word_lbl = os.path.splitext(os.path.basename(ap))[0]
                futures[self.executor.submit(batch_process_worker, ap, params, trim, word_lbl)] = i

        results = [None] * len(sorted_audios)
        for future in concurrent.futures.as_completed(futures):
            orig_idx = futures[future]
            try:
                res = future.result()
                results[orig_idx] = res
            except Exception as e:
                ap = sorted_audios[orig_idx]
                lbl = os.path.splitext(os.path.basename(ap))[0]
                results[orig_idx] = {'label': lbl, 'group': '导入内容', 'success': False, 'error': str(e), 'path': ap}

        for res in results:
            if not res: continue
            grp_name = res.get('group', '导入内容')
            if res.get('success'):
                res['group'] = grp_name
                if 'pitch_floor' not in res:
                    res['pitch_floor'] = params['pitch_floor']
                    res['pitch_ceiling'] = params['pitch_ceiling']
                    res['voicing_threshold'] = params['voicing_threshold']

                try:
                    snd = parselmouth.Sound(res['path'])
                    res['snd'] = snd
                    if 'pitch_data' not in res or not res['pitch_data']:
                        res['pitch_data'] = extract_f0(snd, params)
                    if params.get('analysis_mode') == 'formant' and ('formant_data' not in res or not res['formant_data']):
                        from modules.audio_core import extract_formants
                        res['formant_data'] = extract_formants(snd, params)
                except Exception:
                    pass

                iid = f"batch_tg_{res['label']}_{id(res)}"
                spk.items[iid] = res
            else:
                iid = f"missing_{res['label']}_{id(res)}"
                spk.items[iid] = {'label': res['label'], 'group': grp_name, 'snd': None, 'start': None, 'end': None, 'inner_splits': []}

    def _match_audio_to_textgrid(self, sorted_audios, sorted_tgs):
        matched_audio_to_tg = {}
        matched_tg_to_audio = {}

        import os
        tg_base_map = {os.path.splitext(os.path.basename(tp))[0].lower(): tp for tp in sorted_tgs}
        for ap in sorted_audios:
            abase = os.path.splitext(os.path.basename(ap))[0].lower()
            if abase in tg_base_map:
                tp = tg_base_map[abase]
                matched_audio_to_tg[ap] = tp
                matched_tg_to_audio[tp] = ap

        for ap in sorted_audios:
            if ap in matched_audio_to_tg: continue
            abase = os.path.splitext(os.path.basename(ap))[0].lower()
            for tp in sorted_tgs:
                if tp in matched_tg_to_audio: continue
                tbase = os.path.splitext(os.path.basename(tp))[0].lower()
                if abase in tbase or tbase in abase:
                    matched_audio_to_tg[ap] = tp
                    matched_tg_to_audio[tp] = ap
                    break

        remaining_audios = [ap for ap in sorted_audios if ap not in matched_audio_to_tg]
        remaining_tgs = [tp for tp in sorted_tgs if tp not in matched_tg_to_audio]
        for ap, tp in zip(remaining_audios, remaining_tgs):
            matched_audio_to_tg[ap] = tp
            matched_tg_to_audio[tp] = ap

        return matched_audio_to_tg

    def do_import_batch_and_export(self, arg):
        """
        Import a folder containing subfolders of speaker WAV + TextGrid files and export as a .teproj project.
        Usage: import_batch_and_export <folder_path> <output.teproj>
        """
        args = shlex.split(arg)
        if len(args) != 2:
            self._emit(False, error="Usage: import_batch_and_export <folder_path> <output.teproj>")
            return

        dir_path, teproj_path = args[0], args[1]
        if not os.path.isdir(dir_path):
            self._emit(False, error=f"Not a directory: {dir_path}")
            return

        from modules.audio_core import batch_process_worker_with_textgrid, batch_process_worker
        import re

        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower()
                    for text in re.split('([0-9]+)', s)]

        subdirs = [os.path.join(dir_path, d) for d in os.listdir(dir_path) if os.path.isdir(os.path.join(dir_path, d))]
        if not subdirs:
            subdirs = [dir_path]

        imported_speakers = []
        for subdir in sorted(subdirs, key=natural_sort_key):
            spk_name = os.path.basename(subdir)
            if spk_name in (".", "..") or not spk_name.strip():
                continue

            spk = None
            for s in self.speaker_manager.get_all_speakers():
                if s.name == spk_name:
                    spk = s
                    break
            if not spk:
                spk = self.speaker_manager.add_speaker(spk_name)

            self.speaker_manager.set_active_speaker(spk.id)
            spk = self.current_speaker
            spk.tab_mode = "多条独立音频"
            spk.items.clear()

            wavs = [os.path.join(subdir, f) for f in os.listdir(subdir) if f.lower().endswith('.wav')]
            textgrids = [os.path.join(subdir, f) for f in os.listdir(subdir) if f.lower().endswith('.textgrid')]

            sorted_audios = sorted(wavs, key=natural_sort_key)
            sorted_tgs = sorted(textgrids, key=natural_sort_key)

            matched_audio_to_tg = self._match_audio_to_textgrid(sorted_audios, sorted_tgs)

            spk.pending_batch_paths = sorted_audios
            self._process_speaker_batch_items(spk, sorted_audios, matched_audio_to_tg, self.params)

            imported_speakers.append(spk_name)

        for s_id in list(self.speaker_manager.speakers.keys()):
            s = self.speaker_manager.speakers[s_id]
            if s.name == "发音人 1" and not s.items:
                self.speaker_manager.remove_speaker(s_id)

        all_spks = self.speaker_manager.get_all_speakers()
        if all_spks:
            self.speaker_manager.active_speaker_id = all_spks[0].id

        orig_active_id = self.speaker_manager.active_speaker_id
        try:
            for spk in self.speaker_manager.get_all_speakers():
                self.speaker_manager.set_active_speaker(spk.id)
                for item in spk.items.values():
                    self._ensure_item_loaded(item)
        finally:
            if orig_active_id:
                self.speaker_manager.set_active_speaker(orig_active_id)

        ok = self.project_manager.export_project(teproj_path)
        if ok:
            self._emit(True, f"Imported speakers {imported_speakers} and exported project successfully.", path=teproj_path)
        else:
            self._emit(False, error="Project export failed after importing batch folder")

    def do_project_export(self, arg):
        """
        Export the full CLI project as a .teproj archive.
        Usage: project_export <output.teproj>
        """
        args = shlex.split(arg)
        if len(args) != 1:
            self._emit(False, error="Requires output .teproj path")
            return

        path = args[0]
        orig_active_id = self.speaker_manager.active_speaker_id
        try:
            for spk in self.speaker_manager.get_all_speakers():
                self.speaker_manager.set_active_speaker(spk.id)
                for item in spk.items.values():
                    self._ensure_item_loaded(item)
        finally:
            if orig_active_id:
                self.speaker_manager.set_active_speaker(orig_active_id)

        ok = self.project_manager.export_project(path)
        if ok:
            self._emit(True, "工程已导出。这个文件包含当前发音人、项目项、音频副本和基频缓存。", path=path)
        else:
            self._emit(False, error="Project export failed")

    def do_project_import(self, arg):
        """
        Import a full .teproj project archive.
        Usage: project_import <input.teproj>
        """
        args = shlex.split(arg)
        if len(args) != 1:
            self._emit(False, error="Requires input .teproj path")
            return

        path = args[0]
        if not os.path.exists(path):
            self._emit(False, error=f"File not found: {path}")
            return

        ok = self.project_manager.load_project(path)
        if ok:
            self._sync_cli_state_after_project_load()
            self._emit(
                True,
                "工程已导入。建议下一步运行 status 和 list_items all 确认内容。",
                path=path,
                speakers=len(self.speaker_manager.get_all_speakers()),
                active_speaker=self.current_speaker.name,
                total_items=len(self.items)
            )
        else:
            self._emit(False, error="Project import failed")

    def do_project_save(self, arg):
        """
        Save the current project to the internal workspace without exporting an archive.
        Usage: project_save
        """
        try:
            self.project_manager.save_to_workspace()
            self._emit(True, "工程已保存到内部工作区。若要给别人或下次恢复，请用 project_export。")
        except Exception as e:
            self._emit(False, error=str(e))

    def do_autosave(self, arg):
        """
        Enable, disable, or run project autosave.
        Usage: autosave on|off|now
        """
        action = arg.strip().lower()
        if action == "on":
            self.project_manager.auto_save_enabled = True
            self.project_manager.save_config()
            self.project_manager.trigger_auto_save()
            self._emit(True, "自动保存已开启。之后 CLI 状态变化会触发后台工程备份。", autosave=True)
        elif action == "off":
            self.project_manager.auto_save_enabled = False
            self.project_manager.save_config()
            self.project_manager.cancel_auto_save()
            self._emit(True, "自动保存已关闭。", autosave=False)
        elif action == "now":
            try:
                self.project_manager.save_autosave_snapshot()
                self._emit(True, "已立即保存一次当前工程。", autosave=self.project_manager.auto_save_enabled)
            except Exception as e:
                self._emit(False, error=str(e))
        else:
            self._emit(True, "自动保存状态已读取。", autosave=self.project_manager.auto_save_enabled)

    def _safe_audio_label(self, label):
        import re
        safe = re.sub(r'[\\/*?:"<>|]', "", str(label)).strip()
        return safe or "segment"

    def do_tool_merge(self, arg):
        """
        Merge short audio files into a single WAV, like audio_toolkit's merge tab.
        Usage: tool_merge <output.wav> <gap_sec> <audio1> <audio2> ...
        """
        args = shlex.split(arg)
        if len(args) < 4:
            self._emit(False, error="Requires output.wav, gap_sec, and at least two audio files")
            return

        out_path = args[0]
        try:
            gap_sec = float(args[1])
        except ValueError:
            self._emit(False, error="gap_sec must be a number")
            return

        paths = args[2:]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            self._emit(False, error=f"File not found: {missing[0]}", missing=missing)
            return

        try:
            target_sr = 44100
            all_vals = []
            gap_array = np.zeros(int(target_sr * gap_sec))
            for path in paths:
                snd = parselmouth.Sound(path)
                if snd.sampling_frequency != target_sr:
                    snd = snd.resample(target_sr)
                all_vals.append(snd.values[0])
                all_vals.append(gap_array)

            merged_vals = np.concatenate(all_vals[:-1])
            merged_snd = parselmouth.Sound(np.array([merged_vals]), sampling_frequency=target_sr)
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            merged_snd.save(out_path, "WAV")
            self._emit(True, f"已合并 {len(paths)} 个音频。", path=out_path, count=len(paths), gap_sec=gap_sec)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_tool_sort_batch(self, arg):
        """
        Fuzzy-sort batch audio paths according to a wordlist, like audio_toolkit's auto-sort.
        Usage: tool_sort_batch <wordlist.txt> [audio1 audio2 ...]
        If audio paths are omitted, the current loaded batch paths are used.
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Requires wordlist.txt")
            return

        wordlist_path = args[0]
        paths = args[1:] if len(args) > 1 else list(self.batch_paths)
        if not os.path.exists(wordlist_path):
            self._emit(False, error=f"File not found: {wordlist_path}")
            return
        if not paths:
            self._emit(False, error="No audio paths provided or loaded")
            return

        with open(wordlist_path, "r", encoding="utf-8") as f:
            groups, flat_words = parse_wordlist(f.read())

        sorted_paths = []
        used_indices = []
        for word in flat_words:
            idx = fuzzy_match_word_to_path(word, paths, used_indices=list(used_indices))
            if idx is not None:
                sorted_paths.append(paths[idx])
                used_indices.append(idx)

        leftovers = [p for i, p in enumerate(paths) if i not in used_indices]
        result = sorted_paths + leftovers
        self._emit(
            True,
            "已按字表给音频排序。需要正式载入时可把 sorted_paths 交给 load_batch。",
            matched=len(sorted_paths),
            total_words=len(flat_words),
            sorted_paths=result
        )

    def do_tool_split(self, arg):
        """
        Split a long audio into short WAV files by a wordlist, like audio_toolkit's split tab.
        Usage: tool_split <long_audio> <wordlist.txt> <output_dir> [buffer_sec] [trim]
        trim accepts true/false and defaults to true.
        """
        args = shlex.split(arg)
        if len(args) < 3:
            self._emit(False, error="Requires long_audio, wordlist.txt, and output_dir")
            return

        audio_path, wordlist_path, out_dir = args[:3]
        try:
            buffer_sec = float(args[3]) if len(args) > 3 else 0.1
        except ValueError:
            self._emit(False, error="buffer_sec must be a number")
            return
        do_trim = True if len(args) <= 4 else args[4].lower() in ("1", "true", "yes", "y", "trim")

        if not os.path.exists(audio_path):
            self._emit(False, error=f"File not found: {audio_path}")
            return
        if not os.path.exists(wordlist_path):
            self._emit(False, error=f"File not found: {wordlist_path}")
            return

        try:
            with open(wordlist_path, "r", encoding="utf-8") as f:
                groups, flat_words = parse_wordlist(f.read())
            if not flat_words:
                self._emit(False, error="No words found in wordlist")
                return

            snd = parselmouth.Sound(audio_path)
            segs = macroscopic_vad(snd)
            if not segs:
                self._emit(False, error="No speech segments detected")
                return

            os.makedirs(out_dir, exist_ok=True)
            total = min(len(segs), len(flat_words))
            saved_files = []
            for i in range(total):
                start, end = segs[i]
                word = flat_words[i]
                if do_trim:
                    part = snd.extract_part(from_time=start, to_time=end)
                    vals = part.values[0]
                    xs = part.xs()
                    threshold = 10 ** (-50 / 20)
                    valid_idx = np.where(np.abs(vals) > threshold)[0]
                    if len(valid_idx) > 0:
                        old_start = start
                        start = old_start + xs[valid_idx[0]]
                        end = old_start + xs[valid_idx[-1]]

                start = max(0, start - buffer_sec)
                end = min(snd.get_total_duration(), end + buffer_sec)
                if end <= start:
                    continue

                extract = snd.extract_part(from_time=start, to_time=end)
                out_file = os.path.join(out_dir, f"{str(i + 1).zfill(3)}_{self._safe_audio_label(word)}.wav")
                extract.save(out_file, "WAV")
                saved_files.append(out_file)

            self._emit(
                True,
                f"已拆分保存 {len(saved_files)} 段音频。",
                output_dir=out_dir,
                saved_files=saved_files,
                detected_segments=len(segs),
                word_count=len(flat_words)
            )
        except Exception as e:
            self._emit(False, error=str(e))


    def do_log(self, arg):
        """
        Enable or disable session logging.
        Usage: log on [filename]
               log off
        Default filename is 'phontracer_session.log'.
        """
        args = shlex.split(arg)
        if not args:
            print('{"success": False, "error": "Missing argument: on or off"}')
            return

        action = args[0].lower()
        if action == 'on':
            if self.log_file:
                print('{"success": False, "error": "Logging is already on"}')
                return
            filename = args[1] if len(args) > 1 else 'phontracer_session.log'
            try:
                self.log_file = open(filename, 'a', encoding='utf-8')
                sys.stdout = LoggerOut(self.original_stdout, self.log_file)
                print(json.dumps({"success": True, "message": f"Logging enabled to {filename}"}))
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))
        elif action == 'off':
            if not self.log_file:
                print('{"success": False, "error": "Logging is not currently on"}')
                return
            sys.stdout = self.original_stdout
            self.log_file.close()
            self.log_file = None
            print(json.dumps({"success": True, "message": "Logging disabled"}))
        else:
            print('{"success": False, "error": "Invalid argument. Use on or off"}')

    def do_exit(self, arg):
        """Exit the CLI."""
        print("Exiting...")
        if self.log_file:
            sys.stdout = self.original_stdout
            self.log_file.close()
            self.log_file = None
        self.executor.shutdown(wait=False)
        return True


    def _export_formant_table(self, out_file, speakers_to_process):
        try:
            import xlsxwriter
        except ImportError:
            print(json.dumps({"success": False, "error": "Missing xlsxwriter library. Please install via: pip install xlsxwriter"}))
            return

        workbook = xlsxwriter.Workbook(out_file)
        ws_raw = workbook.add_worksheet("逐点数据")
        ws_sum = workbook.add_worksheet("摘要数据")

        raw_headers = ["发音人", "组别", "编号", "词语", "音节序号", "单字", "时间点序号", "时间(s)", "F1(Hz)", "F2(Hz)"]
        for col, h in enumerate(raw_headers):
            ws_raw.write(0, col, h)

        sum_headers = ["发音人", "组别", "编号", "词语", "音节序号", "单字", "F1_均值(Hz)", "F2_均值(Hz)", "F1_中位数(Hz)", "F2_中位数(Hz)", "有效帧数"]
        for col, h in enumerate(sum_headers):
            ws_sum.write(0, col, h)

        raw_row = 1
        sum_row = 1

        for spk in speakers_to_process:
            is_continuous = (self.params.get('num_rule', 'continuous') == 'continuous')
            pts = int(self.params.get('pts', 11))
            strategy = self.params.get('formant_sample_strategy', '整段11点')

            groups = {}
            for item_id, item in spk.items.items():
                g = item.get('group', '导入内容')
                if g not in groups:
                    groups[g] = []
                groups[g].append(item)

            from modules.data_utils import clean_str, get_item_syllable_bounds, sample_formant_points_by_bounds, split_into_syllables
            sorted_groups = sorted(groups.keys(), key=clean_str)

            global_idx = 1
            for grp_name in sorted_groups:
                if not is_continuous:
                    global_idx = 1
                items_in_grp = groups[grp_name]
                items_in_grp = sorted(items_in_grp, key=lambda x: clean_str(x.get('label', '')))

                for item in items_in_grp:
                    self._ensure_item_loaded(item)
                    if not item.get('snd') or not item.get('formant_data'):
                        continue

                    bounds = get_item_syllable_bounds(item)
                    syls = split_into_syllables(item.get('label', ''))
                    preview_times, f1_vals, f2_vals = sample_formant_points_by_bounds(item, bounds, pts, strategy)

                    # 逐字写入逐点数据和摘要数据
                    for idx_syl, (c_s, c_e) in enumerate(bounds):
                        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                        flat_start = idx_syl * pts
                        flat_end = flat_start + pts

                        f1_slice = f1_vals[flat_start:flat_end]
                        f2_slice = f2_vals[flat_start:flat_end]

                        # 逐点数据写入
                        for idx_pt in range(pts):
                            ws_raw.write(raw_row, 0, spk.name)
                            ws_raw.write(raw_row, 1, grp_name)
                            ws_raw.write(raw_row, 2, global_idx)
                            ws_raw.write(raw_row, 3, item.get('label', ''))
                            ws_raw.write(raw_row, 4, idx_syl + 1)
                            ws_raw.write(raw_row, 5, char)
                            ws_raw.write(raw_row, 6, idx_pt + 1)
                            ws_raw.write(raw_row, 7, preview_times[flat_start + idx_pt])

                            f1_v = f1_slice[idx_pt]
                            f2_v = f2_slice[idx_pt]

                            if np.isnan(f1_v):
                                ws_raw.write_string(raw_row, 8, "--")
                            else:
                                ws_raw.write(raw_row, 8, round(f1_v, 1))

                            if np.isnan(f2_v):
                                ws_raw.write_string(raw_row, 9, "--")
                            else:
                                ws_raw.write(raw_row, 9, round(f2_v, 1))
                            raw_row += 1

                        # 计算共振峰的成对有效统计特征
                        f1_arr = np.array(f1_slice)
                        f2_arr = np.array(f2_slice)
                        valid_mask = ~np.isnan(f1_arr) & ~np.isnan(f2_arr) & (f2_arr > f1_arr)
                        paired_f1 = f1_arr[valid_mask]
                        paired_f2 = f2_arr[valid_mask]

                        valid_cnt = int(np.sum(valid_mask))

                        ws_sum.write(sum_row, 0, spk.name)
                        ws_sum.write(sum_row, 1, grp_name)
                        ws_sum.write(sum_row, 2, global_idx)
                        ws_sum.write(sum_row, 3, item.get('label', ''))
                        ws_sum.write(sum_row, 4, idx_syl + 1)
                        ws_sum.write(sum_row, 5, char)

                        if valid_cnt > 0:
                            mean_f1 = float(np.nanmean(paired_f1))
                            mean_f2 = float(np.nanmean(paired_f2))
                            med_f1 = float(np.nanmedian(paired_f1))
                            med_f2 = float(np.nanmedian(paired_f2))

                            ws_sum.write(sum_row, 6, round(mean_f1, 1))
                            ws_sum.write(sum_row, 7, round(mean_f2, 1))
                            ws_sum.write(sum_row, 8, round(med_f1, 1))
                            ws_sum.write(sum_row, 9, round(med_f2, 1))
                        else:
                            ws_sum.write_string(sum_row, 6, "--")
                            ws_sum.write_string(sum_row, 7, "--")
                            ws_sum.write_string(sum_row, 8, "--")
                            ws_sum.write_string(sum_row, 9, "--")

                        ws_sum.write(sum_row, 10, valid_cnt)
                        sum_row += 1

                    global_idx += 1

        workbook.close()

    def _cli_sample_formant_points(self, item, pts=11, strategy='整段11点'):
        start = item['start']
        end = item['end']
        preview_times = np.linspace(start, end, pts)
        
        f_data = item.get('formant_data')
        if not f_data or 'xs' not in f_data or 'f1' not in f_data or 'f2' not in f_data:
            nan_list = [np.nan] * pts
            return preview_times, nan_list, nan_list
            
        xs = f_data['xs']
        f1_arr = f_data['f1']
        f2_arr = f_data['f2']
        
        if strategy == '中段均值':
            duration = end - start
            m_start = start + duration / 3.0
            m_end = start + 2.0 * duration / 3.0
            
            mask = (xs >= m_start) & (xs <= m_end)
            f1_slice = f1_arr[mask]
            f2_slice = f2_arr[mask]
            
            f1_vals = f1_slice[~np.isnan(f1_slice)]
            f2_vals = f2_slice[~np.isnan(f2_slice)]
            
            mean_f1 = np.nanmean(f1_vals) if len(f1_vals) > 0 else np.nan
            mean_f2 = np.nanmean(f2_vals) if len(f2_vals) > 0 else np.nan
            
            preview_f1 = [mean_f1] * pts
            preview_f2 = [mean_f2] * pts
        else:
            preview_f1 = []
            preview_f2 = []
            
            f1_valid_idx = np.where(~np.isnan(f1_arr))[0]
            f2_valid_idx = np.where(~np.isnan(f2_arr))[0]
            
            for t in preview_times:
                if len(f1_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f1.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f1_valid_idx] - t))
                    if np.abs(xs[f1_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f1.append(np.nan)
                    else:
                        preview_f1.append(float(np.interp(t, xs[f1_valid_idx], f1_arr[f1_valid_idx])))
                if len(f2_valid_idx) == 0 or t < xs[0] or t > xs[-1]:
                    preview_f2.append(np.nan)
                else:
                    nearest_idx = np.argmin(np.abs(xs[f2_valid_idx] - t))
                    if np.abs(xs[f2_valid_idx][nearest_idx] - t) > 0.04:
                        preview_f2.append(np.nan)
                    else:
                        preview_f2.append(float(np.interp(t, xs[f2_valid_idx], f2_arr[f2_valid_idx])))
                        
        return preview_times.tolist(), preview_f1, preview_f2

    def _export_vowel_space_chart(self, out_file, speakers_to_process):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        data_points = []
        for spk in speakers_to_process:
            for item_id, item in spk.items.items():
                self._ensure_item_loaded(item)
                if not item.get('snd') or not item.get('formant_data'):
                    continue
                    
                f_data = item['formant_data']
                f1_arr = f_data['f1']
                f2_arr = f_data['f2']
                
                t_s, t_e = item['start'], item['end']
                xs = f_data['xs']
                mask = (xs >= t_s) & (xs <= t_e)
                
                valid_f1 = f1_arr[mask][~np.isnan(f1_arr[mask])]
                valid_f2 = f2_arr[mask][~np.isnan(f2_arr[mask])]
                
                if len(valid_f1) > 0 and len(valid_f2) > 0:
                    med_f1 = np.nanmedian(valid_f1)
                    med_f2 = np.nanmedian(valid_f2)
                    
                    data_points.append({
                        'spk': spk.name,
                        'label': item.get('label', ''),
                        'f1': med_f1,
                        'f2': med_f2
                    })
                    
        if not data_points:
            print(json.dumps({"success": False, "error": "No valid formant data found"}))
            return
            
        fig, ax = plt.subplots(figsize=(8, 7), dpi=300)
        
        unique_labels = sorted(list(set(pt['label'] for pt in data_points)))
        cmap = plt.get_cmap('tab10')
        colors = {lbl: cmap(i % 10) for i, lbl in enumerate(unique_labels)}
        
        for pt in data_points:
            ax.scatter(pt['f2'], pt['f1'], color=colors[pt['label']], s=60, alpha=0.8, edgecolors='none', zorder=4)
            ax.text(pt['f2'] - 15, pt['f1'] + 10, pt['label'], fontsize=9, fontweight='bold', color='#374151', zorder=5)
            
        if len(unique_labels) >= 3:
            mean_points = {}
            for lbl in unique_labels:
                lbl_pts = [pt for pt in data_points if pt['label'] == lbl]
                mean_f1 = np.mean([pt['f1'] for pt in lbl_pts])
                mean_f2 = np.mean([pt['f2'] for pt in lbl_pts])
                mean_points[lbl] = (mean_f2, mean_f1)
            
            for lbl, (m_f2, m_f1) in mean_points.items():
                ax.scatter(m_f2, m_f1, color=colors[lbl], s=150, marker='*', edgecolors='black', linewidth=1, zorder=6, label=f"{lbl} 均值")
        
        ax.invert_xaxis()
        ax.invert_yaxis()
        
        ax.set_xlabel("F2 (Hz)  [← Front / Back →]", fontsize=12, fontweight='bold', labelpad=10)
        ax.set_ylabel("F1 (Hz)  [← High / Low →]", fontsize=12, fontweight='bold', labelpad=10)
        
        spk_str = speakers_to_process[0].name if len(speakers_to_process) == 1 else "多发音人"
        ax.set_title(f"Vowel Space Chart ({spk_str}) - PhonTracer", fontsize=14, fontweight='bold', pad=15)
        
        ax.grid(True, linestyle='--', alpha=0.5, zorder=1)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#9CA3AF')
        ax.spines['bottom'].set_color('#9CA3AF')
        
        plt.tight_layout()
        fig.savefig(out_file, bbox_inches='tight')
        plt.close(fig)

    def do_EOF(self, arg):
        """Exit cleanly when the host closes stdin."""
        print()
        return self.do_exit(arg)



    def _export_textgrid(self, out_path, structure, speaker=None):
        speaker = speaker or self.current_speaker
        import textgrid
        import os

        flat_items = []
        for grp_name, children in structure:
            for child in children:
                if speaker.items[child].get('start') is not None and speaker.items[child].get('end') is not None:
                    flat_items.append(speaker.items[child])

        if not flat_items:
            print('{"success": False, "error": "No valid items to export"}')
            return

        # Simple heuristic: if we have paths, it might be batch mode, but CLI might not have "long audio" vs "batch" mode
        # If output is a directory or ends in a slash, treat as batch export
        if os.path.isdir(out_path) or not out_path.endswith('.TextGrid'):
            out_subdir = os.path.join(out_path, "Textgrid_export")
            os.makedirs(out_subdir, exist_ok=True)

            path_to_items = {}
            for item in flat_items:
                path = item.get('path', 'unknown')
                if path not in path_to_items:
                    path_to_items[path] = []
                path_to_items[path].append(item)

            used_stems = {}
            for path, items in path_to_items.items():
                base_name = make_textgrid_export_stem(path, items[0].get('label', '') if items else '')
                stem_count = used_stems.get(base_name, 0) + 1
                used_stems[base_name] = stem_count
                if stem_count > 1:
                    base_name = f"{base_name}_{stem_count}"
                tg_path = os.path.join(out_subdir, f"{base_name}.TextGrid")
                self._write_textgrid(tg_path, items)

            print(f'{{"success": True, "message": "Exported batch TextGrids to {out_subdir}"}}')
        else:
            self._write_textgrid(out_path, flat_items)
            print(f'{{"success": True, "message": "Exported TextGrid to {out_path}"}}')

    def _write_textgrid(self, tg_path, items):
        import textgrid
        import numpy as np
        max_time = 0
        for item in items:
            if item.get('snd'):
                dur = item['snd'].get_total_duration()
                if dur > max_time: max_time = dur
            elif item['end'] > max_time:
                max_time = item['end']

        if max_time == 0: max_time = 1.0
        tg = textgrid.TextGrid(maxTime=max_time)
        word_tier = textgrid.IntervalTier(name="words", minTime=0.0, maxTime=max_time)
        char_tier = textgrid.IntervalTier(name="chars", minTime=0.0, maxTime=max_time)
        group_tier = textgrid.IntervalTier(name="groups", minTime=0.0, maxTime=max_time)

        items.sort(key=lambda x: x.get('start', 0))

        last_word_end = 0.0
        last_char_end = 0.0
        last_group_end = 0.0
        has_chars = False

        for item in items:
            t_s, t_e = item['start'], item['end']
            label = item.get('label', '')
            inner_splits = item.get('inner_splits', [])
            grp_name = item.get('group', '导入内容')

            if t_s > last_word_end:
                word_tier.add(last_word_end, t_s, "")
            word_tier.add(t_s, t_e, label)
            last_word_end = t_e

            if t_s > last_group_end:
                group_tier.add(last_group_end, t_s, "")
            group_tier.add(t_s, t_e, grp_name)
            last_group_end = t_e

            syls = split_into_syllables(label)
            if len(syls) > 1:
                has_chars = True
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")

                chars_bounds = item.get('chars_bounds', [])
                if not chars_bounds:
                    splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                    if len(splits) != len(syls) + 1:
                        splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
                    chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                local_last = t_s
                for i in range(len(syls)):
                    if i < len(chars_bounds):
                        c_s, c_e = chars_bounds[i]
                        if c_s > local_last:
                            char_tier.add(local_last, c_s, "")
                        char_tier.add(c_s, c_e, syls[i])
                        local_last = c_e
                if local_last < t_e:
                    char_tier.add(local_last, t_e, "")
                last_char_end = t_e
            else:
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")
                char_tier.add(t_s, t_e, label)
                last_char_end = t_e

        if max_time > last_word_end:
            word_tier.add(last_word_end, max_time, "")
        if max_time > last_char_end:
            char_tier.add(last_char_end, max_time, "")
        if max_time > last_group_end:
            group_tier.add(last_group_end, max_time, "")

        tg.append(group_tier)
        tg.append(word_tier)
        if has_chars:
            tg.append(char_tier)

        tg.write(tg_path)

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Handle multiprocessing bootstrap correctly in frozen Windows builds.
    multiprocessing.freeze_support()
    cli = PhonTracerCLI()
    try:
        if argv:
            first_arg = argv[0].strip().lower()
            if first_arg.startswith("--multiprocessing-"):
                return 0
            if first_arg in ("-h", "--help", "help", "?"):
                cli.do_help("")
                return 0
            # Relaxed startup behavior:
            # If non-option args are provided, treat them as one CLI command and run once.
            # Example: PhonTracerCLI.exe status
            if not first_arg.startswith("-"):
                command_line = " ".join(argv)
                cli.onecmd(command_line)
                return 0
            print(json.dumps({
                "success": False,
                "error": f"Unknown startup option: {argv[0]}",
                "hint": "Use --help for manual, or pass a CLI command directly (e.g. `PhonTracerCLI.exe status`)."
            }))
            return 2

        cli.cmdloop()
        return 0
    finally:
        cli.executor.shutdown(wait=False)


if __name__ == '__main__':
    raise SystemExit(main())
