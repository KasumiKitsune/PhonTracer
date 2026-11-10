# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Command Help Catalog and Documentation Mixin.
Provides comprehensive multi-language command catalogs, agent guide, and help dispatchers.
"""
from __future__ import annotations

import inspect
import json
import os
import shlex
import sys
from typing import Any, Dict, List, Optional

from modules.cli_app.io_utils import dumps_utf8
from modules.version import APP_NAME, __version__

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
     * 支持格式 (`supported_formats`)：xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, trend, line_chart, kde, wav, merged_wav
     * 高级参数：`scale=t_value|hz`, `groupby=group|label|speaker`, `facet=none|group|speaker|syllable_position|label`, `normalization=Hz|Lobanov|speaker|global`, `chart_group=default|label|speaker|item_tag:...|group_tag:...|meta:...`, `contour_x=normalized|duration`, `format=png|svg|pdf`, `target=active|integrated|separate`
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
     * supported_formats: xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, trend, line_chart, kde, wav, merged_wav
     * key=value options: `scale=t_value|hz`, `groupby=group|label|speaker`, `facet=none|group|speaker|syllable_position|label`, `normalization=Hz|Lobanov|speaker|global`, `chart_group=default|label|speaker|item_tag:...|group_tag:...|meta:...`, `contour_x=normalized|duration`, `format=png|svg|pdf`, `target=active|integrated|separate`
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



class CLIHelpMixin:
    """
    Mixin providing help catalogs, multilingual documentation, and agent guide.
    """
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
            "xlsx", "txt", "textgrid", "collection", "ptwl",
            "contour", "tone_sandhi", "tone_effect_time", "density", "distribution",
            "duration", "quality", "spaghetti_qc", "overview_heatmap",
            "formant_space", "f1_f2", "formant_trajectory", "formant_density",
            "formant_overview_heatmap", "formant_pillai", "formant_table",
            "trend", "line_chart", "kde", "wav", "merged_wav"
        ]
        catalog = {
            "export": {
                "command": "export",
                "usage": "export <format> <output_file_or_dir> [rule] [target] [key=value ...]",
                "description": "导出声学数据表（xlsx/txt/formant_table）、标注文件（textgrid）、Praat 工程集合（collection）、高级字表（ptwl）或科研图表（contour/tone_sandhi/tone_effect_time/density/distribution/duration/quality/spaghetti_qc/overview_heatmap/formant_space/f1_f2/formant_trajectory/formant_density/formant_overview_heatmap/formant_pillai 等）。其中 duration 为各组/各词项音节物理时长对比图；若需绘制以物理时长为横轴的基频曲线，请使用 export contour ... contour_x=duration。",
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
                    "groupby": "group（按声调/组别，默认）| label（按词语）| speaker（按发音人）| item_tag:<标签> | group_tag:<组标签> | meta:<自定义字段名>",
                    "facet": "none（单图不分面）| group（按声调类型分面）| speaker（按发音人分面）| syllable_position（按音节位置分面）| label（按词项分面）",
                    "normalization": "Hz | Lobanov | Bark | ERB | Mel（共振峰归一化）| speaker | global（多发音人声调密度归一化）",
                    "chart_group": "default | label | speaker | review | item_tag:<标签1,标签2> | group_tag:<组标签> | contains_item_tag:<标签> | filter_item_tag:<标签> | meta:<自定义字段名>",
                    "format": "png（默认）| svg | pdf",
                    "contour_x": "normalized（归一化采样点）| duration（真实物理时长横轴基频曲线）",
                    "contour_content": "average（仅均值曲线）| average_individual（均值+个体浅色细线）| average_sd_ci（均值+标准差/置信区间阴影）",
                    "dist_type": "boxplot_violin | start_mid_end | range | variability | duration",
                    "dist_style": "raincloud | boxplot | violinplot",
                    "density_bw": "核密度带宽浮点数（默认 0.15）",
                    "overview_metric": "mean（均值热图）| sd（标准差热图）| combination（调类组合热图）",
                    "overview_show_deviation": "true | false（是否显示与组均值的偏差）",
                    "sandhi_subview": "grid（二字组变调分面方阵，默认）| matrix（组合特征矩阵）| eta2（效应时间进程）",
                    "pillai_subview": "matrix（Pillai 重叠度与欧氏距离矩阵，默认）| raincloud（元音动程与时长雨云分布）",
                },
                "examples": [
                    "export xlsx out.xlsx continuous integrated",
                    "export ptwl upgraded.ptwl",
                    "export contour tone_contour.svg target=integrated scale=t_value groupby=group facet=none format=svg",
                    "export contour tone_phys_dur.png target=integrated scale=t_value groupby=group contour_x=duration",
                    "export duration syllable_duration.png target=integrated groupby=group",
                    "export tone_sandhi sandhi_grid.png target=integrated scale=t_value",
                    "export density tone_density.png target=integrated normalization=speaker facet=group",
                    "export formant_space vowel_space_lobanov.png target=integrated groupby=label normalization=Lobanov",
                    "export formant_pillai vowel_pillai.png target=integrated groupby=label normalization=Lobanov",
                    "export overview_heatmap overview.png target=integrated groupby=label overview_metric=mean",
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
                "description": "结合发音人中位基频生理声道先验，自动评估当前发音人样本的共振峰追踪质量评分，给出推荐档 (recommended/reco)、保守复核档 (conservative/anti)、高分辨率档 (high_resolution/fine) 三档参数并支持直接应用。",
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
                "export": "export <format> <output_file> [continuous|per_group] [active|integrated|separate] [key=val ...]  # 单项导出；formats: xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, trend, line_chart, kde, wav, merged_wav；options: scale=t_value|hz, groupby=group|label|speaker, facet=none|group|speaker|syllable_position|label, normalization=Hz|Lobanov|speaker|global, chart_group=default|label|speaker|item_tag:...|group_tag:...|meta:..., contour_x=normalized|duration, format=png|svg|pdf, target=active|integrated|separate",
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


__all__ = [
    "get_cli_help_text",
    "CLIHelpMixin",
]
