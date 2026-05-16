import argparse
import os
import sys
import concurrent.futures
import re
import logging
import parselmouth

from modules.data_utils import parse_wordlist, fuzzy_match_word_to_path
from modules.audio_core import batch_process_worker, auto_split_inner_word

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(description="PhonTracer Command Line Interface")

    # Common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--pitch-floor", type=float, default=75.0, help="Pitch floor (Hz), default 75.0")
    common_parser.add_argument("--pitch-ceiling", type=float, default=600.0, help="Pitch ceiling (Hz), default 600.0")
    common_parser.add_argument("--db", type=float, default=15.0, help="Energy drop threshold (dB), default 15.0")
    common_parser.add_argument("--skip-front", type=float, default=0.2, help="Skip front duration (s), default 0.2")
    common_parser.add_argument("--no-trim-silence", action="store_true", help="Disable silence trimming at edges")
    common_parser.add_argument("--points", type=int, default=11, help="Number of sampling points, default 11")
    common_parser.add_argument("-o", "--output", required=True, help="Output file path (.txt or .xlsx)")

    subparsers = parser.add_subparsers(dest="command", required=True, help="Mode of operation")

    # Batch mode
    batch_parser = subparsers.add_parser("batch", parents=[common_parser], help="Process a directory of independent audio files")
    batch_parser.add_argument("audio_dir", help="Directory containing audio files (.wav, .mp3)")
    batch_parser.add_argument("wordlist", help="Path to the wordlist text file")
    batch_parser.add_argument("--match-mode", choices=["fuzzy", "order"], default="fuzzy", help="Matching mode for files, default 'fuzzy'")

    # Long mode
    long_parser = subparsers.add_parser("long", parents=[common_parser], help="Process a single long audio file with multiple words")
    long_parser.add_argument("audio_file", help="Path to the long audio file")
    long_parser.add_argument("wordlist", help="Path to the wordlist text file")

    return parser.parse_args()

def process_batch(args):
    # Load wordlist
    try:
        with open(args.wordlist, 'r', encoding='utf-8') as f:
            raw_text = f.read()
    except UnicodeDecodeError:
        try:
            with open(args.wordlist, 'r', encoding='gbk') as f:
                raw_text = f.read()
        except Exception as e:
            logger.error(f"Failed to read wordlist file: {e}")
            sys.exit(1)

    groups, flat_words = parse_wordlist(raw_text)
    if not flat_words:
        logger.error("No words found in the wordlist.")
        sys.exit(1)

    # Find audio files
    if not os.path.isdir(args.audio_dir):
        logger.error(f"Audio directory not found: {args.audio_dir}")
        sys.exit(1)

    available_paths = []
    for root, _, files in os.walk(args.audio_dir):
        for file in files:
            if file.lower().endswith(('.wav', '.mp3')):
                available_paths.append(os.path.join(root, file))

    if not available_paths:
        logger.error(f"No audio files (.wav, .mp3) found in directory: {args.audio_dir}")
        sys.exit(1)

    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower()
                for text in re.split('([0-9]+)', s)]

    available_paths = sorted(available_paths, key=natural_sort_key)

    tasks = []
    if args.match_mode == 'fuzzy':
        used_indices = set()
        for grp in groups:
            group_name = grp['group']
            for word in grp['items']:
                idx = fuzzy_match_word_to_path(word, available_paths, used_indices=list(used_indices))
                if idx is not None:
                    path = available_paths[idx]
                    used_indices.add(idx)
                    tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False})
                else:
                    tasks.append({'word': word, 'group': group_name, 'missing': True})
    else:
        path_idx = 0
        for grp in groups:
            group_name = grp['group']
            for word in grp['items']:
                if path_idx < len(available_paths):
                    path = available_paths[path_idx]
                    tasks.append({'word': word, 'group': group_name, 'path': path, 'missing': False})
                    path_idx += 1
                else:
                    tasks.append({'word': word, 'group': group_name, 'missing': True})

    results = [None] * len(tasks)
    params = {
        'db': args.db,
        'skip_front': args.skip_front,
        'pitch_floor': args.pitch_floor,
        'pitch_ceiling': args.pitch_ceiling
    }
    trim_silence = not args.no_trim_silence

    logger.info(f"Processing {len(tasks)} items...")

    futures = {}
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
    for i, t in enumerate(tasks):
        if t['missing']:
            results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
        else:
            futures[executor.submit(batch_process_worker, t['path'], params, trim_silence)] = i

    completed = 0
    for future in concurrent.futures.as_completed(futures):
        orig_idx = futures[future]
        try:
            res = future.result()
            results[orig_idx] = {**res, 'missing': False, 'group': tasks[orig_idx]['group']}
        except Exception as e:
            results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}
        completed += 1
        if completed % 10 == 0 or completed == len(futures):
            logger.info(f"Progress: {completed}/{len(futures)}")

    executor.shutdown(wait=False)

    # Post process: fix inner splits if needed
    for i, res in enumerate(results):
        if res and not res.get('missing') and res.get('success'):
            word = tasks[i]['word']
            cached_label = res.get('label', '')
            if len(word) > 1 and len(cached_label) != len(word):
                try:
                    snd = parselmouth.Sound(res['path'])
                    res['inner_splits'] = auto_split_inner_word(snd, res['start'], res['end'], len(word))
                except Exception:
                    res['inner_splits'] = []
            elif len(word) <= 1:
                res['inner_splits'] = []
            res['label'] = word # update label to word list label

    # Build tree structure and dict for export
    tree_structure = []
    items_dict = {}

    current_group_idx = 0
    for grp in groups:
        group_name = grp['group']
        group_items_list = []
        for word in grp['items']:
            res = results[current_group_idx]
            if res and res.get('success') and not res.get('missing'):
                iid = f"batch_{word}_{id(res)}"
                group_items_list.append(iid)
                items_dict[iid] = res
            current_group_idx += 1

        if group_items_list:
            tree_structure.append((group_name, group_items_list))

    return tree_structure, items_dict

    main()

from modules.audio_core import macroscopic_vad, long_process_worker
import numpy as np

def process_long(args):
    # Load wordlist
    try:
        with open(args.wordlist, 'r', encoding='utf-8') as f:
            raw_text = f.read()
    except UnicodeDecodeError:
        try:
            with open(args.wordlist, 'r', encoding='gbk') as f:
                raw_text = f.read()
        except Exception as e:
            logger.error(f"Failed to read wordlist file: {e}")
            sys.exit(1)

    groups, flat_words = parse_wordlist(raw_text)
    if not flat_words:
        logger.error("No words found in the wordlist.")
        sys.exit(1)

    if not os.path.exists(args.audio_file):
        logger.error(f"Long audio file not found: {args.audio_file}")
        sys.exit(1)

    logger.info(f"Loading long audio file: {args.audio_file}")
    snd = parselmouth.Sound(args.audio_file)
    global_pitch = snd.to_pitch(pitch_floor=args.pitch_floor, pitch_ceiling=args.pitch_ceiling)

    logger.info("Performing macroscopic VAD...")
    macro_segments = macroscopic_vad(snd)
    logger.info(f"Found {len(macro_segments)} segments in audio.")

    params = {
        'db': args.db,
        'skip_front': args.skip_front,
        'pitch_floor': args.pitch_floor,
        'pitch_ceiling': args.pitch_ceiling
    }
    trim_silence = not args.no_trim_silence
    pitch_xs = global_pitch.xs()
    pitch_freqs = global_pitch.selected_array['frequency']

    tasks = []
    word_idx = 0
    for grp in groups:
        for word in grp['items']:
            if word_idx < len(macro_segments):
                ms, me = macro_segments[word_idx]
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
                        'word': word, 'group': grp['group'], 'ms': ms, 'me': me,
                        'snd_values': snd_values, 'snd_sf': snd_sf,
                        'pitch_xs': sliced_xs, 'pitch_freqs': sliced_freqs,
                        'missing': False
                    })
                else:
                    tasks.append({'word': word, 'group': grp['group'], 'missing': True})
                word_idx += 1
            else:
                tasks.append({'word': word, 'group': grp['group'], 'missing': True})

    results = [None] * len(tasks)

    logger.info(f"Processing {len(tasks)} items...")

    futures = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
        for i, task in enumerate(tasks):
            if not task.get('missing'):
                f = executor.submit(
                    long_process_worker,
                    task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                    task['ms'], task['me'], params, trim_silence, task['word']
                )
                futures[f] = i
            else:
                results[i] = {'label': task['word'], 'group': task['group'], 'success': False, 'missing': True}

        completed = 0
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                res = future.result()
                # merge base info
                res_dict = {
                    'label': tasks[idx]['word'],
                    'group': tasks[idx]['group'],
                    'success': res.get('success', False),
                    'missing': False
                }
                if res.get('success'):
                    res_dict.update({
                        'start': res['mis'],
                        'end': res['mie'],
                        'raw_start': res['raw_s'],
                        'raw_end': res['raw_e'],
                        'inner_splits': res.get('inner_splits', []),
                        'has_empty_data': res.get('has_empty_data', False)
                    })
                    # Add base audio info required for export
                    res_dict['snd'] = parselmouth.Sound(tasks[idx]['snd_values'], tasks[idx]['snd_sf'])
                    res_dict['pitch'] = res_dict['snd'].to_pitch(pitch_floor=args.pitch_floor, pitch_ceiling=args.pitch_ceiling)
                results[idx] = res_dict
            except Exception as e:
                results[idx] = {'label': tasks[idx]['word'], 'group': tasks[idx]['group'], 'success': False, 'missing': True, 'error': str(e)}

            completed += 1
            if completed % 10 == 0 or completed == len(futures):
                logger.info(f"Progress: {completed}/{len(futures)}")

    tree_structure = []
    items_dict = {}

    current_group_idx = 0
    for grp in groups:
        group_name = grp['group']
        group_items_list = []
        for word in grp['items']:
            res = results[current_group_idx]
            if res and res.get('success') and not res.get('missing'):
                iid = f"long_{word}_{id(res)}"
                group_items_list.append(iid)
                items_dict[iid] = res
            current_group_idx += 1

        if group_items_list:
            tree_structure.append((group_name, group_items_list))

    return tree_structure, items_dict


from modules.data_utils import get_export_text_for_item

def export_to_txt(out_file, tree_structure, items_dict, num_points):
    with open(out_file, "w", encoding="utf-8") as f:
        global_idx = 1
        for grp_name, children in tree_structure:
            f.write(f"{grp_name}\n")
            for child in children:
                item = items_dict[child]
                if item.get('start') is not None:
                    txt_data = get_export_text_for_item(item, global_idx, num_points)
                    f.write(txt_data)
                    global_idx += 1

def extract_syl_data_for_export(item, num_points):
    if item.get('start') is None or item.get('end') is None: return 0, []

    t_s = item['start']
    t_e = item['end']
    duration = t_e - t_s
    if duration <= 0 or not item.get('snd'): return 0, []

    label = item.get('label', '')
    inner_splits = item.get('inner_splits', [])
    pitch = item.get('pitch')
    if not pitch: return 0, []

    p_xs = pitch.xs()
    p_freqs = pitch.selected_array['frequency']

    syl_data = []

    if len(label) > 1:
        splits = [t_s] + [s for s in inner_splits if t_s < s < t_e] + [t_e]
        if len(splits) != len(label) + 1:
            splits = np.linspace(t_s, t_e, len(label) + 1).tolist()

        for i in range(len(label)):
            c_start = splits[i]
            c_end = splits[i+1]

            valid_idx = np.where((p_xs >= c_start) & (p_xs <= c_end) & (p_freqs > 0))[0]
            if len(valid_idx) >= 2:
                v_start, v_end = p_xs[valid_idx[0]], p_xs[valid_idx[-1]]
            else:
                v_start, v_end = c_start, c_end

            c_dur = v_end - v_start
            if c_dur <= 0:
                syl_data.append((0, [0.0]*num_points))
                continue

            times = np.linspace(v_start, v_end, num_points)
            f0s = [pitch.get_value_at_time(t) for t in times]
            syl_data.append((c_dur, f0s))
    else:
        times = np.linspace(t_s, t_e, num_points)
        f0s = [pitch.get_value_at_time(t) for t in times]
        syl_data.append((duration, f0s))

    return duration, syl_data

def export_to_xlsx(out_file, tree_structure, items_dict, num_points, pitch_floor=75.0, pitch_ceiling=600.0):
    rows = []
    for grp_name, children in tree_structure:
        for child in children:
            item = items_dict[child]
            lbl = item.get('label', '')

            if (not item.get('snd') or not item.get('pitch')) and item.get('path'):
                try:
                    item['snd'] = parselmouth.Sound(item['path'])
                    item['pitch'] = item['snd'].to_pitch(pitch_floor=pitch_floor, pitch_ceiling=pitch_ceiling)
                except Exception: continue

            total_dur, syl_data = extract_syl_data_for_export(item, num_points)
            if total_dur <= 0: continue

            row = {'Group': grp_name, 'Word': lbl, 'Total Duration (s)': round(total_dur, 3)}

            for k, (dur, f0s) in enumerate(syl_data):
                suffix = f"_Syl{k+1}" if len(lbl) > 1 else ""
                row[f'Duration{suffix}'] = round(dur, 3)
                for i, f0 in enumerate(f0s):
                    row[f'pt{i+1}{suffix}'] = round(f0, 2) if not np.isnan(f0) else ""

            rows.append(row)

    df = pd.DataFrame(rows)
    df.to_excel(out_file, index=False)

def main():
    args = parse_args()
    if args.command == 'batch':
        tree_structure, items_dict = process_batch(args)
    elif args.command == 'long':
        tree_structure, items_dict = process_long(args)
    else:
        sys.exit(1)

    if not items_dict:
        logger.warning("No valid items to export.")
        sys.exit(0)

    logger.info(f"Exporting data to {args.output} with {args.points} points...")
    if args.output.lower().endswith('.xlsx'):
        try:
            import pandas as pd
            export_to_xlsx(args.output, tree_structure, items_dict, args.points, pitch_floor=args.pitch_floor, pitch_ceiling=args.pitch_ceiling)
            logger.info("Export to XLSX complete.")
        except ImportError:
            logger.error("pandas or openpyxl not installed. Please install them to export to XLSX.")
            sys.exit(1)
    else:
        export_to_txt(args.output, tree_structure, items_dict, args.points)
        logger.info("Export to TXT complete.")

if __name__ == "__main__":
    main()
