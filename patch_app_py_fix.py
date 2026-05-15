import re

with open('modules/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add global ProcessPoolExecutor logic to PhoneticsApp
init_search = """        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600
        }"""

init_replace = """        self.last_params = {
            'pts': 11,
            'db': 60.0,
            'skip_front': 0.00,
            'pitch_floor': 75,
            'pitch_ceiling': 600
        }

        # Shared ProcessPoolExecutor for performance optimization
        max_workers = min(os.cpu_count() or 4, 8)
        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)

        def on_closing():
            self.executor.shutdown(wait=False)
            self.root.destroy()
        self.root.protocol("WM_DELETE_WINDOW", on_closing)"""

content = content.replace(init_search, init_replace)

# Use shared executor in check_audio
check_audio_search = """                    # 在子进程中运行 parselmouth，避免 C 扩展与主线程 GIL 冲突
                    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(check_audio_segments, path)
                        seg_count = future.result()"""
check_audio_replace = """                    # 使用全局线程池运行 parselmouth
                    future = self.executor.submit(check_audio_segments, path)
                    seg_count = future.result()"""
content = content.replace(check_audio_search, check_audio_replace)


# Use shared executor in recalculate_all_audio
recalc_search = """                if tasks:
                    with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                        futures = {}
                        for idx, task in enumerate(tasks):
                            f = executor.submit(
                                long_process_worker,
                                task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                                task['ms'], task['me'], params, trim_silence
                            )
                            futures[f] = idx

                        completed = 0
                        for future in concurrent.futures.as_completed(futures):
                            idx = futures[future]
                            res = future.result()"""
recalc_replace = """                if tasks:
                    futures = {}
                    for idx, task in enumerate(tasks):
                        f = self.executor.submit(
                            long_process_worker,
                            task['snd_values'], task['snd_sf'], task['pitch_xs'], task['pitch_freqs'],
                            task['ms'], task['me'], params, trim_silence
                        )
                        futures[f] = idx

                    completed = 0
                    for future in concurrent.futures.as_completed(futures):
                        idx = futures[future]
                        res = future.result()"""
content = content.replace(recalc_search, recalc_replace)

# Use shared executor in process_long_with_wordlist
long_search = """            # 多进程执行
            with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {}
                for idx, task in enumerate(tasks):
                    if not task.get('missing'):
                        f = executor.submit(
                            long_process_worker,
                            task['snd_values'], task['snd_sf'], pitch_xs, pitch_freqs,
                            task['ms'], task['me'], params, trim
                        )
                        futures[f] = idx

                # 等待完成
                completed_count = 0
                for future in concurrent.futures.as_completed(futures):
                    idx = futures[future]
                    res = future.result()"""
long_replace = """            # 多进程执行
            futures = {}
            for idx, task in enumerate(tasks):
                if not task.get('missing'):
                    f = self.executor.submit(
                        long_process_worker,
                        task['snd_values'], task['snd_sf'], pitch_xs, pitch_freqs,
                        task['ms'], task['me'], params, trim
                    )
                    futures[f] = idx

            # 等待完成
            completed_count = 0
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                res = future.result()"""
content = content.replace(long_search, long_replace)

# Use shared executor in start_background_batch_processing
bg_batch_search = """            with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:
                futures = {executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    p = futures[future]"""
bg_batch_replace = """            futures = {self.executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                p = futures[future]"""
content = content.replace(bg_batch_search, bg_batch_replace)

# Use shared executor in process_batch_direct
batch_direct_search = """            results = []
            futures = {}
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
            for i, p in enumerate(self.pending_batch_paths):
                if p in self.audio_cache:
                    results.append((i, self.audio_cache[p]))
                else:
                    futures[executor.submit(batch_process_worker, p, params, trim)] = i

            if futures:
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self.audio_cache[self.pending_batch_paths[orig_idx]] = res
                        results.append((orig_idx, res))
                    except Exception as e: print(f"Error: {e}")

                    if i % 2 == 0 or i == len(futures) - 1:
                        self.root.after(0, lambda v=(len(results))/total: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))

            executor.shutdown(wait=False)"""
batch_direct_replace = """            results = []
            futures = {}
            for i, p in enumerate(self.pending_batch_paths):
                if p in self.audio_cache:
                    results.append((i, self.audio_cache[p]))
                else:
                    futures[self.executor.submit(batch_process_worker, p, params, trim)] = i

            if futures:
                for i, future in enumerate(concurrent.futures.as_completed(futures)):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self.audio_cache[self.pending_batch_paths[orig_idx]] = res
                        results.append((orig_idx, res))
                    except Exception as e: print(f"Error: {e}")

                    if i % 2 == 0 or i == len(futures) - 1:
                        self.root.after(0, lambda v=(len(results))/total: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))"""
content = content.replace(batch_direct_search, batch_direct_replace)

# Use shared executor in process_batch_with_wordlist
batch_wordlist_search = """            futures = {}
            executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))
            for i, t in enumerate(tasks):
                if t['missing']:
                    results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
                else:
                    path = t['path']
                    if path in self.audio_cache:
                        res = self.audio_cache[path]
                        results[i] = {**res, 'missing': False, 'group': t['group']}
                    else:
                        futures[executor.submit(batch_process_worker, path, params, trim)] = i

            total_futures = len(futures) if futures else 1
            done_count = 0
            if futures:
                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self.audio_cache[tasks[orig_idx]['path']] = res
                        results[orig_idx] = {**res, 'missing': False, 'group': tasks[orig_idx]['group']}
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}

                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))

            executor.shutdown(wait=False)"""
batch_wordlist_replace = """            futures = {}
            for i, t in enumerate(tasks):
                if t['missing']:
                    results[i] = {'label': t['word'], 'group': t['group'], 'success': False, 'missing': True}
                else:
                    path = t['path']
                    if path in self.audio_cache:
                        res = self.audio_cache[path]
                        results[i] = {**res, 'missing': False, 'group': t['group']}
                    else:
                        futures[self.executor.submit(batch_process_worker, path, params, trim)] = i

            total_futures = len(futures) if futures else 1
            done_count = 0
            if futures:
                for future in concurrent.futures.as_completed(futures):
                    orig_idx = futures[future]
                    try:
                        res = future.result()
                        self.audio_cache[tasks[orig_idx]['path']] = res
                        results[orig_idx] = {**res, 'missing': False, 'group': tasks[orig_idx]['group']}
                    except Exception as e:
                        results[orig_idx] = {'label': tasks[orig_idx]['word'], 'group': tasks[orig_idx]['group'], 'success': False, 'missing': True, 'error': str(e)}

                    done_count += 1
                    self.root.after(0, lambda v=done_count/total_futures: self.set_progress(v))
            else:
                self.root.after(0, lambda: self.set_progress(1.0))"""
content = content.replace(batch_wordlist_search, batch_wordlist_replace)

with open('modules/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
