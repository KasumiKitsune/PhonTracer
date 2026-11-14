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
              {APP_NAME} 语音学标注与声学分析系统 V{__version__} 命令行手册
================================================================================
AI 操作守则：优先通过本 CLI 完成全部操作；先参考 `agent_guide` 或 `help all` 确认参数语法。

--- 全局启动选项 (Global Startup Options) ---
* `--script-file <cmds.txt>` / `--batch-cmds <file>`: 【AI / 批处理全流程首选】将建库、音频导入、字表切分、`detect_f0 recommended`、`fix_warnings --apply`、`stats` 与 `export_bundle` 写入 UTF-8 脚本一次跑完（遇 `success: false` 立即中止并返回 `failed_line` / `failed_command`）。
* `--project <filepath.teproj>`: 绑定持久化工程（适合已有工程增量微调，或与 `--script-file` 联用）。
* `--workspace <dir>`: 绑定持久化工作区目录（自动读写 `<dir>/project.json`）。
* `--lang zh|en`: 指定引导与回执语言（命令行含中文自动切为 `zh`）。
* `--quiet` / `-q`: 静默模式，抑制提示符与警告，保持 `stdout` 纯净 JSON。
* `--json`: 强制纯净单行 JSON 标准流模式（含 `--help`）。
* `--version` / `-v`: 查看版本信息。

--- 工作流生命周期 ---
1. 多发音人管理：`speakers` | `add_speaker <名> [gender=female|male|child] [--switch]` | `switch_speaker <名或ID>` | `rename_speaker [<旧>] <新>` | `remove_speaker <名或ID>`
2. 音频工具箱预处理（可选，独立于当前会话或配合会话使用）：
   - `tool_split <长音频> [字表.txt|.ptwl|.csv] <输出目录> [buffer_sec] [trim=true|false] [wordlist=none]`
     * 支持 `.ptwl`/`.csv`/杂乱 `.txt` 字表对齐切分，也支持省略字表（或 `wordlist=none`）无字表纯拆音；段数与词数不一致时显式告警并保留多余音频为 `extra_*`。
   - `tool_merge <输出.wav> <间隔秒> <音频或目录或通配符...> [sr=<采样率Hz>]`
     * 同采样率音频（如 24 kHz）默认保持原采样率无损拼接，混合采样率才自动对齐至最高采样率。
   - `tool_sort_batch <字表> [音频路径或目录...] [--apply]`
     * 按字表模糊排序独立音频（加 `--apply` 可直接载入当前发音人会话）。
   - `tool_wordlist_convert <输入字表.txt|.csv|.ptwl> <输出.ptwl> [title=<标题>]`
3. 导入音频与对齐字词表 / TextGrid：
   - `load_long <长音频路径>` | `load_batch <文件/目录/*.wav...>`
   - `apply_wordlist <字表.txt|.ptwl|.csv> [fuzzy|order]` (兼容人类草稿注释 `//`、`=== 标题 ===`、括号/冒号组头、行内编号 `1.`、`- ` 列表符、`/` 分隔词)
   - `apply_textgrid <TextGrid路径>` | `batch_textgrid_import <TextGrid文件或目录...>`
   - `textgrid_inspect` / `textgrid_preview` / `textgrid_convert`
4. 声学参数探测与异常修复：
   - `detect_f0 [recommended|conservative|fine]` | `detect_formants [reco|anti|fine]`
   - `fix_warnings [--apply] [scope=active|all] [pitch_floor=<Hz>] [voicing_threshold=<val>]`
     * 清零工程声学/切分警告后，`quality` 质控图中不再出现红色声学警告线（仅以琥珀色虚线展示组内 >2.8σ 统计偏离参考）。
   - `set_params 键=值 ...` | `modify_bounds <ID> <start> <end>` | `modify_params <ID> 键=值 ...` | `recalculate`
5. 统计摘要与一键导出闭环：
   - `stats [by=group|item|speaker] [scope=active|integrated] [out=<.json|.md>]`
   - `export <格式> <输出路径> [continuous|per_group] [active|integrated|separate] [key=val ...]`
     * 支持格式：xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, trend, line_chart, kde, wav, merged_wav
     * 多页图表仅输出 `*_第1页.png`、`*_第2页.png` 等分页文件，不再生成易误导漏看的重复无后缀同名图。
   - `export_bundle <输出目录> [scope=active|integrated|separate] [format=png|svg|pdf] [charts=auto|all|<列表>] [collection=true|false]`
     * 一站式导出 `.teproj`、`.ptwl`、`.Collection`、`acoustic_data.xlsx`、`acoustic_points.txt`、`acoustic_stats.json`、常规与双字调自适应图表（自动包含 `duration`、`tone_sandhi`、`tone_effect_time`）及 `report/研究方法报告.md`。
   - `report_export <路径.teproj> <输出目录> [md|excel|both] [include_cache=true|false]`

--- 核心命令速查 (`help all` 查看全量命令字典，`help tools` 查看工具箱命令，`<cmd> ?` 查看单项详情) ---
status | list_items | speakers | add_speaker | switch_speaker | rename_speaker | remove_speaker
load_long | load_batch | apply_wordlist | apply_textgrid | batch_textgrid_import | import_batch_and_export
textgrid_inspect | textgrid_preview | textgrid_convert | set_params | modify_bounds | modify_params | recalculate
detect_f0 | detect_formants | fix_warnings | stats | export | export_bundle | report_export
tool_split | tool_merge | tool_sort_batch | tool_wordlist_convert
project_export | project_import | project_save | project_load | project_preview | project_convert_zip | autosave
list_scripts | script_info | run_script | script_save | script_delete | script_import | script_export | script_prompt | lang
================================================================================
"""
    return f"""
================================================================================
                     {APP_NAME} CLI MANUAL V{__version__} (Agent & Batch Guide)
================================================================================
AI operating rule: prefer this CLI as the control surface. Check `agent_guide` or
`help all` first instead of editing source files or bypassing the CLI.

--- GLOBAL STARTUP OPTIONS ---
* `--script-file <commands.txt>` / `--batch-cmds <file>`: [PRIMARY DEFAULT FOR AI & BATCH WORKFLOWS] Run setup, audio ingest, wordlist alignment, `detect_f0 recommended`, `fix_warnings --apply`, `stats`, and `export_bundle` in 1 process call; stops on `success: false` with `failed_line` and `failed_command`.
* `--project <filepath.teproj>`: Bind persistent `.teproj` archive (auto-loads if existing, auto-saves on exit; combine with `--script-file` or use for incremental single-step edits).
* `--workspace <dir>`: Bind persistent workspace directory (`<dir>/project.json`).
* `--lang zh|en`: Set CLI language (auto-switches to `zh` when Chinese args are detected).
* `--quiet` / `-q`: Suppress banners/warnings for clean JSON stdout.
* `--json`: Force pure single-line JSON output (including `--help`).
* `--version` / `-v`: Print CLI version.

--- WORKFLOW LIFECYCLE ---
1. Multi-Speaker Management:
   - `speakers` | `add_speaker <name> [gender=female|male|child] [--switch]` | `switch_speaker <name_or_id>` | `rename_speaker [<old>] <new>` | `remove_speaker <name_or_id>`
2. Audio & Wordlist Toolkit Utilities:
   - `tool_split <long_audio> [wordlist.txt|.ptwl|.csv] <output_dir> [buffer_sec] [trim=true|false] [wordlist=none]`
     * Supports `.ptwl`, `.csv`, messy `.txt`, or pure unlabeled splitting (`tool_split <audio> <out_dir>` / `wordlist=none`); warns explicitly on segment/word count mismatch and preserves extra segments as `extra_*` WAVs without silent truncation.
   - `tool_merge <output.wav> <gap_sec> <audio_or_dir_or_glob...> [sr=<Hz>]`
     * Preserves native sampling rate when inputs share the same rate (e.g., 24 kHz); resamples only when mixed or `sr=` is specified.
   - `tool_sort_batch <wordlist> [audio_paths...] [--apply]`
   - `tool_wordlist_convert <in.txt|.csv|.ptwl> <out.ptwl> [title=<title>]`
3. Load Audio & Apply Wordlist / TextGrid:
   - `load_long <filepath>` | `load_batch <file1_or_dir_or_glob> ...`
   - `apply_wordlist <wordlist.txt|.ptwl|.csv> [fuzzy|order]` (fault-tolerant parser handles `//` comments, `=== Title ===` banners, `(Group)` / `Group:` headers, `1. 2.` numbering, `- ` bullets, `/` delimiters)
   - `apply_textgrid <textgrid_filepath>` | `batch_textgrid_import <tg_file_or_dir...>`
   - `textgrid_inspect` / `textgrid_preview` / `textgrid_convert`
4. Acoustic Detection, Tuning & Warning Fixes:
   - `detect_f0 [recommended|conservative|fine]` | `detect_formants [reco|anti|fine]`
   - `fix_warnings [--apply] [scope=active|all] [pitch_floor=<Hz>] [voicing_threshold=<val>]`
     * Clears acoustic/segmentation warnings; `quality` charts distinguish red acoustic-warning curves from amber dashed within-group statistical reference curves (>2.8σ).
   - `set_params key=value ...` | `modify_bounds <id> <start> <end>` | `modify_params <id> key=value ...` | `recalculate`
5. Statistics, Export & One-Step Bundle:
   - `stats [by=group|item|speaker] [scope=active|integrated] [out=<.json|.md>]`
   - `export <format> <output_path> [continuous|per_group] [active|integrated|separate] [key=val ...]`
     * supported_formats: xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, trend, line_chart, kde, wav, merged_wav
     * Multi-page charts emit `*_第1页.png`, `*_第2页.png`, etc., without generating a duplicate unsuffixed base image.
   - `export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg|pdf] [charts=auto|all|<list>] [collection=true|false]`
     * One-stop export of `.teproj`, `.ptwl`, `.Collection`, `acoustic_data.xlsx`, `acoustic_points.txt`, `acoustic_stats.json`, core + auto-adapted disyllabic charts (`duration`, `tone_sandhi`, `tone_effect_time`), and `report/研究方法报告.md`.
   - `report_export <path.teproj> <out_dir> [md|excel|both] [include_cache=true|false]`

--- ALL COMMANDS REFERENCE (`help all` for full schema dict, `help tools` for toolkit commands, `<cmd> ?` for single command) ---
status | list_items | speakers | add_speaker | switch_speaker | rename_speaker | remove_speaker
load_long | load_batch | apply_wordlist | apply_textgrid | batch_textgrid_import | import_batch_and_export
textgrid_inspect | textgrid_preview | textgrid_convert | set_params | modify_bounds | modify_params | recalculate
detect_f0 | detect_formants | fix_warnings | stats | export | export_bundle | report_export
tool_split | tool_merge | tool_sort_batch | tool_wordlist_convert
project_export | project_import | project_save | project_load | project_preview | project_convert_zip | autosave
list_scripts | script_info | run_script | script_save | script_delete | script_import | script_export | script_prompt | lang
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
            "status": {
                "command": "status",
                "category": "inspect",
                "usage": "status",
                "description": "查询当前工程状态、激活发音人、词条总数、声学警告计数与全局提取参数。",
                "key_value_options": {},
                "examples": ["status"],
            },
            "list_items": {
                "command": "list_items",
                "category": "inspect",
                "usage": "list_items [all|warnings|<group_name>] [limit=<N>]",
                "description": "列出当前发音人已切分词条边界、时长、采样预览与声学警告详情。",
                "key_value_options": {
                    "filter": "all（全部，默认）| warnings（仅含声学/切分警告项）| <组名>",
                    "limit": "最大返回条数（可选）",
                },
                "examples": ["list_items", "list_items warnings", "list_items 11_阴阴"],
            },
            "speakers": {
                "command": "speakers",
                "category": "speaker",
                "aliases": ["speaker", "speakers", "add_speaker", "switch_speaker", "rename_speaker", "remove_speaker"],
                "usage": "speakers | add_speaker <name> [gender=female|male|child] [--switch] | switch_speaker <name_or_id> | rename_speaker [<old>] <new> | remove_speaker <name_or_id>",
                "description": "多发音人管理：列表查询、新增（首次自动替换空默认发音人）、切换、重命名与删除。",
                "subcommands": {
                    "speakers": "列出所有发音人（id, name, active, items_count）",
                    "add_speaker": "add_speaker <name> [gender=female|male|child] [--switch]",
                    "switch_speaker": "switch_speaker <name_or_id>",
                    "rename_speaker": "rename_speaker [<old_name_or_id>] <new_name>",
                    "remove_speaker": "remove_speaker <name_or_id>",
                },
                "key_value_options": {
                    "gender": "female | male | child",
                    "--switch": "新增后立即切换为当前激活发音人",
                },
                "examples": [
                    "add_speaker F01_女声 gender=female --switch",
                    "add_speaker M01_男声 gender=male --switch",
                    "speakers",
                ],
            },
            "load_long": {
                "command": "load_long",
                "category": "ingest",
                "usage": "load_long <filepath>",
                "description": "加载单条长音频并切换当前发音人为长音频模式（long）。",
                "supported_formats": ["wav", "mp3", "flac", "ogg"],
                "key_value_options": {},
                "examples": ["load_long 女1.wav"],
            },
            "load_batch": {
                "command": "load_batch",
                "category": "ingest",
                "usage": "load_batch <file1_or_dir_or_glob> [file2 ...] [recursive=true|false]",
                "description": "加载独立短音频（支持文件列表、目录自动扫描或 *.wav 通配符，按自然顺序排序）。",
                "supported_formats": ["wav", "mp3", "flac", "ogg"],
                "key_value_options": {
                    "recursive": "true | false（目录是否递归扫描，默认 false）",
                },
                "examples": ["load_batch ./male_split", "load_batch ./wavs/*.wav"],
            },
            "apply_wordlist": {
                "command": "apply_wordlist",
                "category": "ingest",
                "usage": "apply_wordlist <wordlist_filepath> [fuzzy|order]",
                "description": "导入并应用字词表（原生兼容杂乱草稿 .txt、结构化 .txt、.ptwl 高级字表与 .csv），自动剥离注释/标题/编号并执行长音频切分或短音频匹配。",
                "supported_formats": ["txt", "ptwl", "csv"],
                "positional_args": {
                    "wordlist_filepath": "字表路径（.txt / .ptwl / .csv）",
                    "match_mode": "fuzzy（默认模糊匹配文件名）| order（按物理顺序匹配）",
                },
                "key_value_options": {
                    "match_mode": "fuzzy | order",
                },
                "examples": ["apply_wordlist raw_draft.txt fuzzy", "apply_wordlist standard.ptwl order"],
            },
            "tool_split": {
                "command": "tool_split",
                "category": "tools",
                "usage": "tool_split <long_audio> [wordlist.txt|.ptwl|.csv] <output_dir> [buffer_sec] [trim=true|false] [wordlist=none]",
                "description": "将长音频按 VAD 能量切分为独立短音频（保持原采样率）。支持 .ptwl/.csv/杂乱 .txt 字表对齐命名，也支持省略字表或 wordlist=none 进行无字表纯拆音；当实际检出段数与字表词数不一致时明确返回 warning 并保留多余片段为 NNN_extra_XX.wav，绝不静默截断。",
                "supported_formats": ["ptwl", "csv", "txt", "none"],
                "positional_args": {
                    "long_audio": "输入长音频路径",
                    "wordlist": "可选字表路径（.ptwl/.csv/.txt）；省略或传 none/--no-wordlist 则按 001_segment.wav 纯拆音",
                    "output_dir": "输出短音频目录路径",
                    "buffer_sec": "切分前后静音缓冲秒数（默认 0.10）",
                    "trim": "true | false（是否按振幅阈值精修首尾静音，默认 true）",
                },
                "key_value_options": {
                    "wordlist": "字表路径，或传 none 进行无字表纯拆音",
                    "out": "输出目录路径（同位置参数 output_dir）",
                    "buffer": "前后缓冲秒数（默认 0.10）",
                    "trim": "true | false（默认 true）",
                    "merge_thresh": "词内短暂停顿合并阈值秒数（默认 0.18 双峰自适应）",
                    "min_dur": "最短有效语音段秒数（默认 0.10）",
                    "db": "可选 VAD 峰值能量回落阈值 dB（不传时按信噪比自适应）",
                    "--no-wordlist": "显式开启无字表纯拆音模式",
                },
                "examples": [
                    "tool_split 男1.wav 实验字表.ptwl ./male_split",
                    "tool_split 男1.wav 草稿字表.txt ./male_split buffer=0.10 trim=true",
                    "tool_split 男1.wav ./unlabeled_segments",
                ],
            },
            "tool_merge": {
                "command": "tool_merge",
                "category": "tools",
                "usage": "tool_merge <output.wav> <gap_sec> <audio1_or_dir_or_glob> [audio2 ...] [sr=<Hz>]",
                "description": "将多条短音频（支持文件列表、目录或通配符）按指定静音间隔拼接为单条 WAV。同采样率输入（如 24000 Hz）保持原采样率无损输出，仅在混合采样率或显式指定 sr= 时重采样。",
                "positional_args": {
                    "output.wav": "输出合并 WAV 路径",
                    "gap_sec": "片段间静音间隔秒数（如 0.5）",
                    "inputs": "输入音频文件、目录或通配符列表",
                },
                "key_value_options": {
                    "out": "输出 WAV 路径",
                    "gap": "静音间隔秒数（默认 0.5）",
                    "sr": "可选目标采样率 Hz（默认保持原音频采样率一致）",
                },
                "examples": [
                    "tool_merge merged_male.wav 0.5 ./male_split",
                    "tool_merge merged.wav gap=0.4 a01.wav a02.wav",
                ],
            },
            "tool_sort_batch": {
                "command": "tool_sort_batch",
                "category": "tools",
                "usage": "tool_sort_batch <wordlist.txt|.ptwl|.csv> [audio_paths_or_dir...] [--apply]",
                "description": "按字表（支持 .ptwl/.csv/杂乱 .txt）对批量短音频做模糊文件名对齐排序；附加 --apply 可直接载入当前发音人并绑定字表。",
                "supported_formats": ["txt", "ptwl", "csv"],
                "key_value_options": {
                    "wordlist": "字表文件路径",
                    "--apply": "排序后直接载入当前激活发音人会话并应用字表",
                },
                "examples": [
                    "tool_sort_batch raw_wordlist.txt ./male_split --apply",
                    "tool_sort_batch standard.ptwl ./wavs/*.wav",
                ],
            },
            "tool_wordlist_convert": {
                "command": "tool_wordlist_convert",
                "category": "tools",
                "aliases": ["wordlist_convert"],
                "usage": "tool_wordlist_convert <input_wordlist> <output.ptwl> [title=<字表标题>]",
                "description": "将杂乱草稿 (.txt)、结构化文本或表格 (.csv) 容错解析并保存为标准 v2 高级字表 (.ptwl)，自动过滤注释/分隔符/组头/编号。",
                "supported_formats": ["txt", "csv", "ptwl"],
                "key_value_options": {
                    "title": "指定生成的 .ptwl 字表标题",
                    "out": "输出 .ptwl 路径",
                },
                "examples": [
                    "tool_wordlist_convert raw_draft.txt standard.ptwl title=双字调实验",
                ],
            },
            "detect_f0": {
                "command": "detect_f0",
                "category": "analysis",
                "usage": "detect_f0 [recommended|conservative|fine]",
                "description": "自动探测当前发音人基频分布（P5/P50/P95），传入 recommended 一步应用最佳 pitch_floor / pitch_ceiling。",
                "supported_formats": ["recommended", "conservative", "fine"],
                "key_value_options": {},
                "examples": ["detect_f0 recommended", "detect_f0"],
            },
            "detect_formants": {
                "command": "detect_formants",
                "category": "analysis",
                "usage": "detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution|reco|anti|fine]",
                "description": "结合生理声道先验评估共振峰追踪质量，给出推荐档 (reco)、保守档 (anti)、精细档 (fine) 并支持直接应用。",
                "supported_formats": ["recommended", "conservative", "high_resolution", "reco", "anti", "fine"],
                "key_value_options": {
                    "apply": "true（默认）| false（或 --no-apply）",
                    "preset": "recommended/reco | conservative/anti | high_resolution/fine",
                },
                "examples": ["detect_formants recommended", "detect_formants reco --no-apply"],
            },
            "fix_warnings": {
                "command": "fix_warnings",
                "category": "analysis",
                "usage": "fix_warnings [--apply|--no-apply] [scope=active|all|integrated] [pitch_floor=<Hz>] [voicing_threshold=<val>]",
                "description": "扫描并自动修复倍频跳变 (octave_jump)、低基频覆盖率 (low_f0_coverage)、双音节谷值不明 (no_clear_valley) 与过短音段 (tiny_segment)。清零警告后 quality 质控图红线同步清零（图中琥珀色虚线仅代表组内 >2.8σ 统计偏离参考，非声学提取故障）。",
                "key_value_options": {
                    "apply": "true（或 --apply，直接应用并重算）| false（仅诊断）",
                    "scope": "active（默认当前发音人）| all / integrated（全部发音人）",
                    "pitch_floor": "可选修复音高下限 Hz",
                    "voicing_threshold": "可选修复浊音阈值（默认下探至 0.15）",
                },
                "examples": ["fix_warnings --apply scope=all", "fix_warnings apply=false scope=all"],
            },
            "set_params": {
                "command": "set_params",
                "category": "analysis",
                "usage": "set_params [key=value ...]",
                "description": "查看或更新当前发音人全局提取参数。",
                "key_value_options": {
                    "analysis_mode": "f0 | formant",
                    "pts": "插值采样点数（默认 11）",
                    "db": "VAD 能量阈值（默认 60.0）",
                    "skip_front": "跳过声母秒数（默认 0.0）",
                    "pitch_floor": "基频下限 Hz（默认 75）",
                    "pitch_ceiling": "基频上限 Hz（默认 600）",
                    "voicing_threshold": "浊音阈值（默认 0.25）",
                    "trim_silence": "true | false（默认 true）",
                    "formant_max_hz": "共振峰上限 Hz（默认 5500.0）",
                    "formant_count": "共振峰数量（默认 5）",
                    "formant_window_length": "共振峰窗长秒数（默认 0.025）",
                    "formant_pre_emphasis": "预加重 Hz（默认 50.0）",
                    "formant_sample_strategy": "整段11点 | 中段均值",
                    "show_f3": "true | false",
                },
                "examples": ["set_params pitch_floor=60 pitch_ceiling=420 voicing_threshold=0.20"],
            },
            "modify_bounds": {
                "command": "modify_bounds",
                "category": "analysis",
                "usage": "modify_bounds <item_id> <start_sec> <end_sec>",
                "description": "手动微调指定词条的起止时间边界并自动重算特征。",
                "key_value_options": {},
                "examples": ["modify_bounds item_1 0.42 0.98"],
            },
            "modify_params": {
                "command": "modify_params",
                "category": "analysis",
                "usage": "modify_params <item_id> key=value ...",
                "description": "为单个词条覆盖专属声学参数（如 pitch_floor、pitch_ceiling、voicing_threshold）并重算。",
                "key_value_options": {
                    "pitch_floor": "单条音高下限 Hz",
                    "pitch_ceiling": "单条音高上限 Hz",
                    "voicing_threshold": "单条浊音阈值",
                },
                "examples": ["modify_params item_5 pitch_floor=45 voicing_threshold=0.15"],
            },
            "recalculate": {
                "command": "recalculate",
                "category": "analysis",
                "usage": "recalculate [scope=active|all]",
                "description": "使用最新参数批量重算词条声学特征与插值曲线。",
                "key_value_options": {"scope": "active | all"},
                "examples": ["recalculate"],
            },
            "stats": {
                "command": "stats",
                "category": "export",
                "usage": "stats [by=group|item|speaker] [scope=active|integrated] [out=<path.json|path.md>]",
                "description": "输出结构化声学统计摘要 JSON（样本数、时长、首/中/尾 F0、极差、均值±SD、五度值 T 轨迹与编码、P50 基线、ANOVA 及共振峰）。",
                "key_value_options": {
                    "by": "group（默认按组）| item（按词条）| speaker（按发音人）",
                    "scope": "active（当前发音人）| integrated（合并全发音人）",
                    "out": "可选保存路径（.json 或 .md）",
                },
                "examples": ["stats by=group scope=integrated", "stats by=speaker scope=integrated"],
            },
            "export": {
                "command": "export",
                "category": "export",
                "usage": "export <format> <output_file_or_dir> [rule] [target] [key=value ...]",
                "description": "导出数据表（xlsx/txt/formant_table）、标注（textgrid）、Praat 集合（collection）、字表（ptwl）、音频（wav/merged_wav）或科研图表（含双字调 tone_sandhi/tone_effect_time/duration 等）。分页图表仅生成 *_第1页.png、*_第2页.png 等并在返回 JSON 的 files 列出，不生成重复无后缀首页图。",
                "supported_formats": supported_export_formats,
                "positional_args": {
                    "format": "导出格式（见 supported_formats）",
                    "output_file_or_dir": "输出文件或目录路径（也可通过 out= 传入）",
                    "rule": "continuous（默认连续编号）| per_group（按组编号）| 数值（wav/merged_wav 缓冲或间隔秒数）",
                    "target": "active（默认当前发音人）| integrated / all（多发音人合并）| separate（按发音人分目录/分文件导出）",
                },
                "key_value_options": {
                    "out": "输出文件或目录路径（同第 2 位置参数）",
                    "target": "active | integrated (或 all) | separate（亦可用 scope= 别名）",
                    "scope": "同 target（active | integrated | separate，与 export_bundle/stats 保持一致）",
                    "rule": "continuous | per_group",
                    "scale": "t_value（五度值 0-5，默认）| hz（原始 Hz）",
                    "groupby": "group（默认）| label | speaker | item_tag:<标签> | group_tag:<组标签> | meta:<字段>",
                    "facet": "none | group | speaker | syllable_position | label",
                    "normalization": "Hz | Lobanov | Bark | ERB | Mel | speaker | global",
                    "chart_group": "default | label | speaker | review | item_tag:<标签> | group_tag:<组标签> | meta:<字段>",
                    "format": "png（默认）| svg | pdf",
                    "contour_x": "normalized（归一化等分点）| duration（真实物理时长横轴）",
                    "contour_content": "average | average_individual | average_sd_ci",
                    "dist_type": "boxplot_violin | start_mid_end | range | variability | duration",
                    "dist_style": "raincloud | boxplot | violinplot",
                    "density_bw": "核密度带宽（默认 0.15）",
                    "overview_metric": "mean | sd | combination",
                    "overview_show_deviation": "true | false",
                    "sandhi_subview": "grid（二字组变调方阵）| matrix（组合特征矩阵）| eta2（效应时间进程）",
                    "pillai_subview": "matrix | raincloud",
                    "binary": "true（默认二进制 ooBinaryFile）| false（ooTextFile，用于 collection）",
                    "buffer": "wav 导出前后缓冲秒数（默认 0.05）",
                    "gap": "merged_wav 拼接静音间隔秒数（默认 0.5）",
                    "sr": "merged_wav 可选目标采样率 Hz（默认保持原采样率）",
                },
                "examples": [
                    "export xlsx out.xlsx scope=integrated",
                    "export collection workspace.Collection scope=integrated",
                    "export tone_sandhi sandhi.png scope=integrated scale=t_value",
                    "export tone_effect_time eta2.png scope=integrated",
                    "export duration dur.png scope=integrated groupby=group",
                    "export contour contour.png scope=integrated contour_x=duration",
                ],
            },
            "export_bundle": {
                "command": "export_bundle",
                "category": "export",
                "usage": "export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg|pdf] [title=<name>] [charts=auto|all|<list>] [collection=true|false]",
                "description": "一站式打包导出科研闭环产物：工程 (.teproj)、高级字表 (.ptwl)、Praat 集合 (.Collection)、数据表 (acoustic_data.xlsx)、采样文本 (acoustic_points.txt)、统计摘要 (acoustic_stats.json)、常规+双字调自适应图表集 (charts/，含 contour/distribution/density/duration/quality/overview_heatmap 及双字调自动附加的 tone_sandhi/tone_effect_time) 与研究方法报告 (report/研究方法报告.md)。",
                "key_value_options": {
                    "out": "输出目录路径（同首位置参数）",
                    "scope": "active | integrated（多发音人默认 integrated）| separate（亦可用 target= 别名）",
                    "format": "png（默认）| svg | pdf",
                    "title": "自定义导出的 .teproj / .ptwl / .Collection 文件名前缀",
                    "charts": "auto（默认，自动检测多音节语料并附加 tone_sandhi、tone_effect_time 与 duration）| all | 逗号分隔图表列表",
                    "collection": "true（默认自动导出 Praat .Collection）| false",
                    "binary": "true（默认二进制 .Collection）| false（文本格式 .Collection）",
                },
                "examples": [
                    "export_bundle ./deliverables scope=integrated format=png",
                    "export_bundle ./deliverables scope=integrated title=双字组16声调实验 charts=auto collection=true",
                ],
            },
            "report_export": {
                "command": "report_export",
                "category": "export",
                "usage": "report_export <path.teproj> <out_dir> [format=md|excel|both] [include_cache=true|false]",
                "description": "从 .teproj 工程导出包含核心声学指标分组统计摘要表与单因素方差分析 (ANOVA) 的研究方法报告及数据档案。",
                "key_value_options": {
                    "format": "md | excel | both（默认 both）",
                    "include_cache": "true | false",
                },
                "examples": ["report_export work.teproj ./reports format=md"],
            },
            "project_export": {
                "command": "project_export",
                "category": "project",
                "aliases": ["project_import"],
                "usage": "project_export <path.teproj> | project_import <path.teproj>",
                "description": "将当前工程打包保存为 .teproj 档案，或从已有 .teproj 档案载入恢复会话。",
                "key_value_options": {},
                "examples": ["project_export experiment.teproj", "project_import experiment.teproj"],
            },
        }
        # Alias mappings
        catalog["speaker"] = catalog["speakers"]
        catalog["add_speaker"] = catalog["speakers"]
        catalog["switch_speaker"] = catalog["speakers"]
        catalog["rename_speaker"] = catalog["speakers"]
        catalog["remove_speaker"] = catalog["speakers"]
        catalog["wordlist_convert"] = catalog["tool_wordlist_convert"]
        catalog["project_import"] = catalog["project_export"]
        return catalog

    def _emit_subcommand_help(self, topic: str) -> bool:
        raw_parts = [p for p in self._clean_bom_and_whitespace(topic).split() if p]
        topic_clean = (raw_parts[0] if raw_parts else "").lower().lstrip("-")
        verbose_all = any(p.lower().lstrip("-") in ("full", "verbose", "detail") for p in raw_parts[1:])

        catalog = self._get_subcommand_help_catalog()
        alias_keys = {
            "speaker", "add_speaker", "switch_speaker", "rename_speaker", "remove_speaker",
            "wordlist_convert", "project_import",
        }

        category_aliases = {
            "tools": "tools",
            "tool": "tools",
            "toolkit": "tools",
            "ingest": "ingest",
            "analysis": "analysis",
            "project": "project",
        }

        if topic_clean in ("all", "*") or topic_clean in category_aliases:
            filter_cat = category_aliases.get(topic_clean)
            selected_commands = {}
            for k, v in catalog.items():
                if k in alias_keys:
                    continue
                if filter_cat and v.get("category") != filter_cat:
                    continue
                if verbose_all or filter_cat:
                    item_info = dict(v)
                    item_info["syntax"] = item_info.get("usage", k)
                    if "key_value_options" in item_info:
                        item_info["options"] = item_info["key_value_options"]
                else:
                    # Compact schema for `help all` so terminal / LLM tool outputs never truncate
                    opts = v.get("key_value_options") or {}
                    item_info = {
                        "command": v.get("command", k),
                        "syntax": v.get("usage", k),
                        "description": v.get("description", ""),
                        "options": list(opts.keys()) if len(opts) > 8 else opts,
                    }
                    if "supported_formats" in v:
                        item_info["supported_formats"] = v["supported_formats"]
                    if "aliases" in v:
                        item_info["aliases"] = v["aliases"]
                selected_commands[k] = item_info

            cmd_label = "all" if filter_cat is None else filter_cat
            self._emit(
                True,
                f"Subcommand help schemas ({cmd_label}); run `help <cmd>` for full examples & option descriptions.",
                command=cmd_label,
                syntax=f"help {cmd_label}",
                count=len(selected_commands),
                commands=selected_commands,
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
            usage = next(
                (ln.replace("Usage:", "").replace("用法：", "").strip() for ln in lines if ln.lower().startswith("usage:") or ln.startswith("用法：")),
                topic_clean,
            )
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
                f"No help found for '{arg_clean}'. Use 'help all', 'help tools', or 'agent_guide'.",
                error=f"Unknown help topic: {arg_clean}",
                hint="可用帮助主题：all, tools, status, list_items, speakers, load_long, load_batch, apply_wordlist, tool_split, tool_merge, tool_sort_batch, tool_wordlist_convert, detect_f0, detect_formants, fix_warnings, set_params, stats, export, export_bundle, report_export, project_export。",
            )

    def do_agent_guide(self, arg):
        """
        Show operating rules and workflow paths for AI agents.
        Usage: agent_guide
        """
        self._emit(
            True,
            "AI 极速批处理指南：请将建库、音频拆合/加载、字表应用、detect_f0 recommended、fix_warnings --apply、stats 与 export_bundle 写入 UTF-8 剧本文件，通过 `python cli.py --project <session.teproj> --script-file <cmds.txt>` 一次跑完全流程！",
            primary_recommended_mode="--script-file <cmds.txt> (配合 --project <session.teproj> 与 export_bundle <out_dir> 一步完成全流程)",
            stateful_session_best_practices={
                "title": "有状态会话与极速批处理最佳实践",
                "primary_batch_script_mode": "将多发音人建库、tool_split/tool_merge 预处理、load_long/load_batch、apply_wordlist、detect_f0 recommended、fix_warnings --apply scope=all、stats 与 export_bundle 写入 cmds.txt，单次执行 `python cli.py --project <session.teproj> --script-file cmds.txt`（遇错立即停在 failed_line，结束自动持久化 .teproj 1 次）。",
                "deterministic_batch": "长音频+短音频混合建库与科研交付优先使用 `--script-file` + `export_bundle <out_dir> scope=integrated`。",
                "single_step_interactive": "仅在对已有工程做增量微调或单步核查时使用 `python cli.py --project <session.teproj> <cmd>`。",
                "clean_output": "自动化调用可附加 `--quiet` 或 `--json` 保持 stdout 100% 纯净单行 JSON。",
                "anti_redundancy_rules": [
                    "1. 无需多轮查询 `<cmd> ?`：本响应 `core_command_cheatsheet` 已涵盖全部核心与 `tool_*` 命令语法；需全量字典可单次运行 `help all` 或 `help tools`。",
                    "2. 无需沙盒预演：直接编写完整 `pipeline.txt` 执行一次 `--project <目标.teproj> --script-file pipeline.txt`。",
                    "3. 字表一步直通：`apply_wordlist`、`tool_split`、`tool_wordlist_convert` 统一兼容杂乱草稿 `.txt`（`//` 注释、`=== 标题 ===`、括号/冒号组头、`1.` 编号、`- ` 列表、`/` 分隔词）、`.csv` 与 `.ptwl`。",
                    "4. 一站式闭环免补导：`export_bundle <out_dir> scope=integrated` 自动导出 `.teproj`、`.ptwl`、`.Collection`、`acoustic_data.xlsx`、`acoustic_points.txt`、`acoustic_stats.json`、常规及双字调图表（自动含 `duration`、`tone_sandhi`、`tone_effect_time`）与 `研究方法报告.md`；分页图表仅输出 `*_第1页.png`、`*_第2页.png` 等，无重复同名首页图。",
                    "5. 质控口径说明：`fix_warnings --apply scope=all` 清零的是工程声学/切分警告（对应 `quality` 图中红色实线清零）；`quality` 图中琥珀色虚线仅表示组内 >2.8σ 统计分布偏离参考，属正常个体音高离散提示而非未修复故障。",
                ],
            },
            rules=[
                "【单次 `--script-file` 优先】把预处理、建库、探测、修复、统计与 `export_bundle` 写入同一剧本一次执行。",
                "【拆音与合并规范】拆音用 `tool_split`（支持 `.ptwl`/`.txt` 或省略字表纯拆音；若 `mismatch: true` 请检查 `warnings` 与保留的 `extra_*` 片段）；合并用 `tool_merge`（默认保持 24kHz 等原采样率一致）。",
                "【零重复补导】`export_bundle` 已内置 `.Collection` 与双字调专项图（`tone_sandhi`/`tone_effect_time`/`duration`），无需跑批后再逐张调用 `export`。",
                "【禁止绕过 CLI】所有音频切分、合并、声学计算、图表与报告导出均通过本 CLI 完成。",
            ],
            core_command_cheatsheet={
                "tool_split": "tool_split <long.wav> [wordlist.ptwl|.txt|.csv] <out_dir> [buffer=0.10] [trim=true|false] [merge_thresh=<sec>] [min_dur=0.10] [db=<dB>] [wordlist=none]  # 长音频拆短音频(支持无字表纯拆音；段数不符时显式告警并保留 extra_* 片段，保持原采样率)",
                "tool_merge": "tool_merge <out.wav> <gap_sec> <files_or_dir_or_glob...> [sr=<Hz>]  # 合并短音频(同采样率默认保持原采样率无损输出)",
                "tool_sort_batch": "tool_sort_batch <wordlist.ptwl|.txt|.csv> [audio_paths_or_dir...] [--apply]  # 按字表模糊排序短音频(--apply 直接载入当前发音人)",
                "tool_wordlist_convert": "tool_wordlist_convert <in.txt|.csv|.ptwl> <out.ptwl> [title=<标题>]  # 将杂乱草稿 txt/csv 升级保存为标准 v2 .ptwl",
                "speakers_lifecycle": "add_speaker <name> [gender=female|male|child] [--switch] | switch_speaker <name_or_id> | rename_speaker [<old>] <new> | remove_speaker <name_or_id> | speakers",
                "load_long": "load_long <audio.wav>  # 加载当前发音人单条长音频",
                "load_batch": "load_batch <dir_or_glob_or_files...> [recursive=true|false]  # 批量加载目录或通配符短音频",
                "apply_wordlist": "apply_wordlist <path.ptwl|.csv|.txt> [fuzzy|order]  # 原生容错解析杂乱草稿 .txt/.csv/.ptwl 并完成长音频切分或短音频匹配",
                "detect_f0": "detect_f0 [recommended|conservative|fine]  # 传 recommended 一步探测并应用最佳 pitch_floor/pitch_ceiling",
                "detect_formants": "detect_formants [--apply|--no-apply] [preset=recommended|conservative|high_resolution]  # 自动探测最佳共振峰参数",
                "fix_warnings": "fix_warnings [--apply] [scope=active|all]  # 一键修复倍频跳变/低覆盖率/谷值切分警告并同步清零 quality 红线警告",
                "stats": "stats [by=group|item|speaker] [scope=active|integrated] [out=<path.json|path.md>]  # 返回或落盘分组/词条/发音人声学指标与五度值统计摘要",
                "export_bundle": "export_bundle <out_dir> [scope=active|integrated|separate] [format=png|svg|pdf] [title=<name>] [charts=auto|all|<list>] [collection=true|false]  # 【首选一键闭环导出】含 .teproj/.ptwl/.Collection/xlsx/txt/json/常规+双字调图表/研究方法报告",
                "export": "export <format> <out_path> [continuous|per_group] [scope=active|integrated|separate] [key=val ...]  # 单项导出；支持 xlsx, txt, textgrid, collection, ptwl, contour, tone_sandhi, tone_effect_time, density, distribution, duration, quality, spaghetti_qc, overview_heatmap, formant_space, f1_f2, formant_trajectory, formant_density, formant_overview_heatmap, formant_pillai, formant_table, wav, merged_wav",
                "report_export": "report_export <path.teproj> <out_dir> [format=md|excel|both] [include_cache=true|false]  # 导出研究方法报告与数据档案",
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
                "list_scripts", "script_info", "run_script", "script_save", "script_delete", "script_import", "script_export", "script_prompt",
            ],
            task_paths={
                "one_shot_script_file_workflow": {
                    "description": "【AI 默认首选】单次 --script-file 完成杂乱字表转换、长音频拆短音频、多发音人混合建库、F0 探测、异常修复与双字调全套产物闭环打包",
                    "script_file_example": [
                        "# 执行: python cli.py --project experiment.teproj --script-file pipeline.txt",
                        'tool_wordlist_convert "draft_wordlist.txt" "standard.ptwl" title="双字调实验"',
                        'tool_split "男1.wav" "standard.ptwl" "./male_split"',
                        'add_speaker "F01_女1" gender=female --switch',
                        'load_long "女1.wav"',
                        'apply_wordlist "standard.ptwl"',
                        "detect_f0 recommended",
                        'add_speaker "M01_男1" gender=male --switch',
                        'load_batch "./male_split"',
                        'apply_wordlist "standard.ptwl" fuzzy',
                        "detect_f0 recommended",
                        "fix_warnings --apply scope=all",
                        "stats by=group scope=integrated",
                        'export_bundle "./deliverables" scope=integrated format=png',
                    ],
                },
                "multi_speaker_workflow": {
                    "description": "多发音人联合建模、跨性别五度值归一化与一键打包工作流",
                    "steps": [
                        "1. add_speaker <女声名> gender=female --switch -> load_long <音频> -> apply_wordlist <字表> -> detect_f0 recommended",
                        "2. add_speaker <男声名> gender=male --switch -> load_long <音频> 或 load_batch <短音频目录> -> apply_wordlist <字表> -> detect_f0 recommended",
                        "3. fix_warnings --apply scope=all -> stats by=group scope=integrated",
                        "4. export_bundle <out_dir> scope=integrated format=png (自动含 .teproj/.ptwl/.Collection/双字调图表/报告)",
                    ],
                },
                "batch_audio_with_wordlist_workflow": {
                    "description": "独立短音频批量加载（或先用 tool_split 拆音）并与字表对齐工作流",
                    "steps": [
                        "1. (可选) tool_split <长音频> [字表] <短音频目录>  # 保持原采样率拆音，不符词数自动告警并保留 extra_*",
                        "2. load_batch <短音频目录> -> apply_wordlist <字表> fuzzy (或直接 tool_sort_batch <字表> <短音频目录> --apply)",
                        "3. detect_f0 recommended -> fix_warnings --apply -> export_bundle <out_dir> scope=active",
                    ],
                },
                "audio_toolkit_split_merge_workflow": {
                    "description": "音频工具箱纯拆音、按字表拆音与同采样率合并工作流",
                    "steps": [
                        "1. 无字表纯拆音: tool_split <长音频.wav> <输出目录>",
                        "2. 按字表拆音: tool_split <长音频.wav> <字表.ptwl|.txt|.csv> <输出目录> buffer=0.05 trim=true",
                        "3. 保采样率合并: tool_merge <合并输出.wav> 0.5 <短音频目录或文件列表>",
                    ],
                },
                "inspect_and_adjust_existing_project": {
                    "description": "加载已有 .teproj 工程进行增量微调",
                    "steps": [
                        "1. python cli.py --project <work.teproj> status",
                        "2. python cli.py --project <work.teproj> modify_bounds / modify_params / fix_warnings --apply",
                        "3. python cli.py --project <work.teproj> export_bundle <out_dir> scope=integrated",
                    ],
                },
                "textgrid_converter_workflow": {
                    "description": "任意 TextGrid 转换或批量对齐导入",
                    "steps": [
                        "1. textgrid_inspect <tg> -> textgrid_convert <tg_or_dir> out=<out_dir> item=<词层> core=<音节层> [wordlist=<字表>]",
                        "2. load_batch <audio_dir> -> batch_textgrid_import <tg_dir>",
                    ],
                },
            },
            next_steps=["--script-file <cmds.txt>", "help all", "help tools", "export_bundle <out_dir> scope=integrated"],
        )


__all__ = [
    "get_cli_help_text",
    "CLIHelpMixin",
]
