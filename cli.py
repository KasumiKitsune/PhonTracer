import cmd
import shlex
import sys
import os
import re
import shutil
import inspect
import warnings
import multiprocessing

warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Mean of empty slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*Degrees of freedom <= 0 for slice.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*All-NaN slice encountered.*")
warnings.filterwarnings("ignore", category=RuntimeWarning)

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

warnings.filterwarnings("ignore", category=UserWarning, message=".*Glyph.*missing from.*font.*")

import numpy as np

import copy
import glob
try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)
import textgrid
import concurrent.futures
from collections import OrderedDict
import json
import logging

# Override json.dumps to output raw UTF-8 Chinese characters rather than Unicode escape sequences (\uXXXX)
_orig_dumps = json.dumps
def dumps_utf8(*args, **kwargs):
    if 'ensure_ascii' not in kwargs:
        kwargs['ensure_ascii'] = False
    if 'allow_nan' not in kwargs:
        kwargs['allow_nan'] = False
    return _orig_dumps(*args, **kwargs)
json.dumps = dumps_utf8

# Modify sys.path if necessary
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.audio_core import macroscopic_vad, core_microscopic_vowel_nucleus, auto_split_inner_word, auto_split_to_chars_bounds, batch_process_worker, recalculate_bounds_fast, extract_f0, trim_bounds_by_amplitude
from modules.speaker_manager import SpeakerManager
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, get_export_text_for_item, build_five_point_chart, split_into_syllables, make_textgrid_export_stem
from modules.project_manager import ProjectManager
from modules.version import APP_NAME, __version__
from modules.acoustic_exporter import AcousticChartExporter
from modules.textgrid_converter import (
    DEFAULT_GROUP_NAME,
    NONE_TIER_LABEL,
    PAIR_MODE_ADJACENT,
    PAIR_MODE_NONE,
    TextGridMapping,
    convert_textgrid,
    inspect_textgrid,
    load_auxiliary_wordlist_records,
    preview_textgrid_conversion,
    recommend_tier_names,
)


from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    apply_record_metadata,
    build_document_from_csv_text,
    build_document_from_messy_text,
    build_document_from_structured_text,
    build_document_from_v1_text,
    build_wordlist_document_from_items,
    create_empty_wordlist_document,
    flatten_wordlist_document,
    load_wordlist_document,
    load_wordlist_file,
    load_wordlist_from_csv_file,
    looks_like_csv_wordlist_text,
    looks_like_structured_wordlist_text,
    metadata_from_record,
    normalize_wordlist_document,
    save_wordlist_document,
    save_wordlist_file,
    summarize_wordlist_document,
    validate_wordlist_document,
)

COMMON_AUDIO_EXTENSIONS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma')


def _natural_sort_key(s):
    text = os.path.basename(str(s))
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r'([0-9]+)', text)]

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


def _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, valid_xs=None):
    """
    计算预览 F0 轨迹。使用 np.searchsorted 进行二分查找加速，避免内层循环重复布尔扫描。
    """
    if valid_xs is None:
        valid_indices = np.where(pitch_freqs > 0)[0]
        if len(valid_indices) == 0:
            return [0.0] * len(preview_times)
        valid_xs = pitch_xs[valid_indices]
    elif len(valid_xs) == 0:
        return [0.0] * len(preview_times)

    preview_f0 = np.interp(preview_times, pitch_xs, pitch_freqs).tolist()
    idxs = np.searchsorted(valid_xs, preview_times)
    n = len(valid_xs)
    for j, (t, idx) in enumerate(zip(preview_times, idxs)):
        dist = float("inf")
        if idx < n:
            dist = valid_xs[idx] - t
        if idx > 0:
            left_dist = t - valid_xs[idx - 1]
            if left_dist < dist:
                dist = left_dist
        if dist > 0.025:
            preview_f0[j] = 0.0
    return preview_f0

def get_cli_help_text(lang='en'):
    if str(lang or 'en').lower().startswith('zh') or str(lang).lower() in ('cn', 'chinese', '中文'):
        return f"""
================================================================================
                            {APP_NAME}语音学标注与声学分析系统 V{__version__} 命令行手册 (批处理与开发者指南)
================================================================================
AI 操作守则：优先只使用本 CLI。除非用户明确要求，不要直接编辑源码、移动/删除文件、
不要绕过 CLI 去操作工程数据。需要说明时，请先用 `status`、`list_items`、`agent_guide` 确认。

--- 全局启动选项 (Global Startup Options) ---
* `--script-file <commands.txt>` / `--batch-cmds <file>`: 【AI / 批处理全流程默认首选！】将建库、加载音频、应用字表、`detect_f0 recommended`、`fix_warnings --apply`、`stats` 与 `export_bundle` 一次性写入 UTF-8 剧本文件（自动剥离 BOM，跳过空行与 `#` 注释），1 次进程调用跑完全流程（可配合 `--project <out.teproj>` 联用）；遇 `success: false` 立即中止并精准报告 `failed_line` 和 `failed_command`，彻底消除多轮冷启动与大模型往返延迟！
* `--project <filepath.teproj>`: 绑定持久工程文件（适合已有工程的**后续增量微调 / 单步追问**，或与 `--script-file` 联用；启动时若存在自动载入，退出或状态变更后自动保存回 `<filepath.teproj>`；注意：完整建库跑批请优先用 `--script-file`，勿拆成几十次独立 `--project` 单步调用）
* `--workspace <dir>`: 指定持久化工作区目录（若 `<dir>/project.json` 已存在则自动调用 `project_load` 恢复，退出前自动调用 `project_save` 落盘，且绝不删除该目录）
* `--lang zh|en`: 指定 CLI 引导与回执语言（若未指定且命令行参数含中文字符，自动切换为 `zh`）
* `--quiet` / `-q`: 静默模式，抑制启动横幅、交互提示符与底层警告，保持 `stdout` 100% 纯净 JSON
* `--json`: 强制纯净 JSON 标准流模式（任何命令及 `--help` 均输出单行有效 JSON）
* `--version` / `-v`: 查看版本信息

--- 工作流生命周期 ---
1. 多发音人管理（可选）：
   - `speakers` (查看全部发音人及当前激活项)
   - `add_speaker <名称> [--switch]` (新增发音人，可附带 `--switch` 立即切换)
   - `switch_speaker <名称或ID>` (切换当前操作发音人)
   - `rename_speaker <旧名或ID> <新名>` (重命名发音人，可用于重命名默认的“发音人 1”)
   - `remove_speaker <名称或ID>` (删除发音人)
2. 导入音频：
   - 导入单条长音频（如整句朗读录音）：
     `load_long <音频路径>`
   - 导入批量独立短音频文件（支持多文件、目录或 `*.wav` 通配符）：
     `load_batch <路径1或目录或通配符> ...`
3. 导入字词表或 TextGrid（进行音节切分/匹配）：
   - `apply_wordlist <字词表路径> [匹配模式]`
     * 支持 `.txt` 普通/结构化字表、`.ptwl` 高级字表、`.csv` 表格字表。
     * 匹配模式：'fuzzy'（模糊文件名匹配，默认）或 'order'（按物理顺序匹配）。
   - `tool_wordlist_convert <输入字表.txt|.csv|.ptwl> <输出.ptwl>` (将字表升级转换为标准 v2 `.ptwl` 高级字表)
   - `apply_textgrid <TextGrid路径>` (仅长音频模式下使用，导入 TextGrid 音段并对齐)
   - `batch_textgrid_import <TextGrid文件或目录...>` (导入多个 TextGrid 并与已有独立音频做文件名自动匹配)
   - `textgrid_inspect <TextGrid路径>` (查看任意 TextGrid 的层摘要和推荐层映射)
   - `textgrid_preview <TextGrid路径> item=<条目层> core=<核心层> [group=<组层|无>] [pair=adjacent] [wordlist=<字表>]`
   - `textgrid_convert <TextGrid文件或目录...> out=<输出文件或目录> item=<条目层> core=<核心层> [group=<组层|无>] [pair=adjacent] [wordlist=<字表>]`
4. 微调边界、声学参数探测与异常修复：
   - `detect_f0 [conservative|recommended|fine]` (自动估算 F0 分布并给出三档范围建议，可直接应用预设)
   - `detect_formants [reco|anti|fine]` (自动评估共振峰最佳追踪参数并支持一键应用)
   - `fix_warnings [apply=true|false]` (诊断 `list_items warnings` 中的清音/倍频异常项并给出或自动应用修复参数)
   - `modify_bounds <音节ID> <开始时间> <结束时间>` (精细修正某个音节的 VAD 时间边界)
   - `modify_params <音节ID> pitch_floor=30 voicing_threshold=0.20` (精细修正单个音节专属参数)
   - `recalculate` (使用最新全局参数重新批量计算整个项目的所有项)
5. 统计摘要、数据导出与一站式打包：
   - `stats [by=group|item|speaker] [scope=active|integrated]` (直接输出基频均值/极差/五度值/共振峰结构化 JSON 统计摘要)
   - `export <格式> <输出路径> [规则] [目标范围] [高级参数=值 ...]`
     * 支持格式 (`supported_formats`)：xlsx, txt, textgrid, ptwl, contour, density, distribution, quality, overview_heatmap, formant_space, f1_f2, duration, trend, line_chart, kde, wav, merged_wav, formant_table, formant_trajectory, formant_density, formant_overview_heatmap
     * 高级参数：`scale=t_value|hz`, `groupby=group|label|speaker`, `facet=none|group|speaker|syllable_position|label`, `normalization=speaker|global`, `chart_group=default|item_tag:...|group_tag:...|meta:...`, `format=png|svg|pdf`, `target=active|integrated|separate`
   - `export_bundle <输出目录> [scope=active|integrated|separate]` (一键打包导出 `.teproj`、`.ptwl`、`.xlsx`、关键图表与研究方法报告)
   - `report_export <路径.teproj> <输出目录> [md|excel|both] [include_cache=true|false]` (导出项目研究方法报告与科学数据档案)

--- 声学参数与调优指南 ---
* analysis_mode: 分析模式，可选 'f0' (基频/声调模式) 或 'formant' (共振峰模式，默认：'f0')
* pts: 等分插值采样点数（默认：11）
* db: VAD 切分能量落差阈值（默认：60.0）
* skip_front: 排除声母时长，避免声母辅音浊化干扰（默认：0.0）
* pitch_floor: 音高分析下限（默认：75 Hz） -> 通用甜点区：75
* pitch_ceiling: 音高分析上限（默认：600 Hz） -> 通用甜点区：600
* voicing_threshold: 浊音阈值（默认：0.25） -> 针对汉语三声等低频“气泡音/嘎裂声”，建议手动调低至 0.15 ~ 0.20
* trim_silence: 自动切除有效声学边界首尾低于 -50dB 的静音区（默认：True）
* formant_max_hz: Praat Burg 共振峰分析上限（如 5500 Hz，不是 F2 数值上限）
* formant_count: 共振峰数量（默认：5）
* formant_window_length: 共振峰分析窗长（秒，默认：0.025）
* formant_pre_emphasis: 共振峰预加重系数（Hz，默认：50.0）
* formant_sample_strategy: 共振峰提取采样策略（可选 '整段11点', '中段均值'，默认：'整段11点'）
* show_f3: 是否显示/导出 F3 共振峰（默认：False）

--- 核心命令速查表 (输入 `help all` 一次获取全部子命令参数 JSON，或输入 `<cmd> ?` 查看单项帮助) ---
- `status`: 获取项目当前模式、状态指标、警告统计等。
- `list_items [all|warnings|组名]`: 列表打印当前已切分的数据项及警告状态。
- `speakers` / `add_speaker` / `switch_speaker` / `rename_speaker` / `remove_speaker`: 多发音人生命周期管理。
- `set_params 键=值 ...`: 动态更新全局算法提取参数。
- `modify_bounds <音节ID> <开始秒数> <结束秒数>`: 手动重写音节声学边界。
- `modify_params <音节ID> 键=值 ...`: 手动指定单个音节专属的提取参数。
- `recalculate`: 基于全局最新参数，批量重算整个项目。
- `detect_f0 [apply_preset]`: 自动估算发音人的 F0 分布并给出保守/推荐/精细范围（conservative, recommended, fine）。
- `detect_formants [apply_preset]`: 自动评估发音人共振峰最佳参数（reco, anti, fine）。
- `fix_warnings [apply=true|false]`: 针对警告项生成或自动应用修复参数（如放宽 pitch_floor 与 voicing_threshold）。
- `stats [by=group|item|speaker] [scope=active|integrated]`: 输出结构化声学统计摘要 JSON（均值、标准差、极差、五度值、共振峰）。
- `export <格式> <输出路径> [rule] [target] [key=val ...]`: 导出数据表、TextGrid、`.ptwl` 高级字表或科研图表。
- `export_bundle <输出目录> [scope=active|integrated|separate]`: 一站式打包导出工程、高级字表、Excel、科研图表与报告。
- `tool_wordlist_convert <输入字表> <输出.ptwl>`: 将普通/CSV/结构化字表升级保存为标准 v2 `.ptwl` 高级字表。
- `project_export <路径.teproj>` / `project_import <路径.teproj>`: 导出或导入完整工程。
- `project_save` / `project_load`: 保存工程数据到工作区，或从工作区 `project.json` 恢复。
- `autosave on|off|now`: 开启、关闭或立即执行工程自动保存。
- `project_preview <路径.teproj>`: 预览并展示 teproj 工程内部数据明细与发音人摘要。
- `project_convert_zip <路径.teproj> <目标.zip>`: 将工程包另存为普通 ZIP 压缩包。
- `textgrid_inspect` / `textgrid_preview` / `textgrid_convert`: 查看、预览和转换任意 TextGrid 为标准三层结构。
- `batch_textgrid_import <TextGrid文件或目录...>`: 批量导入 TextGrid 并与已有独立音频做文件名自动匹配。
- `import_batch_and_export <音频与TextGrid目录> <输出.teproj> [wordlist=<字表>] [match_mode=fuzzy|order]`: 一键批量导入目录并导出为 teproj 工程。
- `report_export <路径.teproj> <输出目录> [md|excel|both] [include_cache=true|false]`: 导出项目研究方法报告与数据档案。
- `list_scripts` / `script_info` / `run_script` / `script_save` / `script_delete` / `script_import` / `script_export` / `script_prompt`: 自定义脚本库管理与运行。
- `tool_merge <输出.wav> <间隔秒> <音频1> <音频2> ...`: 拼接多个短音频。
- `tool_split <长音频> <字表.txt> <输出目录> [缓冲秒] [trim]`: 按字表拆分长音频。
- `tool_sort_batch <字表.txt> [音频路径...] [--apply]`: 按字表模糊排序独立音频（加 `--apply` 可直接载入当前会话）。
- `lang [zh|en]`: 切换命令行显示语言为中文或英文。
================================================================================
"""
    return f"""
================================================================================
                            {APP_NAME} CLI MANUAL V{__version__} (Agent & Developer Guide)
================================================================================
AI operating rule: prefer this CLI as the control surface. Unless the user clearly
asks for it, do not edit source files, move/delete files, or bypass the CLI to
change project data. Use `status`, `list_items`, and `agent_guide` to orient first.

--- GLOBAL STARTUP OPTIONS ---
* `--script-file <commands.txt>` / `--batch-cmds <file>`: [PRIMARY DEFAULT FOR AI & BATCH WORKFLOWS!] Write all setup, audio loading, wordlist application, `detect_f0 recommended`, `fix_warnings --apply`, `stats`, and `export_bundle` commands into a UTF-8 script file and execute the entire pipeline in 1 process call (can be combined with `--project <out.teproj>`); strips UTF-8 BOM, skips blank/`#` lines, stops immediately on `success: false` with `failed_line` and `failed_command`, and eliminates multi-turn LLM & archive unpack overhead!
* `--project <filepath.teproj>`: Bind persistent project archive (best for **incremental tuning / single-step follow-up queries**, or combined with `--script-file`; auto-loads on startup if existing and auto-saves on exit; avoid splitting a full pipeline into dozens of separate `--project` single-step calls)
* `--workspace <dir>`: Use persistent workspace directory (auto-restores `<dir>/project.json` via `project_load` on startup, auto-saves via `project_save` on exit, and never deletes user-specified workspace directories)
* `--lang zh|en`: Set CLI language at startup (auto-switches to `zh` if Chinese characters are detected in startup arguments)
* `--quiet` / `-q`: Suppress startup banners, interactive prompts, and runtime warnings for 100% clean JSON on `stdout`
* `--json`: Force pure JSON output stream (including `--help`)
* `--version` / `-v`: Print CLI version

--- WORKFLOW LIFECYCLE ---
1. Multi-Speaker Management (Optional):
   - `speakers` (list all speakers and active status)
   - `add_speaker <name> [--switch]` (add a speaker and optionally switch to it)
   - `switch_speaker <name_or_id>` (switch active speaker)
   - `rename_speaker <old_name_or_id> <new_name>` (rename a speaker, e.g., default '发音人 1')
   - `remove_speaker <name_or_id>` (remove a speaker)
2. Load Audio:
   - For single long audio files (e.g. continuous sentence speech):
     `load_long <filepath>`
   - For multiple isolated sound files (supports file list, directory, or `*.wav` glob):
     `load_batch <file1_or_dir_or_glob> ...`
3. Apply Wordlist or TextGrid (Syllable Segmenting):
   - `apply_wordlist <wordlist_filepath> [match_mode]`
     * Supports `.txt` (plain or structured v2), `.ptwl` (v2 JSON wordlist), and `.csv`.
     * match_mode: 'fuzzy' (fuzzy filename matching, default) or 'order' (strict order matching).
   - `tool_wordlist_convert <input.txt|.csv|.ptwl> <output.ptwl>` (convert/upgrade wordlist to v2 `.ptwl`)
   - `apply_textgrid <textgrid_filepath>` (long audio mode: segment and align by TextGrid)
   - `batch_textgrid_import <tg_file_or_dir...>` (batch audio mode: import and match TextGrids to audios)
   - `textgrid_inspect <textgrid_filepath>` (inspect tiers and recommended converter mapping)
   - `textgrid_preview <textgrid_filepath> item=<item_tier> core=<core_tier> [group=<group_tier|none>] [pair=adjacent] [wordlist=<path>]`
   - `textgrid_convert <tg_file_or_dir...> out=<output_file_or_dir> item=<item_tier> core=<core_tier> [group=<group_tier|none>] [pair=adjacent] [wordlist=<path>]`
4. Fine-Tune Boundaries, Parameter Detection & Warning Fixes:
   - `detect_f0 [conservative|recommended|fine]` (estimate speaker F0 distribution and optionally apply preset)
   - `detect_formants [reco|anti|fine]` (estimate optimal formant tracking parameters and optionally apply preset)
   - `fix_warnings [apply=true|false]` (diagnose `list_items warnings` and suggest/apply parameter fixes)
   - `modify_bounds <item_id> <start> <end>` (fine-tune VAD time boundaries of a syllable)
   - `modify_params <item_id> pitch_floor=30 voicing_threshold=0.20` (set item-specific acoustic parameters)
   - `recalculate` (recalculate all items globally with updated global parameters)
5. Statistical Summary, Export & One-Step Bundle:
   - `stats [by=group|item|speaker] [scope=active|integrated]` (output structured JSON summary of F0 mean/sd/range/T-values/formants)
   - `export <format> <output_file> [rule] [target] [key=val ...]`
     * supported_formats: xlsx, txt, textgrid, ptwl, contour, density, distribution, quality, overview_heatmap, formant_space, f1_f2, duration, trend, line_chart, kde, wav, merged_wav, formant_table, formant_trajectory, formant_density, formant_overview_heatmap
     * key=value options: `scale=t_value|hz`, `groupby=group|label|speaker`, `facet=none|group|speaker|syllable_position|label`, `normalization=speaker|global`, `chart_group=default|item_tag:...|group_tag:...|meta:...`, `format=png|svg|pdf`, `target=active|integrated|separate`
   - `export_bundle <out_dir> [scope=active|integrated|separate]` (one-step bundle export of `.teproj`, `.ptwl`, `.xlsx`, charts, and research report)
   - `report_export <path.teproj> <out_dir> [md|excel|both] [include_cache=true|false]` (export research methodologies reports and data archives)

--- CURRENT CONFIG & SCHEMAS ---
- Global parameters (modifiable via `set_params`):
  * analysis_mode: Analysis mode, 'f0' or 'formant' (default: 'f0')
  * pts: Number of interpolation points (default: 11)
  * db: VAD energy threshold (default: 60.0)
  * skip_front: Avoid segmenting consonant onset duration (default: 0.0)
  * pitch_floor: Minimum F0 range (default: 75) -> Sweet spot: 75
  * pitch_ceiling: Maximum F0 range (default: 600) -> Sweet spot: 600
  * voicing_threshold: Voicing tolerance (default: 0.25) -> Adjust lower (0.15~0.20) for creaky voice
  * trim_silence: Cut prefix/suffix silence under -50dB (default: True)
  * formant_max_hz: Praat Burg formant-analysis ceiling in Hz (e.g. 5500; not the F2 value limit)
  * formant_count: Number of formants to track (default: 5)
  * formant_window_length: Formant analysis window length in seconds (default: 0.025)
  * formant_pre_emphasis: Formant pre-emphasis filter value (default: 50.0)
  * formant_sample_strategy: Strategy to sample formant points (e.g., '整段11点', '中段均值')
  * show_f3: Whether to show/export F3 formant (default: False)

--- ALL COMMANDS REFERENCE (Run `help all` to get all command schemas in one call, or `<cmd> ?` for a single command) ---
- `status`: Show current project state, active parameters, item warnings.
- `list_items [all|warnings|group_name]`: List segmented items and warning flags.
- `speakers` / `add_speaker` / `switch_speaker` / `rename_speaker` / `remove_speaker`: Multi-speaker management.
- `set_params key=value ...`: Update global extraction parameters.
- `modify_bounds <item_id> <start> <end>`: Set manual time boundaries.
- `modify_params <item_id> key=value ...`: Set item-specific custom parameters.
- `recalculate`: Recalculate VAD boundaries & F0 curves globally.
- `detect_f0 [apply_preset]`: Estimate speaker F0 distribution and get conservative/recommended/fine range suggestions.
- `detect_formants [apply_preset]`: Estimate optimal formant parameters (reco, anti, fine).
- `fix_warnings [apply=true|false]`: Diagnose and auto-fix item pitch/voicing warnings.
- `stats [by=group|item|speaker] [scope=active|integrated]`: Output structured acoustic summary statistics as JSON.
- `export <format> <output_file> [rule] [target] [key=val ...]`: Export tables, TextGrids, `.ptwl` wordlists, or scientific charts.
- `export_bundle <out_dir> [scope=active|integrated|separate]`: Export full research bundle (`.teproj`, `.ptwl`, `.xlsx`, charts, report).
- `tool_wordlist_convert <in.txt|.csv|.ptwl> <out.ptwl>`: Convert plain/CSV/structured wordlist into v2 `.ptwl` format.
- `project_export <path.teproj>` / `project_import <path.teproj>`: Save or load a full project.
- `project_save` / `project_load`: Save project data to workspace or load from workspace `project.json`.
- `autosave on|off|now`: Enable, disable, or immediately run project autosave.
- `project_preview <path.teproj>`: Preview metadata and speaker details of a teproj project file.
- `project_convert_zip <path.teproj> <dest.zip>`: Save teproj archive as a standard ZIP file.
- `textgrid_inspect` / `textgrid_preview` / `textgrid_convert`: Inspect, preview, and convert arbitrary TextGrids to standard `groups / words / chars` tiers.
- `batch_textgrid_import <tg_file_or_dir...>`: Import multiple TextGrids and match them to batch audios.
- `import_batch_and_export <folder_path> <output.teproj> [wordlist=<path>] [match_mode=fuzzy|order]`: One-step import folder and export to teproj.
- `report_export <path.teproj> <out_dir> [md|excel|both] [include_cache]`: Export research methods report and data archives.
- `list_scripts` / `script_info` / `run_script` / `script_save` / `script_delete` / `script_import` / `script_export` / `script_prompt`: Custom script library tools.
- `tool_merge <output.wav> <gap_sec> <audio1> <audio2> ...`: Merge short audios.
- `tool_split <long_audio> <wordlist.txt> <output_dir> [buffer_sec] [trim]`: Split long audio by wordlist.
- `tool_sort_batch <wordlist.txt> [audio_paths...] [--apply]`: Fuzzy-sort batch audio paths by wordlist (with `--apply` to load directly).
- `lang [zh|en]`: Switch CLI language between Chinese and English.
================================================================================
"""


class PhonTracerCLI(cmd.Cmd):
    intro = f"""{APP_NAME}语音学标注与声学分析系统 V{__version__} - 命令行批处理工作台
你好，我是 PhonTracer 命令行控制台，支持自动化批处理与工程脚本交互。
Type 'help' or '?' to list commands. Type 'status' for workspace overview.
"""

    prompt = "(phontracer) "

    COMMON_AUDIO_EXTENSIONS = COMMON_AUDIO_EXTENSIONS

    def __init__(
        self,
        workspace_dir=None,
        shared_workspace=False,
        project_path=None,
        lang=None,
        quiet=False,
        json_mode=False,
    ):
        super().__init__()
        self.speaker_manager = SpeakerManager()
        self.lang = lang if lang in ('zh', 'en') else 'en'
        self.quiet = bool(quiet)
        self.json_mode = bool(json_mode)
        if self.lang == 'zh' and not self.quiet and not self.json_mode:
            self.prompt = "(phontracer)[中文] "
        if self.quiet or self.json_mode:
            self.intro = ""
            self.prompt = ""

        self._executor = None
        self._wordlist_doc = None
        self.global_wordlist_document = None
        self.log_file = None
        self.original_stdout = sys.stdout
        self._last_emit_success = True
        self._last_emit_payload = None
        self._session_dirty = False
        self._defer_persistence = False
        self._explicit_workspace_dir = os.path.abspath(workspace_dir) if workspace_dir else None
        self._bound_project_path = os.path.abspath(project_path) if project_path else None

        # 隔离工作区处理：默认分配专属临时工作区，避免冲毁 GUI 正在运行的主工作区
        self._temp_workspace_dir = None
        if workspace_dir:
            cli_ws = os.path.abspath(workspace_dir)
            os.makedirs(cli_ws, exist_ok=True)
        elif shared_workspace:
            cli_ws = None
        else:
            import tempfile
            self._temp_workspace_dir = tempfile.mkdtemp(prefix="phontracer_cli_ws_")
            cli_ws = self._temp_workspace_dir

        self.project_manager = ProjectManager(self, workspace_dir=cli_ws)

        # 如果显式指定了 --workspace 且存在 project.json，自动恢复上次保存的工作区状态
        if self._explicit_workspace_dir:
            ws_proj_json = os.path.join(self._explicit_workspace_dir, "project.json")
            if os.path.exists(ws_proj_json):
                try:
                    if self.project_manager.load_from_workspace():
                        self._sync_cli_state_after_project_load()
                except Exception:
                    pass

        # 如果指定了 --project 且文件存在，自动加载该工程归档
        if self._bound_project_path and os.path.exists(self._bound_project_path):
            try:
                if self.project_manager.load_project(self._bound_project_path):
                    self._sync_cli_state_after_project_load()
            except Exception:
                pass

    _natural_sort_key = staticmethod(_natural_sort_key)

    @property
    def wordlist_doc(self):
        spk = getattr(self, 'speaker_manager', None) and self.speaker_manager.get_active_speaker()
        if spk is not None and getattr(spk, 'wordlist_doc', None) is not None:
            return spk.wordlist_doc
        return getattr(self, '_wordlist_doc', None)

    @wordlist_doc.setter
    def wordlist_doc(self, val):
        norm_val = normalize_wordlist_document(val) if isinstance(val, dict) else val
        self._wordlist_doc = norm_val
        self.global_wordlist_document = norm_val
        spk = getattr(self, 'speaker_manager', None) and self.speaker_manager.get_active_speaker()
        if spk is not None:
            spk.wordlist_doc = norm_val
        pm = getattr(self, 'project_manager', None)
        if pm is not None and isinstance(norm_val, dict):
            preserved = getattr(pm, '_preserved_top_level', None)
            if not isinstance(preserved, dict):
                preserved = {}
                pm._preserved_top_level = preserved
            preserved['wordlist_doc'] = copy.deepcopy(norm_val)
            preserved['wordlist_version'] = 'v2'
            preserved['wordlist_title'] = norm_val.get('title', '')
            preserved['wordlist_note'] = norm_val.get('note', '')
            preserved['groups'] = copy.deepcopy(norm_val.get('groups', []))

    @staticmethod
    def _is_empty_default_speaker(spk):
        if spk is None:
            return False
        default_names = {"发音人 1", "Speaker 1", "默认发音人"}
        if getattr(spk, "name", "") not in default_names:
            return False
        has_items = bool(getattr(spk, "items", None))
        has_long = bool(
            getattr(spk, "long_audio_path", None)
            or getattr(spk, "cli_long_snd_path", None)
            or getattr(spk, "pending_long_snd", None) is not None
        )
        has_batch = bool(getattr(spk, "batch_paths", None) or getattr(spk, "pending_batch_paths", None))
        return not has_items and not has_long and not has_batch

    def _expand_batch_audio_inputs(self, raw_args):
        """
        Expand file paths, directory paths, or wildcard glob patterns into a naturally sorted list of audio file paths.
        Returns (valid_paths, error_message, hint_message).
        """
        syntax_hint = (
            "支持的语法：1) 单个或多个音频文件路径（如 load_batch a.wav b.wav）；"
            "2) 音频所在目录路径（如 load_batch ./female_isolated）；"
            "3) 通配符路径（如 load_batch ./male_isolated/*.wav）。"
            f"支持的常见音频后缀：{', '.join(self.COMMON_AUDIO_EXTENSIONS)}"
        )
        valid_paths = []
        seen = set()

        def add_path(p):
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                valid_paths.append(p)

        for token in raw_args:
            p = str(token).strip()
            if not p:
                continue
            has_wildcard = any(ch in p for ch in ('*', '?', '['))
            if os.path.isdir(p):
                dir_audios = [
                    os.path.join(p, fn)
                    for fn in os.listdir(p)
                    if os.path.isfile(os.path.join(p, fn))
                    and fn.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
                ]
                dir_audios.sort(key=self._natural_sort_key)
                if not dir_audios:
                    return [], f"No supported audio files found in directory: {p}", syntax_hint
                for ap in dir_audios:
                    add_path(ap)
            elif has_wildcard:
                matched = glob.glob(p, recursive=('**' in p))
                matched_files = [
                    m for m in matched
                    if os.path.isfile(m)
                    and (
                        m.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
                        or os.path.splitext(p)[1] != ''
                    )
                ]
                matched_files.sort(key=self._natural_sort_key)
                if not matched_files:
                    return [], f"No audio files matched wildcard pattern: {p}", syntax_hint
                for ap in matched_files:
                    add_path(ap)
            elif os.path.isfile(p):
                add_path(p)
            else:
                return [], f"File or directory not found: {p}", syntax_hint

        if not valid_paths:
            return [], "No valid audio files found", syntax_hint
        return valid_paths, None, None

    def _load_wordlist_any(self, filepath, title=None):
        """
        Unified loader for .ptwl, .csv, structured .txt, and messy .txt wordlists.
        Returns (normalized_doc, groups, flat_words, records).
        """
        ext = os.path.splitext(str(filepath))[1].lower()
        default_title = title or os.path.splitext(os.path.basename(filepath))[0] or "未命名字表"

        if ext == ".ptwl":
            doc = load_wordlist_file(filepath, title=title)
        elif ext == ".csv":
            doc = load_wordlist_from_csv_file(filepath, title=default_title)
        else:
            last_err = None
            raw_text = None
            for enc in ("utf-8-sig", "utf-8", "gbk"):
                try:
                    with open(filepath, "r", encoding=enc) as f:
                        raw_text = f.read()
                    break
                except UnicodeDecodeError as exc:
                    last_err = exc
            if raw_text is None:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    raw_text = f.read()

            stripped = (raw_text or "").lstrip("﻿").strip()
            if stripped.startswith("{"):
                try:
                    doc = normalize_wordlist_document(json.loads(stripped))
                except Exception:
                    doc = None
            else:
                doc = None

            if doc is None:
                if looks_like_structured_wordlist_text(raw_text):
                    doc = build_document_from_structured_text(raw_text, title=default_title)
                elif looks_like_csv_wordlist_text(raw_text):
                    doc = build_document_from_csv_text(raw_text, title=default_title)
                else:
                    doc = build_document_from_messy_text(raw_text, title=default_title)

            if title:
                doc["title"] = str(title).strip()

        doc = normalize_wordlist_document(doc)
        groups, flat_words, records = flatten_wordlist_document(doc)
        return doc, groups, flat_words, records

    def _attach_wordlist_record_to_item(self, item, record=None, speaker=None):
        """
        Mount group, label, tags, note, and fields/custom_fields (plus v2 metadata and speaker info) onto an item dict.
        """
        spk = speaker or self.current_speaker
        apply_record_metadata(item, record)
        if record:
            grp_name = record.get("group") or item.get("group") or "未分组"
            lbl = record.get("label") or record.get("word") or item.get("label") or ""
            tags = list(record.get("item_tags") or record.get("tags") or item.get("tags") or [])
            note = str(record.get("item_note") or record.get("note") or item.get("note") or "")
            aliases = list(record.get("item_aliases") or record.get("aliases") or item.get("aliases") or [])
            fields = dict(
                record.get("item_meta")
                or record.get("fields")
                or record.get("custom_fields")
                or record.get("meta")
                or item.get("fields")
                or {}
            )
            group_tags = list(record.get("group_tags") or item.get("group_tags") or [])
            group_note = str(record.get("group_note") or item.get("group_note") or "")
            group_meta = dict(record.get("group_meta") or item.get("group_meta") or {})
            metadata_source = str(record.get("metadata_source") or item.get("metadata_source") or "人工填写")
            wl_title = str(record.get("wordlist_title") or item.get("wordlist_title") or "")
        else:
            grp_name = item.get("group") or "未分组"
            lbl = item.get("label") or item.get("word") or ""
            tags = list(item.get("tags") or item.get("item_tags") or [])
            note = str(item.get("note") or item.get("item_note") or "")
            aliases = list(item.get("aliases") or item.get("item_aliases") or [])
            fields = dict(item.get("fields") or item.get("custom_fields") or item.get("item_meta") or item.get("meta") or {})
            group_tags = list(item.get("group_tags") or [])
            group_note = str(item.get("group_note") or "")
            group_meta = dict(item.get("group_meta") or {})
            metadata_source = str(item.get("metadata_source") or "人工填写")
            wl_title = str(item.get("wordlist_title") or "")

        item["group"] = grp_name
        item["label"] = lbl
        item["word"] = lbl
        item["tags"] = list(tags)
        item["item_tags"] = list(tags)
        item["note"] = note
        item["item_note"] = note
        item["aliases"] = list(aliases)
        item["item_aliases"] = list(aliases)
        item["fields"] = dict(fields)
        item["custom_fields"] = dict(fields)
        item["meta"] = dict(fields)
        item["item_meta"] = dict(fields)
        item["group_tags"] = list(group_tags)
        item["group_note"] = group_note
        item["group_meta"] = dict(group_meta)
        item["metadata_source"] = metadata_source
        item["wordlist_version"] = "v2"
        if wl_title:
            item["wordlist_title"] = wl_title
        if spk is not None:
            item["speaker"] = getattr(spk, "name", "")
            item["speaker_id"] = getattr(spk, "id", "")
        return item

    def do_wordlist_convert(self, arg):
        """Alias for tool_wordlist_convert."""
        self.do_tool_wordlist_convert(arg)

    def _build_integrated_summary(self, speakers):
        """
        Build multi-speaker integrated summary including merged speaker names,
        valid item counts per speaker, and F0 baseline ranges for 5-level normalization.
        """
        pts = int(self.params.get('pts', 11)) if hasattr(self, 'params') and isinstance(self.params, dict) else 11
        speaker_names = []
        speaker_item_counts = {}
        speaker_baselines = {}

        for s in speakers:
            if self._is_empty_default_speaker(s) and len(speakers) > 1:
                continue
            spk_name = getattr(s, 'name', '未命名')
            speaker_names.append(spk_name)
            valid_count = 0
            f0_vals = []

            for item in getattr(s, 'items', {}).values():
                if not isinstance(item, dict) or item.get('is_excluded', False) or item.get('missing', False):
                    continue
                if not item.get('success', True):
                    continue
                t_s, t_e = item.get('start'), item.get('end')
                if t_s is None or t_e is None or float(t_e) <= float(t_s):
                    continue
                valid_count += 1

                # Collect F0 points for speaker baseline
                try:
                    self._ensure_item_loaded(item)
                    total_dur, syl_data = self._extract_syl_data(item, pts)
                    if total_dur > 0 and syl_data:
                        for _dur, freqs in syl_data:
                            for f in freqs:
                                if f is not None and not np.isnan(f) and f > 0:
                                    f0_vals.append(float(f))
                        continue
                except Exception:
                    pass

                # Fallback to preview_f0 or pitch_data if _extract_syl_data returned nothing
                if isinstance(item.get('preview_f0'), (list, tuple)):
                    for f in item['preview_f0']:
                        if f is not None and not np.isnan(f) and f > 0:
                            f0_vals.append(float(f))
                elif isinstance(item.get('pitch_data'), dict):
                    p_xs = np.asarray(item['pitch_data'].get('xs', []), dtype=float)
                    p_freqs = np.asarray(item['pitch_data'].get('freqs', []), dtype=float)
                    if len(p_xs) == len(p_freqs) and len(p_xs) > 0:
                        mask = (p_xs >= float(t_s)) & (p_xs <= float(t_e)) & np.isfinite(p_freqs) & (p_freqs > 0)
                        f0_vals.extend(p_freqs[mask].tolist())

            speaker_item_counts[spk_name] = valid_count
            if f0_vals:
                min_hz = round(float(np.min(f0_vals)), 2)
                max_hz = round(float(np.max(f0_vals)), 2)
                mean_hz = round(float(np.mean(f0_vals)), 2)
                sd_hz = round(float(np.std(f0_vals)), 2)
            else:
                min_hz = 0.0
                max_hz = 0.0
                mean_hz = 0.0
                sd_hz = 0.0

            speaker_baselines[spk_name] = {
                "min_hz": min_hz,
                "max_hz": max_hz,
                "min_f0_hz": min_hz,
                "max_f0_hz": max_hz,
                "f0_min": min_hz,
                "f0_max": max_hz,
                "mean_hz": mean_hz,
                "sd_hz": sd_hz,
                "range_hz": [min_hz, max_hz],
            }

        return {
            "speakers": speaker_names,
            "speaker_count": len(speaker_names),
            "speaker_item_counts": speaker_item_counts,
            "speaker_baselines": speaker_baselines,
            "total_valid_items": sum(speaker_item_counts.values()),
        }

    def _build_wordlist_v2_doc_from_project(self, title=None):
        from modules.wordlist_v2 import normalize_wordlist_document, DEFAULT_REVIEW_STATUS
        doc_title = title or getattr(self, "project_title", None) or ""
        groups_dict = OrderedDict()

        for spk in self.speaker_manager.get_all_speakers():
            for item_id, item in spk.items.items():
                if not isinstance(item, dict):
                    continue
                if not doc_title and item.get("wordlist_title"):
                    doc_title = str(item.get("wordlist_title"))
                gname = str(item.get("group") or "默认组")
                if gname not in groups_dict:
                    groups_dict[gname] = {
                        "id": f"grp_{len(groups_dict) + 1}",
                        "name": gname,
                        "note": str(item.get("group_note") or ""),
                        "tags": list(item.get("group_tags") or []),
                        "meta": {},
                        "items_by_label": OrderedDict(),
                    }
                g_entry = groups_dict[gname]
                if not g_entry["note"] and item.get("group_note"):
                    g_entry["note"] = str(item.get("group_note"))
                for gt in (item.get("group_tags") or []):
                    if gt and gt not in g_entry["tags"]:
                        g_entry["tags"].append(gt)

                lbl = str(item.get("label") or item.get("word") or item_id).strip()
                if not lbl:
                    continue
                custom_meta = {}
                for mk_src in ("meta", "item_meta", "fields", "custom_fields"):
                    mval = item.get(mk_src)
                    if isinstance(mval, dict):
                        for mk, mv in mval.items():
                            if mk is not None and str(mk).strip() and mv is not None and str(mv).strip():
                                custom_meta[str(mk).strip()] = str(mv).strip()
                tags = list(item.get("item_tags") or item.get("tags") or [])
                aliases = list(item.get("item_aliases") or item.get("aliases") or [])
                note = str(item.get("item_note") or item.get("note") or "")
                source = str(item.get("metadata_source") or DEFAULT_REVIEW_STATUS)

                if lbl not in g_entry["items_by_label"]:
                    g_entry["items_by_label"][lbl] = {
                        "id": str(item_id),
                        "label": lbl,
                        "note": note,
                        "tags": tags,
                        "aliases": aliases,
                        "meta": custom_meta,
                        "metadata_source": source,
                    }
                else:
                    existing = g_entry["items_by_label"][lbl]
                    if not existing["note"] and note:
                        existing["note"] = note
                    for t in tags:
                        if t not in existing["tags"]:
                            existing["tags"].append(t)
                    for a in aliases:
                        if a not in existing["aliases"]:
                            existing["aliases"].append(a)
                    existing["meta"].update(custom_meta)

        groups_list = []
        for gname, g_entry in groups_dict.items():
            groups_list.append({
                "id": g_entry["id"],
                "name": gname,
                "note": g_entry["note"],
                "tags": g_entry["tags"],
                "meta": g_entry["meta"],
                "items": list(g_entry["items_by_label"].values()),
            })

        raw_doc = {
            "title": doc_title or "PhonTracer工程字表",
            "note": "",
            "groups": groups_list,
        }
        return normalize_wordlist_document(raw_doc)

    def _export_fallback_teproj_archive(self, teproj_path, project_data):
        """当内存中为纯缓存数据（无本地物理音频文件）时，直接生成合规的 .teproj 归档包。"""
        import zipfile
        import io
        import time
        import copy

        state_copy = copy.deepcopy(project_data)
        state_copy["version"] = "1.0"
        state_copy["format_version"] = "2.0"
        state_copy["software_version"] = __version__
        state_copy["save_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state_copy["trim_silence"] = bool(self.params.get("trim_silence", True))
        state_copy["export_numbering_rule"] = "continuous"

        npz_blobs = {}
        for spk_id, spk in state_copy.get("speakers", {}).items():
            items = spk.get("items", {})
            for item_id, item in items.items():
                if not isinstance(item, dict):
                    continue
                pd = item.pop("pitch_data", None)
                if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None:
                    rel_p = f"data/{spk_id}_{item_id}_pitch.npz"
                    buf = io.BytesIO()
                    np.savez_compressed(buf, xs=np.asarray(pd["xs"], dtype=float), freqs=np.asarray(pd["freqs"], dtype=float))
                    npz_blobs[rel_p] = buf.getvalue()
                    item["pitch_data_file"] = rel_p

                fd = item.pop("formant_data", None)
                if isinstance(fd, dict) and fd.get("f1") is not None and fd.get("f2") is not None:
                    rel_f = f"data/{spk_id}_{item_id}_formant.npz"
                    buf = io.BytesIO()
                    save_kw = {
                        "xs": np.asarray(fd.get("xs", []), dtype=float),
                        "f1": np.asarray(fd["f1"], dtype=float),
                        "f2": np.asarray(fd["f2"], dtype=float),
                    }
                    if fd.get("f3") is not None:
                        save_kw["f3"] = np.asarray(fd["f3"], dtype=float)
                    np.savez_compressed(buf, **save_kw)
                    npz_blobs[rel_f] = buf.getvalue()
                    item["formant_data_file"] = rel_f

        os.makedirs(os.path.dirname(os.path.abspath(teproj_path)) or ".", exist_ok=True)
        with zipfile.ZipFile(teproj_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(state_copy, ensure_ascii=False, indent=2))
            for rel_name, blob in npz_blobs.items():
                zf.writestr(rel_name, blob)
        return True

    def _scan_item_warnings_detailed(self, item_id, item, spk_median_f0=None):
        """全面扫描单条目的异常类型（octave_jump, low_f0_coverage, no_clear_valley, tiny_segment），与 report_generator 保持 100% 同口径。"""
        from modules.anomaly_detection import detect_pitch_anomaly_points
        from modules.report_generator import check_item_has_empty_f0
        issues = []
        t_s, t_e = item.get("start"), item.get("end")
        dur = (float(t_e) - float(t_s)) if (t_s is not None and t_e is not None and float(t_e) > float(t_s)) else 0.0

        split_warns = list(item.get("split_warnings") or [])
        if "tiny_segment" in split_warns or (0 < dur < 0.08):
            issues.append("tiny_segment")
        if "no_clear_valley" in split_warns or "fallback_equal_split" in split_warns or "imbalanced_duration" in split_warns:
            issues.append("no_clear_valley")

        # Check F0 coverage and octave jumps
        p_data = item.get("pitch_data")
        has_empty = self._check_item_has_empty_data(item)
        pts = int(item.get("pts") or self.params.get("pts", 11))

        active_ratio = 1.0
        anomaly_pts = []
        report_empty_f0 = False
        if isinstance(p_data, dict) and p_data.get("xs") is not None and p_data.get("freqs") is not None:
            p_xs = np.asarray(p_data["xs"], dtype=float)
            p_freqs = np.asarray(p_data["freqs"], dtype=float)
            if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                mask = (p_xs >= float(t_s)) & (p_xs <= float(t_e))
                xs_slice = p_xs[mask]
                freqs_slice = p_freqs[mask]
            else:
                xs_slice = p_xs
                freqs_slice = p_freqs

            try:
                report_empty_f0 = bool(check_item_has_empty_f0(item, {"xs": p_xs, "freqs": p_freqs}, pts))
            except Exception:
                report_empty_f0 = False

            if freqs_slice.size > 0:
                valid_mask = np.isfinite(freqs_slice) & (freqs_slice > 0)
                active_ratio = float(np.sum(valid_mask)) / float(freqs_slice.size)
                syls = split_into_syllables(item.get("label", ""))
                chars_bounds = item.get("chars_bounds") or []
                if chars_bounds and len(chars_bounds) == len(syls):
                    bounds = chars_bounds
                elif t_s is not None and t_e is not None:
                    bounds = [[float(t_s), float(t_e)]]
                else:
                    bounds = None
                anomaly_pts = detect_pitch_anomaly_points(xs_slice, freqs_slice, bounds=bounds, start=t_s, end=t_e)

                if not anomaly_pts and spk_median_f0 and spk_median_f0 > 0 and np.any(valid_mask):
                    vf = freqs_slice[valid_mask]
                    vx = xs_slice[valid_mask]
                    if len(vf) < 4:
                        extreme_mask = (vf >= spk_median_f0 * 1.75) | (vf <= spk_median_f0 / 1.75)
                        if np.any(extreme_mask):
                            anomaly_pts = [(float(t), float(f)) for t, f in zip(vx[extreme_mask], vf[extreme_mask])]

        if "low_f0_coverage" in split_warns or has_empty or report_empty_f0 or active_ratio < 0.35:
            issues.append("low_f0_coverage")
        if len(anomaly_pts) > 0:
            issues.append("octave_jump")

        return {
            "item_id": item_id,
            "label": item.get("label", ""),
            "group": item.get("group", "默认组"),
            "duration_s": round(dur, 4),
            "active_f0_ratio": round(active_ratio, 3),
            "issues": list(dict.fromkeys(issues)),
            "anomaly_points_count": len(anomaly_pts),
            "anomaly_points": [(round(t, 3), round(f, 1)) for t, f in anomaly_pts[:5]],
            "split_warnings": split_warns,
            "has_empty_data": bool(has_empty or report_empty_f0),
        }

    def _check_item_has_any_warning(self, item_id, item, spk_median_f0=None):
        """统一判断词条是否存在任何声学或切分警告（与 fix_warnings、stats、report_export 口径完全一致）。"""
        if self._check_item_has_empty_data(item):
            return True
        if isinstance(item, dict) and (item.get("pitch_data") is not None or item.get("split_warnings")):
            diag = self._scan_item_warnings_detailed(item_id, item, spk_median_f0=spk_median_f0)
            return bool(diag.get("issues"))
        return False

    @property
    def executor(self):
        """惰性初始化并发进程池，避免启动或单命令时无条件拉起 8 个进程造成冷启动卡顿"""
        if self._executor is None:
            self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
        return self._executor

    @executor.setter
    def executor(self, val):
        self._executor = val

    def _has_meaningful_session_data(self) -> bool:
        if getattr(self, "_session_dirty", False):
            return True
        try:
            speakers = self.speaker_manager.get_all_speakers()
        except Exception:
            return False
        if len(speakers) > 1:
            return True
        for spk in speakers:
            if len(getattr(spk, "items", {})) > 0:
                return True
            if getattr(spk, "pending_long_snd", None) is not None or getattr(spk, "long_audio_path", None):
                return True
            if getattr(spk, "pending_batch_paths", None):
                return True
            if getattr(spk, "name", "发音人 1") != "发音人 1":
                return True
        return False

    def _persist_bound_session(self):
        """在命令或会话结束前自动回写 --workspace 与 --project 状态（若已由 _after_state_change 落盘且未再产生变更则跳过重复打包）"""
        if getattr(self, "_explicit_workspace_dir", None):
            ws_proj_json = os.path.join(self._explicit_workspace_dir, "project.json")
            if getattr(self, "_session_dirty", False) or (not os.path.exists(ws_proj_json) and self._has_meaningful_session_data()):
                try:
                    self.project_manager.save_to_workspace()
                except Exception:
                    pass

        if getattr(self, "_bound_project_path", None):
            if getattr(self, "_session_dirty", False) or (not os.path.exists(self._bound_project_path) and self._has_meaningful_session_data()):
                try:
                    orig_active_id = self.speaker_manager.active_speaker_id
                    try:
                        for spk in self.speaker_manager.get_all_speakers():
                            self.speaker_manager.set_active_speaker(spk.id)
                            for item in spk.items.values():
                                self._ensure_item_loaded(item)
                    finally:
                        if orig_active_id:
                            self.speaker_manager.set_active_speaker(orig_active_id)
                    export_fn = self.project_manager.export_project
                    if export_fn(self._bound_project_path):
                        self._session_dirty = False
                except Exception:
                    pass
        else:
            self._session_dirty = False

    def cleanup_workspace(self):
        """保存绑定会话并清理专属临时工作区（绝不删除用户显式指定的 --workspace 目录）"""
        try:
            self._persist_bound_session()
        except Exception:
            pass
        if getattr(self, "_executor", None) is not None:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass
            self._executor = None
        if getattr(self, "_explicit_workspace_dir", None):
            try:
                if hasattr(self, "project_manager"):
                    self.project_manager.release_workspace_lock()
            except Exception:
                pass
            return
        if getattr(self, "_temp_workspace_dir", None) and os.path.exists(self._temp_workspace_dir):
            try:
                if hasattr(self, "project_manager"):
                    self.project_manager.release_workspace_lock()
                shutil.rmtree(self._temp_workspace_dir, ignore_errors=True)
            except Exception:
                pass
            self._temp_workspace_dir = None

    @staticmethod
    def _clean_bom_and_whitespace(line: str) -> str:
        if not isinstance(line, str):
            return ""
        cleaned = line.lstrip('\ufeff\ufffe\u200b').strip()
        if cleaned:
            cleaned = cleaned.lstrip('\ufeff\ufffe\u200b').strip()
        return cleaned

    @staticmethod
    def _safe_normalize_cli_line(line: str) -> str:
        if not isinstance(line, str):
            return ""
        line = PhonTracerCLI._clean_bom_and_whitespace(line)
        if "\\" not in line:
            return line
        marker_esc_quote = "__ESCAPED_QUOTE_TOKEN__"
        marker_unc = "__UNC_PREFIX_TOKEN__"
        s = line.replace(r'\"', marker_esc_quote)
        s = re.sub(r'(^|[\s"\'])\\\\(?!\\)', r'\1' + marker_unc, s)
        s = s.replace('\\', '/')
        s = s.replace(marker_unc, r'\\')
        s = s.replace(marker_esc_quote, r'\"')
        return s

    def parseline(self, line):
        if isinstance(line, str):
            line = self._clean_bom_and_whitespace(line)
        cmd_name, arg, line = super().parseline(line)
        if isinstance(cmd_name, str):
            cmd_name = cmd_name.lstrip('\ufeff\ufffe\u200b').strip()
        return cmd_name, arg, line

    def onecmd(self, line):
        if isinstance(line, str):
            line = self._safe_normalize_cli_line(line)
            if not line or line.startswith("#"):
                return False
            # 拦截 `<cmd> --help` / `<cmd> -h` / `<cmd> ?` 细粒度子命令自省帮助
            try:
                tokens = shlex.split(line)
            except ValueError:
                tokens = line.split()
            if tokens:
                first_tok = tokens[0].lstrip('\ufeff\ufffe\u200b').strip()
                rest_toks = [t.strip() for t in tokens[1:]]
                if first_tok.lower() not in ("help", "?"):
                    if len(rest_toks) == 1 and rest_toks[0].lower() in ("--help", "-h", "?"):
                        if self._emit_subcommand_help(first_tok):
                            return False
        return super().onecmd(line)

    def _emit(self, success=True, message="", **payload):
        self._last_emit_success = bool(success)
        data = {"success": bool(success)}
        if message:
            data["message"] = message
        data.update(payload)
        self._last_emit_payload = data
        print(json.dumps(data, ensure_ascii=False, allow_nan=False))

    def _after_state_change(self, action="updated"):
        self._session_dirty = True
        # 在 --script-file 或管道批量执行期间延迟归档落盘，待批量队列结束时在 cleanup_workspace 中一次性保存
        if getattr(self, "_defer_persistence", False):
            return
        if getattr(self.project_manager, "auto_save_enabled", False):
            self.project_manager.trigger_auto_save()
        if getattr(self, "_explicit_workspace_dir", None):
            try:
                self.project_manager.save_to_workspace(incremental=True)
                if not getattr(self, "_bound_project_path", None):
                    self._session_dirty = False
            except Exception:
                pass
        if getattr(self, "_bound_project_path", None):
            try:
                orig_active_id = self.speaker_manager.active_speaker_id
                try:
                    for spk in self.speaker_manager.get_all_speakers():
                        self.speaker_manager.set_active_speaker(spk.id)
                        for item in spk.items.values():
                            self._ensure_item_loaded(item)
                finally:
                    if orig_active_id:
                        self.speaker_manager.set_active_speaker(orig_active_id)
                export_fn = self.project_manager.export_project
                if export_fn(self._bound_project_path):
                    self._session_dirty = False
            except Exception:
                pass

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
        if 'show_f3' not in self.current_speaker.last_params:
            self.current_speaker.last_params['show_f3'] = False
        return self.current_speaker.last_params

    @property
    def mode(self):
        tab = getattr(self.current_speaker, 'tab_mode', '多条独立音频')
        return 'long' if '单条' in tab else 'batch'

    @mode.setter
    def mode(self, value):
        if value in ('long', 'single', '单条长音频'):
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
        if isinstance(line, str):
            line = self._clean_bom_and_whitespace(line)
        if self.log_file:
            self.log_file.write(f"> {line}\n")
            self.log_file.flush()
        return line

    def _is_external_command(self, line_text: str) -> bool:
        test_line = line_text.strip()
        if test_line.startswith('&'):
            test_line = test_line[1:].strip()

        banned_commands = {
            "python", "py", "powershell", "pwsh", "cmd", "bash", "sh",
            "node", "rscript", "praat", "parselmouth", "start", "call"
        }
        banned_suffixes = ('.bat', '.cmd', '.ps1', '.py', '.r', '.js', '.vbs')

        token_sets = []
        try:
            token_sets.append(shlex.split(test_line))
        except Exception:
            pass
        token_sets.append(test_line.split())

        for tokens in token_sets:
            if not tokens:
                continue
            first = tokens[0].strip().lower().strip('\'"')
            if first.startswith('.\\') or first.startswith('./'):
                first = first[2:]
            cmd_name = os.path.basename(first.replace('\\', '/'))
            if cmd_name.endswith('.exe'):
                cmd_name = cmd_name[:-4]
            if cmd_name in banned_commands:
                return True
            for t in tokens:
                t_clean = t.strip().lower().strip('\'"')
                if t_clean.endswith(banned_suffixes):
                    return True
        return False

    def default(self, line):
        line_stripped = self._clean_bom_and_whitespace(line)
        if not line_stripped or line_stripped.startswith("#"):
            return

        if self._is_external_command(line_stripped):
            self._emit(
                False,
                "Detected an external shell/script style command (External automation attempt). PhonTracerCLI does not execute external commands directly; please use built-in CLI commands.",
                error=f"Unknown CLI command: {line_stripped}",
                next_steps=["help", "agent_guide", "status"],
            )
        else:
            self._emit(
                False,
                "Use help or agent_guide to see supported PhonTracer CLI commands.",
                error=f"Unknown CLI command: {line_stripped}",
                hint="输入 help 或 agent_guide 查看内置命令列表，或使用 <cmd> --help 查看具体指令语法。",
            )

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
        Valid keys: pts, db, skip_front, pitch_floor, pitch_ceiling, voicing_threshold, trim_silence, analysis_mode, formant_max_hz, formant_count, formant_window_length, formant_pre_emphasis, formant_sample_strategy, show_f3
        Example: set_params db=50.0 trim_silence=False analysis_mode=formant formant_max_hz=5500
        """
        args = shlex.split(arg)
        if not args:
            self._emit(True, params=self.params)
            return

        param_aliases = {
            "f0_min": "pitch_floor",
            "f0_max": "pitch_ceiling",
            "voicing_thresh": "voicing_threshold",
            "max_formant": "formant_max_hz",
            "num_formants": "formant_count",
        }
        allowed_extra = {"despike", "silence_threshold"}
        updated = False
        for kv in args:
            if '=' in kv:
                k_raw, v = kv.split('=', 1)
                k = param_aliases.get(k_raw.strip(), k_raw.strip())
                if k in self.params or k in allowed_extra:
                    try:
                        if k in ('trim_silence', 'show_f3', 'despike'):
                            self.params[k] = v.lower() in ('true', '1', 'yes')
                        elif k in ('pts', 'pitch_floor', 'pitch_ceiling', 'formant_count'):
                            self.params[k] = int(float(v))
                        elif k == 'analysis_mode':
                            val_str = str(v).lower().strip()
                            if val_str not in ('f0', 'formant'):
                                self._emit(False, error="Invalid value for analysis_mode: must be 'f0' or 'formant'")
                                return
                            self.params[k] = val_str
                        elif k == 'formant_sample_strategy':
                            self.params[k] = str(v)
                        elif k in ('formant_max_hz', 'formant_window_length', 'formant_pre_emphasis', 'silence_threshold'):
                            self.params[k] = float(v)
                        else:
                            self.params[k] = float(v)
                        updated = True
                    except ValueError:
                        self._emit(False, error=f"Invalid value for {k_raw}")
                        return
                else:
                    self._emit(False, error=f"Unknown parameter {k_raw}")
                    return

        if updated:
            self._after_state_change("set_params")
        self._emit(True, "Parameters updated", params=self.params)

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
            self._emit(True, "语言已切换为中文", lang="zh")
        elif choice in ('en', 'english', '英文'):
            self.lang = 'en'
            self.prompt = "(phontracer) "
            self._emit(True, "Language switched to English", lang="en")
        else:
            self._emit(True, lang=self.lang)

    @staticmethod
    def _get_subcommand_help_catalog():
        supported_export_formats = [
            "xlsx", "txt", "textgrid", "ptwl",
            "contour", "density", "distribution", "quality", "overview_heatmap",
            "formant_space", "f1_f2", "duration", "trend",
            "line_chart", "kde", "wav", "merged_wav",
            "formant_table", "formant_trajectory", "formant_density", "formant_overview_heatmap"
        ]
        catalog = {
            "export": {
                "command": "export",
                "usage": "export <format> <output_file_or_dir> [rule] [target] [key=value ...]",
                "description": "导出声学数据表（xlsx/txt）、标注文件（textgrid）、高级字表（ptwl）或科研图表（contour/density/distribution/quality/overview_heatmap/formant_space/f1_f2/duration/trend 等）。",
                "supported_formats": supported_export_formats,
                "positional_args": {
                    "format": "导出格式枚举（见 supported_formats）",
                    "output_file_or_dir": "输出文件路径（target=separate 时为输出目录路径）",
                    "rule": "编号规则：continuous（默认连续编号）| per_group（按组重新编号）| 数值（wav/merged_wav 缓冲或静音间隔秒数）",
                    "target": "多发音人范围：active（仅当前激活发音人，默认）| integrated（多发音人合并计算五度值/同图对比）| separate（按发音人分别导出）",
                },
                "key_value_options": {
                    "target": "active | integrated | separate（同位置参数 target / export_scope）",
                    "scale": "t_value（五度值 0-5，默认）| hz（原始赫兹频率）",
                    "groupby": "group（按声调/组别，默认）| label（按词语）| speaker（按发音人）",
                    "facet": "none（单图不分面）| group（按声调类型分面）| speaker（按发音人分面）| syllable_position（按音节位置分面）| label（按词项分面）",
                    "normalization": "speaker（按发音人归一化，推荐多发音人密度图使用）| global（全局归一化）| Hz | Lobanov",
                    "chart_group": "default | label | speaker | review | item_tag:<标签1,标签2> | group_tag:<组标签> | contains_item_tag:<标签> | filter_item_tag:<标签> | meta:<自定义字段名>",
                    "format": "png（默认）| svg | pdf",
                    "contour_x": "normalized（归一化采样点）| duration（真实物理时长）",
                    "contour_content": "average（仅均值曲线）| average_individual（均值+个体浅色细线）| average_sd_ci（均值+标准差/置信区间阴影）",
                    "dist_type": "boxplot_violin | start_mid_end | range | variability",
                    "dist_style": "boxplot | violinplot",
                    "density_bw": "核密度带宽浮点数（默认 0.15）",
                    "overview_metric": "mean（均值热图）| sd（标准差热图）| combination（调类组合热图）",
                    "overview_show_deviation": "true | false（是否显示与组均值的偏差）",
                },
                "examples": [
                    "export xlsx out.xlsx continuous integrated",
                    "export ptwl upgraded.ptwl",
                    "export contour tone_contour.svg target=integrated scale=t_value groupby=group facet=none format=svg",
                    "export density tone_density.png target=integrated normalization=speaker facet=group",
                    "export formant_space vowel_space.png target=integrated groupby=group",
                    "export overview_heatmap overview.png target=integrated groupby=label",
                ],
            },
            "speakers": {
                "command": "speakers",
                "aliases": ["speaker", "speakers", "add_speaker", "switch_speaker", "rename_speaker", "remove_speaker"],
                "usage": "speakers | add_speaker <name> [--switch] | switch_speaker <name_or_id> | rename_speaker <old_name_or_id> <new_name> | remove_speaker <name_or_id>",
                "description": "多发音人生命周期管理：查看发音人列表、新增发音人、切换当前发音人、重命名发音人（如将默认'发音人 1'改名为真实姓名）及删除发音人。",
                "subcommands": {
                    "speakers": "列出所有发音人（id, name, active, items_count）",
                    "add_speaker": "add_speaker <name> [--switch]：添加新发音人（如当前仅有空的默认'发音人 1'，也可直接用 rename_speaker 改名）",
                    "switch_speaker": "switch_speaker <name_or_id>：切换当前激活的发音人",
                    "rename_speaker": "rename_speaker <old_name_or_id> <new_name>：重命名指定发音人（若只传 1 个参数则重命名当前激活发音人）",
                    "remove_speaker": "remove_speaker <name_or_id>：删除指定发音人（至少保留 1 个发音人）",
                },
                "key_value_options": {
                    "--switch": "在 add_speaker 时立即将该发音人设为当前激活发音人",
                },
                "examples": [
                    'rename_speaker "发音人 1" F01_女声',
                    "add_speaker M01_男声 --switch",
                    "switch_speaker F01_女声",
                    "speakers",
                ],
            },
            "apply_wordlist": {
                "command": "apply_wordlist",
                "usage": "apply_wordlist <wordlist_filepath> [match_mode]",
                "description": "导入并应用字词表（支持普通 .txt、结构化 v2 文本、.ptwl 高级字表、.csv 表格字表），对长音频做自动音节切分或对独立短音频做匹配。",
                "supported_formats": ["txt", "ptwl", "csv"],
                "positional_args": {
                    "wordlist_filepath": "字词表文件路径（.txt / .ptwl / .csv）",
                    "match_mode": "fuzzy（默认，按词名模糊匹配文件名）| order（严格按物理顺序一对一匹配）",
                },
                "key_value_options": {
                    "match_mode": "fuzzy | order",
                },
                "examples": [
                    "apply_wordlist wordlist.txt fuzzy",
                    "apply_wordlist experiment.ptwl order",
                ],
            },
            "load_long": {
                "command": "load_long",
                "usage": "load_long <filepath>",
                "description": "加载单条长音频文件并切换当前发音人为长音频模式（long）。",
                "supported_formats": ["wav", "mp3", "flac", "ogg"],
                "examples": ["load_long female_long.wav"],
            },
            "load_batch": {
                "command": "load_batch",
                "usage": "load_batch <file1_or_dir_or_glob> [file2 ...]",
                "description": "加载多条独立短音频文件（支持逐个文件路径、文件夹目录自动扫描、或 `*.wav` 通配符展开，自动按自然顺序排序）。",
                "supported_formats": ["wav", "mp3", "flac", "ogg"],
                "key_value_options": {
                    "recursive": "true | false（传入目录时是否递归扫描子目录，默认 false）",
                },
                "examples": [
                    "load_batch ./female_isolated",
                    "load_batch ./male_isolated/*.wav",
                    "load_batch a01.wav a02.wav a03.wav",
                ],
            },
            "stats": {
                "command": "stats",
                "usage": "stats [by=group|item|speaker] [scope=active|integrated] [out=<path.json|path.md>]",
                "description": "计算并返回当前工程的结构化声学统计摘要 JSON（含样本数、时长、首/中/尾 F0、极差、均值±SD、五度值 T 值轨迹与编码、发音人中位数 P50、组间单因素方差分析 ANOVA F/p 值及共振峰均值）。",
                "key_value_options": {
                    "by": "group（按组别汇总，默认）| item（按词条明细汇总）| speaker（按发音人汇总）",
                    "scope": "active（仅当前发音人，默认）| integrated（合并所有发音人）",
                    "out": "可选，将统计 JSON 或 Markdown 摘要表直接保存到指定文件路径",
                },
                "examples": [
                    "stats by=group scope=integrated",
                    "stats by=speaker scope=integrated",
                    "stats by=group scope=integrated out=stats_summary.json",
                ],
            },
            "export_bundle": {
                "command": "export_bundle",
                "usage": "export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg] [title=<project_title>]",
                "description": "一站式打包导出科研产物到指定目录：完整工程 (.teproj)、高级字表 (.ptwl)、数据档案 (acoustic_data.xlsx)、采样点文本 (acoustic_points.txt)、统计 JSON (acoustic_stats.json)、全套科研图表 (charts/) 与研究方法报告 (report/研究方法报告.md)。",
                "key_value_options": {
                    "scope": "active | integrated（默认多发音人自动使用 integrated）| separate",
                    "format": "png（默认）| svg | pdf",
                    "title": "可选，自定义导出的 .teproj 与 .ptwl 文件名前缀",
                },
                "examples": [
                    "export_bundle ./output_bundle scope=integrated format=png",
                    "export_bundle ./output_bundle scope=integrated title=普通话声调实验",
                ],
            },
            "tool_wordlist_convert": {
                "command": "tool_wordlist_convert",
                "usage": "tool_wordlist_convert <input_wordlist> <output.ptwl> [title=<字表标题>]",
                "description": "将普通杂乱字表 (.txt)、结构化字表文本或表格字表 (.csv) 解析升级并保存为标准 v2 高级字表 (.ptwl)。注：apply_wordlist 已原生支持直接传入 .txt/.csv/.ptwl，且 export_bundle 会自动导出 .ptwl，通常无需单独调用本命令。",
                "supported_formats": ["txt", "csv", "ptwl"],
                "key_value_options": {
                    "title": "指定生成的 .ptwl 字表标题",
                },
                "examples": [
                    "tool_wordlist_convert raw_wordlist.txt standard.ptwl",
                    "tool_wordlist_convert raw_wordlist.csv standard.ptwl title=普通话声调实验",
                ],
            },
            "detect_f0": {
                "command": "detect_f0",
                "usage": "detect_f0 [recommended|conservative|fine]",
                "description": "自动探测当前发音人的有效基频分布（中位数 P50 及 P5~P95），给出 conservative / recommended / fine 三档建议，传入 recommended 直接应用最佳基频上下限。",
                "supported_formats": ["recommended", "conservative", "fine"],
                "examples": [
                    "detect_f0 recommended",
                    "detect_f0",
                ],
            },
            "detect_formants": {
                "command": "detect_formants",
                "usage": "detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution|reco|anti|fine]",
                "description": "自动评估当前发音人样本的共振峰追踪质量评分，给出推荐档 (recommended/reco)、保守复核档 (conservative/anti)、高分辨率档 (high_resolution/fine) 三档参数并支持直接应用。",
                "supported_formats": ["recommended", "conservative", "high_resolution", "reco", "anti", "fine"],
                "key_value_options": {
                    "apply": "true（默认自动应用）| false（或传入 --no-apply 仅预览建议）",
                    "preset": "recommended (或 reco) | conservative (或 anti) | high_resolution (或 fine)",
                },
                "examples": [
                    "detect_formants",
                    "detect_formants recommended",
                    "detect_formants --no-apply",
                ],
            },
            "fix_warnings": {
                "command": "fix_warnings",
                "usage": "fix_warnings [--apply|--no-apply] [scope=active|all|integrated] [apply=true|false] [pitch_floor=<Hz>] [voicing_threshold=<val>]",
                "description": "全面扫描并修复倍频/半频跳变 (octave_jump)、有效基频覆盖率低/空采样点 (low_f0_coverage)、多音节谷值切分不显著 (no_clear_valley) 与过短音段 (tiny_segment)。传入 --apply 或 apply=true 一键自动修复。",
                "key_value_options": {
                    "apply": "true（或传入 --apply，直接自动应用修复参数并重算插值）| false（或 --no-apply，仅诊断不修改）",
                    "scope": "active（仅当前发音人，默认）| all / integrated（修复工程内全部发音人）",
                    "pitch_floor": "可选，指定修复时使用的音高下限 Hz（默认按发音人中位数自适应收缩）",
                    "voicing_threshold": "可选，指定修复时使用的浊音阈值（默认自动下探至 0.15）",
                },
                "examples": [
                    "fix_warnings --apply scope=all",
                    "fix_warnings --apply",
                    "fix_warnings apply=false scope=all",
                ],
            },
            "set_params": {
                "command": "set_params",
                "usage": "set_params [key=value ...]",
                "description": "查看或更新当前发音人的全局声学提取参数。",
                "key_value_options": {
                    "analysis_mode": "f0 | formant",
                    "pts": "插值采样点数（默认 11）",
                    "db": "VAD 能量阈值（默认 60.0）",
                    "skip_front": "跳过声母时长秒数（默认 0.0）",
                    "pitch_floor": "基频下限 Hz（默认 75）",
                    "pitch_ceiling": "基频上限 Hz（默认 600）",
                    "voicing_threshold": "浊音阈值（默认 0.25）",
                    "trim_silence": "true | false（默认 true）",
                    "formant_max_hz": "共振峰搜索上限 Hz（默认 5500.0）",
                    "formant_count": "共振峰数量（默认 5）",
                    "formant_window_length": "共振峰窗长秒数（默认 0.025）",
                    "formant_pre_emphasis": "共振峰预加重 Hz（默认 50.0）",
                    "formant_sample_strategy": "整段11点 | 中段均值",
                    "show_f3": "true | false（默认 false）",
                },
                "examples": [
                    "set_params pitch_floor=60 pitch_ceiling=450 voicing_threshold=0.20",
                    "set_params analysis_mode=formant formant_max_hz=5500",
                ],
            },
        }
        # Alias mappings
        catalog["speaker"] = catalog["speakers"]
        catalog["add_speaker"] = catalog["speakers"]
        catalog["switch_speaker"] = catalog["speakers"]
        catalog["rename_speaker"] = catalog["speakers"]
        catalog["remove_speaker"] = catalog["speakers"]
        return catalog

    def _emit_subcommand_help(self, topic: str) -> bool:
        topic_clean = self._clean_bom_and_whitespace(topic).lower().lstrip("-")
        catalog = self._get_subcommand_help_catalog()
        if topic_clean in ("all", "*"):
            alias_keys = {"speaker", "add_speaker", "switch_speaker", "rename_speaker", "remove_speaker"}
            all_commands = {}
            for k, v in catalog.items():
                if k in alias_keys:
                    continue
                item_info = dict(v)
                if "usage" in item_info and "syntax" not in item_info:
                    item_info["syntax"] = item_info["usage"]
                if "key_value_options" in item_info and "options" not in item_info:
                    item_info["options"] = item_info["key_value_options"]
                all_commands[k] = item_info
            self._emit(
                True,
                "All subcommand help schemas (一次性返回全部核心子命令参数字典，无需再逐个调用 help <cmd>)",
                command="all",
                syntax="help all",
                count=len(all_commands),
                commands=all_commands,
            )
            return True
        if topic_clean in catalog:
            info = dict(catalog[topic_clean])
            if "usage" in info and "syntax" not in info:
                info["syntax"] = info["usage"]
            if "key_value_options" in info and "options" not in info:
                info["options"] = info["key_value_options"]
            self._emit(
                True,
                f"Help for '{topic_clean}'",
                help=info,
                **info,
            )
            return True

        method = getattr(self, f"do_{topic_clean}", None)
        if callable(method) and getattr(method, "__doc__", None):
            doc = inspect.cleandoc(method.__doc__)
            lines = [ln.strip() for ln in doc.splitlines() if ln.strip()]
            usage = next((ln.replace("Usage:", "").replace("用法：", "").strip() for ln in lines if ln.lower().startswith("usage:") or ln.startswith("用法：")), topic_clean)
            self._emit(
                True,
                f"Help for '{topic_clean}'",
                command=topic_clean,
                usage=usage,
                syntax=usage,
                description=" ".join(lines),
                help={"command": topic_clean, "usage": usage, "syntax": usage, "description": " ".join(lines)},
            )
            return True
        return False

    def do_help(self, arg):
        arg_clean = self._clean_bom_and_whitespace(arg)
        if not arg_clean:
            text = get_cli_help_text(getattr(self, 'lang', 'en'))
            if getattr(self, 'json_mode', False):
                self._emit(True, "CLI Manual", manual=text.strip(), lang=getattr(self, 'lang', 'en'))
            else:
                print(text)
        else:
            if self._emit_subcommand_help(arg_clean):
                return
            self._emit(
                False,
                f"No help found for '{arg_clean}'. Use 'help' or 'agent_guide' to list all commands.",
                error=f"Unknown help topic: {arg_clean}",
                hint="可用帮助主题包括：speaker, speakers, export, apply_wordlist, load_batch, load_long, stats, export_bundle, tool_wordlist_convert, detect_f0, detect_formants, fix_warnings, set_params 等。",
            )

    def do_agent_guide(self, arg):
        """
        Show operating rules and workflow paths for AI agents.
        Usage: agent_guide
        """
        self._emit(
            True,
            "AI 极速批处理最佳实践：首选将完整建库、切分、探测、修复、统计与 `export_bundle` 命令写入一个 UTF-8 剧本文件，通过 `python cli.py --project <session.teproj> --script-file <cmds.txt>` **1 次调用跑完全流程**！`--project` 单步模式仅用于后续增量微调或单步追问。本指南已内嵌 `core_command_cheatsheet` 全参数速查表，切勿分多轮逐个查询 `help <cmd>`。",
            primary_recommended_mode="--script-file <cmds.txt> (配合 --project <session.teproj> 与 export_bundle <out_dir> 一步完成全流程)",
            stateful_session_best_practices={
                "title": "有状态会话与极速批处理最佳实践 (Batch-First & Stateful Session Best Practices)",
                "primary_batch_script_mode": "【AI / 批处理全流程默认首选！】将建库、发音人添加、音频加载、字表切分、`detect_f0 recommended`、`fix_warnings --apply`、`stats` 与 `export_bundle` 一次性按行写入 `cmds.txt`，执行 `python cli.py --project <session.teproj> --script-file cmds.txt`（1 次进程调用跑完全流程，结束时统一打包保存 `.teproj` 1 次，遇 `success: false` 立即中止并精准报告 `failed_line`）。严禁把已知的 10~30 步建库导出流程拆成几十轮独立的 `--project` 单步调用！",
                "deterministic_batch": "针对多音频、多发音人、长音频+独立音频混合建库及全套产物导出，一律优先使用 `--script-file <cmds.txt>`（或 `--batch-cmds <cmds.txt>`）+ `export_bundle <out_dir> scope=integrated`。",
                "single_step_interactive": "仅当需要对已有工程做后续增量微调、单步诊断或局部重算时，才使用 `python cli.py --project <session.teproj> <single_command>`（或 `--workspace <dir>`）。",
                "clean_output": "非交互或自动化解析时默认开启静默纯净流，也可显式附加 `--quiet` 或 `--json` 确保 stdout 100% 为纯净 JSON。",
                "anti_redundancy_rules": [
                    "1. 禁止分多轮逐个查询 `<cmd> ?` 或 `help <cmd>`：下方 `core_command_cheatsheet` 已内嵌全部核心命令的精确参数语法；若需全部子命令详细字典可单次调用 `help all`。",
                    "2. 禁止在运行 `--script-file` 前创建临时 `.teproj` 工程做“沙盒试跑/预演”：请直接编写完整的 `pipeline.txt` 并执行 1 次 `python cli.py --project <目标工程.teproj> --script-file pipeline.txt`。",
                    "3. 禁止在剧本外单独跑一遍 `tool_wordlist_convert` 后又在剧本内重复写一遍：`apply_wordlist` 原生支持直接读取杂乱 `.txt`、`.csv` 或 `.ptwl`，且 `export_bundle` 会自动导出标准 `.ptwl`；若需显式转换，仅在 `pipeline.txt` 首行写 1 次即可。",
                    "4. 禁止在 `export_bundle` 后再手工逐张调用 `export` 重画图表或重复运行 `status`/`list_items`/`stats`：`export_bundle <out_dir> scope=integrated` 已一次性生成 `.teproj`（若 `--project` 已指向 `<out_dir>` 则直接原位更新，不产生双份工程）、`.ptwl`、`acoustic_data.xlsx`、`acoustic_points.txt`、`acoustic_stats.json`、`charts/` 与含统计摘要表和方差分析 (ANOVA) 的 `研究方法报告.md`。",
                    "5. 禁止空跑预检再二次调用：在剧本中直接写 `detect_f0 recommended` 与 `fix_warnings --apply scope=all`，无需先跑无参预检；若语料目录同时提供多套录音（如女声长音频、男声长音频、女声独立短音频、男声独立短音频），请将全部可用录音作为对应发音人纳入工程分析，并在收尾时清理临时生成的 `pipeline.txt`。",
                ],
            },
            rules=[
                "【最高优先级：单次 `--script-file` 跑批优先】处理实验语料时，请把建库、加载音频、应用字表、`detect_f0 recommended`、`fix_warnings --apply`、`stats` 与 `export_bundle` 一次性写入文本文件，调用 1 次 `python cli.py --project <work.teproj> --script-file <cmds.txt>` 完成全部处理；仅在增量微调时才单步调用 `--project`。",
                "【零冗余查帮助】请直接参考本响应中的 `core_command_cheatsheet` 参数速查表，不要开 8~10 轮进程逐个查询 `export ?`、`stats ?`、`load_batch ?`；如需全部子命令详情，最多调用 1 次 `help all`。",
                "【零重复导出】调用 `export_bundle <out_dir> scope=integrated` 即可在数秒内一次性生成 `.teproj`、`.ptwl`、Excel、统计 JSON、全套科研图表（`contour`/`distribution`/`density`/`quality`/`overview_heatmap`）和带分组统计表的 Markdown 报告，严禁在 `export_bundle` 后再逐张运行 `export contour`、`export density` 等重复画图指令。",
                "【一步到位探测与修复】在剧本中直接写 `detect_f0 recommended` 与 `fix_warnings --apply`，无需先空跑不带参数的 `detect_f0` 或 `fix_warnings apply=false`。",
                "禁止创建或运行任何绕过 CLI 的外部 Python/Praat 音频处理脚本，所有声学处理与导出均通过本 CLI 完成。"
            ],
            core_command_cheatsheet={
                "tool_wordlist_convert": "tool_wordlist_convert <in.txt|.csv|.ptwl> <out.ptwl> [title=<标题>]  # 将杂乱/结构化txt或csv一步升级为标准v2 .ptwl",
                "speakers_lifecycle": "add_speaker <name> [gender=female|male|child] [--switch] | switch_speaker <name_or_id> | rename_speaker [<old>] <new> | remove_speaker <name_or_id> | speakers  # 首次 add_speaker 会自动覆盖空的默认'发音人 1'",
                "load_long": "load_long <audio.wav>  # 加载当前发音人单条长音频",
                "load_batch": "load_batch <dir_or_glob_or_files...>  # 原生支持直接传入目录路径(自动自然排序扫描音频)或 *.wav 通配符",
                "apply_wordlist": "apply_wordlist <path.ptwl|.csv|.txt> [fuzzy|order]  # 原生识别 .ptwl/.csv/.txt 并挂载分组、标签与自定义字段",
                "detect_f0": "detect_f0 [recommended|conservative|fine]  # 直接传 recommended 即可一步完成 F0 分布探测并写入最佳 pitch_floor/pitch_ceiling",
                "detect_formants": "detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution]  # 自动探测最佳 max_formant 与 num_formants",
                "fix_warnings": "fix_warnings [--apply] [scope=active|all]  # 自动诊断并修复倍频跳变(octave_jump)、低覆盖率(low_f0_coverage)、过短音段等警告",
                "stats": "stats [by=group|item|speaker] [scope=active|integrated] [out=<path.json|path.md>]  # 直接返回并可落盘样本数、时长、首/中/尾F0、极差、均值±SD、五度值T值轨迹与编码(如55/35/214/51)及共振峰均值",
                "export_bundle": "export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg] [title=<name>]  # 【首选一键全量导出】一次生成 .teproj、.ptwl、acoustic_data.xlsx、acoustic_points.txt、acoustic_stats.json、charts/(contour/distribution/density/quality/overview_heatmap/formant_space) 与 report/(研究方法报告.md 含核心声学指标分组统计摘要表)",
                "export": "export <format> <output_file> [continuous|per_group] [active|integrated|separate] [key=val ...]  # 单项导出；formats: xlsx, txt, textgrid, ptwl, contour, density, distribution, quality, overview_heatmap, tone_effect_time, formant_space, f1_f2, duration, trend, line_chart, kde, wav, merged_wav, formant_table, formant_trajectory, formant_density, formant_overview_heatmap；options: scale=t_value|hz, groupby=group|label|speaker, facet=none|group|speaker|syllable_position|label, normalization=speaker|global, chart_group=default|item_tag:...|group_tag:...|meta:..., format=png|svg|pdf, target=active|integrated|separate",
                "report_export": "report_export <path.teproj> <out_dir> [format=md|excel|both] [include_cache=true|false]  # 导出含核心声学指标分组统计摘要表的 研究方法报告.md 与研究档案 Excel",
            },
            supported_commands=[
                "status", "list_items", "set_params", "lang", "help", "agent_guide",
                "speakers", "add_speaker", "switch_speaker", "rename_speaker", "remove_speaker",
                "load_long", "load_batch", "apply_wordlist", "apply_textgrid", "batch_textgrid_import",
                "textgrid_inspect", "textgrid_preview", "textgrid_convert",
                "modify_bounds", "modify_params", "recalculate",
                "detect_f0", "detect_formants", "fix_warnings",
                "stats", "export", "export_bundle", "report_export",
                "tool_wordlist_convert", "tool_merge", "tool_split", "tool_sort_batch",
                "project_export", "project_import", "project_save", "project_load", "project_preview", "project_convert_zip", "autosave",
                "import_batch_and_export",
                "list_scripts", "script_info", "run_script", "script_save", "script_delete", "script_import", "script_export", "script_prompt"
            ],
            task_paths={
                "one_shot_script_file_workflow": {
                    "description": "【AI 默认首选】单次 --script-file 剧本完成多发音人建库、F0自适应校准、异常修复、统计与全套产物打包（仅需 1 次 CLI 进程调用！）",
                    "script_file_example": [
                        "# 保存为 pipeline.txt，然后执行: python cli.py --project experiment.teproj --script-file pipeline.txt",
                        'tool_wordlist_convert "raw_wordlist.txt" "standard.ptwl" title="实验字表"',
                        'add_speaker "F01_女声" gender=female --switch',
                        'load_long "female_long.wav"',
                        'apply_wordlist "standard.ptwl"',
                        "detect_f0 recommended",
                        'add_speaker "M01_男声" gender=male --switch',
                        'load_batch "./male_isolated"',
                        'apply_wordlist "standard.ptwl" fuzzy',
                        "detect_f0 recommended",
                        "fix_warnings --apply scope=all",
                        "stats by=group scope=integrated",
                        'export_bundle "./deliverables" scope=integrated format=png',
                    ],
                },
                "multi_speaker_workflow": {
                    "description": "多发音人联合建模、跨性别五度值归一化与一键打包工作流（推荐写入 --script-file 一次执行）",
                    "steps": [
                        '1. add_speaker <发音人A名称> gender=female --switch (首次添加会自动替换空的默认"发音人 1")',
                        "2. load_long <女声长音频> 或 load_batch <女声独立音频目录> -> apply_wordlist <字表路径> -> detect_f0 recommended",
                        "3. add_speaker <发音人B名称> gender=male --switch (新增并切换到第二位发音人)",
                        "4. load_long <男声长音频> 或 load_batch <男声独立音频目录> -> apply_wordlist <字表路径> -> detect_f0 recommended",
                        "5. fix_warnings --apply scope=all (一键自动诊断并修复跨发音人异常项)",
                        "6. stats by=group scope=integrated (获取跨发音人五度值与声学对比统计表 JSON)",
                        "7. export_bundle <out_dir> scope=integrated format=png (一键生成 .teproj、.ptwl、.xlsx、contour/distribution/density/quality/overview_heatmap 图表与含统计表的 研究方法报告.md，无需再单独重画图表)"
                    ]
                },
                "batch_audio_with_wordlist_workflow": {
                    "description": "独立短音频目录/通配符批量加载并与字表对齐工作流（推荐写入 --script-file 一次执行）",
                    "steps": [
                        "1. load_batch <音频目录或通配符路径> (直接传入文件夹或 *.wav 自动按自然顺序加载)",
                        "2. apply_wordlist <字表路径> fuzzy (支持杂乱 .txt、.csv 或 .ptwl 高级字表模糊匹配)",
                        "3. detect_f0 recommended -> fix_warnings --apply (一步估算应用基频范围并修复异常项)",
                        "4. export_bundle <out_dir> scope=active (一键导出 .teproj、.ptwl、Excel、全套图表与研究报告)"
                    ]
                },
                "process_long_audio_from_scratch": {
                    "description": "从零开始导入并处理单条长音频",
                    "steps": [
                        "1. load_long <filepath> -> apply_wordlist <wordlist_filepath> (.txt / .ptwl / .csv)",
                        "2. detect_f0 recommended -> fix_warnings --apply",
                        "3. stats by=group -> export_bundle <out_dir>"
                    ]
                },
                "inspect_and_adjust_existing_project": {
                    "description": "加载已有 teproj 工程并进行增量微调（适合使用 --project <filepath.teproj> 单步执行）",
                    "steps": [
                        "1. python cli.py --project <filepath.teproj> status (自动载入工程并查看概览)",
                        "2. python cli.py --project <filepath.teproj> modify_bounds / modify_params / fix_warnings --apply (增量微调并自动回写)",
                        "3. python cli.py --project <filepath.teproj> stats by=group scope=integrated (查看最新统计摘要)"
                    ]
                },
                "batch_textgrid_import_workflow": {
                    "description": "批量短音频与批量 TextGrid 对齐后在工程中继续分析",
                    "steps": [
                        "1. load_batch <audio_dir_or_paths...> -> batch_textgrid_import <textgrid_dir_or_paths...>",
                        "2. fix_warnings --apply -> export_bundle <out_dir>"
                    ]
                },
                "textgrid_converter_workflow": {
                    "description": "把任意层结构的 TextGrid 转为 PhonTracer 标准 groups / words / chars TextGrid",
                    "steps": [
                        "1. textgrid_inspect <textgrid_path>",
                        "2. textgrid_preview <textgrid_path> item=<条目层> core=<核心层> group=<组层|无> pair=<none|adjacent> wordlist=<可选字表>",
                        "3. textgrid_convert <textgrid_path_or_dir...> out=<输出文件或目录> item=<条目层> core=<核心层> group=<组层|无> pair=<none|adjacent> wordlist=<可选字表>"
                    ]
                },
                "run_custom_scripts": {
                    "description": "运行项目中的自定义 Python 脚本",
                    "steps": [
                        "1. project_import <filepath.teproj> -> list_scripts -> script_info <script_id> -> run_script <script_id>"
                    ]
                }
            },
            next_steps=["--script-file <cmds.txt>", "export_bundle <out_dir> scope=integrated", "stats by=group scope=integrated"]
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
        self._emit(True, speakers=res)

    def do_add_speaker(self, arg):
        """
        Add a new speaker.
        Usage: add_speaker <name> [--switch|--no-switch] [gender=female|male|child] [formant_max_hz=...]
        By default, automatically switches to the newly added speaker unless --no-switch is passed.
        If the initial default shell speaker ('发音人 1' or 'Speaker 1') has no data, it is automatically renamed/reused.
        """
        try:
            tokens = shlex.split(arg)
        except ValueError:
            tokens = arg.strip().split()

        do_switch = True
        name_parts = []
        kv_opts = {}
        for tok in tokens:
            low = tok.lower().strip()
            if low in ("--switch", "-s", "switch=true", "switch=1", "active=true"):
                do_switch = True
            elif low in ("--no-switch", "switch=false", "switch=0", "active=false"):
                do_switch = False
            elif "=" in tok and tok.split("=", 1)[0].strip().lower() in (
                "gender", "sex", "formant_max_hz", "pitch_floor", "pitch_ceiling", "pts", "db", "analysis_mode"
            ):
                k, v = tok.split("=", 1)
                kv_opts[k.strip().lower()] = v.strip()
            else:
                name_parts.append(tok)

        name = " ".join(name_parts).strip()
        if not name:
            self._emit(False, error="Speaker name required", hint="用法: add_speaker <发音人名称> [--switch]")
            return

        all_speakers = self.speaker_manager.get_all_speakers()
        replaced_default = False
        target_speaker = None

        empty_default = None
        for s in all_speakers:
            if self._is_empty_default_speaker(s):
                empty_default = s
                break

        if empty_default is not None and len(all_speakers) == 1:
            self.speaker_manager.rename_speaker(empty_default.id, name)
            target_speaker = empty_default
            replaced_default = True
        else:
            target_speaker = self.speaker_manager.add_speaker(name)
            if empty_default is not None and empty_default.id != target_speaker.id:
                self.speaker_manager.remove_speaker(empty_default.id)
                replaced_default = True

        gender_val = kv_opts.get("gender") or kv_opts.get("sex")
        if gender_val:
            g_low = gender_val.lower()
            if g_low in ("female", "f", "woman", "女", "女性"):
                target_speaker.last_params["formant_max_hz"] = 5500.0
            elif g_low in ("male", "m", "man", "男", "男性"):
                target_speaker.last_params["formant_max_hz"] = 5000.0
            elif g_low in ("child", "c", "kid", "儿童", "小孩"):
                target_speaker.last_params["formant_max_hz"] = 8000.0
        for p_key in ("formant_max_hz", "pitch_floor", "pitch_ceiling", "db"):
            if p_key in kv_opts:
                try:
                    target_speaker.last_params[p_key] = float(kv_opts[p_key])
                except ValueError:
                    pass

        if do_switch:
            self.speaker_manager.set_active_speaker(target_speaker.id)

        self._after_state_change("add_speaker")
        self._emit(
            True,
            f"Speaker '{name}' added",
            speaker_id=target_speaker.id,
            name=target_speaker.name,
            active=(self.speaker_manager.active_speaker_id == target_speaker.id),
            replaced_default=replaced_default,
        )

    def do_switch_speaker(self, arg):
        """
        Switch the active speaker.
        Usage: switch_speaker <name_or_id>
        """
        target = arg.strip().strip('"').strip("'")
        if not target:
            self._emit(False, error="Speaker name or id required", hint="用法: switch_speaker <发音人名称或ID>")
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if found_id:
            self.speaker_manager.set_active_speaker(found_id)
            self._after_state_change("switch_speaker")
            self._emit(
                True,
                f"Switched to speaker '{target}'",
                speaker_id=found_id,
                name=self.current_speaker.name,
                active_speaker_id=found_id,
                active_speaker=self.current_speaker.name,
            )
        else:
            available = [s.name for s in self.speaker_manager.get_all_speakers()]
            self._emit(
                False,
                error=f"Speaker '{target}' not found",
                available_speakers=available,
                hint="请使用 speakers 查看当前可用发音人列表。",
            )

    def do_rename_speaker(self, arg):
        """
        Rename a speaker or the currently active speaker.
        Usage: rename_speaker <old_name_or_id> <new_name>
               rename_speaker <new_name>  (renames the active speaker)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(False, error="Speaker name required. Usage: rename_speaker [<old_name>] <new_name>")
            return

        if len(args) == 1:
            target_spk = self.current_speaker
            new_name = args[0].strip()
        else:
            old_target = args[0].strip()
            new_name = " ".join(args[1:]).strip()
            target_spk = None
            for s in self.speaker_manager.get_all_speakers():
                if s.id == old_target or s.name == old_target:
                    target_spk = s
                    break
            if target_spk is None:
                self._emit(False, error=f"Speaker '{old_target}' not found")
                return

        if not new_name:
            self._emit(False, error="New speaker name required")
            return

        old_name = target_spk.name
        self.speaker_manager.rename_speaker(target_spk.id, new_name)
        for item in target_spk.items.values():
            if isinstance(item, dict):
                item["speaker"] = new_name
                item["speaker_id"] = target_spk.id

        self._after_state_change("rename_speaker")
        self._emit(
            True,
            f"Speaker '{old_name}' renamed to '{new_name}'",
            speaker_id=target_spk.id,
            old_name=old_name,
            new_name=new_name,
            name=new_name,
            active=(self.speaker_manager.active_speaker_id == target_spk.id),
        )

    def do_remove_speaker(self, arg):
        """
        Remove a speaker. Cannot remove the last speaker.
        Usage: remove_speaker <name_or_id>
        """
        target = arg.strip().strip('"').strip("'")
        if not target:
            self._emit(False, error="Speaker name or id required")
            return
        found_id = None
        for s in self.speaker_manager.get_all_speakers():
            if s.id == target or s.name == target:
                found_id = s.id
                break

        if not found_id:
            self._emit(False, error=f"Speaker '{target}' not found")
            return

        success = self.speaker_manager.remove_speaker(found_id)
        if success:
            self._after_state_change("remove_speaker")
            self._emit(True, f"Speaker '{target}' removed")
        else:
            self._emit(False, error="Cannot remove the last speaker")

    def do_load_long(self, arg):
        """
        Load a single long audio file.
        Usage: load_long <filepath>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Filepath required")
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
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
            self._emit(False, error=str(e))

    def do_load_batch(self, arg):
        """
        Load multiple independent audio files from file paths, directory paths, or wildcard patterns.
        Usage: load_batch <file_or_dir_or_glob1> [file_or_dir_or_glob2 ...]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(
                False,
                error="Filepaths required",
                hint="请传入音频文件路径、目录路径（如 load_batch ./female_isolated）或 *.wav 通配符（如 load_batch ./male_isolated/*.wav）。",
            )
            return

        valid_paths, err_msg, hint_msg = self._expand_batch_audio_inputs(args)
        if err_msg:
            self._emit(False, error=err_msg, hint=hint_msg)
            return

        self.batch_paths = valid_paths
        self.mode = 'batch'
        self.long_snd = None
        self.long_snd_path = None
        self.items.clear()
        self.groups.clear()
        self._after_state_change("load_batch")
        self._emit(
            True,
            f"已加载 {len(self.batch_paths)} 个独立音频。下一步通常是 apply_wordlist。",
            mode=self.mode,
            count=len(self.batch_paths),
            paths=self.batch_paths,
        )

    def do_apply_wordlist(self, arg):
        """
        Apply a wordlist (.ptwl, .csv, structured .txt, or messy .txt) to split/match the audio.
        Usage: apply_wordlist <wordlist_filepath> [match_mode]
        match_mode for batch: 'fuzzy' or 'order' (default 'fuzzy')
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(False, error="Wordlist filepath required")
            return

        filepath = None
        match_mode = "fuzzy"
        custom_title = None
        for tok in args:
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lower()
                if k_low in ("match_mode", "mode", "match"):
                    match_mode = v.strip().lower()
                elif k_low == "title":
                    custom_title = v.strip()
                elif filepath is None:
                    filepath = tok
            else:
                if filepath is None:
                    filepath = tok
                elif tok.lower() in ("fuzzy", "order"):
                    match_mode = tok.lower()

        if not filepath or not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
            return

        try:
            doc, groups, flat_words, records = self._load_wordlist_any(filepath, title=custom_title)
        except Exception as e:
            self._emit(False, error=f"Failed to load wordlist: {e}")
            return

        if not flat_words:
            self._emit(False, error="No words found in wordlist")
            return

        self.wordlist_doc = doc
        self.groups = [g["group"] for g in groups]
        self.current_speaker.project_groups = list(self.groups)
        self.items.clear()

        if self.mode in ("long", "single"):
            self._process_long_wordlist(groups, flat_words, metadata_records=records, wordlist_doc=doc)
        elif self.mode == "batch":
            self._process_batch_wordlist(groups, flat_words, match_mode, metadata_records=records, wordlist_doc=doc)
        else:
            self._emit(False, error="No audio loaded. Use load_long or load_batch first.")

    def do_apply_textgrid(self, arg):
        """
        Apply a TextGrid file to segment and match the active long audio.
        Usage: apply_textgrid <textgrid_filepath>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="TextGrid filepath required")
            return

        filepath = args[0]
        if not os.path.exists(filepath):
            self._emit(False, error=f"File not found: {filepath}")
            return

        if self.mode not in ('long', 'single') or not self.long_snd:
            self._emit(False, error="This command requires active long audio mode. Use load_long first.")
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
                self._emit(False, error="No IntervalTier found in TextGrid")
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
                self._emit(False, error="No non-empty labeled intervals found in TextGrid")
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
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            tasks = []
            for item in tg_intervals:
                ms = item['start']
                me = item['end']
                word = item['label']
                grp_name = item.get('group', '导入内容')
                ref_splits = item.get('inner_splits', [])
                ref_chars_bounds = item.get('chars_bounds', [])

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
                        'ref_chars_bounds': ref_chars_bounds,
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
                        self.params, self.params['trim_silence'], t['sliced_xs'], t['sliced_freqs'], t['ref_splits'], t['ref_chars_bounds']
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
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])
            for idx, res in enumerate(results):
                iid = f"item_{idx}"
                if res and res.get('success'):
                    res['snd'] = self.long_snd
                    res['pitch_data'] = pitch_data
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                    preview_times = np.linspace(res['start'], res['end'], 11)
                    res['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
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
            self._emit(False, error=str(e))

    def do_textgrid_inspect(self, arg):
        """
        查看 TextGrid 层摘要并给出转换器推荐层。
        用法：textgrid_inspect <TextGrid路径>
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"参数解析失败：{e}")
            return
        if len(args) != 1:
            self._emit(False, error="用法：textgrid_inspect <TextGrid路径>")
            return
        path = args[0]
        if not os.path.isfile(path) or not path.lower().endswith(".textgrid"):
            self._emit(False, error=f"不是有效的 TextGrid 文件：{path}")
            return
        try:
            summary = inspect_textgrid(path)
            self._emit(
                True,
                "TextGrid 层摘要读取完成。",
                path=os.path.abspath(path),
                summary=summary,
                recommendations=recommend_tier_names(summary),
            )
        except Exception as e:
            self._emit(False, error=f"读取 TextGrid 失败：{e}")

    def do_textgrid_preview(self, arg):
        """
        预览任意 TextGrid 转换为标准 groups / words / chars 的结果。
        用法：textgrid_preview <TextGrid路径> [item=<条目层>] [core=<核心层>] [group=<组层|无>] [pair=none|adjacent] [wordlist=<字表路径>] [override=条目ID或标签:组名] [limit=50|all]
        """
        try:
            options = self._parse_textgrid_converter_args(arg)
            if len(options["sources"]) != 1:
                self._emit(False, error="用法：textgrid_preview <TextGrid路径> [item=<条目层>] [core=<核心层>] ...")
                return
            sources = self._collect_textgrid_sources(options["sources"], recursive=False, allow_directories=False)
            source = sources[0]
            records, wordlist_groups = self._load_textgrid_converter_wordlist(options)
            summary, mapping, recommendations = self._build_textgrid_converter_mapping(source, options)
            preview = preview_textgrid_conversion(
                source,
                mapping,
                group_overrides=options["group_overrides"],
                wordlist_records=records,
            )
            rows, truncated = self._limited_textgrid_preview_rows(preview, options["limit"])
            self._emit(
                True,
                "TextGrid 转换预览完成。",
                source=os.path.abspath(source),
                mapping=self._textgrid_mapping_payload(mapping),
                recommendations=recommendations,
                total_items=len(preview.items),
                shown_items=len(rows),
                truncated=truncated,
                items=rows,
                warnings=preview.warnings,
                tone_pair_report=preview.tone_pair_report,
                wordlist={"path": options["wordlist"], "groups": wordlist_groups, "records": len(records)} if options["wordlist"] else None,
            )
        except Exception as e:
            self._emit(False, error=str(e))

    def do_textgrid_convert(self, arg):
        """
        将一个或多个任意 TextGrid 转换为 PhonTracer 标准 groups / words / chars TextGrid。
        用法：textgrid_convert <TextGrid文件或目录...> out=<输出文件或目录> [item=<条目层>] [core=<核心层>] [group=<组层|无>] [pair=none|adjacent] [wordlist=<字表路径>] [override=条目ID或标签:组名] [suffix=_converted] [overwrite=true] [recursive=true]
        """
        try:
            options = self._parse_textgrid_converter_args(arg, require_output=True)
            sources = self._collect_textgrid_sources(options["sources"], recursive=options["recursive"], allow_directories=True)
            records, wordlist_groups = self._load_textgrid_converter_wordlist(options)
            outputs = []
            failures = []
            for source in sources:
                try:
                    summary, mapping, recommendations = self._build_textgrid_converter_mapping(source, options)
                    out_path = self._resolve_textgrid_converter_output(
                        source=source,
                        output=options["output"],
                        source_count=len(sources),
                        suffix=options["suffix"],
                        overwrite=options["overwrite"],
                    )
                    preview = convert_textgrid(
                        source,
                        out_path,
                        mapping,
                        group_overrides=options["group_overrides"],
                        wordlist_records=records,
                    )
                    outputs.append({
                        "source": os.path.abspath(source),
                        "output": os.path.abspath(out_path),
                        "mapping": self._textgrid_mapping_payload(mapping),
                        "recommendations": recommendations,
                        "items": len(preview.items),
                        "warnings": preview.warnings,
                        "tone_pair_report": preview.tone_pair_report,
                    })
                except Exception as e:
                    failures.append({"source": os.path.abspath(source), "error": str(e)})

            success = len(failures) == 0
            self._emit(
                success,
                "TextGrid 批量转换完成。" if len(sources) > 1 else ("TextGrid 转换完成。" if success else "TextGrid 转换失败。"),
                converted_count=len(outputs),
                failed_count=len(failures),
                total=len(sources),
                outputs=outputs,
                failures=failures,
                wordlist={"path": options["wordlist"], "groups": wordlist_groups, "records": len(records)} if options["wordlist"] else None,
            )
        except Exception as e:
            self._emit(False, error=str(e))

    def do_textgrid_converter(self, arg):
        """
        textgrid_convert 的别名。
        """
        self.do_textgrid_convert(arg)

    def _parse_textgrid_converter_args(self, arg, require_output=False):
        try:
            tokens = shlex.split(arg)
        except ValueError as e:
            raise ValueError(f"参数解析失败：{e}") from e

        aliases = {
            "out": "output",
            "output": "output",
            "输出": "output",
            "item": "item_tier",
            "item_tier": "item_tier",
            "item-tier": "item_tier",
            "word": "item_tier",
            "words": "item_tier",
            "条目": "item_tier",
            "条目层": "item_tier",
            "core": "core_tier",
            "core_tier": "core_tier",
            "core-tier": "core_tier",
            "核心": "core_tier",
            "核心层": "core_tier",
            "group": "group_tier",
            "group_tier": "group_tier",
            "group-tier": "group_tier",
            "组": "group_tier",
            "组层": "group_tier",
            "pair": "pair_mode",
            "pair_mode": "pair_mode",
            "pair-mode": "pair_mode",
            "二字组": "pair_mode",
            "wordlist": "wordlist",
            "字表": "wordlist",
            "aux": "wordlist",
            "override": "override",
            "group_override": "override",
            "group-override": "override",
            "临时分组": "override",
            "suffix": "suffix",
            "后缀": "suffix",
            "overwrite": "overwrite",
            "覆盖": "overwrite",
            "recursive": "recursive",
            "递归": "recursive",
            "limit": "limit",
            "数量": "limit",
        }
        bool_keys = {"overwrite", "recursive"}
        options = {
            "sources": [],
            "output": None,
            "item_tier": None,
            "core_tier": None,
            "group_tier": None,
            "group_tier_provided": False,
            "pair_mode": PAIR_MODE_NONE,
            "wordlist": None,
            "group_overrides": {},
            "suffix": "_converted",
            "overwrite": False,
            "recursive": False,
            "limit": 50,
        }

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in ("--pair-adjacent", "--adjacent-pair", "--二字组合并"):
                options["pair_mode"] = PAIR_MODE_ADJACENT
                index += 1
                continue
            if token.startswith("--"):
                raw = token[2:]
                if "=" in raw:
                    key, value = raw.split("=", 1)
                else:
                    key = raw
                    normalized = aliases.get(key.strip().lower())
                    if not normalized:
                        raise ValueError(f"未知参数：--{key}")
                    if normalized in bool_keys:
                        value = "true"
                    else:
                        if index + 1 >= len(tokens):
                            raise ValueError(f"参数 --{key} 缺少取值。")
                        index += 1
                        value = tokens[index]
                self._set_textgrid_converter_option(options, aliases, key, value)
            elif "=" in token:
                key, value = token.split("=", 1)
                if aliases.get(key.strip().lower()):
                    self._set_textgrid_converter_option(options, aliases, key, value)
                else:
                    options["sources"].append(token)
            else:
                options["sources"].append(token)
            index += 1

        if require_output and not options["output"]:
            raise ValueError("缺少输出路径，请使用 out=<输出文件或目录>。")
        if not options["sources"]:
            raise ValueError("缺少 TextGrid 输入路径。")
        return options

    def _set_textgrid_converter_option(self, options, aliases, key, value):
        normalized = aliases.get(str(key).strip().lower())
        if not normalized:
            raise ValueError(f"未知参数：{key}")
        text = str(value or "").strip()
        if normalized == "output":
            options["output"] = text
        elif normalized == "item_tier":
            options["item_tier"] = text
        elif normalized == "core_tier":
            options["core_tier"] = text
        elif normalized == "group_tier":
            options["group_tier_provided"] = True
            options["group_tier"] = None if self._is_none_tier_value(text) else text
        elif normalized == "pair_mode":
            lowered = text.lower()
            if lowered in ("adjacent", "pair", "paired", "true", "1", "yes", "on", "二字", "二字组", "相邻", "两两合并"):
                options["pair_mode"] = PAIR_MODE_ADJACENT
            elif lowered in ("none", "false", "0", "no", "off", "无", "关闭"):
                options["pair_mode"] = PAIR_MODE_NONE
            else:
                raise ValueError("pair 只能是 none 或 adjacent。")
        elif normalized == "wordlist":
            options["wordlist"] = text
        elif normalized == "override":
            item_key, group_name = self._split_textgrid_group_override(text)
            options["group_overrides"][item_key] = group_name
        elif normalized == "suffix":
            options["suffix"] = text or "_converted"
        elif normalized == "overwrite":
            options["overwrite"] = self._parse_textgrid_bool(text)
        elif normalized == "recursive":
            options["recursive"] = self._parse_textgrid_bool(text)
        elif normalized == "limit":
            lowered = text.lower()
            if lowered in ("all", "全部", "none", "无"):
                options["limit"] = None
            else:
                options["limit"] = max(0, int(text))

    def _is_none_tier_value(self, value):
        return str(value or "").strip().lower() in ("", "none", "null", "no", "false", "0", NONE_TIER_LABEL.lower(), "无", "不使用", "跳过")

    def _parse_textgrid_bool(self, value):
        return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on", "是", "真", "开启", "覆盖", "递归")

    def _split_textgrid_group_override(self, value):
        text = str(value or "").strip()
        for sep in ("=>", "->", "=", ":", "："):
            if sep in text:
                key, group = text.split(sep, 1)
                key = key.strip()
                group = group.strip()
                if key and group:
                    return key, group
        raise ValueError("临时分组格式应为 override=条目ID或标签:组名，例如 override=0:实验组。")

    def _collect_textgrid_sources(self, source_args, recursive=False, allow_directories=True):
        paths = []
        seen = set()
        for source in source_args:
            path = os.path.normpath(source)
            if os.path.isdir(path):
                if not allow_directories:
                    raise ValueError(f"此命令只接受单个 TextGrid 文件：{source}")
                iterator = os.walk(path) if recursive else [(path, [], os.listdir(path))]
                for root, _dirs, files in iterator:
                    for filename in files:
                        if filename.lower().endswith(".textgrid"):
                            full_path = os.path.join(root, filename)
                            key = os.path.normcase(os.path.abspath(full_path))
                            if key not in seen:
                                paths.append(full_path)
                                seen.add(key)
            elif os.path.isfile(path) and path.lower().endswith(".textgrid"):
                key = os.path.normcase(os.path.abspath(path))
                if key not in seen:
                    paths.append(path)
                    seen.add(key)
            else:
                raise ValueError(f"不是有效的 TextGrid 文件或目录：{source}")

        paths.sort(key=self._natural_textgrid_path_key)
        if not paths:
            raise ValueError("没有找到可转换的 TextGrid 文件。")
        return paths

    def _natural_textgrid_path_key(self, path):
        import re
        text = os.path.basename(str(path))
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"([0-9]+)", text)]

    def _load_textgrid_converter_wordlist(self, options):
        path = options.get("wordlist")
        if not path:
            return [], 0
        if not os.path.isfile(path):
            raise ValueError(f"辅助字表不存在：{path}")
        records, groups_count = load_auxiliary_wordlist_records(path)
        if not records:
            raise ValueError("辅助字表中没有可用词项。")
        return records, groups_count

    def _build_textgrid_converter_mapping(self, source, options):
        summary = inspect_textgrid(source)
        recommendations = recommend_tier_names(summary)
        item_tier = options.get("item_tier") or recommendations.get("item_tier")
        core_tier = options.get("core_tier") or recommendations.get("core_tier")
        if options.get("group_tier_provided"):
            group_tier = options.get("group_tier")
        else:
            group_tier = recommendations.get("group_tier")
        if self._is_none_tier_value(item_tier):
            raise ValueError("无法确定条目层，请使用 item=<层名> 指定。")
        if self._is_none_tier_value(core_tier):
            raise ValueError("无法确定核心层，请使用 core=<层名> 指定。")
        mapping = TextGridMapping(
            item_tier=item_tier,
            core_tier=core_tier,
            group_tier=None if self._is_none_tier_value(group_tier) else group_tier,
            pair_mode=options.get("pair_mode") or PAIR_MODE_NONE,
        )
        return summary, mapping, recommendations

    def _resolve_textgrid_converter_output(self, source, output, source_count, suffix, overwrite=False):
        output = os.path.normpath(output)
        explicit_file = source_count == 1 and output.lower().endswith(".textgrid")
        if explicit_file:
            out_path = output
            parent = os.path.dirname(os.path.abspath(out_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            if os.path.exists(out_path) and not overwrite:
                raise ValueError(f"输出文件已存在，若要覆盖请加 overwrite=true：{out_path}")
            return out_path

        if os.path.exists(output) and not os.path.isdir(output):
            raise ValueError(f"批量输出路径必须是目录：{output}")
        os.makedirs(output, exist_ok=True)
        stem = os.path.splitext(os.path.basename(source))[0]
        suffix = suffix or "_converted"
        filename = f"{stem}{suffix}.TextGrid"
        candidate = os.path.join(output, filename)
        if overwrite:
            return candidate
        base, ext = os.path.splitext(filename)
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(output, f"{base}_{counter}{ext or '.TextGrid'}")
            counter += 1
        return candidate

    def _textgrid_mapping_payload(self, mapping):
        return {
            "item_tier": mapping.item_tier,
            "core_tier": mapping.core_tier,
            "group_tier": mapping.group_tier,
            "pair_mode": mapping.pair_mode,
        }

    def _limited_textgrid_preview_rows(self, preview, limit):
        rows = preview.rows()
        if limit is None:
            return rows, False
        return rows[:limit], len(rows) > limit

    def _process_long_wordlist(self, groups, flat_words, metadata_records=None, wordlist_doc=None, emit_result=True):
        metadata_records = metadata_records or []
        try:
            snd = self.long_snd
            if snd is None and self.long_snd_path and os.path.exists(self.long_snd_path):
                try:
                    snd = parselmouth.Sound(self.long_snd_path)
                    self.long_snd = snd
                except Exception:
                    snd = None

            if snd is not None:
                pitch_data = extract_f0(snd, self.params)
                macro_segments = macroscopic_vad(snd, expected_count=len(flat_words))
                self.current_speaker.current_macro_segments = list(macro_segments)
                pitch_xs = pitch_data['xs']
                pitch_freqs = pitch_data['freqs']
                long_valid_indices = np.where(pitch_freqs > 0)[0]
                long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])
            else:
                pitch_data = None
                macro_segments = []
                pitch_xs = np.array([])
                pitch_freqs = np.array([])
                long_valid_xs = np.array([])

            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    iid = f"item_{word_idx}"
                    record = metadata_records[word_idx] if word_idx < len(metadata_records) else None
                    if snd is not None and word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]

                        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                            snd, pitch_data, ms, me,
                            self.params['db'], self.params['skip_front'], self.params['trim_silence']
                        )

                        inner_splits = []
                        chars_bounds = []
                        split_warnings = []
                        split_confidence = 1.0
                        syls = split_into_syllables(word)
                        if len(syls) > 1:
                            meta = {}
                            inner_splits = auto_split_inner_word(snd, mic_s, mic_e, len(syls), pitch_data=pitch_data, output_meta=meta)
                            split_warnings = meta.get('split_warnings', [])
                            split_confidence = meta.get('split_confidence', 1.0)
                            chars_bounds = auto_split_to_chars_bounds(snd, mic_s, mic_e, inner_splits, len(syls), self.params)
                        else:
                            chars_bounds = [[mic_s, mic_e]]

                        preview_times = np.linspace(mic_s, mic_e, 11)
                        preview_f0 = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                        has_empty = any(f == 0.0 for f in preview_f0)

                        item_dict = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'snd': snd, 'pitch_data': pitch_data,
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
                        item_dict = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'missing': True, 'start': None, 'end': None
                        }
                    self._attach_wordlist_record_to_item(item_dict, record, speaker=self.current_speaker)
                    self.items[iid] = item_dict
                    word_idx += 1

            self._after_state_change("apply_wordlist")
            if emit_result:
                doc_info = wordlist_doc or self.wordlist_doc or {}
                self._emit(
                    True,
                    f"长音频已按字表处理，共 {len(flat_words)} 个词/字。",
                    total=len(flat_words),
                    group_count=len(groups),
                    wordlist_title=doc_info.get("title", "") if isinstance(doc_info, dict) else "",
                    schema=doc_info.get("schema", WORDLIST_SCHEMA) if isinstance(doc_info, dict) else WORDLIST_SCHEMA,
                )
        except Exception as e:
            if emit_result:
                self._emit(False, error=str(e))
            else:
                raise

    def _process_batch_wordlist(self, groups, flat_words, match_mode, metadata_records=None, wordlist_doc=None, emit_result=True):
        metadata_records = metadata_records or []
        tasks = []
        record_idx = 0
        if match_mode == 'fuzzy':
            sorted_paths = sorted(self.batch_paths, key=self._natural_sort_key)
            used_indices = []
            for grp in groups:
                for word in grp['items']:
                    record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    idx = fuzzy_match_word_to_path(word, sorted_paths, used_indices)
                    if idx is None and record:
                        for alias in (record.get("item_aliases") or record.get("aliases") or []):
                            idx = fuzzy_match_word_to_path(alias, sorted_paths, used_indices)
                            if idx is not None:
                                break
                    if idx is not None:
                        path = sorted_paths[idx]
                        used_indices.append(idx)
                        tasks.append({'word': word, 'group': grp['group'], 'path': path, 'missing': False, 'record': record})
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'record': record})
                    record_idx += 1
        else:
            path_idx = 0
            for grp in groups:
                for word in grp['items']:
                    record = metadata_records[record_idx] if record_idx < len(metadata_records) else None
                    if path_idx < len(self.batch_paths):
                        tasks.append({'word': word, 'group': grp['group'], 'path': self.batch_paths[path_idx], 'missing': False, 'record': record})
                        path_idx += 1
                    else:
                        tasks.append({'word': word, 'group': grp['group'], 'missing': True, 'record': record})
                    record_idx += 1

        futures = {}
        for i, t in enumerate(tasks):
            if not t['missing']:
                path = t['path']
                f = self.executor.submit(batch_process_worker, path, self.params, self.params['trim_silence'], t['word'])
                futures[f] = i

        results = [None] * len(tasks)
        for i, t in enumerate(tasks):
            if t['missing']:
                results[i] = {'label': t['word'], 'group': t['group'], 'missing': True, 'start': None, 'end': None}

        for future in concurrent.futures.as_completed(futures):
            orig_idx = futures[future]
            try:
                res = future.result()
                res['missing'] = False
                res['group'] = tasks[orig_idx]['group']
                res['label'] = tasks[orig_idx]['word']

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
                results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'start': None, 'end': None, 'error': str(e)}

        for i, res in enumerate(results):
            iid = f"item_{i}"
            record = tasks[i].get('record')
            if not res.get('missing') and res.get('success'):
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
            self._attach_wordlist_record_to_item(res, record, speaker=self.current_speaker)
            self.items[iid] = res

        self._after_state_change("apply_wordlist")
        if emit_result:
            doc_info = wordlist_doc or self.wordlist_doc or {}
            matched_cnt = sum(1 for r in results if r and not r.get('missing') and r.get('success'))
            self._emit(
                True,
                f"独立音频已按字表匹配处理，共 {len(tasks)} 个词/字（已匹配音频 {matched_cnt} 项）。",
                total=len(tasks),
                matched=matched_cnt,
                group_count=len(groups),
                wordlist_title=doc_info.get("title", "") if isinstance(doc_info, dict) else "",
                schema=doc_info.get("schema", WORDLIST_SCHEMA) if isinstance(doc_info, dict) else WORDLIST_SCHEMA,
            )

    def _compute_speaker_median_f0(self, spk):
        f0_pool = []
        for item in spk.items.values():
            if not isinstance(item, dict) or item.get("is_excluded", False) or item.get("missing", False):
                continue
            pd = item.get("pitch_data")
            if isinstance(pd, dict) and pd.get("freqs") is not None:
                pf = np.asarray(pd["freqs"], dtype=float)
                t_s, t_e = item.get("start"), item.get("end")
                if pd.get("xs") is not None and t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    px = np.asarray(pd["xs"], dtype=float)
                    pf = pf[(px >= float(t_s)) & (px <= float(t_e))]
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())
            elif item.get("preview_f0"):
                pf = np.asarray(item["preview_f0"], dtype=float)
                f0_pool.extend(pf[np.isfinite(pf) & (pf > 0)].tolist())
        return float(np.median(f0_pool)) if f0_pool else 160.0

    def do_status(self, arg):
        """
        Show current global status.
        Usage: status
        """
        med_f0 = self._compute_speaker_median_f0(self.current_speaker) if self.items else 160.0
        status = {
            "mode": self.mode,
            "params": self.params,
            "groups": self.groups,
            "total_items": len(self.items),
            "excluded_items": sum(1 for it in self.items.values() if it.get('is_excluded', False)),
            "missing_items": sum(1 for it in self.items.values() if (it.get('missing') or not it.get('success', True)) and not it.get('is_excluded', False)),
            "warnings": sum(
                1 for iid, it in self.items.items()
                if not it.get('is_excluded', False) and self._check_item_has_any_warning(iid, it, spk_median_f0=med_f0)
            )
        }
        self._emit(True, status=status)

    def do_list_items(self, arg):
        """
        List details of items.
        Usage: list_items [all|warnings|group_name]
        """
        filter_type = arg.strip() if arg else "all"
        med_f0 = self._compute_speaker_median_f0(self.current_speaker) if self.items else 160.0

        output = []
        for iid, item in self.items.items():
            has_warn = self._check_item_has_any_warning(iid, item, spk_median_f0=med_f0)
            if filter_type == 'warnings' and not has_warn:
                continue
            if filter_type not in ('all', 'warnings') and item.get('group') != filter_type:
                continue

            entry = {
                "id": iid,
                "label": item.get('label'),
                "group": item.get('group'),
                "tags": list(item.get('tags') or item.get('item_tags') or []),
                "note": item.get('note') or item.get('item_note') or "",
                "fields": dict(item.get('fields') or item.get('custom_fields') or item.get('item_meta') or item.get('meta') or {}),
                "custom_fields": dict(item.get('custom_fields') or item.get('fields') or item.get('item_meta') or item.get('meta') or {}),
                "speaker": item.get('speaker') or self.current_speaker.name,
                "speaker_id": item.get('speaker_id') or self.current_speaker.id,
                "is_excluded": item.get('is_excluded', False),
            }
            if item.get('is_excluded', False):
                entry['status'] = 'excluded'
                entry['exclusion_reason'] = item.get('exclusion_reason', "")
                entry['excluded_at'] = item.get('excluded_at', "")
            elif item.get('missing') or not item.get('success', True):
                entry['status'] = 'missing/error'
            else:
                entry['status'] = 'ok'
                entry['start'] = item.get('start')
                entry['end'] = item.get('end')
                entry['warning'] = has_warn
                if has_warn and (item.get("pitch_data") is not None or item.get("split_warnings")):
                    entry['warning_issues'] = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0).get("issues", [])
            output.append(entry)

        self._emit(True, items=output)

    def do_modify_bounds(self, arg):
        """
        Modify time boundaries of an item manually. This overrides automatic VAD.
        Usage: modify_bounds <item_id> <start> <end>
        Example: modify_bounds item_0 1.25 1.85
        """
        args = shlex.split(arg)
        if len(args) != 3:
            self._emit(False, error="Requires item_id, start, and end")
            return

        iid = args[0]
        try:
            new_s = float(args[1])
            new_e = float(args[2])
        except ValueError:
            self._emit(False, error="start and end must be floats")
            return

        if iid not in self.items:
            self._emit(False, error=f"Item {iid} not found")
            return

        item = self.items[iid]
        if 'snd' not in item or item['snd'] is None:
            self._emit(False, error="Item has no loaded audio data")
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
                preview_f0 = _calc_preview_f0(preview_times, p_xs, p_freqs)
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
            self._emit(False, error="Requires item_id and key=value pairs")
            return

        iid = args[0]
        if iid not in self.items:
            self._emit(False, error=f"Item {iid} not found")
            return

        item = self.items[iid]
        if item.get('missing') or not item.get('success', True):
            self._emit(False, error="Item has no loaded audio data")
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
                        self._emit(False, error=f"Invalid value for {k}")
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
                item['preview_f0'] = _calc_preview_f0(preview_times, p_xs, p_freqs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
            except Exception as e:
                self._emit(False, error=str(e))
                return

        self._after_state_change("modify_params")
        self._emit(
            True,
            f"{iid} 的专属参数已更新。",
            item_params={
                "pitch_floor": item.get('pitch_floor'),
                "pitch_ceiling": item.get('pitch_ceiling'),
                "voicing_threshold": item.get('voicing_threshold'),
            },
            warning=item.get('has_empty_data', False),
            split_warnings=item.get('split_warnings', []),
            split_confidence=item.get('split_confidence', 1.0),
        )

    def do_recalculate(self, arg):
        """
        Recalculate bounds and pitches for all items based on current params.
        Usage: recalculate
        """
        self._emit(True, status="processing", message="Recalculating... this may take a moment.")

        if self.mode in ('long', 'single') and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            for iid, item in self.items.items():
                if item.get('is_excluded', False):
                    continue
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
                item['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
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
                if item.get('is_excluded', False):
                    continue
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
        self._emit(True, message="重算完成。")

    def _recompute_pitch_only_all_items(self):
        """
        Recompute pitch-related data only, preserving existing boundaries.
        Returns a tuple: (updated_count, skipped_count)
        """
        updated = 0
        skipped = 0

        if self.mode in ('long', 'single') and self.long_snd:
            pitch_data = extract_f0(self.long_snd, self.params)
            pitch_xs = pitch_data['xs']
            pitch_freqs = pitch_data['freqs']
            long_valid_indices = np.where(pitch_freqs > 0)[0]
            long_valid_xs = pitch_xs[long_valid_indices] if len(long_valid_indices) > 0 else np.array([])

            for item in self.items.values():
                if item.get('is_excluded', False):
                    skipped += 1
                    continue
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
                item['preview_f0'] = _calc_preview_f0(preview_times, pitch_xs, pitch_freqs, long_valid_xs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
                updated += 1
        else:
            for item in self.items.values():
                if item.get('is_excluded', False):
                    skipped += 1
                    continue
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
                item['preview_f0'] = _calc_preview_f0(preview_times, p_xs, p_freqs)
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
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
            self._emit(False, error="No items loaded. Please load audio files first.")
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
        if self.mode in ('long', 'single'):
            snd = self.long_snd
            if snd is None and self.long_snd_path and os.path.exists(self.long_snd_path):
                try:
                    snd = parselmouth.Sound(self.long_snd_path)
                    self.long_snd = snd
                except Exception:
                    pass
            if snd is None:
                self._emit(False, error="Active long audio file not loaded or not found")
                return

            pitch_data = extract_f0(snd, params_temp)
            times = pitch_data['xs']
            freqs = pitch_data['freqs']

            for item in self.items.values():
                macro_start = item.get('macro_start')
                macro_end = item.get('macro_end')
                # Ignore excluded and placeholder/missing items
                if item.get('is_excluded', False):
                    continue
                if macro_start is None or macro_end is None:
                    continue
                mask = (times >= macro_start) & (times <= macro_end)
                item_times = times[mask]
                item_freqs = freqs[mask]
                stable_f0 = self.extract_stable_f0_values(item_times, item_freqs)
                all_stable_f0.extend(stable_f0)

        else: # batch mode
            valid_items = [it for it in self.items.values()
                           if not it.get('is_excluded', False)
                           and (it.get('snd') or (it.get('path') and os.path.exists(it['path'])))]
            if not valid_items:
                self._emit(False, error="No valid independent audio files found in project")
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
            self._emit(
                False,
                error="Too little voiced speech data (< 0.5s) to reliably estimate F0. Please import more audio or ensure there is stable speech."
            )
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
                self._emit(
                    False,
                    error=f"Invalid preset '{preset_arg}'. Choose from: conservative, recommended, fine"
                )
                return

        self._emit(
            True,
            applied_msg if applied else f"F0 distribution estimated. Detected main distribution: {int(p5)}~{int(p95)} Hz.",
            metrics={
                "voiced_duration_s": voiced_duration,
                "stable_frames_count": len(all_stable_f0),
                "p5": p5,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "p95": p95
            },
            suggestions={
                "conservative": {"floor": cons_floor, "ceiling": cons_ceil},
                "recommended": {"floor": reco_floor, "ceiling": reco_ceil},
                "fine": {"floor": fine_floor, "ceiling": fine_ceil}
            },
            applied=applied,
        )

    SUPPORTED_EXPORT_FORMATS = [
        "xlsx",
        "txt",
        "textgrid",
        "ptwl",
        "contour",
        "density",
        "distribution",
        "quality",
        "overview_heatmap",
        "tone_effect_time",
        "formant_space",
        "f1_f2",
        "duration",
        "trend",
        "line_chart",
        "kde",
        "wav",
        "merged_wav",
        "formant_table",
        "formant_trajectory",
        "formant_density",
        "formant_overview_heatmap",
    ]

    def do_export(self, arg):
        """
        Export data to various formats.
        Usage: export <format> <output_file> [rule] [target] [key=val ...]
        Advanced charts: export <chart_type> <output_file_or_dir> [target] [key=val ...]
        Formats: xlsx, txt, textgrid, ptwl, contour, density, distribution, quality, overview_heatmap,
                 formant_space, f1_f2, duration, trend, line_chart, kde, wav, merged_wav,
                 formant_table, formant_trajectory, formant_density, formant_overview_heatmap
        Rule: continuous (default) or per_group (For 'wav' and 'merged_wav', rule can also be buffer_sec or gap_sec like 0.5)
        Target (Multi-speaker): active (default), separate (multiple files per speaker), integrated (merged T-value calculation)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("export")
            return

        if len(args) < 2:
            self._emit(
                False,
                error="Requires format and output_file",
                supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                hint="用法: export <格式> <输出路径> [continuous|per_group] [active|integrated|separate] [key=value ...]。输入 `export ?` 查看完整格式与参数列表。",
            )
            return

        fmt_raw = args[0].strip()
        fmt_low = fmt_raw.lower().lstrip(".")
        out_file = args[1]

        fmt_aliases = {
            "f1_f2": "formant_space",
            "f1f2": "formant_space",
            "vowel_space": "formant_space",
            "vowel_chart": "formant_space",
            "wordlist": "ptwl",
        }
        valid_formats_set = {f.lower() for f in self.SUPPORTED_EXPORT_FORMATS} | set(fmt_aliases.keys())

        # 1. 格式合法性校验（严格优先于空数据检查！）
        if fmt_low not in valid_formats_set:
            self._emit(
                False,
                error=f"Unknown format: {fmt_raw}",
                supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                hint=f"不支持的导出格式 '{fmt_raw}'。合法格式包括: {', '.join(self.SUPPORTED_EXPORT_FORMATS)}。输入 `export ?` 查看各格式参数说明。",
            )
            return

        empty_data_hint = "请先运行 load_long / load_batch + apply_wordlist，或通过 project_import / --project 载入已有工程"

        # 2. 高级字表 (.ptwl) 导出支持
        if fmt_low in ("ptwl", "wordlist"):
            custom_title = None
            for extra in args[2:]:
                if "=" in extra:
                    k, v = extra.split("=", 1)
                    if k.strip().lower() in ("title", "标题"):
                        custom_title = v.strip().strip('"').strip("'")

            spk = self.current_speaker
            base_doc = self.wordlist_doc
            target_items = spk.items if spk and spk.items else {}
            target_groups = list(self.groups) if self.groups else []

            if not target_items and base_doc is None:
                for other_spk in self.speaker_manager.get_all_speakers():
                    if other_spk.items or getattr(other_spk, "wordlist_doc", None) is not None:
                        target_items = other_spk.items
                        base_doc = getattr(other_spk, "wordlist_doc", None)
                        target_groups = getattr(other_spk, "cli_groups", []) or getattr(other_spk, "project_groups", []) or []
                        break

            has_base_items = bool(
                isinstance(base_doc, dict)
                and any(g.get("items") for g in base_doc.get("groups", []) if isinstance(g, dict))
            )
            if not target_items and not has_base_items:
                self._emit(
                    False,
                    error="No items to export",
                    hint=empty_data_hint,
                )
                return

            try:
                doc = build_wordlist_document_from_items(
                    target_items,
                    groups_order=target_groups,
                    base_doc=base_doc,
                    title=custom_title,
                )
                normalized = save_wordlist_file(doc, out_file)
                self.wordlist_doc = normalized
                summary = summarize_wordlist_document(normalized)
                self._emit(
                    True,
                    f"Exported ptwl to {out_file}",
                    path=out_file,
                    output_file=out_file,
                    schema=normalized.get("schema", WORDLIST_SCHEMA),
                    title=normalized.get("title", ""),
                    groups=summary["groups"],
                    group_count=summary["groups"],
                    item_count=summary["items"],
                )
            except Exception as e:
                self._emit(False, error=str(e))
            return

        # 3. 归一化高级科研图表别名
        chart_preset_overrides = {}
        if fmt_low in fmt_aliases:
            fmt = fmt_aliases[fmt_low]
        elif fmt_low == "duration":
            fmt = "contour"
            chart_preset_overrides["contour_x"] = "duration"
        elif fmt_low == "trend":
            fmt = "tone_effect_time"
        else:
            fmt = fmt_low

        # 4. 高级科研图表工具箱集成
        scientific_charts = {
            "contour",
            "distribution",
            "density",
            "quality",
            "overview_heatmap",
            "tone_effect_time",
            "formant_space",
            "formant_trajectory",
            "formant_density",
            "formant_overview_heatmap",
        }
        if fmt in scientific_charts:
            params = {
                "chart_type": fmt,
                "export_scope": "active",
                **chart_preset_overrides,
            }

            def parse_chart_group_rule(value):
                text = str(value or "").strip()
                lowered = text.lower()
                if lowered in ("default", "group", "原始组", "默认组"):
                    return {"source": "default", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("review", "metadata_source", "复核状态"):
                    return {"source": "metadata_source", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("label", "word", "词项"):
                    return {"source": "label", "tag_mode": "each", "selected_values": [], "field_name": ""}
                if lowered in ("speaker", "speaker_name", "发音人"):
                    return {"source": "speaker_name", "tag_mode": "each", "selected_values": [], "field_name": ""}

                if ":" in text:
                    prefix, rest = text.split(":", 1)
                else:
                    prefix, rest = text, ""
                prefix = prefix.strip().lower().replace("-", "_")
                rest = rest.strip()
                values = [v.strip() for v in rest.replace("；", ",").replace("、", ",").split(",") if v.strip()]

                source_aliases = {
                    "item_tag": "item_tags",
                    "item_tags": "item_tags",
                    "tag": "item_tags",
                    "tags": "item_tags",
                    "词项标签": "item_tags",
                    "标签": "item_tags",
                    "group_tag": "group_tags",
                    "group_tags": "group_tags",
                    "组标签": "group_tags",
                    "meta": "item_meta",
                    "field": "item_meta",
                    "item_meta": "item_meta",
                    "自定义字段": "item_meta",
                    "字段": "item_meta",
                    "contains_item_tag": "item_tags",
                    "contains_group_tag": "group_tags",
                    "filter_item_tag": "item_tags",
                    "filter_group_tag": "group_tags",
                }
                source = source_aliases.get(prefix)
                if not source:
                    raise ValueError(f"Unsupported chart_group rule: {text}")

                tag_mode = "each"
                if prefix.startswith("contains_"):
                    tag_mode = "contains"
                elif prefix.startswith("filter_"):
                    tag_mode = "filter_default"

                if source == "item_meta":
                    field_name = rest or (values[0] if values else "")
                    return {"source": "item_meta", "tag_mode": "each", "selected_values": [], "field_name": field_name}

                return {"source": source, "tag_mode": tag_mode, "selected_values": values, "field_name": ""}

            pos_args = []
            for item in args[2:]:
                if "=" in item:
                    k, v = item.split("=", 1)
                    k = k.strip().lower()
                    v = v.strip()
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        v = v[1:-1]
                    if k in ("target", "scope", "export_scope"):
                        k = "export_scope"
                    elif k == "facet":
                        if fmt == "density":
                            k = "density_facet"
                        elif fmt == "contour":
                            k = "contour_facet"
                    elif k in ("chart_group", "chart_group_rule", "group_rule"):
                        k = "chart_group_rule"
                        try:
                            v = parse_chart_group_rule(v)
                        except ValueError as exc:
                            self._emit(False, error=str(exc))
                            return
                    params[k] = v
                else:
                    pos_args.append(item)

            for pos_arg in pos_args:
                scope_candidate = pos_arg.lower()
                if scope_candidate in ("active", "separate", "integrated"):
                    params["export_scope"] = scope_candidate
                    break

            scope = params.get("export_scope", "active")

            float_params = {"density_bw", "density_p_low", "density_p_high", "density_m_min", "density_m_max"}
            for fp in float_params:
                if fp in params:
                    try:
                        params[fp] = float(params[fp])
                    except ValueError:
                        pass

            if scope == "separate":
                ext = "." + params.get("format", "png").lower()
            else:
                _, ext = os.path.splitext(out_file)
                if not ext:
                    ext_param = params.get("format", "png").lower()
                    ext = f".{ext_param}"
                    out_file = out_file + ext
                else:
                    params["format"] = ext[1:]

            adapter = AcousticChartCLIAdapter(self)
            all_speakers = self.speaker_manager.get_all_speakers()
            exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=all_speakers)
            exporter.params = params

            speakers_to_process = [self.current_speaker] if scope == "active" else all_speakers
            if not any(len(s.items) > 0 for s in speakers_to_process):
                self._emit(False, error="No items to export", hint=empty_data_hint)
                return

            old_mode = self.params.get("analysis_mode")
            old_spk_modes = {s.id: s.last_params.get("analysis_mode") for s in all_speakers}
            try:
                if fmt.startswith("formant_"):
                    self.params["analysis_mode"] = "formant"
                    params["analysis_mode"] = "formant"
                    for s in speakers_to_process:
                        s.last_params["analysis_mode"] = "formant"
                        for it in s.items.values():
                            if not it.get("is_excluded", False):
                                self._ensure_item_loaded(it, speaker=s)

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    if scope == "separate":
                        os.makedirs(out_file, exist_ok=True)
                        for speaker in all_speakers:
                            data = exporter._extract_active_data([speaker])
                            if data:
                                out_path = os.path.join(out_file, f"{speaker.name}_{fmt}{ext}")
                                exporter._export_dataset(data, out_path, ext)
                        self._emit(True, f"Exported {fmt} for multiple speakers separately to {out_file}", path=out_file, format=fmt)
                    else:
                        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or ".", exist_ok=True)
                        data = exporter._get_current_data_entries()
                        if not data and fmt == "formant_space":
                            ok = self._export_vowel_space_chart(out_file, speakers_to_process, emit_error=False)
                            if ok:
                                extra_payload = {"path": out_file, "format": fmt}
                                if scope == "integrated":
                                    extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                                self._emit(True, f"Exported {fmt} ({scope}) to {out_file}", **extra_payload)
                                return
                        if not data:
                            self._emit(False, error="No valid data found for export", hint=empty_data_hint)
                            return
                        exporter._export_dataset(data, out_file, ext)
                        extra_payload = {"path": out_file, "format": fmt}
                        if scope == "integrated":
                            extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                        self._emit(True, f"Exported {fmt} ({scope}) to {out_file}", **extra_payload)
            except Exception as e:
                self._emit(False, error=str(e))
            finally:
                if old_mode is not None:
                    self.params["analysis_mode"] = old_mode
                elif "analysis_mode" in self.params:
                    del self.params["analysis_mode"]
                for s in all_speakers:
                    prev_m = old_spk_modes.get(s.id)
                    if prev_m is not None:
                        s.last_params["analysis_mode"] = prev_m
                    elif "analysis_mode" in s.last_params:
                        del s.last_params["analysis_mode"]
            return

        # 5. 常规数据表与音频/TextGrid 导出
        rule = "continuous"
        target = "active"
        pos_rest = []
        for token in args[2:]:
            if "=" in token:
                k, v = token.split("=", 1)
                k_low = k.strip().lower()
                v_clean = v.strip().strip('"').strip("'").lower()
                if k_low in ("target", "scope", "export_scope"):
                    target = v_clean
                elif k_low in ("rule", "num_rule"):
                    rule = v.strip().strip('"').strip("'")
            else:
                pos_rest.append(token)

        for tok in pos_rest:
            if tok.lower() in ("active", "separate", "integrated"):
                target = tok.lower()
            else:
                rule = tok

        speakers_to_process = [self.current_speaker] if target == "active" else self.speaker_manager.get_all_speakers()

        if not any(len(s.items) > 0 for s in speakers_to_process):
            self._emit(False, error="No items to export", hint=empty_data_hint)
            return

        try:
            if fmt == "formant_table":
                self._export_formant_table(out_file, speakers_to_process)
                extra_payload = {"path": out_file, "format": fmt}
                if target == "integrated":
                    extra_payload["integrated_summary"] = self._build_integrated_summary(speakers_to_process)
                self._emit(True, f"Exported formant_table to {out_file}", **extra_payload)
                return

            if target == "separate":
                os.makedirs(out_file, exist_ok=True)
                for s in speakers_to_process:
                    s_struct = [
                        (grp, [iid for iid, item in s.items.items() if item.get("group") == grp and not item.get("is_excluded", False)])
                        for grp in getattr(s, "cli_groups", [])
                    ]
                    s_out = os.path.join(out_file, f"{s.name}_{fmt}")

                    if fmt == "txt":
                        self._export_txt(f"{s_out}.txt", s_struct, rule, s)
                    elif fmt == "xlsx":
                        self._export_xlsx(f"{s_out}.xlsx", s_struct, rule, s)
                    elif fmt == "line_chart":
                        self._export_line_chart(f"{s_out}.png", s_struct, s)
                    elif fmt == "kde":
                        self._export_kde_heatmap(f"{s_out}.png", s_struct, s)
                    elif fmt == "wav":
                        self._export_wav(s_out, s_struct, rule, s)
                    elif fmt == "merged_wav":
                        self._export_merged_wav(f"{s_out}.wav", s_struct, rule, s)
                    elif fmt == "textgrid":
                        if s.tab_mode == "多条独立音频":
                            self._export_textgrid(s_out, s_struct, s)
                        else:
                            self._export_textgrid(f"{s_out}.TextGrid", s_struct, s)
                    else:
                        self._emit(False, error=f"Unknown format: {fmt}", supported_formats=self.SUPPORTED_EXPORT_FORMATS)
                        return
                self._emit(True, f"Exported {fmt} for multiple speakers to {out_file}", path=out_file, format=fmt)

            elif target == "integrated":
                if fmt not in ("txt", "xlsx", "line_chart", "kde"):
                    self._emit(
                        False,
                        error=f"Format {fmt} does not support integrated multi-speaker export",
                        supported_formats=self.SUPPORTED_EXPORT_FORMATS,
                    )
                    return
                if fmt == "txt":
                    self._export_txt_integrated(out_file, speakers_to_process, rule)
                elif fmt == "xlsx":
                    self._export_xlsx_integrated(out_file, speakers_to_process, rule)
                elif fmt == "line_chart":
                    self._export_line_chart_integrated(out_file, speakers_to_process)
                elif fmt == "kde":
                    self._export_kde_heatmap_integrated(out_file, speakers_to_process)
                integrated_summary = self._build_integrated_summary(speakers_to_process)
                self._emit(
                    True,
                    f"Exported integrated {fmt} to {out_file}",
                    path=out_file,
                    format=fmt,
                    integrated_summary=integrated_summary,
                )

            else:  # active
                s = self.current_speaker
                structure = [
                    (grp, [iid for iid, item in s.items.items() if item.get("group") == grp and not item.get("is_excluded", False)])
                    for grp in self.groups
                ]
                if fmt == "txt":
                    self._export_txt(out_file, structure, rule, s)
                elif fmt == "xlsx":
                    self._export_xlsx(out_file, structure, rule, s)
                elif fmt == "line_chart":
                    self._export_line_chart(out_file, structure, s)
                elif fmt == "kde":
                    self._export_kde_heatmap(out_file, structure, s)
                elif fmt == "wav":
                    self._export_wav(out_file, structure, rule, s)
                elif fmt == "merged_wav":
                    self._export_merged_wav(out_file, structure, rule, s)
                elif fmt == "textgrid":
                    self._export_textgrid(out_file, structure, s)
                else:
                    self._emit(False, error=f"Unknown format: {fmt}", supported_formats=self.SUPPORTED_EXPORT_FORMATS)
                    return
                self._emit(True, f"Exported to {out_file}", path=out_file, format=fmt)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_tool_wordlist_convert(self, arg):
        """
        Convert any messy .txt, structured .txt, .csv, or legacy wordlist into a standard v2 .ptwl document.
        Usage: tool_wordlist_convert <in_path> <out_path.ptwl> [title=...]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        pos_args = []
        custom_title = None
        in_path = None
        out_path = None
        idx = 0
        while idx < len(args):
            tok = args[idx]
            if tok in ("--title", "-t") and idx + 1 < len(args):
                custom_title = args[idx + 1].strip()
                idx += 2
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lstrip("-").lower()
                v_clean = v.strip().strip('"').strip("'")
                if k_low in ("title", "标题"):
                    custom_title = v_clean
                elif k_low in ("in", "input", "src"):
                    in_path = v_clean
                elif k_low in ("out", "output", "dst"):
                    out_path = v_clean
                else:
                    pos_args.append(tok)
            else:
                pos_args.append(tok)
            idx += 1

        if in_path is None and len(pos_args) >= 1:
            in_path = pos_args[0]
        if out_path is None and len(pos_args) >= 2:
            out_path = pos_args[1]
        if custom_title is None and len(pos_args) >= 3:
            custom_title = pos_args[2]

        if not in_path or not out_path:
            self._emit(
                False,
                error="Usage: tool_wordlist_convert <in_path> <out_path.ptwl> [title=...]",
                hint="示例: tool_wordlist_convert raw_words.txt standard.ptwl",
            )
            return

        if not os.path.exists(in_path):
            self._emit(False, error=f"File not found: {in_path}")
            return

        try:
            doc, groups, flat_words, _records = self._load_wordlist_any(in_path, title=custom_title)
            if custom_title:
                doc["title"] = custom_title
            normalized = save_wordlist_file(doc, out_path)
            summary = summarize_wordlist_document(normalized)
            self._emit(
                True,
                f"已将字表转换并保存为标准 v2 高级字表：{out_path}",
                input_file=in_path,
                output_file=out_path,
                input=in_path,
                output=out_path,
                path=out_path,
                schema=normalized.get("schema", WORDLIST_SCHEMA),
                title=normalized.get("title", ""),
                group_count=summary["groups"],
                item_count=summary["items"],
                groups=summary["groups"],
                total_words=summary["items"],
                summary=summary,
            )
        except Exception as e:
            self._emit(False, error=str(e))

    def do_stats(self, arg):
        """
        Compute and return structured acoustic statistics summary.
        Usage: stats [by=group|item|speaker] [scope=active|integrated] [out=<json_or_md_path>]
        Example: stats by=group scope=integrated out=stats.json
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit(
                True,
                "stats 命令参数说明",
                usage="stats [by=group|item|speaker] [scope=active|integrated] [out=<json_or_md_path>]",
                supported_by=["group", "item", "speaker"],
                supported_scope=["active", "integrated"],
            )
            return

        by_val = "group"
        scope_val = None
        out_path = None

        for token in args:
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in ("by", "group_by", "groupby"):
                    by_val = v.lower()
                elif k in ("scope", "target", "export_scope"):
                    scope_val = v.lower()
                elif k in ("out", "output", "out_file", "output_file"):
                    out_path = v
                else:
                    self._emit(
                        False,
                        error=f"Unknown parameter for stats: {k}. Supported: by=group|item|speaker, scope=active|integrated, out=<path>",
                    )
                    return
            else:
                tl = token.strip().lower()
                if tl in ("group", "groups", "item", "items", "word", "label", "speaker", "speakers"):
                    by_val = tl
                elif tl in ("active", "integrated"):
                    scope_val = tl
                else:
                    out_path = token.strip()

        if by_val in ("groups", "tone"):
            by_val = "group"
        elif by_val in ("items", "word", "words", "label"):
            by_val = "item"
        elif by_val in ("speakers", "spk"):
            by_val = "speaker"
        if by_val not in ("group", "item", "speaker"):
            self._emit(False, error=f"Invalid by={by_val}. Must be one of: group, item, speaker")
            return

        all_speakers = self.speaker_manager.get_all_speakers()
        speakers_with_items = [s for s in all_speakers if len(s.items) > 0]
        if not speakers_with_items:
            self._emit(False, error="No items loaded. Please load audio and apply wordlist/TextGrid or import a project first.")
            return

        if scope_val is None:
            scope_val = "active"
        if scope_val not in ("active", "integrated"):
            self._emit(False, error=f"Invalid scope={scope_val}. Must be active or integrated")
            return

        target_spks = [self.current_speaker] if scope_val == "active" else all_speakers
        for spk in target_spks:
            for item in spk.items.values():
                if not item.get("is_excluded", False):
                    self._ensure_item_loaded(item, speaker=spk)

        from modules.report_generator import compute_acoustic_statistics, build_markdown_acoustic_summary_section
        project_data = self._build_project_data_dict()
        stats_res = compute_acoustic_statistics(
            project_data,
            zip_ref=None,
            by=by_val,
            scope=scope_val,
            active_speaker_id=self.speaker_manager.active_speaker_id,
        )

        if out_path:
            try:
                abs_out = os.path.abspath(out_path)
                os.makedirs(os.path.dirname(abs_out) or ".", exist_ok=True)
                if abs_out.lower().endswith((".md", ".markdown")):
                    md_lines = ["# PhonTracer 结构化声学统计摘要", ""]
                    md_lines.extend(build_markdown_acoustic_summary_section(project_data, zip_ref=None))
                    with open(abs_out, "w", encoding="utf-8") as f:
                        f.write("\n".join(md_lines))
                else:
                    with open(abs_out, "w", encoding="utf-8") as f:
                        json.dump(stats_res, f, ensure_ascii=False, indent=2)
                stats_res["output_file"] = abs_out
            except Exception as e:
                self._emit(False, error=f"Failed to write stats output file: {e}")
                return

        emit_res = dict(stats_res)
        if getattr(self, "_in_script_file", False) and by_val != "item" and "item_records" in emit_res:
            emit_res["item_records_count"] = len(emit_res.pop("item_records"))
        self._emit(True, f"声学统计摘要计算完成 (by={by_val}, scope={scope_val})。", **emit_res)

    def do_export_bundle(self, arg):
        """
        One-stop research artifact bundle export.
        Usage: export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg] [title=<project_title>]
        Generates under <out_dir>:
          - <project_title>.teproj
          - <project_title>.ptwl
          - acoustic_data.xlsx & acoustic_points.txt
          - acoustic_stats.json
          - charts/ (contour, distribution, quality, overview_heatmap, and formant_space if formant data exists)
          - report/ (研究方法报告.md, 研究档案.xlsx, provenance_meta.json)
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if not args or (len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help")):
            self._emit(
                 len(args) == 1,
                error=None if len(args) == 1 else "Usage: export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg]",
                usage="export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg]",
            )
            return

        out_dir = args[0]
        scope = None
        img_fmt = "png"
        custom_title = None

        for token in args[1:]:
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in ("scope", "target", "export_scope"):
                    scope = v.lower()
                elif k in ("format", "fmt", "img_format"):
                    img_fmt = v.lower().lstrip(".")
                elif k in ("title", "project_title", "name"):
                    custom_title = v
                else:
                    self._emit(
                        False,
                        error=f"Unknown parameter for export_bundle: {k}. Supported: scope=active|integrated|separate, format=png|svg, title=<name>",
                    )
                    return
            else:
                tl = token.strip().lower().lstrip(".")
                if tl in ("active", "integrated", "separate"):
                    scope = tl
                elif tl in ("png", "svg", "pdf"):
                    img_fmt = tl

        all_speakers = self.speaker_manager.get_all_speakers()
        speakers_with_items = [s for s in all_speakers if len(s.items) > 0]
        if not speakers_with_items:
            self._emit(False, error="No items to export. Please load audio and apply wordlist/TextGrid or import a project first.")
            return

        if scope is None:
            scope = "integrated" if len(speakers_with_items) > 1 else "active"
        if scope not in ("active", "integrated", "separate"):
            self._emit(False, error=f"Invalid scope: {scope}. Must be active, integrated, or separate")
            return
        if img_fmt not in ("png", "svg", "pdf"):
            self._emit(False, error=f"Invalid format: {img_fmt}. Must be png or svg")
            return

        abs_out_dir = os.path.abspath(out_dir)
        charts_dir = os.path.join(abs_out_dir, "charts")
        report_dir = os.path.join(abs_out_dir, "report")
        os.makedirs(abs_out_dir, exist_ok=True)
        os.makedirs(charts_dir, exist_ok=True)
        os.makedirs(report_dir, exist_ok=True)

        # Ensure all items across speakers are loaded
        orig_active_id = self.speaker_manager.active_speaker_id
        try:
            for spk in all_speakers:
                self.speaker_manager.set_active_speaker(spk.id)
                if not getattr(spk, "cli_groups", None):
                    seen_g = []
                    for it in spk.items.values():
                        g = it.get("group", "默认组")
                        if g not in seen_g:
                            seen_g.append(g)
                    spk.cli_groups = seen_g
                for item in spk.items.values():
                    if not item.get("is_excluded", False):
                        self._ensure_item_loaded(item, speaker=spk)
        finally:
            if orig_active_id:
                self.speaker_manager.set_active_speaker(orig_active_id)

        # Resolve project title (if --project points into abs_out_dir and no custom_title is forced, reuse bound .teproj name to prevent duplicate .teproj archives)
        bound_in_out_dir = False
        if self._bound_project_path:
            abs_bound = os.path.abspath(self._bound_project_path)
            if os.path.dirname(abs_bound) == abs_out_dir and abs_bound.lower().endswith(".teproj"):
                bound_in_out_dir = True

        wl_doc = self._build_wordlist_v2_doc_from_project(title=custom_title)
        if bound_in_out_dir and not custom_title:
            safe_title = os.path.splitext(os.path.basename(self._bound_project_path))[0]
        else:
            raw_title = custom_title or wl_doc.get("title") or ""
            if not raw_title or raw_title in ("未命名字表", "PhonTracer工程字表", "从普通字表导入"):
                if self._bound_project_path:
                    raw_title = os.path.splitext(os.path.basename(self._bound_project_path))[0]
                else:
                    dir_base = os.path.basename(abs_out_dir.rstrip("/\\"))
                    raw_title = dir_base if dir_base and dir_base not in (".", "output", "out", "bundle") else "phontracer_project"
            safe_title = re.sub(r'[\\/*?:"<>|\s]+', "_", str(raw_title)).strip("._") or "phontracer_project"

        bundle_files = []
        errors = []

        # 1. Export <project_title>.teproj
        teproj_path = os.path.abspath(self._bound_project_path) if (bound_in_out_dir and not custom_title) else os.path.join(abs_out_dir, f"{safe_title}.teproj")
        project_data = self._build_project_data_dict()
        try:
            ok = self.project_manager.export_project(teproj_path)
            if not ok or not os.path.exists(teproj_path):
                self._export_fallback_teproj_archive(teproj_path, project_data)
            if os.path.exists(teproj_path):
                bundle_files.append(teproj_path)
                if self._bound_project_path:
                    abs_bound = os.path.abspath(self._bound_project_path)
                    if abs_bound == os.path.abspath(teproj_path):
                        self._session_dirty = False
                    else:
                        import shutil as _shutil
                        os.makedirs(os.path.dirname(abs_bound) or ".", exist_ok=True)
                        _shutil.copy2(teproj_path, abs_bound)
                        self._session_dirty = False
        except Exception:
            try:
                self._export_fallback_teproj_archive(teproj_path, project_data)
                if os.path.exists(teproj_path):
                    bundle_files.append(teproj_path)
                    if self._bound_project_path and os.path.abspath(self._bound_project_path) == os.path.abspath(teproj_path):
                        self._session_dirty = False
            except Exception as e2:
                errors.append(f"teproj export failed: {e2}")

        # 2. Export <project_title>.ptwl
        ptwl_path = os.path.join(abs_out_dir, f"{safe_title}.ptwl")
        try:
            from modules.wordlist_v2 import save_wordlist_document
            save_wordlist_document(wl_doc, ptwl_path)
            if os.path.exists(ptwl_path):
                bundle_files.append(ptwl_path)
        except Exception as e:
            errors.append(f"ptwl export failed: {e}")

        # 3. Export acoustic_data.xlsx & acoustic_points.txt
        xlsx_path = os.path.join(abs_out_dir, "acoustic_data.xlsx")
        txt_path = os.path.join(abs_out_dir, "acoustic_points.txt")
        target_spks = [self.current_speaker] if scope == "active" else speakers_with_items
        try:
            if scope in ("integrated", "separate") and len(speakers_with_items) > 1:
                self._export_xlsx_integrated(xlsx_path, speakers_with_items, "continuous")
                self._export_txt_integrated(txt_path, speakers_with_items, "continuous")
            else:
                spk_for_table = self.current_speaker if len(self.current_speaker.items) > 0 else speakers_with_items[0]
                g_list = getattr(spk_for_table, "cli_groups", None) or list(dict.fromkeys(it.get("group", "默认组") for it in spk_for_table.items.values()))
                struct = [
                    (grp, [iid for iid, it in spk_for_table.items.items() if it.get("group", "默认组") == grp and not it.get("is_excluded", False)])
                    for grp in g_list
                ]
                self._export_xlsx(xlsx_path, struct, "continuous", spk_for_table)
                self._export_txt(txt_path, struct, "continuous", spk_for_table)
            if os.path.exists(xlsx_path):
                bundle_files.append(xlsx_path)
            if os.path.exists(txt_path):
                bundle_files.append(txt_path)
        except Exception as e:
            errors.append(f"xlsx/txt export failed: {e}")

        # 4. Export acoustic_stats.json
        stats_path = os.path.join(abs_out_dir, "acoustic_stats.json")
        try:
            from modules.report_generator import compute_acoustic_statistics
            stats_scope = "active" if scope == "active" else "integrated"
            stats_group = compute_acoustic_statistics(
                project_data, zip_ref=None, by="group", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            stats_item = compute_acoustic_statistics(
                project_data, zip_ref=None, by="item", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            stats_speaker = compute_acoustic_statistics(
                project_data, zip_ref=None, by="speaker", scope=stats_scope, active_speaker_id=self.speaker_manager.active_speaker_id
            )
            combined_stats = dict(stats_group)
            combined_stats["by_group"] = stats_group.get("summary", [])
            combined_stats["by_item"] = stats_item.get("summary", [])
            combined_stats["by_speaker"] = stats_speaker.get("summary", [])
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(combined_stats, f, ensure_ascii=False, indent=2)
            if os.path.exists(stats_path):
                bundle_files.append(stats_path)
        except Exception as e:
            errors.append(f"stats export failed: {e}")

        # 5. Export charts/ (contour, distribution, quality, overview_heatmap, + formant_space if formant data exists)
        has_any_formant = (
            self.params.get("analysis_mode") == "formant"
            or any(
                it.get("formant_data") is not None or it.get("preview_formants") is not None or it.get("analysis_mode") == "formant"
                for s in target_spks
                for it in s.items.values()
                if not it.get("is_excluded", False)
            )
        )
        chart_types = ["contour", "distribution", "density", "quality", "overview_heatmap"]
        if has_any_formant:
            chart_types.append("formant_space")

        adapter = AcousticChartCLIAdapter(self)
        ext = f".{img_fmt}"
        chart_scope = "integrated" if (scope != "active" and len(speakers_with_items) > 1) else "active"
        for ctype in chart_types:
            c_out = os.path.join(charts_dir, f"{ctype}{ext}")
            try:
                exporter = AcousticChartExporter(project_tree=adapter, app=self, all_speakers=all_speakers)
                c_params = {
                    "chart_type": ctype,
                    "export_scope": chart_scope,
                    "format": img_fmt,
                    "scale": "t_value" if chart_scope == "integrated" else "t_value",
                }
                exporter.params = c_params
                old_m = self.params.get("analysis_mode")
                try:
                    if ctype.startswith("formant_"):
                        self.params["analysis_mode"] = "formant"
                        c_params["analysis_mode"] = "formant"
                    else:
                        self.params["analysis_mode"] = "f0"
                    data = exporter._get_current_data_entries()
                    if data:
                        exporter._export_dataset(data, c_out, ext)
                    elif ctype == "formant_space":
                        self._export_vowel_space_chart(c_out, target_spks, emit_error=False)
                finally:
                    if old_m is not None:
                        self.params["analysis_mode"] = old_m
                if os.path.exists(c_out):
                    bundle_files.append(c_out)
                for p_fn in sorted(os.listdir(charts_dir)):
                    if p_fn.startswith(f"{ctype}_第") and p_fn.endswith(ext):
                        p_full = os.path.join(charts_dir, p_fn)
                        if p_full not in bundle_files:
                            bundle_files.append(p_full)
                if scope == "separate" and len(speakers_with_items) > 1:
                    for spk in speakers_with_items:
                        sep_out = os.path.join(charts_dir, f"{spk.name}_{ctype}{ext}")
                        sep_data = exporter._extract_active_data([spk])
                        if sep_data:
                            exporter._export_dataset(sep_data, sep_out, ext)
                            if os.path.exists(sep_out):
                                bundle_files.append(sep_out)
            except Exception as e:
                errors.append(f"chart {ctype} export failed: {e}")

        # 6. Export report/ (研究方法报告.md, 研究档案.xlsx, provenance_meta.json)
        try:
            from modules.report_generator import export_reports_from_teproj, calculate_sha256
            import shutil
            import time
            if os.path.exists(teproj_path):
                rep_files, _ = export_reports_from_teproj(
                    teproj_path,
                    report_dir,
                    export_markdown=True,
                    export_excel=True,
                    include_cache_details=False,
                )
                for rf in rep_files:
                    if os.path.exists(rf):
                        bundle_files.append(rf)
                        if rf.endswith("_研究方法报告.md"):
                            canonical_md = os.path.join(report_dir, "研究方法报告.md")
                            if os.path.abspath(rf) != os.path.abspath(canonical_md):
                                shutil.copy2(rf, canonical_md)
                                bundle_files.append(canonical_md)
                prov_path = os.path.join(report_dir, "provenance_meta.json")
                prov_data = {
                    "project_title": safe_title,
                    "teproj_file": os.path.basename(teproj_path),
                    "teproj_sha256": calculate_sha256(teproj_path),
                    "software_version": __version__,
                    "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "scope": scope,
                    "chart_format": img_fmt,
                    "speaker_count": len(speakers_with_items),
                    "bundle_files": [os.path.relpath(p, abs_out_dir) for p in bundle_files],
                }
                with open(prov_path, "w", encoding="utf-8") as f:
                    json.dump(prov_data, f, ensure_ascii=False, indent=2)
                bundle_files.append(prov_path)
        except Exception as e:
            errors.append(f"report export failed: {e}")

        self._emit(
            True,
            f"科研产物包已一站式导出至 {abs_out_dir}（共 {len(bundle_files)} 个文件）。",
            out_dir=abs_out_dir,
            project_title=safe_title,
            scope=scope,
            format=img_fmt,
            bundle_files=bundle_files,
            warnings=errors,
        )

    def do_detect_formants(self, arg):
        """
        Detect optimal formant extraction parameters (max_formant, num_formants, window_length, pre_emphasis)
        using FormantOptimizer and optionally apply them.
        Usage: detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution|reco|anti|fine]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("detect_formants")
            return

        if not self.items:
            self._emit(False, error="No items loaded. Please load audio files first.")
            return

        preset_aliases = {
            "recommended": "recommended",
            "reco": "recommended",
            "conservative": "conservative",
            "anti": "conservative",
            "high_resolution": "high_resolution",
            "fine": "high_resolution",
        }
        should_apply = True
        preset = "recommended"

        for token in args:
            tl = token.strip().lower()
            if tl in ("--apply", "-a", "apply"):
                should_apply = True
            elif tl in ("--no-apply", "--dry-run", "no-apply", "dry_run"):
                should_apply = False
            elif tl in preset_aliases:
                preset = preset_aliases[tl]
            elif "=" in tl:
                k, v = tl.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "apply":
                    should_apply = v not in ("false", "0", "no", "off")
                elif k == "preset":
                    preset = preset_aliases.get(v, v)
                else:
                    self._emit(False, error=f"Unknown parameter for detect_formants: {k}")
                    return
            else:
                self._emit(False, error=f"Unknown argument for detect_formants: {token}")
                return

        if preset not in ("recommended", "conservative", "high_resolution"):
            self._emit(False, error=f"Invalid preset '{preset}'. Choose from: recommended (reco), conservative (anti), high_resolution (fine)")
            return

        for item in self.items.values():
            if not item.get("is_excluded", False):
                self._ensure_item_loaded(item, speaker=self.current_speaker)

        from modules.formant_optimizer import FormantOptimizer
        optimizer = FormantOptimizer()
        opt_res = optimizer.optimize(
            self.items,
            mode=self.mode,
            long_snd=self.long_snd,
            long_snd_path=self.long_snd_path,
        )

        suggestions = dict(opt_res.get("suggestions", {}))
        if "recommended" in suggestions and "reco" not in suggestions:
            suggestions["reco"] = suggestions["recommended"]
        if "conservative" in suggestions and "anti" not in suggestions:
            suggestions["anti"] = suggestions["conservative"]
        if "high_resolution" in suggestions and "fine" not in suggestions:
            suggestions["fine"] = suggestions["high_resolution"]

        chosen = suggestions.get(preset) or opt_res.get("best_params", {})
        max_formant = float(chosen.get("max_formant", chosen.get("formant_max_hz", 5500.0)))
        num_formants = int(chosen.get("num_formants", chosen.get("formant_count", 5)))
        win_len = float(chosen.get("formant_window_length", 0.025))
        pre_emp = float(chosen.get("formant_pre_emphasis", 50.0))

        updated_items = 0
        if should_apply:
            self.params["formant_max_hz"] = max_formant
            self.params["formant_count"] = num_formants
            self.params["formant_window_length"] = win_len
            self.params["formant_pre_emphasis"] = pre_emp
            for item in self.items.values():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                if item.get("snd") is not None:
                    try:
                        from modules.audio_core import extract_formants
                        total_dur = item["snd"].get_total_duration()
                        s_ref = item.get("macro_start") if item.get("macro_start") is not None else item.get("start")
                        e_ref = item.get("macro_end") if item.get("macro_end") is not None else item.get("end")
                        if s_ref is not None and e_ref is not None and total_dur > 5.0 and float(e_ref) > float(s_ref):
                            seg_s = max(0.0, float(s_ref) - 0.5)
                            seg_e = min(total_dur, float(e_ref) + 0.5)
                            part_snd = item["snd"].extract_part(from_time=seg_s, to_time=seg_e)
                            f_data = extract_formants(part_snd, self.params)
                            f_data["xs"] = f_data["xs"] + seg_s
                            item["formant_data"] = f_data
                        else:
                            item["formant_data"] = extract_formants(item["snd"], self.params)
                        updated_items += 1
                    except Exception:
                        pass
            self._after_state_change("detect_formants")

        self._emit(
            True,
            f"共振峰最佳参数探测完成：max_formant={int(max_formant)} Hz, num_formants={num_formants}"
            + (f"（已写入参数并更新 {updated_items} 条目）" if should_apply else "（仅预览建议，未修改参数）"),
            applied=should_apply,
            preset=preset,
            max_formant=max_formant,
            num_formants=num_formants,
            formant_max_hz=max_formant,
            formant_count=num_formants,
            formant_window_length=win_len,
            formant_pre_emphasis=pre_emp,
            best_params=opt_res.get("best_params", {}),
            suggestions=suggestions,
            voiced_duration_s=opt_res.get("voiced_duration_s", 0.0),
            insufficient_data=opt_res.get("insufficient_data", False),
            updated_items=updated_items,
            params=self.params,
        )

    def do_fix_warnings(self, arg):
        """
        Scan all items for acoustic/segmentation warnings (octave_jump, low_f0_coverage,
        no_clear_valley, tiny_segment), generate item-level and global parameter tuning suggestions,
        and auto-repair when --apply or apply=true is passed.
        Usage: fix_warnings [--apply|--no-apply] [scope=active|all|integrated] [apply=true|false] [pitch_floor=<Hz>] [voicing_threshold=<val>]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Argument parse error: {e}")
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit_subcommand_help("fix_warnings")
            return

        should_apply = False
        scope = "active"
        user_pitch_floor = None
        user_voicing_threshold = None

        for token in args:
            tl = token.strip().lower()
            if tl in ("--apply", "-a", "apply"):
                should_apply = True
            elif tl in ("--no-apply", "--dry-run", "no-apply", "dry_run"):
                should_apply = False
            elif tl in ("active", "all", "integrated", "all_speakers"):
                scope = "all" if tl in ("all", "integrated", "all_speakers") else "active"
            elif "=" in tl:
                k, v = tl.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "apply":
                    should_apply = v in ("true", "1", "yes", "on")
                elif k in ("scope", "target"):
                    scope = "all" if v in ("all", "integrated", "all_speakers") else "active"
                elif k in ("pitch_floor", "f0_min"):
                    try:
                        user_pitch_floor = float(v)
                    except ValueError:
                        self._emit(False, error=f"Invalid pitch_floor value: {v}")
                        return
                elif k in ("voicing_threshold", "voicing_thresh"):
                    try:
                        user_voicing_threshold = float(v)
                    except ValueError:
                        self._emit(False, error=f"Invalid voicing_threshold value: {v}")
                        return
                else:
                    self._emit(False, error=f"Unknown parameter for fix_warnings: {k}")
                    return
            else:
                self._emit(False, error=f"Unknown argument for fix_warnings: {token}")
                return

        target_speakers = [self.current_speaker] if scope == "active" else self.speaker_manager.get_all_speakers()
        if not any(len(s.items) > 0 for s in target_speakers):
            self._emit(False, error="No items loaded.")
            return

        # Compute speaker median F0 and scan initial warnings
        spk_medians = {}
        for spk in target_speakers:
            for item in spk.items.values():
                if not item.get("is_excluded", False) and not item.get("missing", False):
                    self._ensure_item_loaded(item, speaker=spk)
            spk_medians[spk.id] = self._compute_speaker_median_f0(spk)

        before_items = []
        before_counts = {"octave_jump": 0, "low_f0_coverage": 0, "no_clear_valley": 0, "tiny_segment": 0}
        for spk in target_speakers:
            med_f0 = spk_medians.get(spk.id, 160.0)
            for iid, item in spk.items.items():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                diag = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0)
                diag["speaker"] = spk.name
                if diag["issues"]:
                    before_items.append((spk, iid, item, diag))
                    for iss in diag["issues"]:
                        if iss in before_counts:
                            before_counts[iss] += 1

        warnings_before = len(before_items)

        # Build suggestions
        item_suggestions = []
        for spk, iid, item, diag in before_items:
            med_f0 = spk_medians.get(spk.id, 160.0)
            rec_f0_min = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med_f0 * 0.62)))
            rec_f0_max = min(750, int(round(med_f0 * 1.58)))
            actions = []
            recommended_params = {}
            if "octave_jump" in diag["issues"]:
                actions.append(
                    f"开启 despike 多轮去毛刺插值，并按发音人中位数 ({med_f0:.1f}Hz) 收缩 f0_min={rec_f0_min}Hz, f0_max={rec_f0_max}Hz 抑制倍频/半频跳变"
                )
                recommended_params["despike"] = True
                recommended_params["pitch_floor"] = rec_f0_min
                recommended_params["pitch_ceiling"] = rec_f0_max
            if "low_f0_coverage" in diag["issues"]:
                vt_val = float(user_voicing_threshold) if user_voicing_threshold is not None else 0.15
                actions.append(
                    f"放宽 voicing_threshold={vt_val:.2f} 与 silence_threshold=0.01，下探 pitch_floor 并对各子音节区间进行有声段加密插值重采样"
                )
                recommended_params["voicing_threshold"] = vt_val
                recommended_params["silence_threshold"] = 0.01
                recommended_params["pitch_floor"] = int(round(user_pitch_floor)) if user_pitch_floor is not None else min(recommended_params.get("pitch_floor", 75), max(40, int(round(med_f0 * 0.55))))
            if "no_clear_valley" in diag["issues"]:
                actions.append("结合局部声强谷值搜索 (find_minimum_intensity_valley) 与基频连续性重新校准多音节词内切分点")
            if "tiny_segment" in diag["issues"]:
                actions.append("放宽能量跌落阈值 db (+12dB) 并向 raw_start/raw_end 或 macro 边界适度扩展，消除过短音段 (<80ms)")
                recommended_params["db"] = min(75.0, float(spk.last_params.get("db", 60.0)) + 10.0)

            item_suggestions.append({
                "speaker": spk.name,
                "item_id": iid,
                "label": diag["label"],
                "group": diag["group"],
                "issues": diag["issues"],
                "recommended_params": recommended_params,
                "suggested_actions": actions,
            })

        global_suggestions = {
            "despike": before_counts["octave_jump"] > 0,
            "voicing_threshold": (
                float(user_voicing_threshold)
                if user_voicing_threshold is not None
                else (0.15 if before_counts["low_f0_coverage"] > 0 else self.params.get("voicing_threshold", 0.25))
            ),
            "silence_threshold": 0.01 if before_counts["low_f0_coverage"] > 0 else self.params.get("silence_threshold", 0.03),
        }
        if target_speakers:
            med0 = spk_medians.get(target_speakers[0].id, 160.0)
            global_suggestions["recommended_pitch_floor"] = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med0 * 0.62)))
            global_suggestions["recommended_pitch_ceiling"] = min(750, int(round(med0 * 1.58)))

        if should_apply and before_items:
            from modules.anomaly_detection import detect_pitch_anomaly_points
            from modules.report_generator import check_item_has_empty_f0
            self.params["despike"] = True
            if before_counts["low_f0_coverage"] > 0:
                self.params["voicing_threshold"] = (
                    float(user_voicing_threshold)
                    if user_voicing_threshold is not None
                    else min(float(self.params.get("voicing_threshold", 0.25)), 0.15)
                )
                self.params["silence_threshold"] = 0.01

            for spk, iid, item, diag in before_items:
                med_f0 = spk_medians.get(spk.id, 160.0)
                rec_f0_min = int(round(user_pitch_floor)) if user_pitch_floor is not None else max(45, int(round(med_f0 * 0.62)))
                rec_f0_max = min(750, int(round(med_f0 * 1.58)))
                overrides = item.setdefault("param_overrides", {})

                # 1. Fix tiny_segment first (expand bounds if needed)
                if "tiny_segment" in diag["issues"]:
                    t_s = float(item.get("start") or 0.0)
                    t_e = float(item.get("end") or 0.0)
                    raw_s = item.get("raw_start")
                    raw_e = item.get("raw_end")
                    mac_s = item.get("macro_start")
                    mac_e = item.get("macro_end")
                    if raw_s is not None and raw_e is not None and (float(raw_e) - float(raw_s)) >= max(0.085, t_e - t_s):
                        t_s, t_e = float(raw_s), float(raw_e)
                    elif mac_s is not None and mac_e is not None and (float(mac_e) - float(mac_s)) >= max(0.085, t_e - t_s):
                        pad = min(0.02, (float(mac_e) - float(mac_s) - 0.085) / 2.0)
                        t_s, t_e = float(mac_s) + max(0.0, pad), float(mac_e) - max(0.0, pad)
                    else:
                        center = 0.5 * (t_s + t_e)
                        t_s = max(0.0, center - 0.05)
                        t_e = center + 0.05
                    item["start"], item["end"] = t_s, t_e
                    syls = split_into_syllables(item.get("label", ""))
                    n_syls = max(1, len(syls))
                    splits = np.linspace(t_s, t_e, n_syls + 1).tolist()
                    item["inner_splits"] = splits[1:-1]
                    item["chars_bounds"] = [[splits[k], splits[k + 1]] for k in range(n_syls)]

                # 2. Fix no_clear_valley / fallback_equal_split / imbalanced_duration before F0 repair
                if "no_clear_valley" in diag["issues"]:
                    t_s, t_e = item.get("start"), item.get("end")
                    syls = split_into_syllables(item.get("label", ""))
                    if t_s is not None and t_e is not None and len(syls) > 1 and float(t_e) > float(t_s):
                        n_syls = len(syls)
                        prop_splits = [float(t_s) + (float(t_e) - float(t_s)) * (i / n_syls) for i in range(1, n_syls)]
                        if item.get("snd") is not None:
                            from modules.audio_core import find_minimum_intensity_valley
                            refined = []
                            win = min(0.05, (float(t_e) - float(t_s)) / (3.0 * n_syls))
                            for sp_t in prop_splits:
                                refined.append(find_minimum_intensity_valley(item["snd"], sp_t, search_window=win))
                            refined.sort()
                            # Guard against degenerate syllable durations (< 35ms)
                            all_candidate = [float(t_s)] + refined + [float(t_e)]
                            if all(all_candidate[k + 1] - all_candidate[k] >= 0.035 for k in range(len(all_candidate) - 1)):
                                item["inner_splits"] = refined
                            else:
                                item["inner_splits"] = prop_splits
                        else:
                            item["inner_splits"] = prop_splits
                        all_pts = [float(t_s)] + list(item["inner_splits"]) + [float(t_e)]
                        item["chars_bounds"] = [[all_pts[i], all_pts[i + 1]] for i in range(len(all_pts) - 1)]
                    item["split_confidence"] = max(float(item.get("split_confidence", 0.8)), 0.85)

                # Ensure chars_bounds is non-degenerate for multi-syllable items
                t_s, t_e = item.get("start"), item.get("end")
                syls = split_into_syllables(item.get("label", ""))
                n_syls = max(1, len(syls))
                if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    cb = item.get("chars_bounds") or []
                    if len(cb) != n_syls or any((float(b[1]) - float(b[0])) < 0.03 for b in cb):
                        splits = np.linspace(float(t_s), float(t_e), n_syls + 1).tolist()
                        item["inner_splits"] = splits[1:-1]
                        item["chars_bounds"] = [[splits[k], splits[k + 1]] for k in range(n_syls)]

                # 3. Unified repair for octave_jump AND/OR low_f0_coverage (never let low_f0_coverage overwrite narrowed pitch_ceiling)
                has_oj = "octave_jump" in diag["issues"]
                has_lc = "low_f0_coverage" in diag["issues"]
                if has_oj or has_lc:
                    if user_pitch_floor is not None:
                        target_pf = int(round(user_pitch_floor))
                    elif has_lc:
                        target_pf = max(40, min(rec_f0_min, int(round(med_f0 * 0.55))))
                    else:
                        target_pf = rec_f0_min

                    if has_oj:
                        target_pc = rec_f0_max
                    else:
                        cur_pc = int(item.get("pitch_ceiling", spk.last_params.get("pitch_ceiling", 600)))
                        target_pc = min(cur_pc, rec_f0_max) if cur_pc > rec_f0_max else cur_pc

                    if user_voicing_threshold is not None:
                        target_vt = float(user_voicing_threshold)
                    elif has_lc:
                        target_vt = min(float(item.get("voicing_threshold", 0.25)), 0.15)
                    else:
                        target_vt = min(float(item.get("voicing_threshold", 0.25)), 0.20)

                    item["despike"] = True
                    item["pitch_floor"] = target_pf
                    item["pitch_ceiling"] = target_pc
                    item["voicing_threshold"] = target_vt
                    item["silence_threshold"] = 0.01
                    overrides["pitch_floor"] = target_pf
                    overrides["pitch_ceiling"] = target_pc
                    overrides["f0_min"] = target_pf
                    overrides["f0_max"] = target_pc
                    overrides["voicing_threshold"] = target_vt
                    overrides["silence_threshold"] = 0.01
                    overrides["despike"] = True

                    if item.get("snd") is not None and getattr(spk, "tab_mode", "") != "单条长音频":
                        try:
                            from modules.audio_core import extract_f0
                            item["pitch_data"] = extract_f0(item["snd"], {
                                "pitch_floor": target_pf,
                                "pitch_ceiling": target_pc,
                                "voicing_threshold": target_vt,
                            })
                        except Exception:
                            pass

                    # Repair & densify item["pitch_data"] across [t_s, t_e] and each syllable in chars_bounds
                    pd = item.get("pitch_data")
                    if t_s is not None and t_e is not None and float(t_e) > float(t_s):
                        ts_f, te_f = float(t_s), float(t_e)
                        if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None:
                            p_xs = np.asarray(pd["xs"], dtype=float).copy()
                            p_freqs = np.asarray(pd["freqs"], dtype=float).copy()
                            engine_name = pd.get("engine", "praat")
                        else:
                            p_xs = np.array([], dtype=float)
                            p_freqs = np.array([], dtype=float)
                            engine_name = "praat"

                        mask = (p_xs >= ts_f) & (p_xs <= te_f) if p_xs.size > 0 else np.array([], dtype=bool)
                        seg_x = p_xs[mask] if p_xs.size > 0 else np.array([], dtype=float)
                        seg_f = p_freqs[mask] if p_freqs.size > 0 else np.array([], dtype=float)

                        # Build dense 10ms grid across [ts_f, te_f] if sparse or if any gap > 18ms exists
                        n_dense = max(15, int(np.ceil((te_f - ts_f) / 0.01)) + 1)
                        dense_x = np.linspace(ts_f, te_f, n_dense)
                        if seg_x.size < n_dense or (seg_x.size >= 2 and np.max(np.diff(seg_x)) > 0.018):
                            valid_orig = np.isfinite(seg_f) & (seg_f >= target_pf) & (seg_f <= target_pc)
                            if np.sum(valid_orig) >= 2:
                                dense_f = np.interp(dense_x, seg_x[valid_orig], seg_f[valid_orig])
                            elif np.sum(valid_orig) == 1:
                                dense_f = np.full(n_dense, float(seg_f[valid_orig][0]), dtype=float)
                            else:
                                dense_f = np.full(n_dense, float(med_f0), dtype=float)
                            # Splice dense_x / dense_f back into p_xs / p_freqs
                            before_m = p_xs < ts_f
                            after_m = p_xs > te_f
                            p_xs = np.concatenate([p_xs[before_m], dense_x, p_xs[after_m]])
                            p_freqs = np.concatenate([p_freqs[before_m], dense_f, p_freqs[after_m]])
                            mask = (p_xs >= ts_f) & (p_xs <= te_f)

                        idx_in_seg = np.where(mask)[0]
                        if idx_in_seg.size >= 2:
                            seg_x = p_xs[idx_in_seg]
                            seg_f = p_freqs[idx_in_seg].copy()
                            bounds = item.get("chars_bounds") or [[ts_f, te_f]]

                            # Filter out unvoiced and out-of-bound octave errors first
                            valid_normal = np.isfinite(seg_f) & (seg_f >= target_pf) & (seg_f <= target_pc)
                            if spk_medians.get(spk.id):
                                valid_normal &= (seg_f < med_f0 * 1.68) & (seg_f > med_f0 / 1.68)

                            # Iteratively despike all anomaly points detected by detect_pitch_anomaly_points
                            for _pass in range(6):
                                if np.sum(valid_normal) >= 2:
                                    seg_f = np.interp(seg_x, seg_x[valid_normal], seg_f[valid_normal])
                                elif np.sum(valid_normal) == 1:
                                    seg_f = np.full_like(seg_x, float(seg_f[valid_normal][0]))
                                else:
                                    seg_f = np.full_like(seg_x, float(med_f0))
                                valid_normal = np.ones(len(seg_x), dtype=bool)
                                anom = detect_pitch_anomaly_points(seg_x, seg_f, bounds=bounds, start=ts_f, end=te_f)
                                if not anom:
                                    break
                                anom_times = {round(float(at), 5) for at, _ in anom}
                                for local_i, gx in enumerate(seg_x):
                                    if round(float(gx), 5) in anom_times:
                                        valid_normal[local_i] = False
                                if not np.any(~valid_normal):
                                    break

                            # If any residual anomaly remains (e.g. step jump between syllables), smooth locally per syllable
                            if detect_pitch_anomaly_points(seg_x, seg_f, bounds=bounds, start=ts_f, end=te_f):
                                for b_s, b_e in bounds:
                                    bm = (seg_x >= float(b_s)) & (seg_x <= float(b_e))
                                    if np.sum(bm) >= 3:
                                        sub_f = seg_f[bm]
                                        sub_med = float(np.median(sub_f))
                                        # Pull points deviating strongly toward syllable median
                                        seg_f[bm] = 0.35 * sub_f + 0.65 * sub_med

                            p_freqs[idx_in_seg] = seg_f
                            item["pitch_data"] = {"xs": p_xs, "freqs": p_freqs, "engine": engine_name}

                # Clear repaired split_warnings
                if "split_warnings" in item and isinstance(item["split_warnings"], list):
                    item["split_warnings"] = [
                        w for w in item["split_warnings"]
                        if w not in ("tiny_segment", "low_f0_coverage", "no_clear_valley", "fallback_equal_split", "imbalanced_duration")
                    ]

                # Recompute preview_f0 and has_empty_data
                t_s, t_e = item.get("start"), item.get("end")
                pd = item.get("pitch_data")
                if isinstance(pd, dict) and pd.get("xs") is not None and pd.get("freqs") is not None and t_s is not None and t_e is not None and float(t_e) > float(t_s):
                    preview_times = np.linspace(float(t_s), float(t_e), 11)
                    item["preview_f0"] = _calc_preview_f0(preview_times, np.asarray(pd["xs"], dtype=float), np.asarray(pd["freqs"], dtype=float))
                    if any(f == 0.0 for f in item["preview_f0"]):
                        valid_p = [f for f in item["preview_f0"] if f > 0]
                        fill_v = float(np.mean(valid_p)) if valid_p else med_f0
                        item["preview_f0"] = [f if f > 0 else fill_v for f in item["preview_f0"]]
                    item["has_empty_data"] = False
                elif item.get("preview_f0"):
                    valid_p = [f for f in item["preview_f0"] if f and f > 0]
                    fill_v = float(np.mean(valid_p)) if valid_p else med_f0
                    item["preview_f0"] = [float(f) if (f and f > 0) else fill_v for f in item["preview_f0"]]
                    item["has_empty_data"] = False
                else:
                    item["has_empty_data"] = False

            self._after_state_change("fix_warnings")

        # Re-scan after repair (using updated speaker median F0)
        after_items = []
        after_counts = {"octave_jump": 0, "low_f0_coverage": 0, "no_clear_valley": 0, "tiny_segment": 0}
        for spk in target_speakers:
            med_f0 = self._compute_speaker_median_f0(spk) if should_apply else spk_medians.get(spk.id, 160.0)
            for iid, item in spk.items.items():
                if item.get("is_excluded", False) or item.get("missing", False):
                    continue
                diag = self._scan_item_warnings_detailed(iid, item, spk_median_f0=med_f0)
                if diag["issues"]:
                    after_items.append(diag)
                    for iss in diag["issues"]:
                        if iss in after_counts:
                            after_counts[iss] += 1

        warnings_after = len(after_items) if should_apply else warnings_before
        fixed_count = max(0, warnings_before - warnings_after)

        # Keep --script-file output compact when repairs are already applied
        emit_suggestions = item_suggestions
        if getattr(self, "_in_script_file", False) and should_apply and warnings_after == 0:
            emit_suggestions = []

        self._emit(
            True,
            (
                f"异常词条自动修复完成：修复前警告词条 {warnings_before} 项 -> 修复后 {warnings_after} 项（已修复 {fixed_count} 项）。"
                if should_apply
                else f"扫描完成：共发现 {warnings_before} 个异常/警告词条。传入 --apply 可一键自动修复。"
            ),
            applied=should_apply,
            warnings_before=warnings_before,
            warnings_after=warnings_after,
            fixed_count=fixed_count,
            before_summary=before_counts,
            after_summary=after_counts if should_apply else before_counts,
            global_suggestions=global_suggestions,
            item_suggestions=emit_suggestions,
        )

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
                    s, e = trim_bounds_by_amplitude(s, e, xs, vals)

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

    def _ensure_item_loaded(self, item, speaker=None):
        if not item:
            return
        spk = speaker
        if spk is None:
            for cand_spk in (self.speaker_manager.get_all_speakers() if hasattr(self.speaker_manager, "get_all_speakers") else []):
                if item in cand_spk.items.values():
                    spk = cand_spk
                    break
            if spk is None:
                spk = self.current_speaker

        has_snd = item.get('snd') is not None
        has_pitch = (item.get('pitch') is not None) or (item.get('pitch_data') is not None)

        if not has_snd and item.get('path') and os.path.exists(str(item['path'])):
            try:
                from modules.audio_core import load_compatible_sound
                item['snd'] = load_compatible_sound(item['path'])
                has_snd = item['snd'] is not None
            except Exception:
                pass

        if not has_snd and spk is not None:
            if getattr(spk, 'pending_long_snd', None) is not None:
                item['snd'] = spk.pending_long_snd
                has_snd = True
            else:
                long_p = getattr(spk, 'cli_long_snd_path', None) or getattr(spk, 'long_audio_path', None)
                if long_p and os.path.exists(str(long_p)):
                    try:
                        from modules.audio_core import load_compatible_sound
                        spk.pending_long_snd = load_compatible_sound(str(long_p))
                        item['snd'] = spk.pending_long_snd
                        has_snd = item['snd'] is not None
                    except Exception:
                        pass

        if not item.get('formant_data') and isinstance(item.get('preview_formants'), dict):
            pf_dict = item['preview_formants']
            if pf_dict.get('f1') is not None and pf_dict.get('f2') is not None:
                f1_a = np.asarray(pf_dict['f1'], dtype=float)
                f2_a = np.asarray(pf_dict['f2'], dtype=float)
                if 'xs' in pf_dict and pf_dict['xs'] is not None:
                    xs_a = np.asarray(pf_dict['xs'], dtype=float)
                elif item.get('start') is not None and item.get('end') is not None:
                    xs_a = np.linspace(float(item['start']), float(item['end']), len(f1_a))
                else:
                    xs_a = np.linspace(0.0, 1.0, len(f1_a))
                f_dict = {'xs': xs_a, 'f1': f1_a, 'f2': f2_a}
                if pf_dict.get('f3') is not None:
                    f_dict['f3'] = np.asarray(pf_dict['f3'], dtype=float)
                item['formant_data'] = f_dict

        if not has_snd:
            return

        eff_params = dict(self.params)
        if spk is not None and getattr(spk, 'last_params', None):
            for pk, pv in spk.last_params.items():
                if pk not in eff_params:
                    eff_params[pk] = pv

        if not has_pitch:
            try:
                from modules.audio_core import extract_f0
                pf = item.get('pitch_floor', eff_params.get('pitch_floor', 75))
                pc = item.get('pitch_ceiling', eff_params.get('pitch_ceiling', 600))
                vt = item.get('voicing_threshold', eff_params.get('voicing_threshold', 0.25))
                item['pitch_data'] = extract_f0(item['snd'], {'pitch_floor': pf, 'pitch_ceiling': pc, 'voicing_threshold': vt})
            except Exception:
                pass

        want_formant = (
            self.params.get('analysis_mode') == 'formant'
            or eff_params.get('analysis_mode') == 'formant'
            or item.get('analysis_mode') == 'formant'
        )
        if want_formant and not item.get('formant_data'):
            try:
                from modules.audio_core import extract_formants
                total_dur = item['snd'].get_total_duration()
                s_ref = item.get('macro_start') if item.get('macro_start') is not None else item.get('start')
                e_ref = item.get('macro_end') if item.get('macro_end') is not None else item.get('end')
                if s_ref is not None and e_ref is not None and total_dur > 5.0 and float(e_ref) > float(s_ref):
                    padding = 0.5
                    seg_start = max(0.0, float(s_ref) - padding)
                    seg_end = min(total_dur, float(e_ref) + padding)
                    part_snd = item['snd'].extract_part(from_time=seg_start, to_time=seg_end)
                    part_formant_data = extract_formants(part_snd, eff_params)
                    part_formant_data['xs'] = part_formant_data['xs'] + seg_start
                    item['formant_data'] = part_formant_data
                else:
                    item['formant_data'] = extract_formants(item['snd'], eff_params)
            except Exception:
                pass

    def _extract_syl_data(self, item, num_points):
        if item.get('start') is None or (not item.get('snd') and not item.get('pitch_data') and not item.get('pitch')): return 0, []

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

        from modules.data_utils import split_into_syllables
        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        syls = split_into_syllables(label)
        chars_bounds = item.get('chars_bounds', [])
        if chars_bounds and len(chars_bounds) == len(syls):
            bounds = chars_bounds
        else:
            splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
            if len(syls) > 1 and len(splits) != len(syls) + 1:
                splits = np.linspace(t_s, t_e, len(syls) + 1).tolist()
            elif len(syls) <= 1:
                splits = [t_s, t_e]
            bounds = [[splits[i], splits[i+1]] for i in range(len(splits)-1)]

        syl_data = []
        for c_s, c_e in bounds:
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
        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_analysis_sheet_with_formulas, build_five_point_chart,
            write_raw_pitch_sheet
        )
        is_continuous = (rule == "continuous")
        num_points = speaker.last_params['pts']

        max_syls = 1
        for grp_name, children in structure:
            for child in children:
                lbl = speaker.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)
        ws_data = workbook.add_worksheet("数据")
        ws_res = workbook.add_worksheet("分析结果")
        raw_pitch_rows = []

        headers = ["组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1):
                headers.append(f"字{k}_P{i}(Hz)")
        for col, header in enumerate(headers):
            ws_data.write(0, col, header, style.header_fmt)

        global_idx = 1
        row_idx = 1
        dict_data = {}
        data_sample_rows = []

        for grp_name, children in structure:
            if not is_continuous: global_idx = 1
            for child in children:
                item = speaker.items[child]
                self._ensure_item_loaded(item)
                total_dur, syl_data = self._extract_syl_data(item, num_points)
                if total_dur <= 0: continue

                raw_pitch_rows.append({
                    'group': grp_name,
                    'index': global_idx,
                    'item': item
                })

                if grp_name not in dict_data:
                    dict_data[grp_name] = {
                        'syl_dur_sums': [0.0]*max_syls, 'syl_counts': [0]*max_syls,
                        'f0_sums': [[0.0]*num_points for _ in range(max_syls)],
                        'f0_counts': [[0]*num_points for _ in range(max_syls)]
                    }

                col = 0
                ws_data.write(row_idx, col, grp_name, style.text_center); col += 1
                ws_data.write(row_idx, col, global_idx, style.int_fmt); col += 1
                ws_data.write(row_idx, col, item['label'], style.text_left); col += 1
                ws_data.write(row_idx, col, round(total_dur, 6), style.duration_fmt); col += 1

                sample_r = [grp_name, global_idx, item['label'], round(total_dur, 6)]

                for k in range(max_syls):
                    if k < len(syl_data):
                        dur, f0s = syl_data[k]
                        ws_data.write(row_idx, col, round(dur, 6), style.duration_fmt); col += 1
                        sample_r.append(round(dur, 6))
                        dict_data[grp_name]['syl_dur_sums'][k] += dur
                        dict_data[grp_name]['syl_counts'][k] += 1
                        for i, f0 in enumerate(f0s):
                            if not np.isnan(f0) and f0 > 0:
                                ws_data.write(row_idx, col, round(f0, 6), style.hz_fmt); col += 1
                                sample_r.append(round(f0, 6))
                                dict_data[grp_name]['f0_sums'][k][i] += f0
                                dict_data[grp_name]['f0_counts'][k][i] += 1
                            else:
                                ws_data.write(row_idx, col, "", style.text_center); col += 1
                                sample_r.append("")
                    else:
                        ws_data.write(row_idx, col, "", style.text_center); col += 1
                        sample_r.append("")
                        for _ in range(num_points):
                            ws_data.write(row_idx, col, "", style.text_center); col += 1
                            sample_r.append("")

                if len(data_sample_rows) < 100:
                    data_sample_rows.append(sample_r)

                row_idx += 1
                global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        autofit_worksheet_columns(ws_data, headers, data_sample_rows)

        write_raw_pitch_sheet(workbook, raw_pitch_rows, style_manager=style, include_speaker=False)

        all_avg_hz = []
        avg_points_map = {}

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
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to generate five point chart in xlsx export: %s", e)

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
            self._emit(False, error="No valid data to plot")
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
        import math
        import numpy as np
        is_continuous = (rule == "continuous")
        num_points = self.current_speaker.last_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1

        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
            rows = []
            f0_values = []
            for grp_name, children in s_struct:
                for child in children:
                    item = s.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('pitch') and not item.get('pitch_data'):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue
                    rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                    for _, freqs in syl_data:
                        for f in freqs:
                            if f > 0: f0_values.append(f)
            if f0_values:
                speaker_stats[spk_id] = (float(np.min(f0_values)), float(np.max(f0_values)))
            else:
                speaker_stats[spk_id] = (0.0, 0.0)
            speaker_rows[spk_id] = rows

        with open(out_file, "w", encoding="utf-8-sig") as f_out:
            headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
            for k in range(1, max_syls + 1):
                headers.append(f"字{k}_时长(s)")
                for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
            f_out.write("\t".join(headers) + "\n")

            for s in speakers:
                spk_id = getattr(s, 'id', s.name)
                rows = speaker_rows.get(spk_id, [])
                s_min, s_max = speaker_stats.get(spk_id, (0.0, 0.0))
                has_valid_range = (s_min > 0 and s_max > s_min)
                log_min = math.log10(s_min) if s_min > 0 else 0.0
                log_max = math.log10(s_max) if s_max > 0 else 0.0
                log_diff = log_max - log_min if log_max > log_min else 1.0

                current_group = None
                group_item_idx = 0
                global_idx = 0
                for r in rows:
                    if r['group'] != current_group:
                        current_group = r['group']
                        group_item_idx = 1
                    else:
                        group_item_idx += 1
                    global_idx += 1
                    display_idx = global_idx if is_continuous else group_item_idx

                    line_parts = [s.name, r['group'], str(display_idx), r['label'], f"{r['total_dur']:.4f}"]
                    for s_dur, freqs in r['syl_data']:
                        line_parts.append(f"{s_dur:.4f}")
                        for f in freqs:
                            if f > 0 and has_valid_range:
                                t_v = ((math.log10(f) - log_min) / log_diff) * 5.0
                                line_parts.append(f"{t_v:.2f}")
                            else:
                                line_parts.append("")
                    fill_count = max_syls - len(r['syl_data'])
                    for _ in range(fill_count):
                        line_parts.append("")
                        for _ in range(num_points): line_parts.append("")
                    f_out.write("\t".join(line_parts) + "\n")

    def _export_xlsx_integrated(self, out_file, speakers, rule):
        import xlsxwriter
        import numpy as np
        import math
        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_raw_pitch_sheet
        )
        is_continuous = (rule == "continuous")
        num_points = self.current_speaker.last_params['pts']
        speaker_stats = {}
        speaker_rows = {}
        max_syls = 1

        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
            rows = []
            f0_values = []
            for grp_name, children in s_struct:
                for child in children:
                    item = s.items[child]
                    lbl = item.get('label', '')
                    if len(lbl) > max_syls: max_syls = len(lbl)
                    self._ensure_item_loaded(item)
                    if not item.get('pitch') and not item.get('pitch_data'):
                        continue
                    total_dur, syl_data = self._extract_syl_data(item, num_points)
                    if total_dur <= 0: continue
                    rows.append({'group': grp_name, 'label': lbl, 'total_dur': total_dur, 'syl_data': syl_data, 'raw_item': item})
                    for _, freqs in syl_data:
                        for f in freqs:
                            if f > 0: f0_values.append(f)
            if f0_values:
                speaker_stats[spk_id] = (float(np.min(f0_values)), float(np.max(f0_values)))
            else:
                speaker_stats[spk_id] = (0.0, 0.0)
            speaker_rows[spk_id] = rows

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("整合数据(T值)")
        ws_res = workbook.add_worksheet("分析结果")
        headers = ["发音人", "组别", "编号", "词语", "总时长(s)"]
        for k in range(1, max_syls + 1):
            headers.append(f"字{k}_时长(s)")
            for i in range(1, num_points + 1): headers.append(f"字{k}_T{i}")
        for col, header in enumerate(headers):
            ws_data.write(0, col, header, style.header_fmt)

        group_stats = {}
        raw_pitch_rows = []
        int_data_sample_rows = []
        row_idx = 1
        for s in speakers:
            spk_id = getattr(s, 'id', s.name)
            rows = speaker_rows.get(spk_id, [])
            s_min, s_max = speaker_stats.get(spk_id, (0.0, 0.0))
            has_valid_range = (s_min > 0 and s_max > s_min)
            log_min = math.log10(s_min) if s_min > 0 else 0.0
            log_max = math.log10(s_max) if s_max > 0 else 0.0
            log_diff = log_max - log_min if log_max > log_min else 1.0

            current_group = None
            group_item_idx = 0
            global_idx = 0
            for r in rows:
                if r['group'] != current_group:
                    current_group = r['group']
                    group_item_idx = 1
                else:
                    group_item_idx += 1
                global_idx += 1
                display_idx = global_idx if is_continuous else group_item_idx

                raw_pitch_rows.append({
                    'speaker': s.name,
                    'group': r['group'],
                    'index': display_idx,
                    'item': r['raw_item']
                })
                ws_data.write(row_idx, 0, s.name, style.text_center)
                ws_data.write(row_idx, 1, r['group'], style.text_center)
                ws_data.write(row_idx, 2, display_idx, style.int_fmt)
                ws_data.write(row_idx, 3, r['label'], style.text_left)
                ws_data.write(row_idx, 4, round(r['total_dur'], 4), style.duration_fmt)
                col_idx = 5

                sample_r = [s.name, r['group'], display_idx, r['label'], round(r['total_dur'], 4)]

                grp_name = r['group']
                if grp_name not in group_stats:
                    group_stats[grp_name] = {
                        'syl_dur_sums': [0.0] * max_syls,
                        'syl_counts': [0] * max_syls,
                        't_sums': [[0.0] * num_points for _ in range(max_syls)],
                        't_counts': [[0] * num_points for _ in range(max_syls)]
                    }
                stats = group_stats[grp_name]

                for k, (s_dur, freqs) in enumerate(r['syl_data']):
                    ws_data.write(row_idx, col_idx, round(s_dur, 4), style.duration_fmt)
                    sample_r.append(round(s_dur, 4))
                    col_idx += 1
                    if k < max_syls:
                        stats['syl_dur_sums'][k] += s_dur
                        stats['syl_counts'][k] += 1
                    for i, f in enumerate(freqs):
                        if f > 0 and has_valid_range:
                            t_val = round(((math.log10(f) - log_min) / log_diff) * 5.0, 2)
                            ws_data.write(row_idx, col_idx, t_val, style.t_val_fmt)
                            sample_r.append(t_val)
                            if k < max_syls:
                                stats['t_sums'][k][i] += t_val
                                stats['t_counts'][k][i] += 1
                        else:
                            ws_data.write(row_idx, col_idx, "", style.text_center)
                            sample_r.append("")
                        col_idx += 1

                fill_count = max_syls - len(r['syl_data'])
                for _ in range(fill_count):
                    ws_data.write(row_idx, col_idx, "", style.text_center)
                    sample_r.append("")
                    col_idx += 1
                    for _ in range(num_points):
                        ws_data.write(row_idx, col_idx, "", style.text_center)
                        sample_r.append("")
                        col_idx += 1
                if len(int_data_sample_rows) < 100:
                    int_data_sample_rows.append(sample_r)
                row_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(row_idx - 1, 1), len(headers) - 1)
        autofit_worksheet_columns(ws_data, headers, int_data_sample_rows)

        res_headers = ["组别"]
        for k in range(1, max_syls + 1):
            res_headers.append(f"字{k}_平均时长(s)")
            for i in range(1, num_points + 1): res_headers.append(f"字{k}_T{i}")
        for col, header in enumerate(res_headers):
            ws_res.write(0, col, header, style.header_fmt)

        res_row = 1
        int_res_sample_rows = []
        for grp, st in group_stats.items():
            ws_res.write(res_row, 0, grp, style.text_center)
            col = 1
            sample_res = [grp]
            for k in range(max_syls):
                cnt = st['syl_counts'][k]
                if cnt > 0:
                    avg_dur = st['syl_dur_sums'][k] / cnt
                    ws_res.write(res_row, col, round(avg_dur, 4), style.duration_fmt)
                    sample_res.append(round(avg_dur, 4))
                else:
                    ws_res.write(res_row, col, "", style.text_center)
                    sample_res.append("")
                col += 1
                for i in range(num_points):
                    t_cnt = st['t_counts'][k][i]
                    if t_cnt > 0:
                        ws_res.write(res_row, col, round(st['t_sums'][k][i] / t_cnt, 2), style.t_val_fmt)
                        sample_res.append(round(st['t_sums'][k][i] / t_cnt, 2))
                    else:
                        ws_res.write(res_row, col, "", style.text_center)
                        sample_res.append("")
                    col += 1
            if len(int_res_sample_rows) < 100:
                int_res_sample_rows.append(sample_res)
            res_row += 1

        ws_res.freeze_panes(1, 0)
        ws_res.autofilter(0, 0, max(res_row - 1, 1), len(res_headers) - 1)
        autofit_worksheet_columns(ws_res, res_headers, int_res_sample_rows)

        write_raw_pitch_sheet(workbook, raw_pitch_rows, style_manager=style, include_speaker=True)

        if group_stats:
            try:
                ws_chart_data = workbook.add_worksheet("图表数据")
                ws_chart_data.hide()
                ws_chart_data.write(0, 0, "组别")
                for p in range(1, max_syls * num_points + 1):
                    ws_chart_data.write(0, p, p)
                chart_row = 1
                for grp, st in group_stats.items():
                    ws_chart_data.write(chart_row, 0, grp)
                    col_idx = 1
                    for k in range(max_syls):
                        for i in range(num_points):
                            t_cnt = st['t_counts'][k][i]
                            if t_cnt > 0:
                                ws_chart_data.write(chart_row, col_idx, round(st['t_sums'][k][i] / t_cnt, 2))
                            else:
                                ws_chart_data.write(chart_row, col_idx, "")
                            col_idx += 1
                    chart_row += 1
                chart = workbook.add_chart({'type': 'scatter', 'subtype': 'straight_with_markers'})
                for r in range(1, len(group_stats) + 1):
                    chart.add_series({
                        'name':       ['图表数据', r, 0],
                        'categories': ['图表数据', 0, 1, 0, max_syls * num_points],
                        'values':     ['图表数据', r, 1, r, max_syls * num_points],
                        'line':       {'width': 2.5},
                        'marker':     {'type': 'circle', 'size': 6},
                    })
                chart.set_title({
                    'name': '多发音人整合声调格局图',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 14, 'bold': True}
                })
                chart.set_x_axis({
                    'name': '测量点 (时序展开)',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 9}
                })
                chart.set_y_axis({
                    'name': '赵元任五度标调法',
                    'name_font': {'name': 'Microsoft YaHei', 'size': 10},
                    'num_font': {'name': 'Arial', 'size': 1, 'color': 'white'},
                    'min': 0, 'max': 5,
                    'major_unit': 1,
                    'major_gridlines': {'visible': True, 'line': {'color': '#D0D0D0', 'width': 0.5}},
                    'major_tick_mark': 'none',
                })
                chart.set_legend({
                    'position': 'right',
                    'font': {'name': 'Microsoft YaHei', 'size': 9}
                })
                chart.set_size({'width': 650, 'height': 450})
                ws_res.insert_chart(f'A{res_row + 3}', chart)
            except Exception as e:
                logging.getLogger(__name__).warning("Failed to insert integrated xlsx chart: %s", e)

        workbook.close()

    def _export_line_chart_integrated(self, out_file, speakers):
        import matplotlib.pyplot as plt
        import math

        num_points = self.current_speaker.last_params['pts']
        max_syls = 1
        combined_points_map = {}
        all_groups = []

        for s in speakers:
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
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
            s_struct = [(grp, [iid for iid, item in s.items.items() if item.get('group') == grp and not item.get('is_excluded', False)]) for grp in getattr(s, 'cli_groups', [])]
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
            self._emit(False, error="No valid data to plot")
            return

        group_norm_points = self._normalize_kde_points(group_syl_contours, min_f0, max_f0, N_DENSE)
        self._draw_kde_heatmap_integrated(out_file, speakers, group_norm_points, max_syls)

    def _sync_cli_state_after_project_load(self):
        preserved = getattr(self.project_manager, '_preserved_top_level', {}) or {}
        top_wl = preserved.get('wordlist_doc')
        if not isinstance(top_wl, dict) and isinstance(preserved.get('groups'), list) and preserved.get('groups'):
            top_wl = normalize_wordlist_document({
                'title': preserved.get('wordlist_title') or '未命名字表',
                'note': preserved.get('wordlist_note') or '',
                'groups': preserved.get('groups'),
            })

        for speaker in self.speaker_manager.get_all_speakers():
            if getattr(speaker, 'long_audio_path', None):
                speaker.cli_long_snd_path = speaker.long_audio_path
            seen = []
            for item in speaker.items.values():
                if isinstance(item, dict):
                    item.setdefault('speaker', speaker.name)
                    item.setdefault('speaker_id', speaker.id)
                group = item.get('group', '导入内容')
                if group not in seen:
                    seen.append(group)
            speaker.cli_groups = seen
            if getattr(speaker, 'wordlist_doc', None) is None and isinstance(top_wl, dict):
                speaker.wordlist_doc = copy.deepcopy(top_wl)

        if self.current_speaker and getattr(self.current_speaker, 'wordlist_doc', None) is not None:
            self._wordlist_doc = self.current_speaker.wordlist_doc

    def _process_speaker_batch_items(self, spk, sorted_audios, matched_audio_to_tg, params):
        from modules.audio_core import batch_process_worker_with_textgrid, batch_process_worker, extract_f0

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
                res['speaker'] = spk.name
                res['speaker_id'] = spk.id
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
                res['id'] = iid
                spk.items[iid] = res
            else:
                iid = f"missing_{res['label']}_{id(res)}"
                spk.items[iid] = {
                    'id': iid,
                    'label': res['label'],
                    'group': grp_name,
                    'speaker': spk.name,
                    'speaker_id': spk.id,
                    'snd': None,
                    'start': None,
                    'end': None,
                    'inner_splits': []
                }

    def _match_audio_to_textgrid(self, sorted_audios, sorted_tgs):
        matched_audio_to_tg = {}
        matched_tg_to_audio = {}

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
        Import a folder containing single-speaker or multi-speaker audio files (+ optional TextGrid or wordlist) and export as a .teproj project.
        Usage: import_batch_and_export <folder_path> <output.teproj> [wordlist=<path>] [match_mode=fuzzy|order] [--replace-default-speaker]
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        pos_args = []
        wordlist_path = None
        match_mode = "fuzzy"
        replace_default_speaker = True

        idx = 0
        while idx < len(args):
            tok = args[idx]
            low = tok.lower().strip()
            if low == "--replace-default-speaker":
                replace_default_speaker = True
                idx += 1
                continue
            if low in ("--no-replace-default-speaker", "--keep-default-speaker"):
                replace_default_speaker = False
                idx += 1
                continue
            if low in ("--wordlist", "-w") and idx + 1 < len(args):
                wordlist_path = args[idx + 1].strip()
                idx += 2
                continue
            if low in ("--match-mode", "--mode") and idx + 1 < len(args):
                match_mode = args[idx + 1].strip().lower()
                idx += 2
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                k_low = k.strip().lstrip("-").lower()
                v_clean = v.strip().strip('"').strip("'")
                if k_low in ("wordlist", "wl", "字表"):
                    wordlist_path = v_clean
                elif k_low in ("match_mode", "match-mode", "mode", "match"):
                    match_mode = v_clean.lower()
                elif k_low in ("replace_default_speaker", "replace-default-speaker"):
                    replace_default_speaker = v_clean.lower() in ("true", "1", "yes", "on")
                else:
                    pos_args.append(tok)
            else:
                pos_args.append(tok)
            idx += 1

        if len(pos_args) < 2:
            self._emit(
                False,
                error="Usage: import_batch_and_export <folder_path> <output.teproj> [wordlist=<path>] [match_mode=fuzzy|order] [--replace-default-speaker]",
            )
            return

        dir_path, teproj_path = pos_args[0], pos_args[1]
        if len(pos_args) >= 3:
            if pos_args[2].lower() in ("fuzzy", "order"):
                match_mode = pos_args[2].lower()
            elif wordlist_path is None:
                wordlist_path = pos_args[2]
        if len(pos_args) >= 4 and pos_args[3].lower() in ("fuzzy", "order"):
            match_mode = pos_args[3].lower()

        if match_mode not in ("fuzzy", "order"):
            self._emit(False, error=f"Invalid match_mode '{match_mode}'. Choose from: fuzzy, order")
            return

        if not os.path.isdir(dir_path):
            self._emit(False, error=f"Not a directory: {dir_path}")
            return

        wl_doc = None
        wl_groups = None
        wl_flat_words = None
        wl_records = None
        if wordlist_path:
            if not os.path.exists(wordlist_path):
                self._emit(False, error=f"Wordlist file not found: {wordlist_path}")
                return
            try:
                wl_doc, wl_groups, wl_flat_words, wl_records = self._load_wordlist_any(wordlist_path)
            except Exception as e:
                self._emit(False, error=f"Failed to load wordlist: {e}")
                return
            if not wl_flat_words:
                self._emit(False, error="No words found in wordlist")
                return

        candidate_subdirs = [
            os.path.join(dir_path, d)
            for d in os.listdir(dir_path)
            if os.path.isdir(os.path.join(dir_path, d)) and not d.startswith(".")
        ]
        subdirs = []
        for sd in sorted(candidate_subdirs, key=self._natural_sort_key):
            has_audio = any(
                os.path.isfile(os.path.join(sd, f))
                and f.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
                for f in os.listdir(sd)
            )
            if has_audio:
                subdirs.append(sd)
        if not subdirs:
            subdirs = [dir_path]

        if wl_doc is not None:
            self.wordlist_doc = wl_doc

        imported_speakers = []
        for subdir in sorted(subdirs, key=self._natural_sort_key):
            spk_name = os.path.basename(os.path.abspath(subdir))
            if spk_name in (".", "..") or not spk_name.strip():
                continue

            spk = None
            for s in self.speaker_manager.get_all_speakers():
                if s.name == spk_name:
                    spk = s
                    break
            if not spk:
                # Reuse initial empty default speaker if replace_default_speaker is enabled
                if replace_default_speaker:
                    for s in self.speaker_manager.get_all_speakers():
                        if self._is_empty_default_speaker(s):
                            self.speaker_manager.rename_speaker(s.id, spk_name)
                            spk = s
                            break
                if not spk:
                    spk = self.speaker_manager.add_speaker(spk_name)

            self.speaker_manager.set_active_speaker(spk.id)
            spk = self.current_speaker
            spk.tab_mode = "多条独立音频"
            spk.items.clear()

            audios = [
                os.path.join(subdir, f)
                for f in os.listdir(subdir)
                if os.path.isfile(os.path.join(subdir, f))
                and f.lower().endswith(self.COMMON_AUDIO_EXTENSIONS)
            ]
            textgrids = [
                os.path.join(subdir, f)
                for f in os.listdir(subdir)
                if os.path.isfile(os.path.join(subdir, f))
                and f.lower().endswith('.textgrid')
            ]

            sorted_audios = sorted(audios, key=self._natural_sort_key)
            sorted_tgs = sorted(textgrids, key=self._natural_sort_key)
            spk.pending_batch_paths = sorted_audios

            if wl_doc is not None and not sorted_tgs:
                spk.wordlist_doc = copy.deepcopy(wl_doc)
                spk.cli_groups = [g["group"] for g in wl_groups]
                spk.project_groups = list(spk.cli_groups)
                self._process_batch_wordlist(
                    wl_groups,
                    wl_flat_words,
                    match_mode,
                    metadata_records=wl_records,
                    wordlist_doc=wl_doc,
                    emit_result=False,
                )
            else:
                matched_audio_to_tg = self._match_audio_to_textgrid(sorted_audios, sorted_tgs)
                self._process_speaker_batch_items(spk, sorted_audios, matched_audio_to_tg, self.params)
                if wl_doc is not None and wl_records:
                    spk.wordlist_doc = copy.deepcopy(wl_doc)
                    spk.cli_groups = [g["group"] for g in wl_groups]
                    spk.project_groups = list(spk.cli_groups)
                    item_list = list(spk.items.values())
                    used_rec_indices = set()
                    for idx_it, item in enumerate(item_list):
                        rec = None
                        if match_mode == "order":
                            if idx_it < len(wl_records):
                                rec = wl_records[idx_it]
                        else:
                            lbl = str(item.get("label") or "")
                            for r_idx, cand in enumerate(wl_records):
                                if r_idx in used_rec_indices:
                                    continue
                                cand_lbl = str(cand.get("label") or "")
                                cand_aliases = cand.get("item_aliases") or []
                                if cand_lbl == lbl or cand_lbl in lbl or lbl in cand_lbl or any(a in lbl for a in cand_aliases):
                                    rec = cand
                                    used_rec_indices.add(r_idx)
                                    break
                        self._attach_wordlist_record_to_item(item, rec, speaker=spk)
                else:
                    seen_g = []
                    for item in spk.items.values():
                        g = item.get('group', '导入内容')
                        if g not in seen_g:
                            seen_g.append(g)
                    spk.cli_groups = seen_g
                    spk.project_groups = list(seen_g)

            imported_speakers.append(spk_name)

        if replace_default_speaker:
            for s_id in list(self.speaker_manager.speakers.keys()):
                s = self.speaker_manager.speakers.get(s_id)
                if self._is_empty_default_speaker(s) and len(self.speaker_manager.speakers) > 1:
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
            self._emit(
                True,
                f"Imported speakers {imported_speakers} and exported project successfully.",
                path=teproj_path,
                speakers=imported_speakers,
                wordlist=wordlist_path,
                match_mode=match_mode if wordlist_path else None,
            )
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
            self._session_dirty = False
            self._emit(True, "工程已保存到内部工作区。若要给别人或下次恢复，请用 project_export。", workspace=self.project_manager.workspace_dir)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_project_load(self, arg):
        """
        Load project state from the current workspace directory (or a specified workspace directory / .teproj archive).
        Usage: project_load [workspace_dir_or_teproj]
        """
        args = shlex.split(arg) if arg.strip() else []
        target = args[0] if args else None
        try:
            if target and os.path.isfile(target):
                ok = self.project_manager.load_project(target)
            elif target and os.path.isdir(target):
                self.project_manager.workspace_dir = os.path.abspath(target)
                ok = self.project_manager.load_from_workspace()
            else:
                ok = self.project_manager.load_from_workspace()
            if ok:
                self._sync_cli_state_after_project_load()
                self._emit(
                    True,
                    "已从工作区恢复工程状态。",
                    workspace=self.project_manager.workspace_dir,
                    speakers=len(self.speaker_manager.get_all_speakers()),
                    active_speaker=self.current_speaker.name,
                    total_items=len(self.items),
                )
            else:
                self._emit(False, error="Failed to load project from workspace")
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
        Merge short audio files into a single WAV, like toolkit's merge tab.
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
        Fuzzy-sort batch audio paths according to a wordlist, like toolkit's auto-sort.
        Usage: tool_sort_batch <wordlist> [audio_or_dir_or_glob ...] [--apply]
        Supports .ptwl, .csv, structured .txt, and messy .txt wordlists.
        If --apply (or apply=true) is specified, directly updates the session's batch_paths.
        """
        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid arguments: {e}")
            return

        if not args:
            self._emit(False, error="Requires wordlist path")
            return

        apply_to_session = False
        pos_args = []
        for tok in args:
            low = tok.lower().strip()
            if low in ("--apply", "-a"):
                apply_to_session = True
            elif low == "--no-apply":
                apply_to_session = False
            elif low.startswith("apply="):
                val = low.split("=", 1)[1].strip()
                apply_to_session = val in ("true", "1", "yes", "on")
            else:
                pos_args.append(tok)

        if not pos_args:
            self._emit(False, error="Requires wordlist path")
            return

        wordlist_path = pos_args[0]
        raw_audio_args = pos_args[1:]
        if not os.path.exists(wordlist_path):
            self._emit(False, error=f"File not found: {wordlist_path}")
            return

        if raw_audio_args:
            paths, err_msg, hint_msg = self._expand_batch_audio_inputs(raw_audio_args)
            if err_msg:
                self._emit(False, error=err_msg, hint=hint_msg)
                return
        else:
            paths = list(self.batch_paths)

        if not paths:
            self._emit(
                False,
                error="No audio paths provided or loaded",
                hint="请传入音频文件路径、目录路径或 *.wav 通配符，或先运行 load_batch。",
            )
            return

        try:
            _doc, _groups, flat_words, records = self._load_wordlist_any(wordlist_path)
        except Exception as e:
            self._emit(False, error=f"Failed to load wordlist: {e}")
            return

        sorted_paths = []
        used_indices = []
        for idx_w, word in enumerate(flat_words):
            idx = fuzzy_match_word_to_path(word, paths, used_indices=list(used_indices))
            if idx is None and idx_w < len(records) and records[idx_w]:
                for alias in (records[idx_w].get("item_aliases") or records[idx_w].get("aliases") or []):
                    idx = fuzzy_match_word_to_path(alias, paths, used_indices=list(used_indices))
                    if idx is not None:
                        break
            if idx is not None:
                sorted_paths.append(paths[idx])
                used_indices.append(idx)

        leftovers = [p for i, p in enumerate(paths) if i not in used_indices]
        result = sorted_paths + leftovers

        if apply_to_session:
            self.batch_paths = list(result)
            self.mode = 'batch'
            self.long_snd = None
            self.long_snd_path = None
            self.items.clear()
            self.groups.clear()
            self._after_state_change("load_batch")
            self._emit(
                True,
                f"已按字表给音频排序并直接载入当前会话（共 {len(result)} 个音频，匹配 {len(sorted_paths)}/{len(flat_words)} 词）。",
                applied=True,
                mode=self.mode,
                count=len(result),
                matched=len(sorted_paths),
                total_words=len(flat_words),
                sorted_paths=result,
                batch_paths=result,
            )
        else:
            self._emit(
                True,
                "已按字表给音频排序。需要正式载入时可把 sorted_paths 交给 load_batch，或直接加 --apply 参数。",
                applied=False,
                matched=len(sorted_paths),
                total_words=len(flat_words),
                sorted_paths=result,
            )

    def do_tool_split(self, arg):
        """
        Split a long audio into short WAV files by a wordlist, like toolkit's split tab.
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
                    start, end = trim_bounds_by_amplitude(start, end, xs, vals)

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

    def do_list_scripts(self, arg):
        """
        List all available custom and builtin scripts.
        Usage: list_scripts
        """
        try:
            from modules.script_manager import load_all_scripts
            scripts = load_all_scripts()
            res_list = []
            for s in scripts:
                res_list.append({
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "type": s.get("type", "chart")
                })
            self._emit(True, "Scripts loaded successfully.", scripts=res_list)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_script_info(self, arg):
        """
        Get details and code of a specific script by ID or Name.
        Usage: script_info <script_id_or_name>
        """
        arg_norm = arg.replace('\\', '/')
        args = shlex.split(arg_norm)
        if not args:
            self._emit(False, error="Requires script ID or name")
            return
            
        target = args[0].strip()
        try:
            from modules.script_manager import load_all_scripts
            scripts = load_all_scripts()
            
            # Find by ID first, then by Name (case-insensitive)
            found = None
            for s in scripts:
                if s.get("id") == target:
                    found = s
                    break
            if not found:
                for s in scripts:
                    if s.get("name", "").lower() == target.lower():
                        found = s
                        break
            
            if found:
                self._emit(True, "Script details retrieved.", script={
                    "id": found.get("id"),
                    "name": found.get("name"),
                    "description": found.get("description"),
                    "type": found.get("type", "chart"),
                    "code": found.get("code")
                })
            else:
                self._emit(False, error=f"Script not found: {target}")
        except Exception as e:
            self._emit(False, error=str(e))

    def do_run_script(self, arg):
        """
        Run a script by ID, Name, or local python file path.
        Usage: run_script <script_id_or_name_or_file_path> [key=value ...]
        Parameters:
          timeout=30       Execution timeout in seconds (default 30)
          archive=true     Archive run history in active project (default true)
          desc="Goal"      User goal description for archiving (default "CLI script execution")
          name="Name"      Custom script name for archiving (default "CLI script")
        """
        import re
        import uuid
        import hashlib
        import datetime
        
        arg_norm = arg.replace('\\', '/')
        args = shlex.split(arg_norm)
        if not args:
            self._emit(False, error="Requires script ID, name, or file path")
            return
            
        target = args[0].strip()
        
        # Parse extra key-value parameters
        kwargs = {}
        for item in args[1:]:
            if '=' in item:
                k, v = item.split('=', 1)
                kwargs[k.strip().lower()] = v.strip()
                
        # Parse standard parameter defaults
        try:
            timeout = float(kwargs.get("timeout", 30))
        except ValueError:
            self._emit(False, error="timeout must be a number")
            return
            
        archive = kwargs.get("archive", "true").lower() in ("true", "1", "yes", "y")
        script_desc = kwargs.get("desc", kwargs.get("user_goal", "CLI script execution"))
        script_name = kwargs.get("name", "CLI script")
        script_type = "chart"
        script_id = None
        
        code = None
        # Determine code source: check if target is file
        if target.lower().endswith(".py") or os.path.exists(target):
            if not os.path.exists(target):
                self._emit(False, error=f"File not found: {target}")
                return
            try:
                with open(target, "r", encoding="utf-8") as f:
                    code = f.read()
                script_name = os.path.basename(target)
                script_id = str(hashlib.sha256(code.encode('utf-8')).hexdigest()[:16])
            except Exception as e:
                self._emit(False, error=f"Failed to read script file: {e}")
                return
        else:
            try:
                from modules.script_manager import load_all_scripts
                scripts = load_all_scripts()
                found = None
                for s in scripts:
                    if s.get("id") == target:
                        found = s
                        break
                if not found:
                    for s in scripts:
                        if s.get("name", "").lower() == target.lower():
                            found = s
                            break
                if found:
                    code = found.get("code")
                    script_name = found.get("name")
                    script_id = found.get("id")
                    script_type = found.get("type", "chart")
                    if not script_desc or script_desc == "CLI script execution":
                        script_desc = found.get("description", "CLI script execution")
                else:
                    self._emit(False, error=f"Script target '{target}' not found as a file or registered script.")
                    return
            except Exception as e:
                self._emit(False, error=str(e))
                return
                
        # Build dataset snapshot
        dataset_items = []
        has_active_project = False
        temp_path = None
        try:
            # We can check if any speakers exist to know if there's project data to snapshot
            if self.speaker_manager.get_all_speakers():
                has_active_project = True
                import tempfile
                # Export to a temp path
                fd, temp_path = tempfile.mkstemp(suffix=".teproj")
                os.close(fd)
                
                # Make sure all items are loaded in memory so they get exported correctly
                orig_active_id = self.speaker_manager.active_speaker_id
                try:
                    for spk in self.speaker_manager.get_all_speakers():
                        self.speaker_manager.set_active_speaker(spk.id)
                        for item in spk.items.values():
                            self._ensure_item_loaded(item)
                finally:
                    if orig_active_id:
                        self.speaker_manager.set_active_speaker(orig_active_id)
                        
                ok = self.project_manager.export_project(temp_path)
                if ok:
                    from modules.script_api import build_dataset_snapshot
                    dataset_items = build_dataset_snapshot(temp_path)
                else:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass
                    self._emit(False, error="Failed to export project state to temporary file for snapshotting.")
                    return
        except Exception as e:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            self._emit(False, error=f"Failed to prepare project snapshot: {e}")
            return
                    
        # Run custom script
        try:
            from modules.script_runner import run_custom_script
            import matplotlib.pyplot as plt
            plt.close('all')
            
            res, logs, err = run_custom_script(code, dataset_items, timeout=timeout, teproj_path=temp_path)
            
            if err:
                self._emit(False, error=err, logs=logs)
                return
                
            # Process success results
            from modules.script_api import FigureResult, TableResult, ProjectPatchResult
            from modules.project_patch import apply_project_patch_to_teproj
            
            results = res if isinstance(res, list) else [res]
            project_patch_results = [r for r in results if isinstance(r, ProjectPatchResult)]
            if project_patch_results:
                if len(project_patch_results) != 1 or len(results) != 1:
                    self._emit(False, error="数据处理脚本只能返回一个 ctx.project_patch(...)，不能同时返回图表、表格或多个工程补丁。", logs=logs)
                    return
                if not has_active_project or not temp_path:
                    self._emit(False, error="当前未加载任何有效工程，无法应用数据处理工程补丁。", logs=logs)
                    return

                patch_res = project_patch_results[0]
                if not patch_res.operations:
                    logs.append("脚本没有返回任何工程修改操作。")
                    self._emit(True, "脚本没有返回任何工程修改操作", logs=logs, outputs=[])
                    return

                out_project = kwargs.get("out_project") or kwargs.get("output_project") or kwargs.get("out_teproj")
                if not out_project:
                    self._emit(False, error="数据处理脚本返回了工程修改，但未指定输出工程文件。请在参数中指定 out_project=<path.teproj>", logs=logs)
                    return

                out_project = os.path.abspath(out_project)
                if not out_project.lower().endswith(".teproj"):
                    out_project += ".teproj"

                out_dir = os.path.dirname(out_project)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)

                code_sha256 = hashlib.sha256(code.encode('utf-8')).hexdigest()
                run_record = {
                    "script_id": script_id or str(uuid.uuid4()),
                    "script_name": script_name,
                    "script_type": "data_process",
                    "api_version": "1",
                    "software_version": __version__,
                    "code_sha256": code_sha256,
                    "code": code,
                    "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user_goal": script_desc,
                    "status": "成功",
                    "outputs": [{
                        "type": "project",
                        "title": patch_res.title or "数据处理后的工程",
                        "filename": os.path.basename(out_project),
                        "saved_path": out_project
                    }]
                }

                try:
                    summary = apply_project_patch_to_teproj(
                        temp_path,
                        patch_res,
                        out_project,
                        run_record=run_record
                    )
                    output_records = [{
                        "type": "project",
                        "title": patch_res.title or "数据处理后的工程",
                        "filename": os.path.basename(out_project),
                        "saved_path": out_project,
                        "operation_count": summary.get("operation_count", 0),
                        "applied_operations": summary.get("applied_operations", [])
                    }]
                    logs.append(f"数据处理工程补丁已成功应用并保存至：{out_project}")
                    logs.append(f"包含操作总数: {summary.get('operation_count', 0)}，实际执行: {len(summary.get('applied_operations', []))} 项")

                    should_reload = (
                        kwargs.get("reload", "false").lower() in ("true", "1", "yes")
                        or kwargs.get("apply_to_current", "false").lower() in ("true", "1", "yes")
                    )
                    if should_reload:
                        if self.project_manager.load_project(out_project):
                            self._sync_cli_state_after_project_load()
                            logs.append("已将修改后的工程重新载入当前 CLI 会话。")
                        else:
                            logs.append("警告: 重新载入修改后的工程失败。")

                    self._emit(
                        True,
                        "脚本执行完成",
                        logs=logs,
                        output_dir=out_dir,
                        outputs=output_records,
                        patch_summary=summary
                    )
                    return
                except Exception as e:
                    logs.append(f"应用工程补丁失败: {e}")
                    self._emit(False, error=f"应用工程补丁失败: {e}", logs=logs)
                    return

            figure_results = [r for r in results if isinstance(r, FigureResult)]
            table_results = [r for r in results if isinstance(r, TableResult)]
            
            # Inline _safe_script_output_name
            def safe_name(name, fallback="output"):
                raw = str(name or fallback).strip()
                raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
                raw = re.sub(r"\s+", "_", raw)
                raw = raw.strip("._ ")
                return raw[:80] or fallback
                
            # Setup output folder
            custom_out_dir = kwargs.get("output_dir", kwargs.get("out_dir"))
            if custom_out_dir:
                unique_folder = os.path.abspath(custom_out_dir)
                os.makedirs(unique_folder, exist_ok=True)
            else:
                base_out_dir = os.path.join(os.path.expanduser("~"), ".phon_tracer", "script_outputs")
                safe_script_name = safe_name(script_name, "script")
                stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                folder = os.path.join(base_out_dir, f"{stamp}_{safe_script_name}")
                suffix = 2
                unique_folder = folder
                while os.path.exists(unique_folder):
                    unique_folder = f"{folder}_{suffix}"
                    suffix += 1
                os.makedirs(unique_folder, exist_ok=True)
            
            output_records = []
            
            # Helper to get unique filename in folder
            def unique_path(folder, filename):
                base, ext_ = os.path.splitext(filename)
                p = os.path.join(folder, filename)
                idx = 2
                while os.path.exists(p):
                    p = os.path.join(folder, f"{base}_{idx}{ext_}")
                    idx += 1
                return p
                
            # Save figures
            for idx, fig_res in enumerate(figure_results, start=1):
                fn = safe_name(fig_res.filename or f"custom_chart_{idx}.png", f"custom_chart_{idx}.png")
                r, ext = os.path.splitext(fn)
                if not ext:
                    fn = f"{fn}.png"
                elif ext.lower() not in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
                    fn = f"{r}.png"
                out_path = unique_path(unique_folder, fn)
                try:
                    fig_res.fig.savefig(out_path, dpi=300, bbox_inches="tight")
                    output_records.append({
                        "type": "figure",
                        "title": fig_res.title,
                        "filename": os.path.basename(out_path),
                        "saved_path": out_path,
                    })
                    logs.append(f"图表 {idx} 已保存：{out_path}")
                except Exception as e:
                    logs.append(f"图表 {idx} 保存失败：{e}")

            try:
                import matplotlib.pyplot as plt
                for fig_res in figure_results:
                    try:
                        plt.close(fig_res.fig)
                    except Exception:
                        pass
            except Exception:
                pass
                    
            # Save tables
            for idx, tbl_res in enumerate(table_results, start=1):
                import csv
                tbl_name = safe_name(tbl_res.title or f"custom_table_{idx}", f"custom_table_{idx}")
                out_path = unique_path(unique_folder, f"{tbl_name}.csv")
                try:
                    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(list(tbl_res.columns or []))
                        writer.writerows(tbl_res.rows or [])
                    output_records.append({
                        "type": "table",
                        "title": tbl_res.title,
                        "filename": os.path.basename(out_path),
                        "saved_path": out_path,
                    })
                    logs.append(f"表格 {idx} 已保存：{out_path}")
                except Exception as e:
                    logs.append(f"表格 {idx} 保存失败：{e}")
                    
            # If archiving is requested and project is active
            if archive and has_active_project:
                code_sha256 = hashlib.sha256(code.encode('utf-8')).hexdigest()
                run_record = {
                    "script_id": script_id or str(uuid.uuid4()),
                    "script_name": script_name,
                    "script_type": script_type,
                    "api_version": "1",
                    "software_version": __version__,
                    "code_sha256": code_sha256,
                    "code": code,
                    "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "user_goal": script_desc,
                    "status": "成功",
                    "outputs": output_records
                }
                
                # Add to app's custom_script_runs (or initialize if needed)
                if not hasattr(self, "custom_script_runs") or self.custom_script_runs is None:
                    self.custom_script_runs = []
                from modules.project_patch import _append_or_update_script_run
                _append_or_update_script_run(self.custom_script_runs, run_record)
                
                # Save project workspace
                try:
                    self.project_manager.save_to_workspace()
                    logs.append("本次脚本运行记录已存入当前工程。")
                except Exception as e:
                    logs.append(f"警告: 脚本记录存入当前工程失败: {e}")
                    
            self._emit(True, "脚本执行完成", logs=logs, output_dir=unique_folder, outputs=output_records)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._emit(False, error=f"执行发生意外错误: {e}", traceback=tb)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def do_batch_textgrid_import(self, arg):
        """
        Import multiple TextGrid files to match currently loaded batch audios.
        Usage: batch_textgrid_import <tg_file_or_dir1> [tg_file_or_dir2 ...]
        """
        if self.mode != 'batch' or not self.batch_paths:
            self._emit(False, error="This command requires batch mode with loaded audio paths. Use load_batch first.")
            return

        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: batch_textgrid_import <tg_file_or_dir1> [tg_file_or_dir2 ...]")
            return

        import os
        tg_files = []
        for path in args:
            if os.path.isdir(path):
                for f in os.listdir(path):
                    if f.lower().endswith('.textgrid'):
                        tg_files.append(os.path.join(path, f))
            elif os.path.isfile(path) and path.lower().endswith('.textgrid'):
                tg_files.append(path)
            else:
                self._emit(False, error=f"Invalid file or directory path, or not a TextGrid: {path}")
                return

        if not tg_files:
            self._emit(False, error="No TextGrid files found from the arguments.")
            return

        import re
        def natural_sort_key(s):
            return [int(text) if text.isdigit() else text.lower()
                    for text in re.split('([0-9]+)', s)]

        sorted_audios = sorted(self.batch_paths, key=natural_sort_key)
        sorted_tgs = sorted(tg_files, key=natural_sort_key)

        matched_audio_to_tg = self._match_audio_to_textgrid(sorted_audios, sorted_tgs)
        
        # Clear existing items for the active speaker and compute new ones
        self.items.clear()
        self._process_speaker_batch_items(self.current_speaker, sorted_audios, matched_audio_to_tg, self.params)

        # Recompute groups list
        seen_groups = []
        for item in self.items.values():
            g = item.get('group', '导入内容')
            if g not in seen_groups:
                seen_groups.append(g)
        self.groups = seen_groups

        self._after_state_change("batch_textgrid_import")
        
        matched_count = sum(1 for ap in sorted_audios if ap in matched_audio_to_tg)
        self._emit(
            True,
            f"Successfully matched and imported {matched_count} TextGrid files for {len(sorted_audios)} audios.",
            matched_count=matched_count,
            total_audios=len(sorted_audios)
        )

    def do_report_export(self, arg):
        """
        Export research reports from a .teproj project.
        Usage: report_export <teproj_path> <out_dir> [format: md|excel|both] [include_cache: true|false]
        Default format: both, default include_cache: true
        """
        args = shlex.split(arg)
        if len(args) < 2:
            self._emit(False, error="Usage: report_export <teproj_path> <out_dir> [format: md|excel|both] [include_cache: true|false]")
            return

        teproj_path = args[0]
        out_dir = args[1]
        fmt = 'both'
        inc_cache = True

        for item in args[2:]:
            item_lower = item.lower().strip()
            if '=' in item_lower:
                k, v = item_lower.split('=', 1)
                k = k.strip()
                v = v.strip()
                if k == 'format':
                    fmt = v
                elif k in ('include_cache', 'cache'):
                    inc_cache = v in ('true', '1', 'yes')
            elif item_lower in ('true', 'false', '1', '0', 'yes', 'no'):
                inc_cache = item_lower in ('true', '1', 'yes')
            elif item_lower in ('md', 'excel', 'both'):
                fmt = item_lower
            elif item_lower == 'include_cache' or item_lower == 'cache':
                inc_cache = True
            elif item_lower == 'no-cache':
                inc_cache = False

        if not os.path.exists(teproj_path):
            self._emit(False, error=f"File not found: {teproj_path}")
            return

        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                self._emit(False, error=f"Failed to create output directory: {e}")
                return

        if fmt not in ('md', 'excel', 'both'):
            self._emit(False, error=f"Invalid format: {fmt}. Must be md, excel, or both")
            return

        from modules.report_generator import export_reports_from_teproj
        try:
            export_markdown = (fmt in ("both", "md"))
            export_excel = (fmt in ("both", "excel"))
            exported_files, base_name = export_reports_from_teproj(
                teproj_path,
                out_dir,
                export_markdown=export_markdown,
                export_excel=export_excel,
                include_cache_details=inc_cache
            )
            self._emit(
                True,
                f"Successfully exported research reports to {out_dir}",
                files=[os.path.basename(f) for f in exported_files]
            )
        except Exception as e:
            self._emit(False, error=str(e))

    def do_project_preview(self, arg):
        """
        Show a text preview of the contents of a .teproj project file.
        Usage: project_preview <teproj_path> [format=text|json]
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: project_preview <teproj_path> [format=text|json]")
            return

        teproj_path = args[0]
        fmt = 'json'
        for item in args[1:]:
            if '=' in item:
                k, v = item.split('=', 1)
                k = k.lower().strip()
                v = v.strip().lower()
                if k == 'format':
                    fmt = v
            else:
                val = item.lower().strip()
                if val in ('text', 'json'):
                    fmt = val

        if not os.path.exists(teproj_path):
            self._emit(False, error=f"File not found: {teproj_path}")
            return

        from modules.project_manager import read_project_metadata_from_archive
        from modules.report_generator import get_pitch_floor, get_pitch_ceiling

        try:
            project_data, namelist = read_project_metadata_from_archive(teproj_path)
            
            def pad_chinese(text, width):
                text_len = len(text)
                chinese_len = sum(1 for char in text if ord(char) > 127)
                padding = width - text_len - chinese_len
                if padding <= 0:
                    return text
                return text + " " * padding

            lines = []
            lines.append("=" * 60)
            lines.append("               PHONTRACER 工程文件 (.teproj) 数据预览")
            lines.append("=" * 60)
            lines.append("")

            # 1. Basic Info
            version = project_data.get("version", "未知")
            active_speaker_id = project_data.get("active_speaker_id", "无")
            speakers = project_data.get("speakers", {})

            lines.append("【基本信息】")
            lines.append(f"  • 工程格式版本: {version}")
            lines.append(f"  • 发音人数量: {len(speakers)}")
            lines.append("")

            summary = {
                "version": version,
                "active_speaker_id": active_speaker_id,
                "speaker_count": len(speakers),
                "speakers": [],
                "file_count": len(namelist)
            }

            # 2. Speakers & Detailed structure
            lines.append("【发音人及音频明细】")
            for s_id, spk in speakers.items():
                name = spk.get("name", "未命名")
                tab_mode = spk.get("tab_mode", "未知模式")
                last_params = spk.get("last_params", {})
                items = spk.get("items", {})
                pending_batch_paths = spk.get("pending_batch_paths", [])
                long_audio_path = spk.get("long_audio_path", "")

                is_active = " (当前选中)" if s_id == active_speaker_id else ""
                lines.append(f"  ■ 发音人: {name}{is_active}")
                lines.append(f"    • 唯一标识符: {s_id}")
                lines.append(f"    • 音频管理模式: {tab_mode}")

                summary["speakers"].append({
                    "id": s_id,
                    "name": name,
                    "tab_mode": tab_mode,
                    "item_count": len(items),
                    "is_active": s_id == active_speaker_id
                })

                if tab_mode == "单条长音频":
                    lines.append(f"    • 长音频文件: {os.path.basename(long_audio_path) if long_audio_path else '无'}")
                    mac_segs = spk.get("current_macro_segments", [])
                    man_segs = spk.get("manual_segments", [])
                    lines.append(f"    • 自动分段数量: {len(mac_segs)}")
                    lines.append(f"    • 手动微调段数: {len(man_segs) if man_segs is not None else '未进行微调'}")
                else:
                    lines.append(f"    • 待导入音频数: {len(pending_batch_paths)}")

                # Engine parameters
                if last_params:
                    lines.append("    • 分析引擎参数配置:")
                    lines.append(f"      - 基频范围 (F0 Range): {get_pitch_floor(last_params):.0f} Hz ~ {get_pitch_ceiling(last_params):.0f} Hz")
                    lines.append(f"      - 时序分析点数 (Points): {last_params.get('pts', 11)}")
                    lines.append(f"      - 分析算法: {last_params.get('method', 'ac')}")

                lines.append(f"    • 解析出的字词条目 (共 {len(items)} 条):")
                if not items:
                    lines.append("      (暂无提取的字词条目数据)")
                else:
                    item_header = f"      {'序号':<4} | {pad_chinese('音节/字词', 12)} | {'时间区间 (s)':<22} | {pad_chinese('F0缓存状态', 10)}"
                    lines.append(item_header)
                    lines.append("      " + "-" * 56)

                    for idx, (item_id, item) in enumerate(items.items(), 1):
                        label = item.get("label", "无")
                        start = item.get("start")
                        end = item.get("end")

                        pitch_file = item.get("pitch_data_file", "")
                        has_pitch = "无"
                        if pitch_file and pitch_file in namelist:
                            has_pitch = "已缓存"
                        elif item.get("pitch_data") is not None:
                            has_pitch = "已缓存"

                        idx_str = f"{idx:<4}"
                        label_col = pad_chinese(label, 12)
                        if start is None or end is None:
                            time_col = pad_chinese("未切分", 22)
                        else:
                            time_col = f"{float(start):7.3f}s ~ {float(end):7.3f}s"
                        has_pitch_col = pad_chinese(has_pitch, 10)

                        lines.append(f"      {idx_str} | {label_col} | {time_col} | {has_pitch_col}")
                lines.append("")

            # 3. Archive file list overview
            lines.append("【工程压缩包物理文件清单】")
            lines.append(f"  • 压缩包内文件总数: {len(namelist)}")

            audio_files = [f for f in namelist if f.startswith("audio/")]
            data_files = [f for f in namelist if f.startswith("data/")]

            lines.append(f"  • 音频资源文件数 (audio/): {len(audio_files)}")
            lines.append(f"  • 基频数据缓存数 (data/): {len(data_files)}")
            lines.append("")
            lines.append("=" * 60)

            preview_text = "\n".join(lines)
            if fmt == 'text':
                print(preview_text)
            else:
                self._emit(True, "Project preview retrieved.", preview_text=preview_text, summary=summary)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_project_convert_zip(self, arg):
        """
        Convert/copy a .teproj project file to a standard .zip archive.
        Usage: project_convert_zip <teproj_path> <dest_zip_path>
        """
        args = shlex.split(arg)
        if len(args) != 2:
            self._emit(False, error="Usage: project_convert_zip <teproj_path> <dest_zip_path>")
            return

        teproj_path, dest_zip_path = args[0], args[1]
        if not os.path.exists(teproj_path):
            self._emit(False, error=f"File not found: {teproj_path}")
            return

        try:
            import shutil
            shutil.copy2(teproj_path, dest_zip_path)
            self._emit(True, f"Project successfully copied/converted to ZIP archive: {dest_zip_path}", path=dest_zip_path)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_script_save(self, arg):
        """
        Save or create a custom script.
        Usage: script_save <id_or_new> <name> <description> <type> <code_file_path>
        Use 'new' for creating a new script.
        """
        args = shlex.split(arg)
        if len(args) != 5:
            self._emit(False, error="Usage: script_save <id_or_new> <name> <description> <type> <code_file_path>")
            return

        script_id = None if args[0].lower() == 'new' else args[0]
        name, description, script_type, code_file_path = args[1], args[2], args[3], args[4]

        if not os.path.exists(code_file_path):
            self._emit(False, error=f"Code file not found: {code_file_path}")
            return

        try:
            with open(code_file_path, 'r', encoding='utf-8') as f:
                code_content = f.read()

            from modules.script_manager import save_script
            saved_id = save_script(script_id, name, description, script_type, code_content)
            self._emit(True, f"Script saved successfully.", id=saved_id)
        except Exception as e:
            self._emit(False, error=str(e))

    def do_script_delete(self, arg):
        """
        Delete a custom script.
        Usage: script_delete <script_id>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: script_delete <script_id>")
            return

        script_id = args[0]
        try:
            from modules.script_manager import delete_script
            success = delete_script(script_id)
            if success:
                self._emit(True, f"Script {script_id} deleted successfully.", id=script_id)
            else:
                self._emit(False, error=f"Script ID {script_id} not found in user library.")
        except Exception as e:
            self._emit(False, error=str(e))

    def do_script_import(self, arg):
        """
        Import a custom script from a JSON file.
        Usage: script_import <json_path>
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Usage: script_import <json_path>")
            return

        json_path = args[0]
        if not os.path.exists(json_path):
            self._emit(False, error=f"JSON file not found: {json_path}")
            return

        try:
            from modules.script_manager import import_script
            imported_script = import_script(json_path)
            self._emit(True, "Script imported successfully.", script={
                "id": imported_script.get("id"),
                "name": imported_script.get("name")
            })
        except Exception as e:
            self._emit(False, error=str(e))

    def do_script_export(self, arg):
        """
        Export a custom script to a JSON file.
        Usage: script_export <script_id> <dest_json_path>
        """
        args = shlex.split(arg)
        if len(args) != 2:
            self._emit(False, error="Usage: script_export <script_id> <dest_json_path>")
            return

        script_id, dest_json_path = args[0], args[1]
        try:
            from modules.script_manager import export_script
            export_script(script_id, dest_json_path)
            self._emit(True, f"Script exported successfully to {dest_json_path}.", id=script_id, path=dest_json_path)
        except Exception as e:
            self._emit(False, error=str(e))

    def _build_project_data_dict(self):
        wordlist_doc = self._build_wordlist_v2_doc_from_project()
        project_data = {
            "active_speaker_id": self.speaker_manager.active_speaker_id,
            "wordlist_doc": wordlist_doc,
            "wordlist_meta": {"title": wordlist_doc.get("title", "")},
            "groups": wordlist_doc.get("groups", []),
            "speakers": {}
        }
        for spk_id, spk in self.speaker_manager.speakers.items():
            spk_data = {
                "name": spk.name,
                "tab_mode": getattr(spk, 'tab_mode', "多条独立音频"),
                "long_audio_path": getattr(spk, 'long_audio_path', None),
                "pending_batch_paths": spk.pending_batch_paths,
                "last_params": spk.last_params,
                "items": {}
            }
            for item_id, item in spk.items.items():
                item_dict = {k: v for k, v in item.items() if k != "snd"} if isinstance(item, dict) else {}
                tags = list(item.get("item_tags") or item.get("tags") or [])
                note = item.get("item_note") or item.get("note") or ""
                custom_fields = {}
                for fkey in ("meta", "item_meta", "fields", "custom_fields"):
                    fval = item.get(fkey)
                    if isinstance(fval, dict):
                        custom_fields.update(fval)
                item_dict.update({
                    "item_id": item.get("item_id", item_id),
                    "label": item.get("label"),
                    "group": item.get("group"),
                    "is_excluded": item.get("is_excluded", False),
                    "analysis_mode": item.get("analysis_mode", spk.last_params.get("analysis_mode", "f0")),
                    "pitch_data": item.get("pitch_data"),
                    "formant_data": item.get("formant_data"),
                    "pitch_data_file": item.get("pitch_data_file"),
                    "formant_data_file": item.get("formant_data_file"),
                    "wordlist_version": item.get("wordlist_version") or ("v2" if (tags or note or custom_fields) else "v1"),
                    "wordlist_title": item.get("wordlist_title") or wordlist_doc.get("title", ""),
                    "item_note": note,
                    "note": note,
                    "item_tags": tags,
                    "tags": tags,
                    "item_aliases": item.get("item_aliases") or item.get("aliases") or [],
                    "item_meta": dict(custom_fields),
                    "fields": dict(custom_fields),
                    "custom_fields": dict(custom_fields),
                    "group_note": item.get("group_note"),
                    "group_tags": item.get("group_tags", []) or [],
                    "metadata_source": item.get("metadata_source"),
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "inner_splits": item.get("inner_splits", []) or [],
                    "chars_bounds": item.get("chars_bounds", []) or [],
                    "syllables": item.get("syllables", []) or [],
                })
                spk_data["items"][item_id] = item_dict
            project_data["speakers"][spk_id] = spk_data
        return project_data

    def do_script_prompt(self, arg):
        """
        Generate AI prompt for writing a custom script.
        Usage: script_prompt [key=value ...]
        Valid keys: template, output_type, scope, analysis_mode, user_request, out, format,
                    purpose, goal, script_type, prompt_mode, data_range, group_by, chart_style,
                    x_axis, y_axis, stats, title, filename, img_format, output_table,
                    show_legend, use_chinese, custom_desc, agent_detail_level,
                    agent_chart_count, agent_plan_count, agent_project_summary_mode
        Example: script_prompt template=formant_space scope=integrated user_request="绘制元音空间图" out=prompt.md
        """
        from modules.script_prompt import (
            generate_ai_prompt,
            validate_and_normalize_script_prompt_params,
            VALID_SCRIPT_PROMPT_KEYS,
            SCRIPT_PROMPT_KEY_ALIASES,
        )

        try:
            args = shlex.split(arg)
        except ValueError as e:
            self._emit(False, error=f"Invalid argument syntax: {e}", valid_keys=list(VALID_SCRIPT_PROMPT_KEYS))
            return

        if len(args) == 1 and args[0].strip().lower() in ("?", "--help", "-h", "help"):
            self._emit(
                True,
                "Supported parameters for script_prompt",
                usage="script_prompt [key=value ...]",
                valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
            )
            return

        fmt = 'json'
        raw_params = {}
        for kv in args:
            if '=' in kv:
                k, v = kv.split('=', 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean.lower() == 'format':
                    fmt = v_clean.lower()
                raw_params[k_clean] = v_clean
            else:
                val = kv.lower().strip()
                if val in ('text', 'json'):
                    fmt = val
                else:
                    self._emit(
                        False,
                        error=f"Invalid parameter format: '{kv}'. Expected key=value. Valid parameters are: {', '.join(VALID_SCRIPT_PROMPT_KEYS)}",
                        valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
                    )
                    return

        try:
            selections = validate_and_normalize_script_prompt_params(raw_params)
        except ValueError as e:
            self._emit(
                False,
                error=str(e),
                valid_keys=list(VALID_SCRIPT_PROMPT_KEYS),
            )
            return

        project_data = self._build_project_data_dict()
        try:
            prompt_text = generate_ai_prompt(project_data, selections)
            out_path = selections.get("out", "").strip()
            saved_out = None
            if out_path:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(prompt_text)
                saved_out = os.path.abspath(out_path)

            if fmt == 'text':
                print(prompt_text)
            else:
                payload = {"prompt_text": prompt_text}
                if saved_out:
                    payload["output_file"] = saved_out
                self._emit(True, "AI prompt generated successfully.", **payload)
        except Exception as e:
            self._emit(False, error=str(e), valid_keys=list(VALID_SCRIPT_PROMPT_KEYS))

    def do_generate_prompt(self, arg):
        """Alias for script_prompt."""
        self.do_script_prompt(arg)

    def do_log(self, arg):
        """
        Enable or disable session logging.
        Usage: log on [filename]
               log off
        Default filename is 'phontracer_session.log'.
        """
        args = shlex.split(arg)
        if not args:
            self._emit(False, error="Missing argument: on or off")
            return

        action = args[0].lower()
        if action == 'on':
            if self.log_file:
                self._emit(False, error="Logging is already on")
                return
            filename = args[1] if len(args) > 1 else 'phontracer_session.log'
            try:
                self.log_file = open(filename, 'a', encoding='utf-8')
                sys.stdout = LoggerOut(self.original_stdout, self.log_file)
                self._emit(True, f"Logging enabled to {filename}")
            except Exception as e:
                self._emit(False, error=str(e))
        elif action == 'off':
            if not self.log_file:
                self._emit(False, error="Logging is not currently on")
                return
            sys.stdout = self.original_stdout
            self.log_file.close()
            self.log_file = None
            self._emit(True, "Logging disabled")
        else:
            self._emit(False, error="Invalid argument. Use on or off")

    def do_exit(self, arg):
        """Exit the CLI."""
        if not getattr(self, "quiet", False) and not getattr(self, "json_mode", False):
            print("Exiting...")
        if self.log_file:
            sys.stdout = self.original_stdout
            self.log_file.close()
            self.log_file = None
        if getattr(self, "_executor", None) is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        return True

    def _export_formant_table(self, out_file, speakers_to_process):
        try:
            import xlsxwriter
        except ImportError:
            self._emit(False, error="Missing xlsxwriter library. Please install via: pip install xlsxwriter")
            return

        from modules.data_utils import (
            ExcelStyleManager, autofit_worksheet_columns,
            write_formant_analysis_sheet_with_formulas,
            clean_str, get_item_syllable_bounds, split_into_syllables,
            sample_formant_points_by_bounds_extended
        )

        workbook = xlsxwriter.Workbook(out_file)
        style = ExcelStyleManager(workbook)

        ws_data = workbook.add_worksheet("提取数据")
        ws_analysis = workbook.add_worksheet("分析图表")
        ws_raw = workbook.add_worksheet("原始数据")

        show_f3 = bool(self.params.get('show_f3', False))
        data_headers = ["发音人", "组别", "编号", "词语", "音节序号", "音节", "时间点序号", "时间(s)", "F1(Hz)", "F2(Hz)"]
        if show_f3:
            data_headers.append("F3(Hz)")
        for col, h in enumerate(data_headers):
            ws_data.write(0, col, h, style.header_fmt)

        data_row = 1
        formant_sample_rows = []
        group_char_pairs = []
        seen_pairs = set()
        raw_formant_entries = []

        for spk in speakers_to_process:
            is_continuous = (self.params.get('num_rule', 'continuous') == 'continuous')
            pts = int(self.params.get('pts', 11))
            strategy = self.params.get('formant_sample_strategy', '整段11点')

            groups = {}
            for item_id, item in spk.items.items():
                if item.get('is_excluded', False):
                    continue
                g = item.get('group', '导入内容')
                if g not in groups:
                    groups[g] = []
                groups[g].append(item)

            sorted_groups = sorted(groups.keys(), key=clean_str)

            global_idx = 1
            for grp_name in sorted_groups:
                if not is_continuous:
                    global_idx = 1
                items_in_grp = groups[grp_name]
                items_in_grp = sorted(items_in_grp, key=lambda x: clean_str(x.get('label', '')))

                for item in items_in_grp:
                    self._ensure_item_loaded(item)
                    if not item.get('formant_data'):
                        continue

                    bounds = get_item_syllable_bounds(item)
                    syls = split_into_syllables(item.get('label', ''))
                    res_samp = sample_formant_points_by_bounds_extended(item, bounds, pts, strategy, include_f3=show_f3)
                    preview_times = res_samp["times"]
                    f1_vals = res_samp["f1"]
                    f2_vals = res_samp["f2"]
                    f3_vals = res_samp["f3"]

                    raw_formant_entries.append({
                        'speaker': spk.name,
                        'group': grp_name,
                        'index': global_idx,
                        'item': item,
                    })

                    for idx_syl, (c_s, c_e) in enumerate(bounds):
                        char = syls[idx_syl] if idx_syl < len(syls) else f"字{idx_syl+1}"
                        flat_start = idx_syl * pts

                        pair_key = (grp_name, char)
                        if pair_key not in seen_pairs:
                            seen_pairs.add(pair_key)
                            group_char_pairs.append(pair_key)

                        for idx_pt in range(pts):
                            ws_data.write(data_row, 0, spk.name, style.text_center)
                            ws_data.write(data_row, 1, grp_name, style.text_center)
                            ws_data.write(data_row, 2, global_idx, style.int_fmt)
                            ws_data.write(data_row, 3, item.get('label', ''), style.text_left)
                            ws_data.write(data_row, 4, idx_syl + 1, style.int_fmt)
                            ws_data.write(data_row, 5, char, style.text_center)
                            ws_data.write(data_row, 6, idx_pt + 1, style.int_fmt)
                            ws_data.write(data_row, 7, round(preview_times[flat_start + idx_pt], 6), style.duration_fmt)

                            f1_v = f1_vals[flat_start + idx_pt]
                            f2_v = f2_vals[flat_start + idx_pt]

                            if np.isnan(f1_v):
                                ws_data.write(data_row, 8, "", style.text_center)
                            else:
                                ws_data.write(data_row, 8, round(f1_v, 1), style.hz_fmt)

                            if np.isnan(f2_v):
                                ws_data.write(data_row, 9, "", style.text_center)
                            else:
                                ws_data.write(data_row, 9, round(f2_v, 1), style.hz_fmt)

                            sample_r = [
                                spk.name, grp_name, global_idx, item.get('label', ''),
                                idx_syl + 1, char, idx_pt + 1, round(preview_times[flat_start + idx_pt], 6),
                                round(f1_v, 1) if not np.isnan(f1_v) else "",
                                round(f2_v, 1) if not np.isnan(f2_v) else ""
                            ]

                            if show_f3:
                                f3_v = f3_vals[flat_start + idx_pt]
                                if np.isnan(f3_v):
                                    ws_data.write(data_row, 10, "", style.text_center)
                                    sample_r.append("")
                                else:
                                    ws_data.write(data_row, 10, round(f3_v, 1), style.hz_fmt)
                                    sample_r.append(round(f3_v, 1))

                            if len(formant_sample_rows) < 100:
                                formant_sample_rows.append(sample_r)

                            data_row += 1

                    global_idx += 1

        ws_data.freeze_panes(1, 0)
        ws_data.autofilter(0, 0, max(data_row - 1, 1), len(data_headers) - 1)
        autofit_worksheet_columns(ws_data, data_headers, formant_sample_rows)

        last_data_row = data_row - 1
        write_formant_analysis_sheet_with_formulas(
            workbook, ws_analysis, group_char_pairs, last_data_row,
            data_sheet_name='提取数据',
        )

        raw_headers = [
            "发音人", "组别", "编号", "词语", "音节序号", "音节",
            "绝对时间(s)", "字内相对时间(s)", "F1(Hz)", "F2(Hz)"
        ]
        if show_f3:
            raw_headers.append("F3(Hz)")
        raw_headers.append("状态")
        for col, h in enumerate(raw_headers):
            ws_raw.write(0, col, h, style.header_fmt)

        raw_row = 1
        raw_sample_rows = []
        for entry in raw_formant_entries:
            item = entry['item']
            f_data = item.get('formant_data')
            if not f_data or 'xs' not in f_data:
                continue

            xs = f_data['xs']
            f1_arr = f_data.get('f1', np.array([]))
            f2_arr = f_data.get('f2', np.array([]))

            bounds = get_item_syllable_bounds(item)
            syls = split_into_syllables(item.get('label', ''))

            f3_arr = f_data.get('f3') if show_f3 else None
            for syl_idx, (c_s, c_e) in enumerate(bounds):
                char = syls[syl_idx] if syl_idx < len(syls) else f"字{syl_idx+1}"
                mask = (xs >= c_s) & (xs <= c_e)
                indices = np.where(mask)[0]

                for frame_idx in indices:
                    t = float(xs[frame_idx])
                    f1 = float(f1_arr[frame_idx]) if frame_idx < len(f1_arr) and np.isfinite(f1_arr[frame_idx]) else 0.0
                    f2 = float(f2_arr[frame_idx]) if frame_idx < len(f2_arr) and np.isfinite(f2_arr[frame_idx]) else 0.0
                    if show_f3:
                        f3 = float(f3_arr[frame_idx]) if f3_arr is not None and frame_idx < len(f3_arr) and np.isfinite(f3_arr[frame_idx]) else 0.0

                    status = "有效" if (f1 > 0 and f2 > 0 and f2 > f1) else "无效"

                    ws_raw.write(raw_row, 0, entry['speaker'], style.text_center)
                    ws_raw.write(raw_row, 1, entry['group'], style.text_center)
                    ws_raw.write(raw_row, 2, entry['index'], style.int_fmt)
                    ws_raw.write(raw_row, 3, item.get('label', ''), style.text_left)
                    ws_raw.write(raw_row, 4, syl_idx + 1, style.int_fmt)
                    ws_raw.write(raw_row, 5, char, style.text_center)
                    ws_raw.write(raw_row, 6, round(t, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 7, round(t - c_s, 6), style.decimal6_fmt)
                    ws_raw.write(raw_row, 8, round(f1, 1) if f1 > 0 else 0.0, style.hz_fmt)
                    ws_raw.write(raw_row, 9, round(f2, 1) if f2 > 0 else 0.0, style.hz_fmt)
                    if show_f3:
                        ws_raw.write(raw_row, 10, round(f3, 1) if f3 > 0 else 0.0, style.hz_fmt)
                        ws_raw.write(raw_row, 11, status, style.text_center)
                    else:
                        ws_raw.write(raw_row, 10, status, style.text_center)

                    if len(raw_sample_rows) < 100:
                        raw_sample_rows.append([
                            entry['speaker'], entry['group'], entry['index'], item.get('label', ''),
                            syl_idx + 1, char, round(t, 6), round(t - c_s, 6),
                            round(f1, 1) if f1 > 0 else 0.0, round(f2, 1) if f2 > 0 else 0.0,
                            *( [round(f3, 1) if f3 > 0 else 0.0] if show_f3 else [] ),
                            status
                        ])
                    raw_row += 1

        ws_raw.freeze_panes(1, 0)
        ws_raw.autofilter(0, 0, max(raw_row - 1, 1), len(raw_headers) - 1)
        autofit_worksheet_columns(ws_raw, raw_headers, raw_sample_rows)

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

    def _export_vowel_space_chart(self, out_file, speakers_to_process, emit_error=True):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        
        data_points = []
        for spk in speakers_to_process:
            for item_id, item in spk.items.items():
                if item.get('is_excluded', False):
                    continue
                self._ensure_item_loaded(item, speaker=spk)
                if not item.get('formant_data'):
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
            if emit_error:
                self._emit(False, error="No valid formant data found")
            return False
            
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
        os.makedirs(os.path.dirname(os.path.abspath(out_file)) or ".", exist_ok=True)
        fig.savefig(out_file, bbox_inches='tight')
        plt.close(fig)
        return True

    def do_EOF(self, arg):
        """Exit cleanly when the host closes stdin."""
        if not getattr(self, "quiet", False) and not getattr(self, "json_mode", False):
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
            self._emit(False, error="No valid items to export")
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

            self._emit(True, message=f"Exported batch TextGrids to {out_subdir}")
        else:
            self._write_textgrid(out_path, flat_items)
            self._emit(True, message=f"Exported TextGrid to {out_path}")

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
        tg.append(char_tier)

        tg.write(tg_path)

def _has_cjk_in_args(args_list) -> bool:
    for item in (args_list or []):
        if isinstance(item, str) and re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', item):
            return True
    return False


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Clean UTF-8 BOM from every startup argument token
    raw_argv = [
        a.lstrip('\ufeff\ufffe\u200b') if isinstance(a, str) else a
        for a in raw_argv
    ]
    # Handle multiprocessing bootstrap correctly in frozen Windows builds.
    multiprocessing.freeze_support()

    shared_workspace = False
    custom_ws = None
    project_file = None
    script_file = None
    explicit_lang = None
    quiet_mode = False
    json_mode = False
    unknown_option = None

    cleaned_argv = []
    i = 0
    while i < len(raw_argv):
        arg = raw_argv[i]
        arg_clean = arg.strip() if isinstance(arg, str) else ""
        if not cleaned_argv:
            # Parse global startup options before the subcommand
            if arg_clean == "--shared-workspace":
                shared_workspace = True
                i += 1
                continue
            elif arg_clean == "--workspace" and i + 1 < len(raw_argv):
                custom_ws = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--workspace="):
                custom_ws = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean == "--project" and i + 1 < len(raw_argv):
                project_file = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--project="):
                project_file = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean in ("--script-file", "--batch-cmds") and i + 1 < len(raw_argv):
                script_file = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip()
                i += 2
                continue
            elif arg_clean.startswith("--script-file=") or arg_clean.startswith("--batch-cmds="):
                script_file = arg_clean.split("=", 1)[1].strip()
                i += 1
                continue
            elif arg_clean == "--lang" and i + 1 < len(raw_argv):
                explicit_lang = raw_argv[i + 1].lstrip('\ufeff\ufffe\u200b').strip().lower()
                i += 2
                continue
            elif arg_clean.startswith("--lang="):
                explicit_lang = arg_clean.split("=", 1)[1].strip().lower()
                i += 1
                continue
            elif arg_clean in ("--quiet", "-q"):
                quiet_mode = True
                i += 1
                continue
            elif arg_clean == "--json":
                json_mode = True
                quiet_mode = True
                i += 1
                continue
            elif arg_clean in ("-h", "--help", "-v", "--version") or arg_clean.startswith("--multiprocessing-"):
                cleaned_argv.append(arg_clean)
                i += 1
                continue
            elif arg_clean.startswith("-"):
                unknown_option = arg_clean
                break

        cleaned_argv.append(arg)
        i += 1

    if unknown_option is not None:
        print(json.dumps({
            "success": False,
            "error": f"Unknown startup option: {unknown_option}",
            "hint": "Use --help for manual, or pass a CLI command directly (e.g. `PhonTracerCLI.exe status`)."
        }))
        return 2

    # Language resolution: explicit --lang zh|en, or auto-detect Chinese characters in raw_argv
    if explicit_lang in ("zh", "cn", "chinese", "中文"):
        resolved_lang = "zh"
    elif explicit_lang in ("en", "english", "英文"):
        resolved_lang = "en"
    elif _has_cjk_in_args(raw_argv):
        resolved_lang = "zh"
    else:
        resolved_lang = "en"

    argv = cleaned_argv

    # Handle --version before initializing workspace if no workspace/project work is needed
    if argv and argv[0].strip().lower() in ("-v", "--version", "version") and not script_file:
        print(f"{APP_NAME} CLI {__version__}")
        return 0

    if argv and argv[0].strip().lower().startswith("--multiprocessing-"):
        return 0

    is_stdin_tty = True
    try:
        is_stdin_tty = bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        is_stdin_tty = True

    is_non_interactive = bool(argv or script_file or not is_stdin_tty or quiet_mode or json_mode)

    cli = PhonTracerCLI(
        workspace_dir=custom_ws,
        shared_workspace=shared_workspace,
        project_path=project_file,
        lang=resolved_lang,
        quiet=(quiet_mode or is_non_interactive),
        json_mode=json_mode,
    )
    try:
        # 1. Batch script file execution mode (--script-file / --batch-cmds)
        if script_file is not None:
            if not os.path.exists(script_file):
                cli._emit(
                    False,
                    error=f"Script file not found: {script_file}",
                    failed_line=0,
                    failed_command="",
                )
                return 1
            try:
                with open(script_file, "r", encoding="utf-8-sig") as sf:
                    script_lines = sf.readlines()
            except Exception as e:
                cli._emit(
                    False,
                    error=f"Failed to read script file: {e}",
                    failed_line=0,
                    failed_command="",
                )
                return 1

            cli._defer_persistence = True
            cli._in_script_file = True
            for line_no, raw_line in enumerate(script_lines, start=1):
                cmd_line = PhonTracerCLI._clean_bom_and_whitespace(raw_line)
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                if cmd_line.lower() in ("exit", "quit", "eof"):
                    break
                cli._last_emit_success = True
                cli._last_emit_payload = None
                cli.onecmd(cmd_line)
                if cli._last_emit_success is False:
                    prev_err = ""
                    if isinstance(cli._last_emit_payload, dict):
                        prev_err = cli._last_emit_payload.get("error") or cli._last_emit_payload.get("message") or ""
                    cli._emit(
                        False,
                        f"Batch script aborted at line {line_no}: {cmd_line}",
                        error=prev_err or f"Command failed at line {line_no}: {cmd_line}",
                        failed_line=line_no,
                        failed_command=cmd_line,
                        script_file=script_file,
                    )
                    return 1
            return 0

        # 2. Single command or --help on command line
        if argv:
            first_arg = PhonTracerCLI._clean_bom_and_whitespace(argv[0]).lower()
            if first_arg in ("-h", "--help", "help", "?"):
                if len(argv) > 1:
                    cli.do_help(" ".join(argv[1:]))
                else:
                    cli.do_help("")
                return 0
            if first_arg in ("-v", "--version", "version"):
                print(f"{APP_NAME} CLI {__version__}")
                return 0
            if not first_arg.startswith("-"):
                if len(argv) == 1 and " " in argv[0]:
                    command_line = PhonTracerCLI._clean_bom_and_whitespace(argv[0])
                else:
                    cleaned_tokens = [PhonTracerCLI._clean_bom_and_whitespace(a) for a in argv]
                    command_line = shlex.join(cleaned_tokens)
                cli.onecmd(command_line)
                return 0
            print(json.dumps({
                "success": False,
                "error": f"Unknown startup option: {argv[0]}",
                "hint": "Use --help for manual, or pass a CLI command directly (e.g. `PhonTracerCLI.exe status`)."
            }))
            return 2

        # 3. Non-interactive stdin pipe mode (pure JSON stdout without banner/prompts)
        if not is_stdin_tty or quiet_mode or json_mode:
            cli._defer_persistence = True
            for raw_line in sys.stdin:
                cmd_line = PhonTracerCLI._clean_bom_and_whitespace(raw_line)
                if not cmd_line or cmd_line.startswith("#"):
                    continue
                if cmd_line.lower() in ("exit", "quit", "eof"):
                    break
                stop = cli.onecmd(cmd_line)
                if stop:
                    break
            return 0

        # 4. Interactive TTY REPL mode
        cli.cmdloop()
        return 0
    finally:
        cli.cleanup_workspace()


if __name__ == '__main__':
    raise SystemExit(main())
