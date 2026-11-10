# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Toolkit Tools and Custom Scripting Mixin.
Provides standalone audio tools (merge, sort, split), wordlist conversion,
custom Python script execution in sandbox, and AI prompt generation.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from modules.audio_core import macroscopic_vad, trim_bounds_by_amplitude
from modules.cli_app.io_utils import _natural_sort_key, dumps_utf8
from modules.data_utils import fuzzy_match_word_to_path, parse_wordlist
from modules.project_patch import _append_or_update_script_run, apply_project_patch_to_teproj
from modules.script_api import (
    FigureResult,
    ProjectPatchResult,
    TableResult,
    build_dataset_snapshot,
)
from modules.script_manager import (
    delete_script,
    export_script,
    import_script,
    load_all_scripts,
    save_script,
)
from modules.script_prompt import (
    SCRIPT_PROMPT_KEY_ALIASES,
    VALID_SCRIPT_PROMPT_KEYS,
    generate_ai_prompt,
    validate_and_normalize_script_prompt_params,
)
from modules.script_runner import run_custom_script
from modules.version import APP_NAME, __version__
from modules.wordlist_v2 import (
    WORDLIST_SCHEMA,
    save_wordlist_file,
    summarize_wordlist_document,
)


class CLIToolkitScriptMixin:
    """
    Toolkit and scripting mixin providing tool commands, user script execution,
    and prompt engineering utilities.
    """
    def do_wordlist_convert(self, arg):
        """Alias for tool_wordlist_convert."""
        self.do_tool_wordlist_convert(arg)


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


__all__ = ["CLIToolkitScriptMixin"]
