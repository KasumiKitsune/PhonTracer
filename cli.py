import cmd
import shlex
import sys
import os
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

from modules.audio_core import macroscopic_vad, core_microscopic_vowel_nucleus, auto_split_inner_word, auto_split_to_chars_bounds, batch_process_worker, recalculate_bounds_fast
from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path, get_export_text_for_item, build_five_point_chart

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

class PhonTracerCLI(cmd.Cmd):
    intro = """PhonTracer CLI - AI Agent Mode
Type 'help' or '?' to list commands.
Rules:
- Output is optimized for token efficiency.
- All actions result in 'success' or 'error' messages.
- Use 'status' to get current project state.
- Use 'list_items' to view extracted audio segments and warnings.
"""
    prompt = "(phontracer) "

    def __init__(self):
        super().__init__()
        self.params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600,
            'voicing_threshold': 0.25,
            'trim_silence': True
        }
        self.lang = 'en'

        self.items = OrderedDict()
        self.groups = []
        self.mode = None # 'long' or 'batch'

        self.long_snd = None
        self.long_snd_path = None
        self.batch_paths = []
        self.audio_cache = {}

        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
        self.log_file = None
        self.original_stdout = sys.stdout

    def precmd(self, line):
        if self.log_file:
            self.log_file.write(f"> {line}\n")
            self.log_file.flush()
        return line

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
        Valid keys: pts, db, skip_front, pitch_floor, pitch_ceiling, voicing_threshold, trim_silence
        Example: set_params db=50.0 trim_silence=False
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
                        elif k in ('pts', 'pitch_floor', 'pitch_ceiling'):
                            self.params[k] = int(v)
                        else:
                            self.params[k] = float(v)
                        updated = True
                    except ValueError:
                        print(f'{{"success": False, "error": "Invalid value for {k}"}}')
                        return
                else:
                    print(f'{{"success": False, "error": "Unknown parameter {k}"}}')
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
   - `export <格式> <输出路径> [规则]`
     * 支持的导出格式：txt, xlsx, line_chart, kde, wav, merged_wav

--- 声学参数与调优指南 ---
* pts: 等分插值采样点数（默认：11）
* db: VAD 切分能量落差阈值（默认：60.0）
* skip_front: 排除声母时长，避免声母辅音浊化干扰（默认：0.0）
* pitch_floor: 音高分析下限（默认：75 Hz） -> 通用甜点区：75
* pitch_ceiling: 音高分析上限（默认：600 Hz） -> 通用甜点区：600
* voicing_threshold: 浊音阈值（默认：0.25） -> 针对汉语三声等低频“气泡音/嘎裂声”，建议手动调低至 0.15 ~ 0.20
* trim_silence: 自动切除有效声学边界首尾低于 -50dB 的静音区（默认：True）

--- 核心命令速查表 ---
- `status`: 获取项目当前模式、状态指标、警告统计等。
- `list_items [all|warnings|组名]`: 列表打印当前已切分的数据项及警告状态。
- `set_params 键=值 ...`: 动态更新全局算法提取参数。
- `modify_bounds <音节ID> <开始秒数> <结束秒数>`: 手动重写音节声学边界。
- `modify_params <音节ID> 键=值 ...`: 手动指定单个音节专属的提取参数。
- `recalculate`: 基于全局最新参数，批量重算整个项目。
- `lang [zh|en]`: 切换命令行显示语言为中文或英文。
================================================================================
""")
            else:
                print("""
================================================================================
                            PhonTracer CLI MANUAL (Agent & Developer Guide)
================================================================================
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
   - `export <format> <output_file> [rule]`
     * format: txt, xlsx, line_chart, kde, wav, merged_wav

--- CURRENT CONFIG & SCHEMAS ---
- Global parameters (modifiable via `set_params`):
  * pts: Number of interpolation points (default: 11)
  * db: VAD energy threshold (default: 60.0)
  * skip_front: Avoid segmenting consonant onset duration (default: 0.0)
  * pitch_floor: Minimum F0 range (default: 75) -> Sweet spot: 75
  * pitch_ceiling: Maximum F0 range (default: 600) -> Sweet spot: 600
  * voicing_threshold: Voicing tolerance (default: 0.25) -> Adjust lower (0.15~0.20) for creaky voice
  * trim_silence: Cut prefix/suffix silence under -50dB (default: True)

--- ALL COMMANDS REFERENCE ---
- `status`: Show current project state, active parameters, item warnings.
- `list_items [all|warnings|group_name]`: List segmented items and warning flags.
- `set_params key=value ...`: Update global extraction parameters.
- `modify_bounds <item_id> <start> <end>`: Set manual time boundaries.
- `modify_params <item_id> key=value ...`: Set item-specific custom parameters.
- `recalculate`: Recalculate VAD boundaries & F0 curves globally.
- `lang [zh|en]`: Switch CLI language between Chinese and English.
================================================================================
""")
        else:
            super().do_help(arg)

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
            print('{"success": True, "message": "Long audio loaded"}')
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
        print(json.dumps({"success": True, "message": f"Loaded {len(self.batch_paths)} batch files"}))

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
                if t.name == "words":
                    words_tier = t
                elif t.name == "chars":
                    chars_tier = t
                elif t.name in ["groups", "group"]:
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
                        w_len = len(lbl)
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
            global_pitch = snd.to_pitch_ac(time_step=None, pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)

            pitch_xs = global_pitch.xs()
            pitch_freqs = global_pitch.selected_array['frequency']

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
                    res['pitch'] = global_pitch
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                    
                    preview_times = np.linspace(res['start'], res['end'], 11)
                    preview_f0 = [global_pitch.get_value_at_time(t) for t in preview_times]
                    res['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
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

            print(json.dumps({"success": True, "message": f"TextGrid applied: processed {matched_count}/{len(results)} items."}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    def _process_long_wordlist(self, groups, flat_words):
        try:
            global_pitch = self.long_snd.to_pitch_ac(time_step=None, pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
            macro_segments = macroscopic_vad(self.long_snd)

            pitch_xs = global_pitch.xs()
            pitch_freqs = global_pitch.selected_array['frequency']

            word_idx = 0
            for grp in groups:
                for word in grp['items']:
                    iid = f"item_{word_idx}"
                    if word_idx < len(macro_segments):
                        ms, me = macro_segments[word_idx]

                        mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                            self.long_snd, global_pitch, ms, me,
                            self.params['db'], self.params['skip_front'], self.params['trim_silence']
                        )

                        inner_splits = []
                        chars_bounds = []
                        if len(word) > 1:
                            inner_splits = auto_split_inner_word(self.long_snd, mic_s, mic_e, len(word))
                            chars_bounds = auto_split_to_chars_bounds(self.long_snd, mic_s, mic_e, inner_splits, len(word), self.params)
                        else:
                            chars_bounds = [[mic_s, mic_e]]

                        # Preview
                        preview_times = np.linspace(mic_s, mic_e, 11)
                        preview_f0 = [global_pitch.get_value_at_time(t) for t in preview_times]
                        preview_f0 = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                        has_empty = any(f == 0.0 for f in preview_f0)

                        self.items[iid] = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'snd': self.long_snd, 'pitch': global_pitch,
                            'macro_start': ms, 'macro_end': me,
                            'start': mic_s, 'end': mic_e,
                            'raw_start': raw_s, 'raw_end': raw_e,
                            'inner_splits': inner_splits, 'chars_bounds': chars_bounds,
                            'preview_f0': preview_f0, 'has_empty_data': has_empty, 'missing': False,
                            'pitch_floor': self.params['pitch_floor'],
                            'pitch_ceiling': self.params['pitch_ceiling'],
                            'voicing_threshold': self.params.get('voicing_threshold', 0.25)
                        }
                    else:
                        self.items[iid] = {
                            'id': iid, 'label': word, 'group': grp['group'],
                            'missing': True, 'start': None, 'end': None
                        }
                    word_idx += 1
            print(json.dumps({"success": True, "message": f"Processed long audio with {len(flat_words)} words"}))
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
                if res.get('success') and len(word) > 1:
                    snd = parselmouth.Sound(res['path'])
                    res['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(word))
                    res['chars_bounds'] = auto_split_to_chars_bounds(snd, res['start'], res['end'], res['inner_splits'], len(word), self.params)
                results[orig_idx] = res
            except Exception as e:
                results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'missing': True, 'error': str(e)}

        for i, res in enumerate(results):
            iid = f"item_{i}"
            if not res.get('missing') and res.get('success'):
                # Load sound and pitch object into memory for fast recalculation
                try:
                    snd = parselmouth.Sound(res['path'])
                    pitch = snd.to_pitch_ac(time_step=None, pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
                    res['snd'] = snd
                    res['pitch'] = pitch
                    res['pitch_floor'] = self.params['pitch_floor']
                    res['pitch_ceiling'] = self.params['pitch_ceiling']
                    res['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                except Exception:
                    pass
            res['id'] = iid
            self.items[iid] = res

        print(json.dumps({"success": True, "message": f"Processed batch files with {len(tasks)} words"}))

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
        if len(label) > 1:
            item['inner_splits'] = auto_split_inner_word(item['snd'], new_s, new_e, len(label))
            item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], new_s, new_e, item['inner_splits'], len(label), self.params)
        else:
            item['inner_splits'] = []
            item['chars_bounds'] = [[new_s, new_e]]

        # Re-evaluate warnings
        if item.get('pitch'):
            preview_times = np.linspace(new_s, new_e, 11)
            preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
            item['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
            item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

        print(json.dumps({"success": True, "message": f"Bounds updated for {iid}", "warning": item.get('has_empty_data', False)}))

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
            # Recompute pitch object for this item
            try:
                item['pitch'] = item['snd'].to_pitch_ac(
                    time_step=None, pitch_floor=item['pitch_floor'], pitch_ceiling=item['pitch_ceiling'],
                    voicing_threshold=item['voicing_threshold'], very_accurate=True, octave_jump_cost=0.9
                )
                
                # Recompute chars bounds with new params if word mode
                label = item['label']
                if len(label) > 1:
                    item['inner_splits'] = auto_split_inner_word(item['snd'], item['start'], item['end'], len(label))
                    item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], item['start'], item['end'], item['inner_splits'], len(label), item)
                else:
                    item['inner_splits'] = []
                    item['chars_bounds'] = [[item['start'], item['end']]]
                    
                # Re-evaluate warnings
                preview_times = np.linspace(item['start'], item['end'], 11)
                preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                item['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}))
                return

        print(json.dumps({
            "success": True, 
            "message": f"Parameters updated for {iid}", 
            "item_params": {
                "pitch_floor": item.get('pitch_floor'), 
                "pitch_ceiling": item.get('pitch_ceiling'), 
                "voicing_threshold": item.get('voicing_threshold')
            },
            "warning": item.get('has_empty_data', False)
        }))

    def do_recalculate(self, arg):
        """
        Recalculate bounds and pitches for all items based on current params.
        Usage: recalculate
        """
        print('{"status": "processing", "message": "Recalculating... this may take a moment."}')

        recompute_pitch = True # For simplicity, always recompute pitch on full recalculate

        if self.mode == 'long' and self.long_snd:
            # Recompute global pitch
            global_pitch = self.long_snd.to_pitch_ac(time_step=None, pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)

            for iid, item in self.items.items():
                if item.get('missing') or not item.get('success', True): continue

                item['pitch'] = global_pitch
                item['pitch_floor'] = self.params['pitch_floor']
                item['pitch_ceiling'] = self.params['pitch_ceiling']
                item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)
                mac_s, mac_e = item['macro_start'], item['macro_end']

                mic_s, mic_e, raw_s, raw_e = core_microscopic_vowel_nucleus(
                    item['snd'], item['pitch'], mac_s, mac_e,
                    self.params['db'], self.params['skip_front'], self.params['trim_silence']
                )

                item['start'], item['end'] = mic_s, mic_e
                item['raw_start'], item['raw_end'] = raw_s, raw_e

                label = item['label']
                if len(label) > 1:
                    item['inner_splits'] = auto_split_inner_word(item['snd'], mic_s, mic_e, len(label))
                    item['chars_bounds'] = auto_split_to_chars_bounds(item['snd'], mic_s, mic_e, item['inner_splits'], len(label), self.params)
                else:
                    item['inner_splits'] = []
                    item['chars_bounds'] = [[mic_s, mic_e]]

                preview_times = np.linspace(mic_s, mic_e, 11)
                preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                item['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])

        elif self.mode == 'batch':
            # Use multiprocessing
            tasks = []
            iids = []
            for iid, item in self.items.items():
                if item.get('missing') or not item.get('success', True): continue
                tasks.append({'path': item['path'], 'label': item.get('label', '')})
                iids.append(iid)

            futures = {}
            for i, t in enumerate(tasks):
                f = self.executor.submit(batch_process_worker, t['path'], self.params, self.params['trim_silence'], t['label'])
                futures[f] = i

            for future in concurrent.futures.as_completed(futures):
                orig_idx = futures[future]
                iid = iids[orig_idx]
                try:
                    res = future.result()
                    if res.get('success'):
                        item = self.items[iid]
                        item['start'] = res['start']
                        item['end'] = res['end']
                        item['raw_start'] = res['raw_start']
                        item['raw_end'] = res['raw_end']

                        word = item['label']
                        if len(word) > 1:
                            snd = parselmouth.Sound(res['path'])
                            item['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(word))
                            item['chars_bounds'] = auto_split_to_chars_bounds(snd, res['start'], res['end'], item['inner_splits'], len(word), self.params)
                        else:
                            item['inner_splits'] = []
                            item['chars_bounds'] = [[res['start'], res['end']]]

                        # Refresh pitch obj
                        item['snd'] = parselmouth.Sound(res['path'])
                        item['pitch'] = item['snd'].to_pitch_ac(time_step=None, pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25), very_accurate=True, octave_jump_cost=0.9)
                        item['pitch_floor'] = self.params['pitch_floor']
                        item['pitch_ceiling'] = self.params['pitch_ceiling']
                        item['voicing_threshold'] = self.params.get('voicing_threshold', 0.25)

                        preview_times = np.linspace(item['start'], item['end'], 11)
                        preview_f0 = [item['pitch'].get_value_at_time(t) for t in preview_times]
                        item['preview_f0'] = [0.0 if (np.isnan(hz) or hz <= 0) else hz for hz in preview_f0]
                        item['has_empty_data'] = any(f == 0.0 for f in item['preview_f0'])
                except Exception:
                    pass

        print('{"success": True, "message": "Recalculation complete"}')

    def do_export(self, arg):
        """
        Export data to various formats.
        Usage: export <format> <output_file> [rule]
        Formats: txt, xlsx, line_chart, kde, wav, merged_wav, textgrid
        Rule: continuous (default) or per_group (For 'wav' and 'merged_wav', rule can also be buffer_sec or gap_sec like 0.5)
        Example: export xlsx output.xlsx continuous
        Example: export wav scratch/output_dir 0.1
        Example: export merged_wav scratch/merged.wav 0.5
        """
        args = shlex.split(arg)
        if len(args) < 2:
            print('{"success": False, "error": "Requires format and output_file"}')
            return

        fmt = args[0]
        out_file = args[1]
        rule = args[2] if len(args) > 2 else 'continuous'

        if not self.items:
            print('{"success": False, "error": "No items to export"}')
            return

        # Structure items by group
        structure = []
        for grp in self.groups:
            grp_items = [iid for iid, item in self.items.items() if item.get('group') == grp]
            structure.append((grp, grp_items))

        try:
            if fmt == 'txt':
                self._export_txt(out_file, structure, rule)
            elif fmt == 'xlsx':
                self._export_xlsx(out_file, structure, rule)
            elif fmt == 'line_chart':
                self._export_line_chart(out_file, structure)
            elif fmt == 'kde':
                self._export_kde_heatmap(out_file, structure)
            elif fmt == 'wav':
                self._export_wav(out_file, structure, rule)
            elif fmt == 'merged_wav':
                self._export_merged_wav(out_file, structure, rule)
            elif fmt == 'textgrid':
                self._export_textgrid(out_file, structure)
            else:
                print(f'{{"success": False, "error": "Unknown format: {fmt}"}}')
                return
            print(json.dumps({"success": True, "message": f"Exported to {out_file}"}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))

    # --- Export Implementation Helpers (Abstracted from GUI) ---
    def _export_merged_wav(self, out_file, structure, rule):
        import numpy as np

        if self.mode != 'batch':
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
                item = self.items[child]
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

    def _export_wav(self, out_dir, structure, rule):
        import re
        if self.mode != 'long' or not self.long_snd:
            raise Exception("WAV export is only supported for long audio mode")

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        buffer_sec = 0.1
        try:
            if rule and rule not in ('continuous', 'per_group'):
                buffer_sec = float(rule)
        except ValueError:
            pass

        snd = self.long_snd
        do_trim = self.params.get('trim_silence', True)
        
        global_idx = 1
        for grp_name, children in structure:
            for child in children:
                item = self.items[child]
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

    def _export_txt(self, out_file, structure, rule):
        is_continuous = (rule == "continuous")
        with open(out_file, "w", encoding="utf-8") as f:
            global_idx = 1
            for grp_name, children in structure:
                if not is_continuous: global_idx = 1
                f.write(f"{grp_name}\n")
                for child in children:
                    item = self.items[child]
                    if item.get('start') is not None:
                        txt_data = get_export_text_for_item(item, global_idx, self.params['pts'], pitch_floor=self.params['pitch_floor'], pitch_ceiling=self.params['pitch_ceiling'], voicing_threshold=self.params.get('voicing_threshold', 0.25))
                        f.write(txt_data)
                        global_idx += 1

    def _extract_syl_data(self, item, num_points):
        if item.get('start') is None or not item.get('snd') or not item.get('pitch'): return 0, []
        t_s, t_e = item['start'], item['end']
        if t_e <= t_s: return 0, []

        label = item.get('label', '')
        inner_splits = item.get('inner_splits', [])
        pitch = item['pitch']
        p_xs = pitch.xs()
        p_freqs = pitch.selected_array['frequency']

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

    def _export_xlsx(self, out_file, structure, rule):
        import xlsxwriter
        is_continuous = (rule == "continuous")
        num_points = self.params['pts']

        max_syls = 1
        for grp_name, children in structure:
            for child in children:
                lbl = self.items[child].get('label', '')
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
                item = self.items[child]
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

    def _collect_group_avg_data(self, structure):
        num_points = self.params['pts']
        max_syls = 1
        dict_data = {}
        for grp_name, children in structure:
            for child in children:
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)
                item = self.items[child]
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

    def _export_line_chart(self, out_file, structure):
        import matplotlib.pyplot as plt
        data, max_syls = self._collect_group_avg_data(structure)
        if not data:
            raise Exception("No valid data for charting")

        num_points = self.params['pts']
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(6 + 4 * max_syls, 6))
        total_points = max_syls * num_points
        x_vals = list(range(1, total_points + 1))

        colors = ['#2563EB', '#DC2626', '#16A34A', '#9333EA', '#EA580C', '#0891B2', '#CA8A04', '#6366F1']

        for i, (name, t_vals) in enumerate(data.items()):
            valid_x = [x for x, v in zip(x_vals, t_vals) if v is not None]
            valid_y = [v for v in t_vals if v is not None]
            if valid_x:
                ax.plot(valid_x, valid_y, '-o', color=colors[i % len(colors)], linewidth=2, markersize=5, label=name)

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

    def _export_kde_heatmap(self, out_file, structure):
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
                lbl = self.items[child].get('label', '')
                if len(lbl) > max_syls: max_syls = len(lbl)

        for grp_name, children in structure:
            for child in children:
                item = self.items[child]
                if item.get('start') is None or not item.get('snd') or not item.get('pitch'): continue

                t_s, t_e = item['start'], item['end']
                label = item.get('label', '')
                inner_splits = item.get('inner_splits', [])

                splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                if len(splits) != len(label) + 1: splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                if len(label) <= 1: splits = [t_s, t_e]

                pitch = item['pitch']
                p_xs, p_freqs = pitch.xs(), pitch.selected_array['frequency']

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

        groups_with_data = [g for g in self.groups if group_norm_points.get(g) and len(group_norm_points[g][0]) > 0]
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



    def _export_textgrid(self, out_path, structure):
        import textgrid
        import os

        flat_items = []
        for grp_name, children in structure:
            for child in children:
                if self.items[child].get('start') is not None and self.items[child].get('end') is not None:
                    flat_items.append(self.items[child])

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

            for path, items in path_to_items.items():
                base_name = os.path.splitext(os.path.basename(path))[0]
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

            if len(label) > 1:
                has_chars = True
                if t_s > last_char_end:
                    char_tier.add(last_char_end, t_s, "")

                chars_bounds = item.get('chars_bounds', [])
                if not chars_bounds:
                    splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
                    if len(splits) != len(label) + 1:
                        splits = np.linspace(t_s, t_e, len(label) + 1).tolist()
                    chars_bounds = [(splits[j], splits[j+1]) for j in range(len(splits)-1)]

                local_last = t_s
                for i in range(len(label)):
                    if i < len(chars_bounds):
                        c_s, c_e = chars_bounds[i]
                        if c_s > local_last:
                            char_tier.add(local_last, c_s, "")
                        char_tier.add(c_s, c_e, label[i])
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

        tg.append(word_tier)
        tg.append(group_tier)
        if has_chars:
            tg.append(char_tier)

        tg.write(tg_path)

if __name__ == '__main__':
    PhonTracerCLI().cmdloop()