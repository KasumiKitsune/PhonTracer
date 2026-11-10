# -*- coding: utf-8 -*-
"""
PhonTracer CLI - Project Storage, Batch Pipeline, and Session Lifecycle Mixin.
Provides commands for saving, loading, importing, exporting, and converting project archives,
as well as executing the end-to-end import_batch_and_export automation pipeline.
"""
from __future__ import annotations

import concurrent.futures
import copy
import glob
import json
import os
import re
import shlex
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import parselmouth
except ImportError:
    from modules import _parselmouth_fallback as parselmouth
    sys.modules.setdefault("parselmouth", parselmouth)
    sys.modules.setdefault("parselmouth.praat", parselmouth.praat)

from modules.audio_core import (
    batch_process_worker,
    batch_process_worker_with_textgrid,
    extract_f0,
    extract_formants,
)
from modules.cli_app.io_utils import (
    COMMON_AUDIO_EXTENSIONS,
    LoggerOut,
    _natural_sort_key,
    dumps_utf8,
)
from modules.project_manager import ProjectManager, read_project_metadata_from_archive
from modules.report_generator import (
    export_reports_from_teproj,
    get_pitch_ceiling,
    get_pitch_floor,
)
from modules.speaker_manager import SpeakerManager
from modules.wordlist_v2 import normalize_wordlist_document


class CLIProjectMixin:
    """
    Project management mixin handling archive persistence, workspace synchronization,
    and end-to-end batch ingestion pipelines.
    """
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
                lbl = res.get('label') or os.path.splitext(os.path.basename(res.get('path', '')))[0] or "未知条目"
                iid = f"missing_{lbl}_{id(res)}"
                spk.items[iid] = {
                    'id': iid,
                    'label': lbl,
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


    def do_EOF(self, arg):
        """Exit cleanly when the host closes stdin."""
        if not getattr(self, "quiet", False) and not getattr(self, "json_mode", False):
            print()
        return self.do_exit(arg)


__all__ = ["CLIProjectMixin"]
